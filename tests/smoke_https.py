# HTTPS with the box's own root certificate (core/services/tlscert.py,
# https.py, httpsrun.py): what is issued, when it is issued again, and where
# the keys live. No network, no server (smoke_httpslive.py does the
# handshake, smoke_httpsroutes.py the card's routes).
#
#   a. the root: CA:TRUE pathlen 0, keyCertSign + cRLSign, critical name
#      constraints WITHOUT the bare hostname, 10 years, EC P-256, the
#      subject names the box (smoke_httpsroot.py: what the root refuses)
#   b. the leaf: chains to the root, SANs = the names/addresses the root
#      allows (a public address is left out), serverAuth, digitalSignature,
#      <= 397 days, never past the root, a fresh key every issue
#   c. re-issue: near expiry, a new name or address, another root, damage --
#      and NOT for a vanished address or nothing at all
#   d. the file: off by default, 0600, atomic (no temp file left, a failed
#      write leaves the old file), never logged, the public root only
#   e. damage: an unreadable file or root fails closed with a sentence;
#      settings() still reads OFF so the hub starts on HTTP; a new root
#      recovers; a damaged leaf is simply issued again
#   f. cooldown on "make a new one"; a clock that is not set refuses
#
#     python tests/smoke_https.py
import datetime
import io
import ipaddress
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

try:
    from cryptography import x509                               # noqa: E402
    from cryptography.hazmat.primitives.asymmetric import ec    # noqa: E402
    from cryptography.x509.oid import ExtendedKeyUsageOID       # noqa: E402
except ImportError:
    print("skip: cryptography not installed")
    sys.exit(77)

from core import config                                         # noqa: E402
from core.services import https, httpsrun, tlscert, tlsnames    # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


NOW = datetime.datetime(2026, 10, 10, 12, 0, tzinfo=datetime.timezone.utc)
NAMES = ["iriv-abcd1234.local", "localhost"]
IPS = ["127.0.0.1", "::1", "192.168.1.234", "fd00::12"]
https._label = lambda: "abcd1234"


def ext(cert, cls):
    return cert.extensions.get_extension_for_class(cls)


# -- a. the root ----------------------------------------------------------------
ca, ca_key = tlscert.make_ca("abcd1234", "iriv-abcd1234", NOW)
bc = ext(ca, x509.BasicConstraints)
check("root: CA:TRUE, pathlen 0, critical",
      bc.critical and bc.value.ca and bc.value.path_length == 0)
ku = ext(ca, x509.KeyUsage)
check("root: keyCertSign + cRLSign only, critical",
      ku.critical and ku.value.key_cert_sign and ku.value.crl_sign
      and not ku.value.digital_signature)
nc = ext(ca, x509.NameConstraints)
dns = {s.value for s in nc.value.permitted_subtrees if isinstance(s, x509.DNSName)}
check("root: name constraints, critical, the LAN names, never the bare hostname (M1)",
      nc.critical and {"local", "localhost"} <= dns and "iriv-abcd1234" not in dns, dns)
check("root: 10 years, EC P-256, a subject naming the box",
      (tlscert.not_after(ca) - NOW).days == tlscert.CA_DAYS
      and isinstance(ca.public_key(), ec.EllipticCurvePublicKey)
      and ca.public_key().curve.name == "secp256r1"
      and "IRIV EdgeAI Studio local CA abcd1234" in ca.subject.rfc4514_string())
for value, want in (("box.local", True), ("localhost", True), ("iriv-abcd1234", False),
                    ("google.com", False), ("evil.localx", False), ("10.1.2.3", True),
                    ("192.168.9.9", True), ("100.101.102.103", True), ("8.8.8.8", False),
                    ("fd12::1", True), ("2001:db8::1", False)):
    check(f"constraints: {value} {'allowed' if want else 'refused'}",
          tlscert.allowed(ca, value) is want)

# -- b. the leaf ----------------------------------------------------------------
leaf, leaf_key = tlscert.make_leaf(ca, ca_key, NAMES, IPS + ["8.8.8.8"], NOW)
check("leaf chains to the root", tlscert.chains_to(leaf, ca))
other, _ = tlscert.make_ca("other", "", NOW)
check("...and not to another root", not tlscert.chains_to(leaf, other))
n, i = tlscert.sans(leaf)
check("leaf SANs: every name and every allowed address, the public one left out",
      n == NAMES and i == IPS, (n, i))
check("leaf subject is <host>.local",
      leaf.subject.rfc4514_string() == "CN=iriv-abcd1234.local")
eku = ext(leaf, x509.ExtendedKeyUsage).value
check("leaf EKU serverAuth only", list(eku) == [ExtendedKeyUsageOID.SERVER_AUTH])
lku = ext(leaf, x509.KeyUsage)
check("leaf: digitalSignature, not a CA",
      lku.critical and lku.value.digital_signature and not lku.value.key_cert_sign
      and not ext(leaf, x509.BasicConstraints).value.ca)
days = (tlscert.not_after(leaf) - NOW).days
check("leaf valid <= 397 days", days == tlscert.LEAF_DAYS, days)
late = NOW + datetime.timedelta(days=tlscert.CA_DAYS - 10)
short, _ = tlscert.make_leaf(ca, ca_key, NAMES, IPS, late)
check("...and never past the root", tlscert.not_after(short) <= tlscert.not_after(ca))
leaf2, key2 = tlscert.make_leaf(ca, ca_key, NAMES, IPS, NOW)
check("a fresh key every issue", tlscert.key_pem(key2) != tlscert.key_pem(leaf_key)
      and tlscert.key_matches(leaf2, key2) and not tlscert.key_matches(leaf2, leaf_key))

# -- c. re-issue ----------------------------------------------------------------
R = tlscert.reissue_reason
check("nothing changed: no re-issue", R(leaf, ca, NAMES, IPS, NOW) == "")
check("an address went away: no re-issue", R(leaf, ca, NAMES, IPS[:3], NOW) == "")
check("a public address the root may not name: no re-issue (no churn)",
      R(leaf, ca, NAMES, IPS + ["8.8.4.4"], NOW) == "")
check("a new DHCP address: re-issue",
      "192.168.1.77" in R(leaf, ca, NAMES, IPS + ["192.168.1.77"], NOW))
check("a new hostname: re-issue",
      "new name" in R(leaf, ca, ["iriv-new.local"] + NAMES, IPS, NOW))
soon = tlscert.not_after(leaf) - datetime.timedelta(days=29)
check("within 30 days of expiry: re-issue", "expires" in R(leaf, ca, NAMES, IPS, soon))
check("31 days before: not yet",
      R(leaf, ca, NAMES, IPS, tlscert.not_after(leaf) - datetime.timedelta(days=31)) == "")
check("signed by another root: re-issue", "another root" in R(leaf, other, NAMES, IPS, NOW))

# -- d. the file ------------------------------------------------------------------
CFG = tempfile.mkdtemp()
st = https.settings(CFG)
check("nothing set: HTTPS ON on port 8443, and the redirect with it (one switch)",
      st == {"enabled": True, "port": 8443, "redirect_http": True,
             "plain_http_chosen": False, "error": ""}, st)
check("...and no file, no key yet: settings() generates nothing (the listener does at start)",
      not os.path.exists(https.path(CFG)) and httpsrun.status(CFG)["root"] is None)
done = https.ensure(CFG, NAMES, IPS, NOW)
check("ensure on an empty box: a root and a certificate", done == {
      "issued": True, "reason": "new root"}, done)
P = str(https.path(CFG))
check("the file is 0600", os.name == "nt" or (os.stat(P).st_mode & 0o777) == 0o600,
      oct(os.stat(P).st_mode))
check("no temp file left beside it",
      [f for f in os.listdir(CFG) if f.startswith(".https")] == [], os.listdir(CFG))
check("ensure again: nothing to do", https.ensure(CFG, NAMES, IPS, NOW)["issued"] is False)
check("...a new address: issued again",
      https.ensure(CFG, NAMES, IPS + ["10.0.0.5"], NOW)["issued"] is True)
check("it is a *.local.toml in config/, so every update carries it and git ignores it",
      P.endswith("https.local.toml") and os.path.dirname(P) == CFG)
gitignore = io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
check(".gitignore covers config/*.local.toml", "config/*.local.toml" in gitignore)
before = io.open(P, encoding="utf-8").read()
real_replace = os.replace


def broken_replace(a, b):
    raise OSError("disk full")


os.replace = broken_replace
try:
    https.ensure(CFG, NAMES, IPS + ["10.0.0.6"], NOW)
    check("a failed write raises", False)
except OSError:
    check("a failed write raises", True)
finally:
    os.replace = real_replace
check("...and leaves the old file whole and no temp file",
      io.open(P, encoding="utf-8").read() == before
      and [f for f in os.listdir(CFG) if f.startswith(".https")] == [])
status = httpsrun.status(CFG)
text = repr(status)
check("status: fingerprint, subject, leaf SANs and expiry",
      len(status["root"]["fingerprint"]) == 95 and "abcd1234" in status["root"]["subject"]
      and "192.168.1.234" in status["leaf"]["ips"] and status["leaf"]["not_after"], status)
check("status never carries a key", "PRIVATE KEY" not in text and "key" not in status)
pem = tlscert.cert_pem(https.root_cert(CFG))
check("the root served is the public certificate only",
      pem.startswith("-----BEGIN CERTIFICATE-----") and "PRIVATE" not in pem)
cert_pem, key_pem = https.material(CFG)
check("material(): the leaf and its key for the listener",
      "BEGIN CERTIFICATE" in cert_pem and "PRIVATE KEY" in key_pem)
ctx = tlscert.server_context(cert_pem, key_pem, CFG)
import ssl                                                       # noqa: E402
check("the context: TLS 1.2 minimum, and the key file is gone again",
      ctx.minimum_version == ssl.TLSVersion.TLSv1_2
      and [f for f in os.listdir(CFG) if f.startswith(".https")] == [])

# -- e. damage ---------------------------------------------------------------------
https.set_enabled(True, CFG)
check("enabled reads back", https.settings(CFG)["enabled"] is True)
doc = config.load_local("https", CFG)
doc["leaf"]["cert"] = "-----BEGIN CERTIFICATE-----\ngarbage\n-----END CERTIFICATE-----\n"
https.write(doc, CFG)
check("a damaged certificate is issued again, the root kept",
      https.ensure(CFG, NAMES, IPS, NOW) == {"issued": True, "reason": "damaged"}
      and tlscert.fingerprint(https.root_cert(CFG)) == status["root"]["fingerprint"])
doc = config.load_local("https", CFG)
doc["ca"]["key"] = "not a key"
https.write(doc, CFG)
try:
    https.ensure(CFG, NAMES, IPS, NOW)
    check("a damaged root fails closed (no silent new root)", False)
except https.HttpsError as exc:
    check("a damaged root fails closed (no silent new root)", "damaged" in str(exc), exc)
io.open(P, "w", encoding="utf-8").write("[https\nenabled = true\n")
st = https.settings(CFG)
check("an unreadable file reads as OFF with the reason (HTTP carries on)",
      st["enabled"] is False and "cannot be read" in st["error"], st)
check("...the card says so", "cannot be read" in httpsrun.status(CFG)["error"])
try:
    https.material(CFG)
    check("...and the listener gets an error, not a crash", False)
except https.HttpsError:
    check("...and the listener gets an error, not a crash", True)
check("a new root recovers a damaged file",
      https.ensure(CFG, NAMES, IPS, NOW, new_root=True)["reason"] == "new root"
      and https.root_cert(CFG) is not None)

# -- f. cooldown and the clock ----------------------------------------------------
t = [1000.0]
https._state["last"] = {"leaf": None, "root": None}
real_current = tlsnames.current
tlsnames.current = lambda: (NAMES, IPS)
try:
    first = https.regenerate("leaf", CFG, clock=lambda: t[0])
    t[0] += 5
    try:
        https.regenerate("leaf", CFG, clock=lambda: t[0])
        check("a second new certificate within the cooldown is refused", False)
    except https.TooSoon as exc:
        check("a second new certificate within the cooldown is refused", "try again" in str(exc))
    t[0] += 20
    check("...and allowed after it", https.regenerate("leaf", CFG, clock=lambda: t[0])["issued"])
    fp = tlscert.fingerprint(https.root_cert(CFG))
    https.regenerate("root", CFG, clock=lambda: t[0])
    check("a new root is a different root", tlscert.fingerprint(https.root_cert(CFG)) != fp)
finally:
    tlsnames.current = real_current
EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
check("a clock that reads 1970 still serves the certificate it has",
      https.ensure(CFG, NAMES, IPS, EPOCH)["issued"] is False)
try:
    https.ensure(CFG, NAMES + ["new.local"], IPS, EPOCH)
    check("...but refuses to issue one", False)
except https.HttpsError as exc:
    check("...but refuses to issue one", "clock" in str(exc))

# -- the box's names -------------------------------------------------------------
proc = os.path.join(CFG, "if_inet6")
io.open(proc, "w").write(
    "fd000000000000000000000000000012 02 40 00 80     eth0\n"   # ULA, permanent
    "20010db8000000000000000000000099 02 40 00 01     eth0\n"   # temporary
    "fe800000000000000000000000000001 02 40 20 80     eth0\n"   # link-local
    "00000000000000000000000000000001 01 80 10 80       lo\n")
check("IPv6: the stable ULA only (no temporary, link-local or loopback)",
      tlsnames.ipv6s(proc) == ["fd00::12"], tlsnames.ipv6s(proc))
names, ips = tlsnames.current()
check("current(): <host>.local first, localhost and loopback always",
      names[0].endswith(".local") and "localhost" in names and ips[:2] == ["127.0.0.1", "::1"]
      and all(ipaddress.ip_address(x) for x in ips), (names, ips))
check("current(): never the bare hostname (a rename is not a missing name)",
      all("." in n or n == "localhost" for n in names), names)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
