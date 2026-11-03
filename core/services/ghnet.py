# -*- coding: utf-8 -*-
"""
ghnet.py -- the one HTTP door to GitHub.

Two features fetch from GitHub with a token: the template store (bundles) and
the OTA updater (release assets). Both send `Authorization: Bearer <PAT>`, and
both follow redirects into S3 -- GitHub answers a private release asset with a
302 to a signed storage URL. Forwarding our Bearer header across that hop is
both broken (S3 rejects two credentials with a 400) and a credential leak to a
host that never needed it.

That rule is security-critical and must exist exactly once, which is why the
handler lives here rather than in each caller. core/services/store.py imports
it; core/services/update.py imports it.

Nothing here knows about templates, licenses or releases -- it opens URLs.
"""

import urllib.parse
import urllib.request

#: Hosts that may keep our Authorization header across a redirect. Anything
#: else (S3, CDNs) gets the request with the header removed.
GITHUB_HOSTS = ("github.com", "api.github.com", "raw.githubusercontent.com",
                "uploads.github.com", "objects.githubusercontent.com")

#: Default request timeout. GitHub is either quick or gone; a minute is
#: generous for the small JSON reads and each streamed chunk of a big one.
TIMEOUT_S = 60


class NoAuthAcrossHosts(urllib.request.HTTPRedirectHandler):
    """Drop Authorization when a redirect leaves GitHub.

    Private release assets 302 to a signed S3 URL; forwarding the Bearer
    header there makes S3 answer 400 (two credentials in one request). The
    signed URL IS the credential after the hop, so dropping ours is correct,
    not lax.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        host = urllib.parse.urlparse(newurl).hostname or ""
        if host not in GITHUB_HOSTS:
            new.remove_header("Authorization")
        return new


_opener = urllib.request.build_opener(NoAuthAcrossHosts)


def opener():
    """The shared opener, for a caller that must build its own Request (a PUT
    with a body, say). Going through this rather than urllib.request.urlopen
    keeps the redirect rule above in force -- that is the whole reason this
    module exists."""
    return _opener


def open_url(url, token=None, accept=None, agent="irivai", timeout=TIMEOUT_S,
             headers=None, method=None):
    """Open `url`, optionally authenticated. The caller closes the response.

    `headers` adds request headers (Range for a resumed download, If-None-Match
    for a cheap poll); it can never remove the redirect protection above.
    """
    head = {"User-Agent": agent}
    if accept:
        head["Accept"] = accept
    if token:
        head["Authorization"] = f"Bearer {token}"
    for k, v in (headers or {}).items():
        head[str(k)] = str(v)
    req = urllib.request.Request(url, headers=head, method=method)
    return _opener.open(req, timeout=timeout)
