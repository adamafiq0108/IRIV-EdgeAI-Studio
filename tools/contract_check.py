# -*- coding: utf-8 -*-
"""
contract_check.py -- Conformance harness for IRIV Edge AI Studio System Contract v1.

This file is the arbiter. docs/contract-v1.md explains the contract; this
decides it. If the two disagree, the doc is the bug.

An adapter is done when this passes UNEDITED against it.

Run:
    python tools/contract_check.py http://127.0.0.1:5101 --token mock-token
    python tools/contract_check.py http://192.0.2.10:5101 --token abc --verbose

Exit code 0 = conforming, 1 = not.
"""

import argparse
import datetime
import json
import sys
import urllib.error
import urllib.request

SUPPORTED_MAJOR = 1

EVENT_TYPES = {"violation", "info", "alert", "system"}
SEVERITIES = {"ok", "warn", "crit"}
KNOWN_CAPABILITIES = {
    "stream", "footage", "snapshots", "analytics.daily",
    "analytics.direction", "settings.zones", "email", "mqtt",
    # v1.2 control domains (contract-v1.md, control section)
    "control.device", "control.mqtt", "control.camera", "control.storage",
    "control.detection",
}
STATS_TYPES = {
    "events_today": int, "events_total": int,
    "violations_today": int, "violations_total": int,
    "uptime_s": int, "camera_running": bool, "camera_label": str,
}

_results = []
_verbose = False


def check(name, ok, detail=""):
    _results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if detail and (not ok or _verbose):
        line += f"\n         {detail}"
    print(line)
    return ok


def get(base, path, token=None, raw=False, timeout=10):
    """GET a path. Returns (status, parsed_or_bytes, headers).

    Headers are returned as urllib's own mapping, never dict(): header names are
    case-insensitive per RFC 7230, and a dict is not. Servers differ -- uvicorn
    emits `content-type`, others title-case it -- so a dict here would fail a
    conforming slave over nothing but capitalisation.
    """
    req = urllib.request.Request(base.rstrip("/") + path)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read() if raw else json.loads(r.read() or b"{}")
            return r.status, body, r.headers
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except Exception:
            body = {}
        return e.code, body, e.headers if e.headers is not None else {}
    except Exception as e:
        return 0, {"error": str(e)}, {}


def parse_ts(value):
    """Parse an ISO timestamp. Returns (datetime, error_string)."""
    if not isinstance(value, str):
        return None, f"not a string: {value!r}"
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        return None, str(e)
    if dt.tzinfo is None:
        return None, "no UTC offset - naive timestamps are rejected"
    return dt, None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_health(base):
    print("\n/api/health")
    status, body, _ = get(base, "/api/health")
    if not check("reachable, 200", status == 200, f"got {status}: {body}"):
        return None
    check("ok is true", body.get("ok") is True, f"ok={body.get('ok')!r}")
    check("uptime_s is int", isinstance(body.get("uptime_s"), int))
    return check_version(body.get("api_version"), "/api/health")


def check_version(version, where):
    if not check(f"api_version present in {where}", isinstance(version, str) and version,
                 f"got {version!r}"):
        return None
    parts = version.split(".")
    if not check("api_version is MAJOR.MINOR", len(parts) == 2 and all(p.isdigit() for p in parts),
                 f"got {version!r}"):
        return None
    major = int(parts[0])
    check(f"major {major} supported", major == SUPPORTED_MAJOR,
          f"harness supports major {SUPPORTED_MAJOR}; master must refuse this slave outright")
    return version


def check_info(base):
    print("\n/api/agent/info")
    status, body, _ = get(base, "/api/agent/info")
    if not check("reachable, 200", status == 200, f"got {status}: {body}"):
        return set()

    for field in ("host_serial", "system_slug", "display_name", "api_version"):
        check(f"{field} present and non-empty",
              isinstance(body.get(field), str) and body[field].strip(),
              f"got {body.get(field)!r}")

    check_version(body.get("api_version"), "/api/agent/info")

    serial = body.get("host_serial") or ""
    check("host_serial has no trailing NUL", "\x00" not in serial,
          "devicetree strings are null-terminated - strip \\x00 (contract-v1.md sec 5)")
    check("host_serial is lowercase", serial == serial.lower(), f"got {serial!r}")

    slug = body.get("system_slug") or ""
    check("system_slug is lowercase/underscore",
          slug and slug.replace("_", "").isalnum() and slug == slug.lower(),
          f"got {slug!r}")

    caps = body.get("capabilities")
    if not check("capabilities is a list", isinstance(caps, list), f"got {caps!r}"):
        return set()
    unknown = set(caps) - KNOWN_CAPABILITIES
    check("capabilities are all known", not unknown,
          f"unknown: {sorted(unknown)} - add to contract-v1.md sec 6 or fix the adapter")

    camera = body.get("camera")
    check("camera is an object", isinstance(camera, dict), f"got {camera!r}")
    check("app_running is bool", isinstance(body.get("app_running"), bool))
    return set(caps)


def check_auth(base, token):
    print("\nAuth")
    status, _, _ = get(base, "/api/summary")
    check("/api/summary without token -> 401", status == 401, f"got {status}")
    status, _, _ = get(base, "/api/summary", token="definitely-wrong-token")
    check("/api/summary with bad token -> 401", status == 401, f"got {status}")


def check_event(event, index):
    """Validate one envelope. Returns list of problems."""
    problems = []
    for field in ("id", "ts", "type", "severity", "label"):
        if not isinstance(event.get(field), str) or not event[field].strip():
            problems.append(f"event[{index}].{field} missing or not a string")

    _, err = parse_ts(event.get("ts"))
    if err:
        problems.append(f"event[{index}].ts {err}")

    if event.get("type") not in EVENT_TYPES:
        problems.append(f"event[{index}].type={event.get('type')!r} not in {sorted(EVENT_TYPES)}")
    if event.get("severity") not in SEVERITIES:
        problems.append(f"event[{index}].severity={event.get('severity')!r} not in {sorted(SEVERITIES)}")
    if "detail" in event and event["detail"] is not None and not isinstance(event["detail"], dict):
        problems.append(f"event[{index}].detail must be an object")
    return problems


def check_summary(base, token):
    print("\n/api/summary")
    status, body, _ = get(base, "/api/summary", token=token)
    if not check("reachable, 200", status == 200, f"got {status}: {body}"):
        return None

    for field in ("host_serial", "now", "next_cursor"):
        check(f"{field} present", isinstance(body.get(field), str) and body[field],
              f"got {body.get(field)!r}")

    events = body.get("events")
    if not check("events is a list", isinstance(events, list), f"got {type(events).__name__}"):
        return None

    now, err = parse_ts(body.get("now"))
    check("now is ISO with offset", err is None, err or "")
    if now:
        skew = (datetime.datetime.now().astimezone() - now).total_seconds()
        check("clock skew under 60s", abs(skew) < 60,
              f"skew {skew:.0f}s - Pis have no RTC; master will flag this slave")

    problems = []
    for i, e in enumerate(events):
        problems.extend(check_event(e, i))
    check(f"all {len(events)} event envelopes valid", not problems,
          "\n         ".join(problems[:8]))

    stats = body.get("stats")
    if check("stats is an object", isinstance(stats, dict), f"got {stats!r}"):
        bad = [f"{k} should be {t.__name__}, got {type(stats[k]).__name__}"
               for k, t in STATS_TYPES.items()
               if k in stats and not isinstance(stats[k], t)]
        check("standard stats keys have correct types", not bad, "; ".join(bad))
        if _verbose:
            extra = set(stats) - set(STATS_TYPES)
            if extra:
                print(f"         extra stats keys (render generically): {sorted(extra)}")
    return body


def check_cursor(base, token):
    print("\nCursor rules")
    status, first, _ = get(base, "/api/summary", token=token)
    if status != 200 or not isinstance(first.get("events"), list):
        check("cursor tests runnable", False, "first summary call failed")
        return

    if not first["events"]:
        check("cursor tests runnable", False,
              "slave returned zero events - cannot verify cursors; seed it with data first")
        return

    cursor = first["next_cursor"]
    first_ids = [e["id"] for e in first["events"]]

    # Exclusive: since=<cursor> must not replay what we already saw.
    _, second, _ = get(base, f"/api/summary?since={cursor}", token=token)
    second_ids = [e["id"] for e in second.get("events", [])]
    overlap = set(first_ids) & set(second_ids)
    check("since=<cursor> is exclusive", not overlap,
          f"{len(overlap)} event(s) replayed, e.g. {sorted(overlap)[:3]} - master would double-count")

    # Stable: same query twice, same answer.
    _, again, _ = get(base, "/api/summary", token=token)
    again_ids = [e["id"] for e in again.get("events", [])]
    check("same cursor returns same events", first_ids == again_ids,
          "cursor is not stable across identical requests")

    # Drain, then confirm an exhausted cursor echoes `since` back.
    seen, guard = set(first_ids), 0
    cur = cursor
    while guard < 50:
        guard += 1
        _, page, _ = get(base, f"/api/summary?since={cur}", token=token)
        evs = page.get("events", [])
        if not evs:
            check("empty page echoes since back as next_cursor",
                  page.get("next_cursor") == cur,
                  f"since={cur!r} but next_cursor={page.get('next_cursor')!r}")
            break
        ids = [e["id"] for e in evs]
        dupes = seen & set(ids)
        if dupes:
            check("no duplicates while paging", False, f"repeated: {sorted(dupes)[:3]}")
            return
        seen.update(ids)
        cur = page.get("next_cursor")
    else:
        check("pagination terminates", False, "50 pages and still going - cursor may not advance")

    check("no duplicates while paging", True, f"drained {len(seen)} unique events")

    status, body, _ = get(base, "/api/summary?since=!!!not-a-cursor!!!", token=token)
    check("garbage cursor -> 4xx, not 5xx", 400 <= status < 500,
          f"got {status}: {body} - must fail cleanly, master keys off status codes")


def check_footage(base, token, caps):
    print("\n/api/footage")
    if "footage" not in caps:
        print("  [skip] no 'footage' capability")
        return
    status, body, _ = get(base, "/api/footage", token=token)
    if not check("reachable, 200", status == 200, f"got {status}: {body}"):
        return
    items = body.get("items")
    if not check("items is a list", isinstance(items, list), f"got {items!r}"):
        return
    if not items:
        print("  [skip] no footage items to fetch")
        return
    item = items[0]
    for field in ("id", "ts"):
        check(f"item.{field} present", isinstance(item.get(field), str) and item[field])
    _, err = parse_ts(item.get("ts"))
    check("item.ts is ISO with offset", err is None, err or "")

    status, blob, headers = get(base, f"/api/footage/{item['id']}", token=token, raw=True)
    check("footage bytes fetch -> 200", status == 200, f"got {status}")
    check("footage returns non-empty bytes", isinstance(blob, bytes) and len(blob) > 0,
          f"got {len(blob) if isinstance(blob, bytes) else blob!r}")
    check("footage has a Content-Type", bool(headers.get("Content-Type")))


def post(base, path, token=None, payload=None, timeout=10):
    """POST json. Returns (status, parsed)."""
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(base.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def check_control(base, token, caps):
    """v1.2 control channel. Runs only when control.* capabilities are
    declared; read-only slaves skip cleanly."""
    control_caps = {c for c in caps if c.startswith("control.")}
    print("\n/api/control (v1.2)")
    if not control_caps:
        print("  [skip] no control.* capabilities")
        return

    status, _, _ = get(base, "/api/control")
    check("without token -> 401", status == 401, f"got {status}")

    status, body, _ = get(base, "/api/control", token=token)
    if not check("reachable, 200", status == 200, f"got {status}: {body}"):
        return
    domains = body.get("domains")
    if not check("domains is an object", isinstance(domains, dict), f"got {domains!r}"):
        return

    expected = {c.split(".", 1)[1] for c in control_caps}
    missing = expected - set(domains)
    check("a domain per declared control.* capability", not missing,
          f"declared but not served: {sorted(missing)}")
    extra = set(domains) - expected
    check("no undeclared domains served", not extra,
          f"served without capability: {sorted(extra)}")

    problems = []
    for dname, env in domains.items():
        if not isinstance(env, dict):
            problems.append(f"{dname}: envelope not an object")
            continue
        for key in ("settings", "status", "actions"):
            if key not in env:
                problems.append(f"{dname}: missing {key}")
        for i, action in enumerate(env.get("actions") or []):
            for field in ("name", "label"):
                if not isinstance(action.get(field), str) or not action[field]:
                    problems.append(f"{dname}.actions[{i}].{field} missing")
            if not isinstance(action.get("destructive"), bool):
                problems.append(f"{dname}.actions[{i}].destructive must be bool "
                                "(it drives confirm UX - never name-matching)")
            if action.get("name") and not action["name"].split(".")[0]:
                problems.append(f"{dname}.actions[{i}].name must be domain-prefixed")
    check("domain envelopes well-formed", not problems, "; ".join(problems[:8]))

    status, body = post(base, "/api/control/definitely-not-a-domain", token=token)
    check("unknown domain -> 404", status == 404, f"got {status}: {body}")
    status, body = post(base, "/api/control/action/no.such_action", token=token)
    check("unknown action -> 404", status == 404, f"got {status}: {body}")


def check_stream(base, token, caps):
    print("\n/video_feed")
    if "stream" not in caps:
        print("  [skip] no 'stream' capability")
        return
    req = urllib.request.Request(base.rstrip("/") + "/video_feed")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ctype = r.headers.get("Content-Type", "")
            check("Content-Type is multipart/x-mixed-replace",
                  "multipart/x-mixed-replace" in ctype, f"got {ctype!r}")
            check("boundary declared", "boundary=" in ctype, f"got {ctype!r}")
            chunk = r.read(256)
            check("stream yields bytes", len(chunk) > 0, "read 0 bytes")
    except Exception as e:
        check("stream reachable", False, str(e))


# ---------------------------------------------------------------------------

def main():
    global _verbose
    p = argparse.ArgumentParser(description="Check a slave against IRIV Edge AI Studio System Contract v1.")
    p.add_argument("base_url", help="e.g. http://192.0.2.10:5101")
    p.add_argument("--token", default="", help="bearer token issued at pairing")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    _verbose = args.verbose

    print(f"\nContract v1 conformance - {args.base_url}")

    if not check_health(args.base_url):
        print("\nHealth failed. Nothing else can be trusted. Stopping.\n")
        return 1

    caps = check_info(args.base_url)

    if not args.token:
        print("\n  No --token given; skipping every authenticated check.")
        print("  Pass a paired token to run the real suite.\n")
        return 0

    check_auth(args.base_url, args.token)
    check_summary(args.base_url, args.token)
    check_cursor(args.base_url, args.token)
    check_footage(args.base_url, args.token, caps)
    check_stream(args.base_url, args.token, caps)
    check_control(args.base_url, args.token, caps)

    failed = [(n, d) for n, ok, d in _results if not ok]
    total = len(_results)
    print(f"\n{'-' * 60}")
    if failed:
        print(f"NOT CONFORMING - {len(failed)} of {total} checks failed:\n")
        for name, detail in failed:
            print(f"  - {name}" + (f"\n      {detail}" if detail else ""))
        print(f"\nSee docs/contract-v1.md\n")
        return 1
    print(f"CONFORMING - {total}/{total} checks passed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
