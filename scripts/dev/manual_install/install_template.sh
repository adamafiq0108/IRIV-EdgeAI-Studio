#!/usr/bin/env bash
# install_v@@VER@@.sh -- install IRIV EdgeAI Studio v@@VER@@ on ONE box, by hand.
#
# UNSIGNED, DEVELOPER/BOX-ONLY INSTALL. The package beside this script was
# built on the release laptop and carries no OTA signature. It is not an
# over-the-air update and the Update card is not involved. It installs by the
# same blue/green layout the signed updater uses (scripts/apply_update.py),
# calling that file's own functions for every step that touches the box's
# settings, models and system app copies:
#
#   1. new release directory <releases>/v@@VER@@ (refused if it exists)
#   2. its own venv, offline, from the wheels the running release carries
#      (requirements.txt is unchanged since 1.0.36), plus the Hailo runtime
#      carried across exactly as the updater does
#   3. this box's config/*.local.toml and deploy.toml carried in (twice: now,
#      and again just before the flip), model files carried in
#   4. the release's root helpers installed by its own root_steps.py --tree
#      (the documented by-hand path; nothing else in /etc is touched), run
#      from a root-only copy of the package's scripts/ and VERSION, never
#      from the release directory the service account owns (Pipi F-1a)
#   5. each store system's app code snapshotted for a rollback
#   6. LAST: the symlink flips, the hub restarts and must report @@VER@@,
#      the store systems get the new app code, the masters that were running
#      are stopped and started again ONE AT A TIME, each system that was
#      detecting is started after the Hailo service has been up a few seconds
#
# usage (as pi, from the folder holding both files):
#   sudo bash install_v@@VER@@.sh irivai-v@@VER@@-box-unsigned.tar.gz
#   sudo bash install_v@@VER@@.sh --rollback        # back to the previous release
#
# Optional environment:
#   IRIV_APP_ORDER="asri_test_4 pcb_sensata people_counting"   start order
#   IRIV_ALLOW_DEV_BOX=1   install even though the running release has core/dev
set -euo pipefail
umask 022

VER="@@VER@@"
PKG_SHA256="@@SHA256@@"
APP_ORDER="${IRIV_APP_ORDER:-asri_test_4 pcb_sensata people_counting}"
LOG="/var/log/irivai-manual-v${VER}.log"

say()  { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '    WARNING: %s\n' "$*" >&2; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run it with sudo: sudo bash $0 ..."
( umask 077; : >> "$LOG" ); chmod 600 "$LOG"     # root-only log (Pipi R-3)
exec > >(tee -a "$LOG") 2>&1
echo "---- $(date -Is) $0 $*"

# One EXIT handler for everything this run may leave behind; the steps below
# switch its parts on and off instead of replacing the trap.
ROOTCOPY=""             # root-only copy of the package (Pipi F-1a)
HELPERS=""              # root-owned, world-readable helper scripts (Pipi R-1)
CLEAN_UNFLIPPED=0       # before the flip: remove what was made
CLEAN_MAINT=0           # after the flip: remove the maintenance marker
on_exit() {
    if (( CLEAN_UNFLIPPED )); then cleanup_unflipped; fi
    if (( CLEAN_MAINT )); then rm -f "$UPDATE_DIR/maintenance"; fi
    if [[ -n "$ROOTCOPY" ]]; then rm -rf -- "$ROOTCOPY"; fi
    if [[ -n "$HELPERS" ]]; then rm -rf -- "$HELPERS"; fi
}
trap on_exit EXIT

# The install record a --rollback reads (Pipi F-1b): root-owned, 0600, in a
# root-owned directory, plain KEY=value lines. It is PARSED, never sourced,
# and every value is checked on the way in and on the way out.
RB_DIR="/var/lib/irivai"
RB_FILE="${RB_DIR}/manual-v${VER}.env"

check_value() {   # check_value <key> <value>: silent if fine, else says why
    local k="$1" v="$2"
    if [[ -z "$v" ]]; then echo "$k is empty"; return 1; fi
    case "$v" in
        *"'"*|*'"'*|*'$'*|*'`'*|*\\*|*$'\n'*|*$'\r'*)
            echo "$k contains a quote, a \$, a backtick, a backslash or a line break: $(printf '%q' "$v")"
            return 1 ;;
    esac
    case "$k" in
        HUB_PORT) [[ "$v" =~ ^[0-9]{1,5}$ ]] ;;
        PREV_VER) [[ "$v" =~ ^[0-9A-Za-z._+-]{1,40}$ ]] ;;
        PREV|STORE) [[ "$v" == /* ]] ;;
        *) echo "unknown key $(printf '%q' "$k")"; return 1 ;;
    esac || { echo "$k has an unexpected shape: $(printf '%q' "$v")"; return 1; }
}

rb_dir_ok() {   # the record's directory: root-owned, writable by root only
    [[ -d "$RB_DIR" || -L "$RB_DIR" ]] || install -d -m 755 -o root -g root "$RB_DIR"
    local own mode
    own="$(stat -c %u "$RB_DIR")"; mode="$(stat -c %a "$RB_DIR")"
    if ! [[ "$own" == 0 && -d "$RB_DIR" && ! -L "$RB_DIR" ]] || (( (8#$mode & 8#022) != 0 )); then
        die "$RB_DIR is not a root-owned directory writable by root only (owner uid $own, mode $mode) - refusing"
    fi
}

write_rollback_record() {
    local kv msg tmp
    rb_dir_ok
    for kv in "PREV=$PREV" "PREV_VER=$PREV_VER" "HUB_PORT=$HUB_PORT" "STORE=$STORE"; do
        msg="$(check_value "${kv%%=*}" "${kv#*=}")" || die "not recording the install: $msg"
    done
    tmp="$(mktemp "${RB_FILE}.XXXXXX")"          # 0600, root, same directory
    printf '%s\n' "PREV=$PREV" "PREV_VER=$PREV_VER" "HUB_PORT=$HUB_PORT" "STORE=$STORE" > "$tmp"
    chown root:root "$tmp"; chmod 600 "$tmp"
    mv -f -- "$tmp" "$RB_FILE"
    note "install record: $RB_FILE (root, 0600)"
}

read_rollback_record() {   # sets PREV PREV_VER HUB_PORT STORE, or dies
    local line k v n=0 msg
    [[ -e "$RB_FILE" || -L "$RB_FILE" ]] || die "no install record at $RB_FILE - nothing to roll back"
    rb_dir_ok
    [[ -f "$RB_FILE" && ! -L "$RB_FILE" ]] || die "$RB_FILE is not a regular file - refusing"
    [[ "$(stat -c '%u %a' "$RB_FILE")" == "0 600" ]] \
        || die "$RB_FILE is not root-owned 0600 ($(stat -c 'uid %u mode %a' "$RB_FILE")) - refusing"
    PREV="" PREV_VER="" HUB_PORT="" STORE=""
    while IFS= read -r line || [[ -n "$line" ]]; do
        n=$((n + 1))
        [[ -z "$line" ]] && continue
        [[ "$line" == *=* ]] || die "$RB_FILE line $n is not KEY=value - refusing"
        k="${line%%=*}"; v="${line#*=}"
        msg="$(check_value "$k" "$v")" || die "$RB_FILE line $n: $msg - refusing"
        case "$k" in
            PREV) PREV="$v" ;;
            PREV_VER) PREV_VER="$v" ;;
            HUB_PORT) HUB_PORT="$v" ;;
            STORE) STORE="$v" ;;
        esac
    done < "$RB_FILE"
    [[ -n "$PREV" && -n "$PREV_VER" && -n "$HUB_PORT" && -n "$STORE" ]] \
        || die "$RB_FILE lacks one of PREV PREV_VER HUB_PORT STORE - refusing"
}

MODE="install"
PKG=""
case "${1:-}" in
    --rollback) MODE="rollback" ;;
    -h|--help|"") sed -n '2,33p' "$0"; exit 0 ;;
    *) PKG="$(readlink -f "$1")" ;;
esac

# ---------------------------------------------------------------------------
# Where things are: /etc/irivai/update.conf (written by install.sh), else the
# product defaults. Only absolute values are taken, as recover.py does.
# ---------------------------------------------------------------------------
LIVE_LINK="/opt/irivai-systor"
RELEASES_DIR="/opt/irivai-releases"
UPDATE_DIR=""
if [[ -r /etc/irivai/update.conf ]]; then
    while IFS='=' read -r k v; do
        [[ "$v" == /* ]] || continue
        case "$k" in
            LIVE_LINK) LIVE_LINK="$v" ;;
            RELEASES_DIR) RELEASES_DIR="$v" ;;
            UPDATE_DIR) UPDATE_DIR="$v" ;;
        esac
    done < /etc/irivai/update.conf
fi
TARGET="${RELEASES_DIR}/v${VER}"

SVC_USER="$(systemctl show -p User --value irivai-hub.service 2>/dev/null || true)"
[[ -n "$SVC_USER" ]] || SVC_USER="$(stat -c %U "$(readlink -f "$LIVE_LINK")")"
SVC_GROUP="$(id -gn "$SVC_USER")"
SVC_HOME="$(getent passwd "$SVC_USER" | cut -d: -f6)"
# The hub unit's own environment (CYTRON_STORE and the like): every helper
# below must resolve the store exactly as the hub does.
HUB_ENV=()
for kv in $(systemctl show -p Environment --value irivai-hub.service 2>/dev/null || true); do
    HUB_ENV+=("$kv")
done

as_svc() {   # as_svc <cwd> <cmd...>
    # setsid -w (Pipi I-3): the service account's commands run in a session of
    # their own with no controlling terminal, so nothing they do can push
    # keystrokes (TIOCSTI) into the staff's root terminal. -w waits and hands
    # back the command's exit status; stdin and stdout pass through unchanged.
    local cwd="$1"; shift
    ( cd "$cwd" && setsid -w runuser -u "$SVC_USER" -- env HOME="$SVC_HOME" "${HUB_ENV[@]}" "$@" )
}

health_version() {   # health_version <port> -> version or empty
    curl -s --max-time 3 "http://127.0.0.1:$1/api/health" 2>/dev/null \
        | python3 -I -c 'import json,re,sys
try: v = str(json.load(sys.stdin).get("version") or "")
except Exception: v = ""
print(v if re.fullmatch(r"[0-9A-Za-z._+-]{1,40}", v) else "")' 2>/dev/null || true
}

wait_hub() {   # wait_hub <port> <version> <seconds>
    local port="$1" want="$2" budget="$3" seen="" t=0
    while (( t < budget )); do
        seen="$(health_version "$port")"
        [[ "$seen" == "$want" ]] && { note "hub on :$port reports $seen"; return 0; }
        sleep 3; t=$((t + 3))
    done
    warn "hub on :$port reports '${seen:-nothing}', expected $want"
    return 1
}

unit_masters() {   # systems with a systemd unit of their own (/etc/irivai/<slug>.env)
    ls /etc/irivai 2>/dev/null | sed -n 's/^\([a-z0-9_-]*\)\.env$/\1/p'
}

restart_hub() {
    systemctl restart irivai-hub.service
}

restart_units() {
    for slug in $(unit_masters); do
        note "restarting irivai-master@${slug}.service"
        systemctl restart "irivai-master@${slug}.service" || warn "irivai-master@${slug} did not restart"
    done
}

# Write the two small Python helpers into a root-owned directory (make_helpers,
# Pipi R-1): never into $STATE, which the service account owns, so root never
# writes through a name it could have planted. Both run as the service
# account, which can read them but not change them.
# helper.py drives scripts/apply_update.py's own functions;
# revive.py stops and starts systems through core/services (provision,
# detector_control), the same calls the updater and the Hub buttons use.
write_helpers() {
    local dir="$1"
    cat > "$dir/helper.py" <<'PY'
import json, os, sys, types
cmd, tree = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(tree, "scripts"))
import apply_update as au
a = json.loads(sys.argv[3])
job = types.SimpleNamespace(link=a["link"], store_dir=a["store"],
                            config_dir=a.get("config_dir", ""), work=a["state"],
                            started=0, version=a.get("version", ""))
if cmd == "prepare":
    au.build_venv(job, a["target"])
    print("venv built")
    rt = au.carry_runtime(job, a["prev"], a["target"])
    print("accelerator runtime carried:", ", ".join(rt) or "nothing needed")
    print("config carried:", ", ".join(au.carry_config(a["prev"], a["target"])) or "none")
    print("model files carried:", len(au.carry_models(a["prev"], a["target"])))
elif cmd == "record":
    masters = au.running_masters(job)
    # "Detecting" = the master says its AI is on (/api/health ai_running).
    # The updater's own running_systems() also counts an app that runs its
    # workflow with the AI switched off, and starting those would turn
    # their AI on. Shown for information only.
    detecting = sorted(s for s, p in masters.items()
                       if (au.health(p, timeout=3) or {}).get("ai_running"))
    print("apps alive (AI on or off):", au.running_systems(job) or "none")
    with open(os.path.join(a["state"], "plan.json"), "w", encoding="utf-8") as f:
        json.dump({"masters": masters, "detecting": detecting}, f, indent=1)
    print("masters answering:", masters or "none")
    print("systems detecting:", detecting or "none")
elif cmd == "snapshot":
    print("app code snapshot:", au.snapshot_apps(job, os.path.join(a["state"], "prev-apps")) or "none")
elif cmd == "carry":
    print("config carried:", ", ".join(au.carry_config(a["prev"], a["target"])) or "none")
    if a.get("models"):
        print("model files carried:", len(au.carry_models(a["prev"], a["target"])))
elif cmd == "sync":
    print("store systems given the new app code:", au.sync_apps(job, a["target"]) or "none")
elif cmd == "credcheck":
    # Camera passwords in the state.json files the NEW apps wrote (Pipi H2).
    # Prints PASS/FAIL per system and never the secret itself.
    import re, time
    masks = ("***", "__redacted__")
    since = float(a.get("since") or 0)
    url = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://([^\s\"]+)")
    bare = re.compile(r"(?<![/\w])[\w.-]+:([^\s@/]+)@\d{1,3}\.\d{1,3}\.")
    def leaks(text):
        n = 0
        for m in url.finditer(text):
            rest = m.group(1)
            at = rest.rfind("@")
            if at > 0:
                user, colon, pw = rest[:at].partition(":")
                if colon and pw and pw not in masks:
                    n += 1
        n += sum(1 for m in bare.finditer(text) if m.group(1) not in masks)
        return n
    judged = failed = 0
    for slug, pkg in sorted(au.system_packages(job).items()):
        path = os.path.join(au._data_dir(pkg), "state.json")
        try:
            raw = open(path, encoding="utf-8").read()
            beat = float(json.loads(raw).get("heartbeat") or 0)
        except (OSError, ValueError):
            print(f"    {slug}: no state.json, not judged")
            continue
        if beat < since:
            print(f"    {slug}: state.json not rewritten since the restart (app not running), not judged")
            continue
        judged += 1
        n = leaks(raw)
        if n:
            failed += 1
            print(f"    FAIL {slug}: {n} camera credential(s) in plain text in {path}")
        else:
            print(f"    PASS {slug}: no camera password in state.json")
    print(f"    camera password check: {'FAIL' if failed else 'PASS'} ({judged} state.json judged)")
    sys.exit(1 if failed else 0)
elif cmd == "restore":
    au.restore_apps(job, os.path.join(a["state"], "prev-apps"))
    print("store systems' app code restored")
else:
    raise SystemExit("unknown helper command " + cmd)
PY
    cat > "$dir/revive.py" <<'PY'
import json, os, pathlib, subprocess, sys, time, urllib.request
phase, repo, cfgdir, plan_path, version, order = sys.argv[1:7]
sys.path.insert(0, repo)
sys.path.insert(0, os.path.join(repo, "scripts"))
from core import config
from core.services import provision, detector_control as dc
import apply_update as au
def P(*a, **k):
    print(time.strftime('%H:%M:%S'), *a, flush=True)
try:
    from core.services import sysconfig
    sysconfig.export_location(cfgdir)
except Exception:
    os.environ["IRIVAI_BOX_CONFIG"] = str(pathlib.Path(cfgdir).resolve())
P("IRIVAI_BOX_CONFIG =", os.environ.get("IRIVAI_BOX_CONFIG"))

plan = json.load(open(plan_path, encoding="utf-8"))
masters = {s: int(p) for s, p in (plan.get("masters") or {}).items()}
detecting = set(plan.get("detecting") or [])
units = {n[:-4] for n in os.listdir("/etc/irivai") if n.endswith(".env")} \
    if os.path.isdir("/etc/irivai") else set()
scfg = config.systems(cfgdir)
sysdir = pathlib.Path(scfg.get("dir", "systems"))
if not sysdir.is_absolute():
    sysdir = pathlib.Path(repo) / sysdir
wanted = [s for s in order.split() if s in masters] + \
         sorted(s for s in masters if s not in order.split())

def root(slug):
    return str(provision._pkg_root(str(sysdir), slug))

def health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return json.loads(r.read(1 << 16) or b"{}")
    except Exception:
        return None


def hailo_settled(up_for=5, budget=180):
    """hailort.service active for `up_for` seconds in a row (no unit = ready)."""
    r = subprocess.run(["systemctl", "cat", "hailort.service"], capture_output=True)
    if r.returncode != 0:
        return True
    run, deadline = 0, time.time() + budget
    while time.time() < deadline:
        a = subprocess.run(["systemctl", "is-active", "hailort.service"],
                           capture_output=True, text=True).stdout.strip()
        run = run + 1 if a == "active" else 0
        if run >= up_for:
            P(f"  hailort.service active for {up_for} s in a row")
            return True
        time.sleep(1)
    P("  hailort.service did not stay active")
    return False

if phase == "stop":
    # Whole app process, not only the AI switch: dc.stop() would leave the
    # OLD app code running with its AI off. The AI switch is left as it is.
    for slug in wanted:
        if slug in units:
            continue
        try:
            P(f"{slug}: app", dc.stop_app(root(slug), slug).get("message", ""))
        except Exception as exc:
            P(f"{slug}: app stop: {exc}")
        provision.stop_master(f"http://127.0.0.1:{masters[slug]}")
    time.sleep(3)
    from core.services import portpids
    for slug in wanted:
        if slug in units:
            continue
        for _ in range(10):
            if health(masters[slug]) is None:
                break
            time.sleep(1)
        else:
            for pid in sorted(portpids.listening_pids(masters[slug])):
                P(f"{slug}: master pid {pid} ignored SIGTERM, kill -9")
                subprocess.run(["kill", "-9", str(pid)])
    P("stopped:", ", ".join(s for s in wanted if s not in units) or "none")
    sys.exit(0)

bad = []
for slug in wanted:
    port = masters[slug]
    if slug not in units:
        provision.spawn_master(repo, slug, port)
    t0 = time.time()
    while time.time() - t0 < 90:
        h = health(port)
        if h is not None:
            break
        time.sleep(2)
    got = str((h or {}).get("version") or "")
    if h is None or (got and got.lstrip("vV") != version):
        P(f"{slug}: master on :{port} {'did not answer' if h is None else 'reports ' + got}")
        bad.append(slug)
        continue
    P(f"{slug}: master on :{port} up ({got or 'version not reported'})")
    if slug not in detecting:
        P(f"{slug}: was not detecting before, AI left off")
        continue
    hailo_settled()
    try:
        r = dc.start(root(slug), slug)
        P(f"{slug}: start:", (r or {}).get("message", r))
    except Exception as exc:
        P(f"{slug}: start refused: {exc}")
        bad.append(slug)
        continue
    t1 = time.time()
    while time.time() - t1 < 150 and not (health(port) or {}).get("ai_running"):
        time.sleep(3)
    if (health(port) or {}).get("ai_running"):
        P(f"{slug}: detecting (master reports AI running)")
    else:
        P(f"{slug}: NOT detecting after 150 s")
        bad.append(slug)
    time.sleep(5)
    hailo_settled()
P("needs a look:", ", ".join(bad) if bad else "nothing")
sys.exit(1 if bad else 0)
PY
    chmod 644 "$dir/helper.py" "$dir/revive.py"
}

make_helpers() {   # HELPERS = a fresh root-owned 0755 directory under /run
    HELPERS="$(mktemp -d /run/irivai-manual-helpers.XXXXXX)" || die "could not make a helper directory under /run"
    chown root:root "$HELPERS"; chmod 755 "$HELPERS"
    write_helpers "$HELPERS"
}

json_args() {   # key=value ... -> one JSON object
    python3 -I - "$@" <<'PY'
import json, sys
print(json.dumps(dict(a.split("=", 1) for a in sys.argv[1:])))
PY
}

revive() {   # revive <stop|start> <tree> <hub-config> <expected version>
    local phase="$1" tree="$2" cfg="$3" want="$4"
    if [[ "$phase" == "stop" ]]; then
        as_svc "$LIVE_LINK" "$tree/.venv/bin/python" "$HELPERS/revive.py" stop \
            "$LIVE_LINK" "$cfg" "$STATE/plan.json" "$want" "$APP_ORDER" || true
        return 0
    fi
    # Started in a transient unit with KillMode=process, as the updater's own
    # unit is: the masters it spawns outlive it and this ssh session.
    local envargs=()
    for kv in "${HUB_ENV[@]}"; do envargs+=("--setenv=$kv"); done
    systemd-run --quiet --wait --pipe --collect \
        --unit="irivai-manual-start-$(date +%s)" \
        --uid="$SVC_USER" --gid="$SVC_GROUP" \
        -p KillMode=process -p WorkingDirectory="$LIVE_LINK" \
        --setenv=HOME="$SVC_HOME" --setenv=IRIVAI_BOX_CONFIG="$(readlink -f "$cfg")" \
        "${envargs[@]}" \
        "$tree/.venv/bin/python" "$HELPERS/revive.py" start \
        "$LIVE_LINK" "$cfg" "$STATE/plan.json" "$want" "$APP_ORDER"
}

gateway_check() {   # PASS when MediaMTX listens on loopback only, plus UDP 8189
    local want got bad
    if [[ -f /etc/mediamtx/mediamtx.yml ]]; then
        want="$(sha256sum "$ROOTCOPY/tree/scripts/mediamtx/mediamtx.yml" | cut -d' ' -f1)"
        got="$(sha256sum /etc/mediamtx/mediamtx.yml | cut -d' ' -f1)"
        if [[ "$want" == "$got" ]]; then note "PASS /etc/mediamtx/mediamtx.yml is the release's hardened copy"
        else
            warn "FAIL /etc/mediamtx/mediamtx.yml differs from the release's copy (one-time steps skipped or refused, see above)."
            warn "     By hand: sudo bash $LIVE_LINK/scripts/upgrade_steps.sh   (its step 3 writes it)"
        fi
    else
        note "no /etc/mediamtx/mediamtx.yml: the video gateway is not installed here, not judged"
        return 0
    fi
    if ! pgrep -x mediamtx >/dev/null; then
        warn "the video gateway is not running: listeners not judged (live video stays on the fallback picture)"
        return 1
    fi
    command -v ss >/dev/null || { warn "ss not found (iproute2): listeners not judged"; return 1; }
    got="$(ss -H -lntup 2>/dev/null | grep '"mediamtx"' | awk '{print $1" "$5}')"
    bad="$(printf '%s\n' "$got" | grep -v -E '^(tcp|udp) (127\.0\.0\.1|\[::1\]):[0-9]+$' \
              | grep -v -E '^udp \S+:8189$' | grep -v '^$' || true)"
    note "gateway listeners: $(printf '%s' "$got" | tr '\n' ' ')"
    if [[ -z "$bad" ]]; then note "PASS the gateway listens on 127.0.0.1 only, plus UDP 8189 for WebRTC media"; return 0; fi
    warn "FAIL the gateway listens beyond loopback: $(printf '%s' "$bad" | tr '\n' ' ')"
    return 1
}

flip_to() {   # flip_to <release dir>: atomic, the install.sh way
    ln -sfn "$1" "${LIVE_LINK}.new"
    mv -Tf "${LIVE_LINK}.new" "$LIVE_LINK"
    note "$LIVE_LINK -> $(readlink "$LIVE_LINK")"
}

# ===========================================================================
# ROLLBACK
# ===========================================================================
if [[ "$MODE" == "rollback" ]]; then
    STATE="${UPDATE_DIR:-/nonexistent}/manual-v${VER}"
    read_rollback_record             # PREV PREV_VER HUB_PORT STORE, parsed, never sourced
    [[ -d "$PREV" ]] || die "the previous release $PREV is gone"
    HPY="$PREV/.venv/bin/python"     # the interpreter the updater itself runs under
    [[ "$(readlink -f "$LIVE_LINK")" == "$(readlink -f "$TARGET")" ]] \
        || die "$LIVE_LINK does not point at v${VER} (it points at $(readlink -f "$LIVE_LINK")) - nothing to roll back"
    make_helpers
    A="$(json_args link="$LIVE_LINK" store="$STORE" state="$STATE" prev="$TARGET" target="$PREV" \
          config_dir="$TARGET/config" models=1)"

    say "Recording what runs now"
    as_svc "$STATE" "$HPY" "$HELPERS/helper.py" record "$TARGET" "$A"
    say "Carrying this box's current settings back into ${PREV_VER} (as the Roll back button does)"
    as_svc "$STATE" "$HPY" "$HELPERS/helper.py" carry "$TARGET" "$A"

    say "Switching back to ${PREV_VER}"
    as_svc "$STATE" touch "$UPDATE_DIR/maintenance"
    CLEAN_MAINT=1
    flip_to "$PREV"
    as_svc "$STATE" "$HPY" "$HELPERS/helper.py" restore "$TARGET" "$A"
    restart_hub
    wait_hub "$HUB_PORT" "$PREV_VER" 120 || warn "the hub has not reported ${PREV_VER} yet - check: systemctl status irivai-hub"

    say "Stopping the masters that ran v${VER}"
    revive stop "$PREV" "$PREV/config" "$PREV_VER"
    restart_units
    rm -f "$UPDATE_DIR/maintenance"
    say "Starting them again on ${PREV_VER}, one at a time: ${APP_ORDER}"
    revive start "$PREV" "$PREV/config" "$PREV_VER" || warn "some systems need a look (see above)"

    say "Rolled back to ${PREV_VER}"
    note "v${VER} is still on disk at $TARGET (blue/green). To install it again later:"
    note "    sudo rm -rf $TARGET $STATE $RB_FILE   then run the installer again"
    note "root helpers in /usr/local/lib/irivai and the hardened /etc/mediamtx/mediamtx.yml stay as they are"
    note "(the previous gateway config is kept beside it as mediamtx.yml.pre-1.0.37); ${PREV_VER} works with both"
    exit 0
fi

# ===========================================================================
# INSTALL
# ===========================================================================
say "Preflight"
[[ -n "$PKG" && -f "$PKG" ]] || die "package not found: ${1:-}"
# Root-only working copy (Pipi F-1a): the package is copied in FIRST and the
# checksum taken on that copy, so a file swapped beside this script after the
# check is never read. Everything root runs comes from here.
ROOTCOPY="$(mktemp -d /run/irivai-manual-v${VER}.XXXXXX)" || die "could not make a root-only directory under /run"
chown root:root "$ROOTCOPY"; chmod 700 "$ROOTCOPY"
if ! [[ "$(stat -c '%u %a' "$ROOTCOPY")" == "0 700" && "$(stat -c %u /run)" == 0 ]] \
        || (( (8#$(stat -c %a /run) & 8#022) != 0 )); then
    die "$ROOTCOPY is not a root-only directory - refusing"
fi
cp -- "$PKG" "$ROOTCOPY/package.tar.gz"
PKG="$ROOTCOPY/package.tar.gz"
GOT="$(sha256sum "$PKG" | cut -d' ' -f1)"
[[ "$GOT" == "$PKG_SHA256" ]] || die "package SHA-256 is $GOT, expected $PKG_SHA256 - copy it again"
note "package SHA-256 OK ($GOT)"
install -d -m 700 -o root -g root "$ROOTCOPY/tree"
tar -xzf "$PKG" -C "$ROOTCOPY/tree" --no-same-owner VERSION scripts
[[ "$(tr -d ' \t\r\n' < "$ROOTCOPY/tree/VERSION")" == "$VER" ]] || die "the package's VERSION is not $VER"
note "root-only copy of scripts/ and VERSION: $ROOTCOPY/tree"
[[ -L "$LIVE_LINK" ]] || die "$LIVE_LINK is not a symlink - this box is not on the blue/green layout"
PREV="$(readlink -f "$LIVE_LINK")"
[[ -d "$PREV" ]] || die "$LIVE_LINK points at $PREV, which does not exist"
# $PREV belongs to the service account: root reads nothing in it (Pipi R-3).
# The version is read as the service account and checked before it is shown.
PREV_VER="$(as_svc / head -c 64 "$PREV/VERSION" | tr -d ' \t\r\n')" || PREV_VER=""
msg="$(check_value PREV_VER "$PREV_VER")" || die "refusing: the running release's VERSION, read as $SVC_USER: $msg"
note "running release: $PREV ($PREV_VER)"
[[ "$(stat -c %U "$PREV")" == "$SVC_USER" ]] \
    || die "$PREV is owned by $(stat -c %U "$PREV"), not the service account $SVC_USER: the new release is unpacked as $SVC_USER and must match"
[[ -e "$TARGET" ]] && die "$TARGET already exists - refusing (remove it first if a failed attempt left it)"
[[ "$PREV" != "$TARGET" ]] || die "v${VER} is already live"
[[ "$PREV_VER" != "$VER" ]] || die "the running release already says $VER"
[[ -n "$UPDATE_DIR" && -d "$UPDATE_DIR" ]] || die "UPDATE_DIR missing from /etc/irivai/update.conf"
STATE="$UPDATE_DIR/manual-v${VER}"
[[ -e "$STATE" ]] && die "$STATE exists from an earlier attempt - remove it first"
[[ -e "$UPDATE_DIR/maintenance" ]] && die "an update is in progress ($UPDATE_DIR/maintenance)"
systemctl is-active --quiet irivai-apply.service && die "irivai-apply.service is running an update"
if [[ -d "$PREV/core/dev" && "${IRIV_ALLOW_DEV_BOX:-0}" != "1" ]]; then
    die "the running release carries core/dev (a developer box). This customer-mode package would remove the developer tooling. Set IRIV_ALLOW_DEV_BOX=1 to install anyway."
fi
if [[ -d "$PREV/agent" ]] && systemctl is-enabled --quiet irivai-agent.service 2>/dev/null; then
    die "irivai-agent.service runs from $LIVE_LINK/agent, and this customer-mode package has no agent/ (as the customer branch). Disable that unit first or ask for a package with agent/."
fi
[[ -x "$PREV/.venv/bin/python" ]] || die "no venv in the running release ($PREV/.venv)"
# The interpreter the signed updater runs under (irivai-apply.service ExecStart
# = the live venv's python): its `-m venv` builds on the same base Python.
HPY="$PREV/.venv/bin/python"
command -v systemd-run >/dev/null || die "systemd-run not found"
AVAIL_MB=$(( $(df -Pk "$RELEASES_DIR" | awk 'NR==2{print $4}') / 1024 ))
(( AVAIL_MB > 900 )) || die "only ${AVAIL_MB} MB free on $RELEASES_DIR, need about 900 MB"
note "service account: $SVC_USER ($SVC_HOME), free on $RELEASES_DIR: ${AVAIL_MB} MB"
if command -v arping >/dev/null; then
    note "arping: $(command -v arping) ($(dpkg-query -W -f='${Package} ${Version}' iputils-arping 2>/dev/null || echo 'not the iputils package'))"
else
    note "arping is not installed yet: the release's one-time root steps below install"
    note "iputils-arping (with nftables and avahi-utils) when the box can reach its apt mirror."
fi

PROBE="$(as_svc "$PREV" "$PREV/.venv/bin/python" -c '
import json, os, sys
sys.path.insert(0, os.getcwd())
from core import config
from core.services import hubport
print(json.dumps({"store": os.path.abspath(str(config.store_dir())), "hub_port": int(hubport.port())}))' | tail -n 1)"
STORE="$(printf '%s' "$PROBE" | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["store"])')"
HUB_PORT="$(printf '%s' "$PROBE" | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["hub_port"])')"
[[ "$STORE" == /* && -d "$STORE" ]] || die "could not resolve the store directory (got $(printf '%q' "$STORE"))"
# The service account reported these: checked before anything is made, with
# the same rule the install record is read back with (Pipi F-1b).
for kv in "PREV=$PREV" "PREV_VER=$PREV_VER" "HUB_PORT=$HUB_PORT" "STORE=$STORE"; do
    msg="$(check_value "${kv%%=*}" "${kv#*=}")" || die "refusing: $msg"
done
note "store: $STORE   hub port: $HUB_PORT"
[[ "$(health_version "$HUB_PORT")" == "$PREV_VER" ]] || warn "the hub on :$HUB_PORT does not report $PREV_VER right now"
case "$(readlink -f "$STORE")/" in "$(readlink -f "$RELEASES_DIR")"/*) die "the store is inside $RELEASES_DIR - fix storage first";; esac

# From here until the flip, any failure removes what was made.
cleanup_unflipped() {
    if [[ "$(readlink -f "$LIVE_LINK")" != "$(readlink -f "$TARGET" 2>/dev/null || echo /x)" ]]; then
        warn "install stopped before the flip - removing $TARGET, $STATE and $RB_FILE; $PREV_VER untouched"
        rm -rf "$TARGET" "$STATE"
        rm -f "$RB_FILE"
    fi
}
CLEAN_UNFLIPPED=1

# The release tree is the service account's from the first byte (Pipi R-3,
# R-5): it makes the directory and unpacks into it, root only feeding the
# verified copy on stdin, and every later read of $PREV or $TARGET below runs
# as the service account too. No chown -R, no root path inside its tree.
say "Unpacking into $TARGET (as $SVC_USER)"
if runuser -u "$SVC_USER" -- test -w "$RELEASES_DIR"; then
    as_svc / mkdir -m 755 "$TARGET" || die "could not create $TARGET as $SVC_USER (something is already there)"
else
    # test -w failing does NOT mean the service account cannot write here: it
    # may own the directory and chmod it back and forth (Pipi R-6). So root
    # only ever makes a NEW directory (mkdir refuses anything already there,
    # a planted symlink included) and hands over that name without following
    # it (chown -h).
    mkdir -m 755 "$TARGET" || die "could not create $TARGET (something is already there)"
    chown -h "$SVC_USER:$SVC_GROUP" "$TARGET"
fi
as_svc / tar -xzf - -C "$TARGET" --no-same-owner < "$PKG" || die "unpacking as $SVC_USER failed"
as_svc / rm -f "$TARGET/config/deploy.toml" "$TARGET/config/store_signing.pem"   # NEVER_SHIP, as the applier
[[ "$(as_svc / head -c 64 "$TARGET/VERSION" | tr -d ' \t\r\n')" == "$VER" ]] || die "the unpacked VERSION is not $VER"
[[ -f "$TARGET/core/static/app/index.html" ]] || die "the package has no built frontend"
if as_svc / grep -rIl $'\r$' "$TARGET/scripts" "$TARGET/run.sh" >/dev/null 2>&1; then die "CRLF line endings in scripts"; fi

say "Dependency wheels (offline, copied as $SVC_USER)"
as_svc / cmp -s "$PREV/requirements.txt" "$TARGET/requirements.txt" \
    || die "requirements.txt differs from ${PREV_VER}: the running release's wheels may not cover it"
if [[ ! -f "$TARGET/requirements.lock" && -f "$PREV/requirements.lock" ]]; then
    as_svc / cp -p "$PREV/requirements.lock" "$TARGET/requirements.lock" \
        || die "could not copy ${PREV_VER}'s requirements.lock as $SVC_USER"
fi
as_svc / mkdir -p -m 755 "$TARGET/wheels"
if compgen -G "$PREV/wheels/*.whl" >/dev/null; then
    # shellcheck disable=SC2016  # $1 and $2 expand in the child, as the service account
    as_svc / bash -c 'cp -n "$1"/wheels/*.whl "$2/wheels/"' _ "$PREV" "$TARGET" \
        || die "could not copy ${PREV_VER}'s wheels as $SVC_USER"
else
    die "the running release has no wheels/ to build the venv from offline"
fi
note "$(ls "$TARGET/wheels" | wc -l) wheels, lock: $( [[ -f "$TARGET/requirements.lock" ]] && echo requirements.lock || echo requirements.txt )"

# $STATE is made by the service account itself (Pipi R-2): mkdir fails if
# anything already sits at that name, and root never resolves a path in it.
as_svc / mkdir -m 755 "$STATE" || die "could not create $STATE as $SVC_USER (something is already there)"
make_helpers
write_rollback_record
A="$(json_args link="$LIVE_LINK" store="$STORE" state="$STATE" prev="$PREV" target="$TARGET" \
      config_dir="$PREV/config" version="$VER")"

say "Building the release venv, carrying the Hailo runtime, settings and models"
as_svc "$STATE" "$HPY" "$HELPERS/helper.py" prepare "$TARGET" "$A"
as_svc "$TARGET" "$TARGET/.venv/bin/python" -c 'import fastapi, uvicorn; print("    venv imports OK")'

say "Root helpers and one-time upgrade steps (scripts/root_steps.py --tree, the by-hand path)"
/usr/bin/python3 -I "$ROOTCOPY/tree/scripts/root_steps.py" --tree "$ROOTCOPY/tree" || warn "root_steps reported a refusal (see above); continuing - the HTTPS card names a missing helper"
if [[ -r /var/lib/irivai/upgrade-steps.json ]]; then
    note "one-time upgrade steps record: $(cat /var/lib/irivai/upgrade-steps.json)"
else
    warn "one-time upgrade steps not recorded yet (see the lines above); the next update retries them"
fi
if command -v arping >/dev/null; then note "arping: $(command -v arping)"; else warn "arping still missing: the OLED IP CONFLICT line stays silent until it is installed"; fi

say "Snapshot of each store system's app code (for a rollback)"
as_svc "$STATE" "$HPY" "$HELPERS/helper.py" snapshot "$TARGET" "$A"
as_svc "$STATE" "$HPY" "$HELPERS/helper.py" record "$TARGET" "$A"
for d in "$PREV"/systems/*/; do
    s="$(basename "$d")"
    [[ -f "$d/config.local.toml" ]] && note "note: $PREV/systems/$s/config.local.toml is not carried (the OTA does not carry it either)"
done

say "Settings once more, the last step before the flip"
as_svc "$STATE" "$HPY" "$HELPERS/helper.py" carry "$TARGET" "$A"

# ---------------------------------------------------------------------------
say "Going live: flip, restart the hub"
as_svc "$STATE" touch "$UPDATE_DIR/maintenance"
CLEAN_UNFLIPPED=0
CLEAN_MAINT=1
FLIPPED_AT="$(date +%s)"
flip_to "$TARGET"
restart_hub
if ! wait_hub "$HUB_PORT" "$VER" 120; then
    warn "the new hub did not come up - putting ${PREV_VER} back"
    flip_to "$PREV"
    rm -f "$UPDATE_DIR/maintenance"
    restart_hub
    wait_hub "$HUB_PORT" "$PREV_VER" 120 || true
    die "v${VER} did not start; the box is back on ${PREV_VER} (masters untouched). See: journalctl -u irivai-hub -n 200. To try again later: sudo rm -rf $TARGET $STATE $RB_FILE"
fi

say "New app code into every store system"
as_svc "$STATE" "$HPY" "$HELPERS/helper.py" sync "$TARGET" "$A"

say "Stopping the masters that ran ${PREV_VER}"
revive stop "$TARGET" "$TARGET/config" "$VER"
restart_units
rm -f "$UPDATE_DIR/maintenance"

say "Starting masters and detection one at a time: ${APP_ORDER}"
RC=0
revive start "$TARGET" "$TARGET/config" "$VER" || RC=$?

say "Video gateway check (Pipi H1): listeners and config"
gateway_check || RC=1
say "Camera password check (Pipi H2): state.json written by the new apps"
sleep 10
as_svc "$STATE" "$HPY" "$HELPERS/helper.py" credcheck "$TARGET" \
    "$(json_args link="$LIVE_LINK" store="$STORE" state="$STATE" since="$FLIPPED_AT")" || RC=1

say "Done: $(readlink "$LIVE_LINK"), hub reports $(health_version "$HUB_PORT")"
[[ $RC -eq 0 ]] || warn "some systems need a look (lines above). Logs: $LIVE_LINK/logs/master-<slug>.log and each system's app.log"
note "log of this run: $LOG"
note ""
note "ROLLBACK to ${PREV_VER} (flips the link back, restores the app code, restarts in the same order):"
note "    sudo bash $(readlink -f "$0") --rollback"
exit 0
