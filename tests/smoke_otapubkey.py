# A customer box verifies updates with the shipped OTA key, whatever a
# machine-writable file says.
#
# WHAT WAS WRONG. update.settings() read [signing].pubkey through config.load,
# which layers config/update.local.toml over the committed update.toml. The
# local file is the half the service account writes (the token page saves it
# through config.save_local), so a [signing].pubkey slipped into it -- a bug in
# any writer -- would make the box accept a bundle signed by someone else's
# key, and a bundle replaces the code that verifies everything else (Pipi,
# item 6).
#
# Pinned: on a customer box only PUBKEY_B64 and the committed update.toml
# count; "customer" is read from the committed deploy.toml, so a
# deploy.local.toml saying dev does not reopen it; a dev box still honours
# the local override; the job an update runs with carries the same key.
#
#     python tests/smoke_otapubkey.py
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core import config                                       # noqa: E402
from core.services import update                              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


SHIPPED = "U0hJUFBFRC1LRVktLS0tLS0tLS0tLS0tLS0tLS0tLS0="
EVIL = "RVZJTC1LRVktLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0="


def box(mode=None, local_mode=None, committed=True):
    d = tempfile.mkdtemp()
    if committed:
        with open(os.path.join(d, "update.toml"), "w", encoding="utf-8") as fh:
            fh.write('[signing]\npubkey = "%s"\n' % SHIPPED)
    with open(os.path.join(d, "update.local.toml"), "w", encoding="utf-8") as fh:
        fh.write('token = "x"\n[signing]\npubkey = "%s"\n' % EVIL)
    if mode:
        with open(os.path.join(d, "deploy.toml"), "w", encoding="utf-8") as fh:
            fh.write('mode = "%s"\n' % mode)
    if local_mode:
        with open(os.path.join(d, "deploy.local.toml"), "w", encoding="utf-8") as fh:
            fh.write('mode = "%s"\n' % local_mode)
    config.load.cache_clear()
    return d


d = box("customer")
check("customer box: update.local.toml's [signing].pubkey is ignored",
      update.signing_pubkey(d) == SHIPPED, update.signing_pubkey(d))
check("...and settings(), which every check and job reads, agrees",
      update.settings(d)["pubkey"] == SHIPPED)
d = box(None)
check("no deploy.toml reads as customer (fails closed)", update.signing_pubkey(d) == SHIPPED)
d = box("customer", local_mode="dev")
check("a deploy.local.toml saying dev does not reopen the override",
      update.signing_pubkey(d) == SHIPPED)
d = box("customer", committed=False)
check("no committed key: the constant, never the local one",
      update.signing_pubkey(d) == update.PUBKEY_B64)
d = box("dev")
check("a dev box still honours the local override (how a test key is tried)",
      update.signing_pubkey(d) == EVIL)

src = open(os.path.join(ROOT, "core", "services", "update.py"), encoding="utf-8").read()
settings_body = src.split("def settings(", 1)[1].split("\ndef ", 1)[0]
check("settings() takes the key from signing_pubkey(), not from the merged config",
      '"pubkey": signing_pubkey(config_dir)' in settings_body
      and 'sign.get("pubkey")' not in settings_body)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
