# What the box's root certificate REFUSES to vouch for (core/services/tlscert.py,
# Pipi's HTTPS review M1/M2, 2026-10-10). smoke_https.py covers what it issues.
#
#   a. M1: a root made on a box named "ai" cannot sign claude.ai -- the bare
#      hostname is no permitted subtree -- while <host>.local and a private
#      address still validate; a bare name never counts as "missing"
#   b. M2: the root carries serverAuth and critical constraints that permit
#      e-mail only at `invalid` and URIs only on the local names; a leaf for
#      ceo@gmail.com, a code-signing leaf and a leaf with no EKU at all are
#      refused for anything but a web server
#   c. the real leaf still chains: OpenSSL verify -purpose sslserver, the
#      cryptography verifier, and a Python ssl handshake with hostname check
#
# Verifiers that are missing are skipped, each on its own line: OpenSSL (the
# `openssl` command) and cryptography.x509.verification (cryptography 42+).
#
#     python tests/smoke_httpsroot.py
import datetime
import ipaddress
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

try:
    from cryptography import x509                               # noqa: E402
    from cryptography.hazmat.primitives import hashes            # noqa: E402
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: E402
except ImportError:
    print("skip: cryptography not installed")
    sys.exit(77)
try:
    from cryptography.x509 import verification                  # noqa: E402
except ImportError:
    verification = None

from core.services import tlscert                               # noqa: E402

bad = 0
OPENSSL = shutil.which("openssl")
TMP = tempfile.mkdtemp()
NOW = tlscert.now_utc()


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def rogue(ca, ca_key, sans=None, eku=None):
    """A leaf signed by the root WITHOUT make_leaf's own filter: what a thief
    holding the root key could make."""
    key = tlscert.new_key()
    b = (x509.CertificateBuilder()
         .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "rogue")]))
         .issuer_name(ca.subject).public_key(key.public_key())
         .serial_number(x509.random_serial_number())
         .not_valid_before(NOW - tlscert.SKEW)
         .not_valid_after(NOW + datetime.timedelta(days=30))
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
             ca_key.public_key()), critical=False))
    if sans:
        b = b.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    if eku:
        b = b.add_extension(x509.ExtendedKeyUsage(eku), critical=False)
    return b.sign(ca_key, hashes.SHA256())


def openssl_ok(ca, leaf, purpose):
    """True/False from `openssl verify -purpose`, None without OpenSSL."""
    if not OPENSSL:
        return None
    paths = []
    for name, cert in (("ca.pem", ca), ("leaf.pem", leaf)):
        p = os.path.join(TMP, name)
        with open(p, "w", encoding="ascii", newline="\n") as fh:
            fh.write(tlscert.cert_pem(cert))
        paths.append(p)
    r = subprocess.run([OPENSSL, "verify", "-purpose", purpose, "-CAfile", paths[0],
                        paths[1]], capture_output=True, text=True, timeout=30)
    return r.returncode == 0


def crypto_ok(ca, leaf, target):
    """True/False from cryptography's server verifier, None without it."""
    if verification is None:
        return None
    try:
        ip = ipaddress.ip_address(target)
        subject = x509.IPAddress(ip)
    except ValueError:
        subject = x509.DNSName(target)
    try:
        (verification.PolicyBuilder().store(verification.Store([ca])).time(NOW)
         .build_server_verifier(subject).verify(leaf, []))
        return True
    except Exception:                                            # noqa: BLE001
        return False


def expect(label, got, want):
    if got is None:
        print("skip " + label + " (verifier not available)")
    else:
        check(label, got is want, got)


print("verifiers: openssl=%s, cryptography.x509.verification=%s"
      % (OPENSSL or "none", "yes" if verification else "no"))

# -- a. M1: no bare hostname --------------------------------------------------
ca, ca_key = tlscert.make_ca("m1", hostname="ai", now=NOW)
dns = {s.value for s in ca.extensions.get_extension_for_class(
    x509.NameConstraints).value.permitted_subtrees if isinstance(s, x509.DNSName)}
check("a box named 'ai': 'ai' is not a permitted subtree", "ai" not in dns, dns)
check("...allowed() refuses ai and claude.ai",
      not tlscert.allowed(ca, "ai") and not tlscert.allowed(ca, "claude.ai"))
try:
    tlscert.make_leaf(ca, ca_key, ["ai"], [], NOW)
    check("...make_leaf will not issue for the bare name alone", False)
except ValueError:
    check("...make_leaf will not issue for the bare name alone", True)
evil = rogue(ca, ca_key, [x509.DNSName("claude.ai")], [ExtendedKeyUsageOID.SERVER_AUTH])
expect("a rogue leaf for claude.ai: openssl refuses", openssl_ok(ca, evil, "sslserver"), False)
expect("a rogue leaf for claude.ai: cryptography refuses", crypto_ok(ca, evil, "claude.ai"), False)
leaf, leaf_key = tlscert.make_leaf(ca, ca_key, ["ai.local", "ai", "localhost"],
                                   ["127.0.0.1", "192.168.1.5"], NOW)
names, ips = tlscert.sans(leaf)
check("the real leaf: <host>.local and the address, no bare name",
      names == ["ai.local", "localhost"] and "192.168.1.5" in ips, (names, ips))
expect("ai.local validates (cryptography)", crypto_ok(ca, leaf, "ai.local"), True)
expect("192.168.1.5 validates (cryptography)", crypto_ok(ca, leaf, "192.168.1.5"), True)
check("a rename's bare name is never 'missing' (no re-issue for it)",
      tlscert.reissue_reason(leaf, ca, ["ai.local", "cam", "localhost"],
                             ["127.0.0.1", "192.168.1.5"], NOW) == "")

# -- b. M2: web servers only ----------------------------------------------------
eku = ca.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
check("root EKU: serverAuth only", list(eku) == [ExtendedKeyUsageOID.SERVER_AUTH], list(eku))
nc = ca.extensions.get_extension_for_class(x509.NameConstraints)
mail = [s.value for s in nc.value.permitted_subtrees if isinstance(s, x509.RFC822Name)]
uris = [s.value for s in nc.value.permitted_subtrees
        if isinstance(s, x509.UniformResourceIdentifier)]
check("constraints critical; e-mail only at 'invalid'", nc.critical and mail == ["invalid"], mail)
check("...URIs only on the local names",
      uris and all(u.lstrip(".") in tlscert.PERMITTED_DNS for u in uris), uris)
smime = rogue(ca, ca_key, [x509.RFC822Name("ceo@gmail.com")],
              [ExtendedKeyUsageOID.EMAIL_PROTECTION])
expect("a mail leaf for ceo@gmail.com: openssl smimesign refuses",
       openssl_ok(ca, smime, "smimesign"), False)
expect("...and refuses it for any purpose (name constraint)",
       openssl_ok(ca, smime, "any"), False)
code = rogue(ca, ca_key, None, [ExtendedKeyUsageOID.CODE_SIGNING])
expect("a code-signing leaf: refused as a web server", openssl_ok(ca, code, "sslserver"), False)
# A leaf with NO EKU is good for every purpose by itself; only the root's
# serverAuth stops it. OpenSSL applies a CA's EKU for the purposes below (its
# codesign purpose, 3.2+, does not look at the CA's EKU -- Windows does: a
# real-client check, see the review's "needs a real box" list).
anyleaf = rogue(ca, ca_key)
expect("a leaf with no EKU: refused for S/MIME by the root's EKU",
       openssl_ok(ca, anyleaf, "smimesign"), False)
expect("...and as a TLS client", openssl_ok(ca, anyleaf, "sslclient"), False)

# -- c. the real leaf still chains ------------------------------------------------
expect("the real leaf: openssl -purpose sslserver OK", openssl_ok(ca, leaf, "sslserver"), True)
ca_pem = os.path.join(TMP, "root.pem")
with open(ca_pem, "w", encoding="ascii", newline="\n") as fh:
    fh.write(tlscert.cert_pem(ca))
server = tlscert.server_context(tlscert.cert_pem(leaf), tlscert.key_pem(leaf_key), TMP)
for target in ("ai.local", "192.168.1.5"):
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)

    def serve(s=lsock):
        conn, _ = s.accept()
        try:
            with server.wrap_socket(conn, server_side=True) as tls:
                tls.recv(1)
        except (OSError, ssl.SSLError):
            pass

    threading.Thread(target=serve, daemon=True).start()
    client = ssl.create_default_context(cafile=ca_pem)
    try:
        with client.wrap_socket(socket.create_connection(lsock.getsockname(), timeout=10),
                                server_hostname=target) as tls:
            check(f"Python ssl handshake to {target}: chain and name OK", bool(tls.version()))
    except (OSError, ssl.SSLError) as exc:
        check(f"Python ssl handshake to {target}: chain and name OK", False, exc)
    lsock.close()

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
