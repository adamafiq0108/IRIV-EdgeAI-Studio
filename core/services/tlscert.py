# -*- coding: utf-8 -*-
"""
tlscert.py -- the box's own certificate authority and the server
certificate it issues itself. Bytes in, objects out: no files, no
settings, no clock but the one passed in (core/services/https.py owns
those).

WHY A LOCAL CA AND NOT ONE SELF-SIGNED CERTIFICATE (owner decision,
2026-10-10): the operator trusts the ROOT once per client, and the box may
then re-issue its server certificate for ever -- a new address from DHCP,
a new hostname, the yearly expiry -- without anybody touching a client
again. A self-signed leaf would have to be re-trusted at every one of those.

EC P-256 for both: every browser and OS this product meets (Windows 7+,
macOS/iOS, Android 4+, Firefox's own store) verifies ECDSA P-256 with
SHA-256, and the key is generated in a millisecond on a Pi, which matters
because a re-issue can happen inside a TLS handshake (https.py).

THE ROOT IS NAME-CONSTRAINED. Every client that trusts it would otherwise
accept ANY certificate it signs. The root may only vouch for the names a LAN
box can have (.local, localhost, home.arpa, .lan, .internal) and private,
link-local, CGNAT (Tailscale) and loopback addresses; the leaf is only ever
issued for names the root permits (`allowed`), so a public address the box
holds is left out and reported. NEVER THE BARE HOSTNAME (Pipi M1): a
permitted DNS name is a whole subtree and a hostname is any single label,
so a box called `ai` would get a root for every site under .ai; and
`<host>.local` already reaches the box.

WEB SERVERS ONLY (Pipi M2): ExtendedKeyUsage serverAuth on the root (Windows,
macOS, Chrome and Firefox apply it to the chain), e-mail names permitted only
under the reserved `invalid`, URIs only on the local names -- no code, mail
or client certificate. They are the ROOT's extensions: a client keeps the
ones it trusted, so changing them means "New root" and trusting again. A
leaked key still reaches every local name and private address on the LAN,
not only this box (docs/security-review-2026-08.md, item 5).
"""

import datetime
import hashlib
import ipaddress
import os
import ssl
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_DAYS = 3650
#: Browsers distrust a server certificate valid for more than 398 days.
LEAF_DAYS = 397
RENEW_DAYS = 30
#: A client whose clock is a little behind the box's must not see a
#: certificate that is "not yet valid".
SKEW = datetime.timedelta(hours=1)
#: A Pi without a set clock thinks it is 1970 (or the last shutdown); a
#: certificate issued then is expired or not yet valid everywhere else.
MIN_YEAR = 2025

PERMITTED_DNS = ("local", "localhost", "home.arpa", "lan", "internal")
PERMITTED_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                  "169.254.0.0/16", "100.64.0.0/10", "127.0.0.0/8",
                  "fc00::/7", "::1/128")
#: RFC 2606's reserved top-level domain: no mailbox can ever be there.
PERMITTED_EMAIL = "invalid"
#: TLS 1.2 suites with forward secrecy and an AEAD only; TLS 1.3 suites are
#: OpenSSL's defaults, which are all of that already.
CIPHERS = "ECDHE+AESGCM:ECDHE+CHACHA20:!aNULL:!eNULL:!MD5:!SHA1:!DSS"


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def _serial():
    return x509.random_serial_number()


def new_key():
    return ec.generate_private_key(ec.SECP256R1())


def key_pem(key):
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode("ascii")


def cert_pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def cert_der(cert):
    return cert.public_bytes(serialization.Encoding.DER)


def load_cert(pem):
    """A certificate from PEM text, or ValueError (empty, torn, not one)."""
    if not pem:
        raise ValueError("no certificate")
    return x509.load_pem_x509_certificate(str(pem).encode("ascii"))


def load_key(pem):
    if not pem:
        raise ValueError("no key")
    try:
        return serialization.load_pem_private_key(str(pem).encode("ascii"), None)
    except (TypeError, ValueError) as exc:
        raise ValueError("unreadable key") from exc


def key_matches(cert, key):
    pub = serialization.PublicFormat.SubjectPublicKeyInfo
    enc = serialization.Encoding.DER
    return cert.public_key().public_bytes(enc, pub) == key.public_key().public_bytes(enc, pub)


def fingerprint(cert):
    """SHA-256 of the DER, AB:CD:... -- what an OS shows beside a root."""
    digest = hashlib.sha256(cert_der(cert)).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def not_after(cert):
    """Aware UTC. cryptography 42 added the _utc form; older ones are naive."""
    return getattr(cert, "not_valid_after_utc", None) or \
        cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)


def _name_constraints():
    """DNS and IP as above; e-mail only at `invalid` (so none); a URI only
    with one of the DNS names as its host (`d` exactly, `.d` below it --
    RFC 5280 4.2.1.10 reads a URI subtree that way, unlike a DNS one)."""
    return x509.NameConstraints(
        permitted_subtrees=[x509.DNSName(d) for d in PERMITTED_DNS]
        + [x509.IPAddress(ipaddress.ip_network(n)) for n in PERMITTED_NETS]
        + [x509.RFC822Name(PERMITTED_EMAIL)]
        + [x509.UniformResourceIdentifier(u) for d in PERMITTED_DNS for u in (d, "." + d)],
        excluded_subtrees=None)


def make_ca(label, hostname="", now=None):
    """(cert, key): the root. CA:TRUE pathlen 0 (it signs leaves, never
    another CA), keyCertSign + cRLSign only, serverAuth only,
    name-constrained. `hostname` is accepted and deliberately NOT used:
    the bare name never becomes a permitted subtree (M1, see the header)."""
    now = now or now_utc()
    key = new_key()
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"IRIV EdgeAI Studio local CA {label}"[:64]),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "IRIV EdgeAI Studio (this box only)"),
    ])
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(_serial())
            .not_valid_before(now - SKEW)
            .not_valid_after(now + datetime.timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=False, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False), critical=True)
            .add_extension(_name_constraints(), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
            .sign(key, hashes.SHA256()))
    return cert, key


def _constraints(ca):
    try:
        return ca.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return None


def allowed(ca, value):
    """May this root vouch for `value` (a DNS name or an IP string)?"""
    nc = _constraints(ca)
    if nc is None:
        return True
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        ip = None
    for sub in nc.permitted_subtrees or ():
        if ip is not None and isinstance(sub, x509.IPAddress):
            if ip.version == sub.value.version and ip in sub.value:
                return True
        elif ip is None and isinstance(sub, x509.DNSName):
            base = sub.value.lower().lstrip(".")
            v = value.lower()
            if v == base or v.endswith("." + base):
                return True
    return False


def make_leaf(ca, ca_key, names, ips, now=None):
    """(cert, key) for this box: a FRESH key every time (nothing reused
    across re-issues), the names and addresses the root allows as SANs,
    serverAuth only, never valid past the root."""
    now = now or now_utc()
    names = [n for n in dict.fromkeys(n.lower() for n in names if n) if allowed(ca, n)]
    ips = [i for i in dict.fromkeys(ips) if allowed(ca, i)]
    if not names and not ips:
        raise ValueError("no name or address this root may vouch for")
    key = new_key()
    sans = [x509.DNSName(n) for n in names] + \
        [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    end = min(now + datetime.timedelta(days=LEAF_DAYS), not_after(ca))
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(
                NameOID.COMMON_NAME, (names or ips)[0][:64])]))
            .issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(_serial())
            .not_valid_before(now - SKEW).not_valid_after(end)
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    return cert, key


def sans(cert):
    """(names, ips) the certificate covers, as lists of strings."""
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return [], []
    return (list(ext.get_values_for_type(x509.DNSName)),
            [str(ip) for ip in ext.get_values_for_type(x509.IPAddress)])


def chains_to(leaf, ca):
    try:
        leaf.verify_directly_issued_by(ca)
        return True
    except Exception:                                        # noqa: BLE001
        return False


def reissue_reason(leaf, ca, names, ips, now=None):
    """Why the box should issue itself a new certificate, or "".

    Only a name or address the box HAS (and the root allows) that the
    certificate LACKS counts: one that went away (a cable pulled, the
    hotspot off) breaks nothing, and re-issuing for it would churn."""
    now = now or now_utc()
    if leaf is None:
        return "no certificate yet"
    if not chains_to(leaf, ca):
        return "issued by another root"
    if not_after(leaf) - now < datetime.timedelta(days=RENEW_DAYS):
        return "expires within %d days" % RENEW_DAYS
    have_n, have_i = sans(leaf)
    have = {n.lower() for n in have_n} | {str(ipaddress.ip_address(i)) for i in have_i}
    want = [n.lower() for n in names if n and allowed(ca, n)] + \
        [str(ipaddress.ip_address(i)) for i in ips if allowed(ca, i)]
    missing = [w for w in want if w not in have]
    if missing:
        return "new name or address: " + ", ".join(missing[:4])
    return ""


def server_context(cert_pem_text, key_pem_text, workdir):
    """An ssl.SSLContext serving this certificate: TLS 1.2 minimum, AEAD +
    forward-secret suites, no compression, no renegotiation. OpenSSL loads
    a key only from a file, so it is written to a 0600 temp file in
    `workdir` (the config directory, where the key already lives -- never
    /tmp) and removed at once. Raises ssl.SSLError / ValueError / OSError."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers(CIPHERS)
    ctx.options |= ssl.OP_NO_COMPRESSION | getattr(ssl, "OP_NO_RENEGOTIATION", 0)
    fd, path = tempfile.mkstemp(prefix=".https-load-", suffix=".pem", dir=workdir)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as fh:
            fh.write(cert_pem_text.strip() + "\n" + key_pem_text.strip() + "\n")
        ctx.load_cert_chain(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return ctx
