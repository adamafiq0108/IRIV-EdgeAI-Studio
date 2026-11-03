# Store backend smoke test on the dev box, against the LIVE public repo.
import json
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# This one publishes and installs a SIGNED bundle, so it needs the owner's
# store signing key -- which exists only on the machine that publishes
# templates. A customer box and CI deliberately do not have it, so there the
# test SKIPS (exit 77) rather than failing: "no key here" is the correct state
# for those machines, not a regression.
SIGNING_KEY = pathlib.Path.home() / ".iriv" / "store_signing.key"
if not SIGNING_KEY.is_file():
    print(f"SKIP: no store signing key at {SIGNING_KEY} -- this test runs on "
          "the machine that publishes templates")
    raise SystemExit(77)

# Keep every store write in a scratch sandbox, away from the real repo config.
SAND = pathlib.Path(__file__).parent / "store_sandbox"
if SAND.exists():
    shutil.rmtree(SAND)
(SAND / "config").mkdir(parents=True)
os.environ["CYTRON_STORE"] = str(SAND / "store")
shutil.copy(ROOT / "config" / "store.toml", SAND / "config" / "store.toml")
shutil.copy(SIGNING_KEY, SAND / "config" / "store_signing.pem")

from core import config as config_mod                       # noqa: E402
# Point the DEFAULT config dir at the sandbox so save_local("store") lands
# there too. store.py writes last_seen_time and installs.<key> with no
# config_dir, i.e. to config.root(None) == config._CONFIG_DIR. This used to set
# a `DEFAULT_DIR` attribute config.py never had, so every run rewrote the
# REPO's config/store.local.toml -- the developer's own store state, and a
# file every other test reading the default store config could see change.
config_mod._CONFIG_DIR = SAND / "config"
import core.config                                          # noqa: E402
CFG = str(SAND / "config")

from core.services import store                             # noqa: E402

print("== crypto:", store.crypto_available())

# --- catalog against the real public repo ---------------------------------
cat = store.catalog(CFG, force=True)
print("== catalog offline:", cat["offline"], "| arch:", repr(cat["arch"]))
for t in cat["templates"]:
    print("   template:", t["key"], t["tier"], t["archs"],
          "compatible:", t["compatible"], "locked:", t["locked"])
assert any(t["key"] == "people_counting" for t in cat["templates"])

# --- license issue + verify -------------------------------------------------
issued = store.issue_license(CFG, "Test Sdn Bhd", "ABCDEF1234567890",
                             "pro", 365, "ghp_dummy_token_123")
key_text = issued["key"]
print("== issued key:", key_text[:40] + "...")
payload = store.parse_license(key_text, CFG)
assert payload["serial"] == "abcdef1234567890"
assert payload["tier"] == "pro"
print("== parse ok: expires", payload["expires_at"], "customer",
      payload["customer"])

# tamper: flip one char in the payload part
parts = key_text.split(".")
bad = parts[1][:-2] + ("A" if parts[1][-2] != "A" else "B") + parts[1][-1]
try:
    store.parse_license(".".join([parts[0], bad, parts[2]]), CFG)
    print("!! TAMPERED KEY ACCEPTED")
except store.StoreError as e:
    print("== tampered key refused:", str(e)[:60])

# PAT round-trip (decrypt like _pro_token would, with the key's serial)
import base64                                                # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
enc = store._unb64u(payload["pat_enc"])
pat = AESGCM(store._pat_key("abcdef1234567890")).decrypt(
    enc[:12], enc[12:], b"abcdef1234567890").decode()
assert pat == "ghp_dummy_token_123"
print("== PAT decrypts on matching serial")
try:
    AESGCM(store._pat_key("0000000000000000")).decrypt(
        enc[:12], enc[12:], b"0000000000000000")
    print("!! PAT OPENED ON WRONG SERIAL")
except Exception:
    print("== PAT refuses wrong serial")

# ledger
rows = store.issued_licenses(CFG)
print("== ledger rows:", len(rows), rows[0]["customer"], rows[0]["revoked"])
store.set_revoked(CFG, rows[0]["serial"], rows[0]["issued_at"], True)
assert store.issued_licenses(CFG)[0]["revoked"] is True
print("== revoke flag ok")

# --- internet time + activation guards -------------------------------------
now = store.internet_now()
print("== internet_now:", now)
try:
    store.activate_license(key_text, CFG)
    print("!! ACTIVATED WITHOUT DEVICE SERIAL")
except store.StoreError as e:
    print("== activation refused on dev box (no Pi serial):", str(e)[:60])

# --- full install with a mocked arch ----------------------------------------
from core.services import builtin_models                    # noqa: E402
builtin_models.detect_arch = lambda: "hailo8"
store.builtin_models.detect_arch = lambda: "hailo8"

sysdir = SAND / "systems"
sysdir.mkdir()
# create_vision_system scaffolds from the reference package -- point systems
# root at the real repo systems dir so 'demo' is found as reference.
status = store.start_install(str(ROOT / "systems"), CFG,
                             "people_counting", name="Store Demo")
import time                                                  # noqa: E402
while True:
    s = store.install_status("people_counting")
    print("   ", s.get("state"), s.get("pct"), s.get("step") or s.get("error"))
    if s.get("state") in ("done", "failed"):
        break
    time.sleep(2)
print("== final:", json.dumps(s, indent=1)[:600])
