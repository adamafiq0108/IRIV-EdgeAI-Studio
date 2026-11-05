# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
profile_cpu.py -- What each process, thread and camera costs in CPU, on the box.

Every number about the video path so far came from reading code. This one
measures: it reads /proc directly (utime+stime per pid and per tid, total and
per-core from /proc/stat) at a fixed interval, for the processes this product
runs -- the hub, each master, each detection app, MediaMTX, the HailoRT
service, and any ffmpeg/gst child -- and prints average and peak CPU% per
process and per top thread. Standard library only: it runs on the Pi over
SSH with nothing installed.

Run (on the box):
    python3 tools/profile_cpu.py                          # 30 s, 1 s samples
    python3 tools/profile_cpu.py --duration 60 --json before.json
    python3 tools/profile_cpu.py --scenario viewer-webrtc --json webrtc.json
    python3 tools/profile_cpu.py --scenario viewer-mjpeg --wait 0 --settle 15
    python3 tools/profile_cpu.py diff before.json after.json [--threads]

Scenarios do not drive a browser. Before each timed window they print what to
open or close, then wait for ENTER (or --wait N seconds), then let the box
settle (--settle, default 12 s: the app keeps publishing for 10 s after the
last viewer leaves, run.py WEBRTC_IDLE_GRACE_S) before measuring.

CPU% "of one core" is how top shows a process (a busy 4-thread process can
read 400); "of box" divides by the core count. Elapsed time per sample is
taken from /proc/stat itself, so the arithmetic needs no wall clock.

Testing off the box: --proc-root points every read at a fake tree (see
tests/smoke_profile_cpu.py), which is the only way it runs on Windows.
"""

import argparse
import datetime
import json
import os
import socket
import sys
import time
import urllib.request

#: Indirection so the test can advance a fake /proc tree between samples
#: instead of sleeping.
_sleep = time.sleep

#: The gateway's control API, loopback only (scripts/mediamtx/mediamtx.yml).
#: Same URL the app polls for the viewer gate (app/publisher.py readers()).
READERS_URL = "http://127.0.0.1:9997/v3/paths/list?itemsPerPage=1000"

#: Threads kept per process in the JSON. Enough to see every camera's named
#: GStreamer threads; a 200-thread process would otherwise bloat each run.
JSON_THREADS = 64

#: Wording printed before each window, per scenario: (label, instruction).
_CLOSE_ALL = ("Close EVERY browser tab that shows this box's video "
              "(Detection, Zones, Main, a live wall, a dashboard video "
              "widget). Leave the systems running.")
_REOPEN = ("Close that tab again (all video tabs closed). This window checks "
           "the box falls back to its idle cost.")
SCENARIOS = {
    "idle": [("idle", _CLOSE_ALL)],
    "viewer-webrtc": [
        ("idle", _CLOSE_ALL),
        ("viewer-webrtc",
         "Open ONE browser tab on ONE camera's live picture (e.g. the "
         "system's Detection page) and keep it in front. It must be playing "
         "WebRTC: a <video> element (right-click the picture: no 'Save image "
         "as'). If it fell back to MJPEG, this window measures the wrong "
         "thing."),
        ("idle-after", _REOPEN),
    ],
    "viewer-mjpeg": [
        ("idle", _CLOSE_ALL),
        ("viewer-mjpeg",
         "Open ONE browser tab on ONE camera's live picture (e.g. the "
         "system's Detection page) and keep it in front. It must be the "
         "MJPEG <img> (right-click the picture: 'Save image as' is offered)."),
        ("idle-after", _REOPEN),
    ],
}
SCENARIO_PREFACE = {
    "viewer-mjpeg": (
        "This scenario needs the system in MJPEG mode BEFORE you start: set\n"
        "  [app]\n  stream = \"mjpeg\"\n"
        "in that system's config.toml under the store (not the release tree),\n"
        "then restart its app -- a running app keeps the transport it started\n"
        "with. Set it back (or delete the key) afterwards."),
}


# ---------------------------------------------------------------- /proc reads

def _read(path):
    """File text, or None for anything that vanished or is not readable: a
    pid or tid can exit between listing the directory and opening its file,
    and that is normal, not an error."""
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return None


def parse_stat(text):
    """/proc/<pid>/stat (or task/<tid>/stat) -> dict, or None if malformed.

    The comm field is in parentheses and may itself contain spaces and ')'
    (a thread can name itself anything), so the split is on the LAST ')'.
    Fields after it, 0-based: state, ppid, ... utime at 11, stime at 12,
    starttime at 19 (proc(5) numbers them 3, 4, 14, 15, 22).
    """
    if not text:
        return None
    left, right = text.find("("), text.rfind(")")
    if left < 0 or right < left:
        return None
    rest = text[right + 2:].split()
    try:
        return {"comm": text[left + 1:right],
                "ppid": int(rest[1]),
                "ticks": int(rest[11]) + int(rest[12]),
                "start": int(rest[19])}
    except (IndexError, ValueError):
        return None


def parse_proc_stat(text):
    """/proc/stat -> {"cpu": (busy, total), "cpu0": (busy, total), ...} in
    jiffies. guest/guest_nice are already inside user/nice, so only the
    first eight fields are summed; idle and iowait are the not-busy part."""
    out = {}
    for line in (text or "").splitlines():
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        try:
            vals = [int(v) for v in parts[1:9]]
        except ValueError:
            continue
        vals += [0] * (8 - len(vals))
        total = sum(vals)
        out[parts[0]] = (total - vals[3] - vals[4], total)
    return out


def read_cmdline(root, pid):
    text = _read(os.path.join(root, str(pid), "cmdline"))
    if not text:
        return []
    return [a for a in text.split("\0") if a]


def _readlink(path):
    try:
        return os.readlink(path)
    except (OSError, NotImplementedError):
        return None


def classify(argv, comm, cwd=None):
    """A label for a process this product runs, or None for anything else.

    By command line, not by name: every Python process is "python3" to the
    kernel. The app is launched as `python -m app.run` with its system's
    package dir as cwd (app/runtime.py start), so the cwd names the system.
    """
    base = os.path.basename(argv[0]) if argv else comm
    for i, a in enumerate(argv):
        if a.endswith("hub_server.py"):
            return "hub"
        if a.endswith("master_server.py"):
            for j, b in enumerate(argv):
                if b == "--system" and j + 1 < len(argv):
                    return "master:" + argv[j + 1]
                if b.startswith("--system="):
                    return "master:" + b.split("=", 1)[1]
            return "master"
        if a == "app.run" or (a == "-m" and i + 1 < len(argv)
                              and argv[i + 1] == "app.run"):
            slug = os.path.basename(cwd.rstrip("/\\")) if cwd else ""
            return "app:" + (slug or "?")
    for name in (base, comm):
        if not name:
            continue
        if name.startswith("mediamtx"):
            return "mediamtx"
        if name.startswith("hailort_service"):
            return "hailort_service"
        if name.startswith("ffmpeg") or name.startswith("gst-"):
            return name
    return None


def _is_self(pid, argv, own_pids):
    # The profiler itself, and anything wrapping it (sudo, a shell -c line):
    # its own reads must not show up as one of the measured processes.
    return pid in own_pids or any("profile_cpu" in a for a in argv)


class Scanner:
    """Finds and labels the relevant processes on each sample.

    Re-scanned every sample, because pids come and go (an app restarts, a
    master is bounced). cmdline/cwd are cached per (pid, starttime) so a
    reused pid number is re-read, not mislabelled.
    """

    def __init__(self, root, own_pid=None):
        self.root = root
        self.own_pid = own_pid
        self._meta = {}                       # (pid, start) -> (label, argv)

    def snapshot(self):
        root = self.root
        cpu = parse_proc_stat(_read(os.path.join(root, "stat")))
        stats = {}
        try:
            names = os.listdir(root)
        except OSError:
            names = []
        for name in names:
            if not name.isdigit():
                continue
            st = parse_stat(_read(os.path.join(root, name, "stat")))
            if st:
                stats[int(name)] = st
        own = {self.own_pid} if self.own_pid else set()
        labels, seen = {}, set()
        for pid, st in stats.items():
            key = (pid, st["start"])
            seen.add(key)
            if key not in self._meta:
                argv = read_cmdline(root, pid)
                cwd = _readlink(os.path.join(root, str(pid), "cwd"))
                if _is_self(pid, argv, own):
                    self._meta[key] = ("(profiler)", argv)
                else:
                    label = classify(argv, st["comm"], cwd)
                    if label == "app:?":
                        # cwd unreadable (another user's process without
                        # root): still one row per app, not one merged row.
                        label = "app:pid%d" % pid
                    self._meta[key] = (label, argv)
            label = self._meta[key][0]
            if label:
                labels[pid] = label
        for key in list(self._meta):
            if key not in seen:
                del self._meta[key]
        # Children of a product process (an ffmpeg, a helper the app spawned)
        # are its cost too: labelled "<parent>/<comm>". Walked up a few
        # levels; a kernel thread or a login shell ends at pid 1.
        for pid, st in stats.items():
            if pid in labels:
                continue
            p, depth = st["ppid"], 0
            while p in stats and depth < 6:
                parent = labels.get(p)
                if parent and parent != "(profiler)":
                    labels[pid] = parent.split("/")[0] + "/" + st["comm"]
                    break
                p, depth = stats[p]["ppid"], depth + 1
        procs = {}
        for pid, label in labels.items():
            st = stats[pid]
            threads = {}
            if label != "(profiler)":
                task = os.path.join(root, str(pid), "task")
                try:
                    tids = os.listdir(task)
                except OSError:
                    tids = []
                for tid in tids:
                    if not tid.isdigit():
                        continue
                    ts = parse_stat(_read(os.path.join(task, tid, "stat")))
                    if ts:
                        threads[int(tid)] = (ts["comm"], ts["ticks"])
            procs[pid] = {"label": label, "start": st["start"],
                          "ticks": st["ticks"], "threads": threads,
                          "argv": self._meta.get((pid, st["start"]),
                                                 (None, []))[1]}
        return {"mono": time.monotonic(), "cpu": cpu, "procs": procs}


# ---------------------------------------------------------- per-interval math

def interval(prev, cur, hz, ncpu):
    """CPU% (of one core) per label and per thread between two snapshots,
    plus box busy% total and per core. A pid/tid counts only if present in
    BOTH with the same starttime (a reused pid is a different process)."""
    dt = 0.0
    if "cpu" in prev["cpu"] and "cpu" in cur["cpu"]:
        dt = (cur["cpu"]["cpu"][1] - prev["cpu"]["cpu"][1]) / float(hz * ncpu)
    if dt <= 0:
        dt = max(cur["mono"] - prev["mono"], 1e-6)
    box = {}
    for name, (busy, total) in cur["cpu"].items():
        if name in prev["cpu"]:
            db = busy - prev["cpu"][name][0]
            dtot = total - prev["cpu"][name][1]
            box[name] = 100.0 * db / dtot if dtot > 0 else 0.0
    per_label, per_thread, argv = {}, {}, {}
    for pid, p in cur["procs"].items():
        q = prev["procs"].get(pid)
        if not q or q["start"] != p["start"]:
            continue
        lab = p["label"]
        d = max(0, p["ticks"] - q["ticks"])
        per_label[lab] = per_label.get(lab, 0.0) + 100.0 * d / hz / dt
        argv.setdefault(lab, p["argv"])
        for tid, (comm, ticks) in p["threads"].items():
            old = q["threads"].get(tid)
            if old is None:
                continue
            dtk = max(0, ticks - old[1])
            per_thread[(lab, pid, tid)] = (comm, 100.0 * dtk / hz / dt)
    return {"dt": dt, "box": box, "labels": per_label,
            "threads": per_thread, "argv": argv}


def _pids_by_label(snap, into):
    for pid, p in snap["procs"].items():
        into.setdefault(p["label"], set()).add(pid)


def aggregate(intervals, ncpu, pids):
    """Average and peak over a window. A process absent from an interval
    counts as 0 there, so an app that restarted mid-window averages over the
    whole window, not just the part it was alive."""
    n = max(len(intervals), 1)
    box = {}
    for iv in intervals:
        for name, pct in iv["box"].items():
            s = box.setdefault(name, [0.0, 0.0])
            s[0] += pct
            s[1] = max(s[1], pct)
    procs = {}
    for iv in intervals:
        for lab, pct in iv["labels"].items():
            s = procs.setdefault(lab, {"sum": 0.0, "peak": 0.0, "argv": [],
                                       "threads": {}})
            s["sum"] += pct
            s["peak"] = max(s["peak"], pct)
            if not s["argv"]:
                s["argv"] = iv["argv"].get(lab) or []
        for (lab, pid, tid), (comm, pct) in iv["threads"].items():
            s = procs.setdefault(lab, {"sum": 0.0, "peak": 0.0, "argv": [],
                                       "threads": {}})
            t = s["threads"].setdefault((pid, tid), {"comm": comm,
                                                     "sum": 0.0, "peak": 0.0})
            t["comm"] = comm
            t["sum"] += pct
            t["peak"] = max(t["peak"], pct)
    out_procs = {}
    for lab, s in procs.items():
        threads = sorted(
            ({"pid": pid, "tid": tid, "comm": t["comm"],
              "avg_core": round(t["sum"] / n, 2),
              "peak_core": round(t["peak"], 2)}
             for (pid, tid), t in s["threads"].items()),
            key=lambda r: (-r["avg_core"], r["tid"]))
        groups = {}
        for t in threads:
            groups[t["comm"]] = round(groups.get(t["comm"], 0.0)
                                      + t["avg_core"], 2)
        avg = s["sum"] / n
        out_procs[lab] = {
            "pids": sorted(pids.get(lab, ())),
            "cmdline": " ".join(s["argv"])[:300],
            "avg_core": round(avg, 2), "peak_core": round(s["peak"], 2),
            "avg_box": round(avg / ncpu, 2),
            "peak_box": round(s["peak"] / ncpu, 2),
            "threads": threads[:JSON_THREADS],
            "thread_groups": groups,
        }
    total = box.pop("cpu", [0.0, 0.0])
    return {
        "box": {"avg": round(total[0] / n, 2), "peak": round(total[1], 2),
                "cores": {k: {"avg": round(v[0] / n, 2),
                              "peak": round(v[1], 2)}
                          for k, v in sorted(box.items(),
                                             key=lambda kv: _core_no(kv[0]))}},
        "processes": out_procs,
    }


def _core_no(name):
    try:
        return int(name[3:])
    except ValueError:
        return -1


# ------------------------------------------------- app state and the gateway

def app_state(root, pid, cwd):
    """Cameras and their fps from an app's state.json, or None.

    The app's stdout is <data_dir>/app.log (app/runtime.py start), and
    state.json sits beside it -- so fd 1 finds the data dir wherever storage
    put it (NVMe, [app].data_dir, or the package's ./data). Falls back to
    <cwd>/data. Anything missing or unreadable is skipped silently: this is
    context for the numbers, not part of them.
    """
    candidates = []
    log = _readlink(os.path.join(root, str(pid), "fd", "1"))
    if log and log.endswith("app.log"):
        candidates.append(os.path.dirname(log))
    if cwd:
        candidates.append(os.path.join(cwd, "data"))
    for d in candidates:
        text = _read(os.path.join(d, "state.json"))
        if not text:
            continue
        try:
            state = json.loads(text)
        except ValueError:
            continue
        cams = state.get("cameras") if isinstance(state, dict) else None
        if not isinstance(cams, list):
            continue
        return {"data_dir": d, "cameras": [
            {"id": c.get("id"), "fps": c.get("fps"),
             "measured_fps": c.get("measured_fps")}
            for c in cams if isinstance(c, dict)]}
    return None


def readers(url=READERS_URL, timeout=1.0):
    """{path: reader count} from MediaMTX, or None when unknown. Mirrors
    app/publisher.py readers(): a path without a readers list is left out."""
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8") or "{}")
    except (OSError, ValueError):
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    return {i["name"]: len(i["readers"]) for i in items
            if isinstance(i, dict) and isinstance(i.get("name"), str)
            and isinstance(i.get("readers"), list)}


def context(root, snap, url):
    apps = []
    for pid, p in sorted(snap["procs"].items()):
        if not p["label"].startswith("app:") or "/" in p["label"]:
            continue
        cwd = _readlink(os.path.join(root, str(pid), "cwd"))
        st = app_state(root, pid, cwd)
        if st:
            apps.append(dict(st, label=p["label"], pid=pid))
    return {"apps": apps, "readers": readers(url)}


# --------------------------------------------------------------------- output

def print_window(w, ncpu, top):
    print()
    print("== %s: %.0f s, %d samples, %d cores ==" % (
        w["label"], w["duration_s"], w["samples"], ncpu))
    b = w["box"]
    print("box busy   avg %5.1f%%   peak %5.1f%%   (of all %d cores)"
          % (b["avg"], b["peak"], ncpu))
    print("per core   " + "  ".join("%s %4.1f/%4.1f" % (k, v["avg"], v["peak"])
                                    for k, v in b["cores"].items())
          + "   (avg/peak %)")
    for a in w.get("apps_start") or []:
        cams = a.get("cameras") or []
        print("%-22s %d camera(s): %s" % (
            a["label"], len(cams),
            ", ".join("%s: %s fps processed, %s delivered" % (
                c.get("id"), c.get("fps"), c.get("measured_fps"))
                      for c in cams) or "-"))
    for key, word in (("readers_start", "start"), ("readers_end", "end")):
        r = w.get(key)
        print("readers (%s) %s" % (word, "unknown (gateway API not answering)"
                                   if r is None else
                                   (", ".join("%s=%d" % kv
                                              for kv in sorted(r.items()))
                                    or "no paths")))
    print()
    print("%-34s %-12s %9s %9s %8s %8s" % ("process / thread", "pids",
                                         "avg%core", "peak%core",
                                         "avg%box", "peak%box"))
    procs = sorted(w["processes"].items(), key=lambda kv: -kv[1]["avg_core"])
    for lab, p in procs:
        pids = ",".join(str(x) for x in p["pids"])
        print("%-34s %-12s %9.1f %9.1f %8.1f %8.1f" % (
            lab[:34], pids[:12], p["avg_core"], p["peak_core"],
            p["avg_box"], p["peak_box"]))
        for t in p["threads"][:top]:
            if t["avg_core"] < 0.05 and t["peak_core"] < 0.05:
                continue
            print("   %-31s %-12s %9.1f %9.1f" % (
                t["comm"][:31], "tid %d" % t["tid"], t["avg_core"],
                t["peak_core"]))
    total = sum(p["avg_core"] for lab, p in procs if lab != "(profiler)")
    print("%-34s %-12s %9.1f %9s %8.1f" % ("= product processes", "", total,
                                          "", total / ncpu))


# ------------------------------------------------------------------- the run

def run_window(scanner, label, interval_s, duration_s, hz, ncpu, url):
    samples = max(1, int(round(duration_s / interval_s)))
    prev = scanner.snapshot()
    ctx0 = context(scanner.root, prev, url)
    pids = {}
    _pids_by_label(prev, pids)
    ivs = []
    started = time.monotonic()
    for i in range(samples):
        # Paced against the start, so time spent reading /proc does not
        # stretch the window.
        delay = started + (i + 1) * interval_s - time.monotonic()
        _sleep(max(0.0, delay))
        cur = scanner.snapshot()
        ivs.append(interval(prev, cur, hz, ncpu))
        _pids_by_label(cur, pids)
        prev = cur
    ctx1 = context(scanner.root, prev, url)
    w = {"label": label, "samples": len(ivs),
         "duration_s": round(sum(iv["dt"] for iv in ivs), 2)}
    w.update(aggregate(ivs, ncpu, pids))
    w["apps_start"], w["apps_end"] = ctx0["apps"], ctx1["apps"]
    w["readers_start"], w["readers_end"] = ctx0["readers"], ctx1["readers"]
    return w


def _wait(seconds, prompt):
    if seconds is None:
        try:
            input(prompt)
        except EOFError:
            print("(no terminal to read ENTER from; starting now -- use "
                  "--wait N when not interactive)")
        return
    if seconds > 0:
        print("starting in %d s ..." % seconds)
        _sleep(seconds)


def cmd_run(args):
    root = args.proc_root
    if root == "/proc" and not (sys.platform.startswith("linux")
                                and os.path.isdir("/proc/self")):
        sys.exit("profile_cpu: this reads /proc and runs on the Linux box "
                 "(this is %s). To exercise it here, point --proc-root at a "
                 "fake /proc tree." % sys.platform)
    if not os.path.isfile(os.path.join(root, "stat")):
        sys.exit("profile_cpu: %s/stat not found -- not a /proc tree" % root)
    if args.interval <= 0 or args.duration <= 0:
        sys.exit("profile_cpu: --interval and --duration must be > 0")
    hz = args.hz
    if not hz:
        try:
            hz = os.sysconf("SC_CLK_TCK")
        except (AttributeError, ValueError, OSError):
            hz = 100
    cores = [k for k in parse_proc_stat(_read(os.path.join(root, "stat")))
             if k != "cpu"]
    ncpu = max(len(cores), 1)
    scanner = Scanner(root, own_pid=os.getpid() if root == "/proc" else None)
    url = None if args.no_readers else args.readers_url

    if args.scenario:
        plan = SCENARIOS[args.scenario]
        settle = args.settle
    else:
        plan = [(args.label, None)]
        settle = 0 if args.settle is None else args.settle
    if settle is None:
        settle = 12
    preface = SCENARIO_PREFACE.get(args.scenario)
    if preface:
        print(preface)
        print()

    windows = []
    for n, (label, text) in enumerate(plan, 1):
        if text:
            print("-- window %d/%d: %s --" % (n, len(plan), label))
            print(text)
            _wait(args.wait, "Press ENTER when done to start the window... ")
        if settle:
            print("settling %d s ..." % settle)
            _sleep(settle)
        print("measuring '%s' for %g s ..." % (label, args.duration))
        sys.stdout.flush()
        w = run_window(scanner, label, args.interval, args.duration, hz, ncpu,
                       url)
        windows.append(w)
        print_window(w, ncpu, args.top)

    if args.json:
        doc = {"tool": "profile_cpu", "format": 1,
               "host": socket.gethostname(),
               "when": datetime.datetime.now().isoformat(timespec="seconds"),
               "scenario": args.scenario, "interval_s": args.interval,
               "hz": hz, "ncpu": ncpu, "windows": windows}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
            f.write("\n")
        print()
        print("wrote %s" % args.json)
    return 0


# ---------------------------------------------------------------------- diff

def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as exc:
        sys.exit("profile_cpu diff: cannot read %s: %s" % (path, exc))
    if not isinstance(doc, dict) or doc.get("tool") != "profile_cpu":
        sys.exit("profile_cpu diff: %s is not a profile_cpu --json file" % path)
    return doc


def _pair_windows(a, b):
    """Windows matched by label when both runs have it, else by position, so
    a scenario run diffs window by window and two plain runs just pair up."""
    wa, wb = a.get("windows") or [], b.get("windows") or []
    lb = {w["label"]: w for w in wb}
    if wa and all(w["label"] in lb for w in wa) \
            and len(lb) == len(wb):
        return [(w, lb[w["label"]]) for w in wa]
    return list(zip(wa, wb))


def cmd_diff(args):
    a, b = _load(args.before), _load(args.after)
    ncpu = b.get("ncpu") or a.get("ncpu") or 1
    if a.get("ncpu") != b.get("ncpu"):
        print("note: core counts differ (%s vs %s); %%box uses %s"
              % (a.get("ncpu"), b.get("ncpu"), ncpu))
    pairs = _pair_windows(a, b)
    if not pairs:
        sys.exit("profile_cpu diff: no windows to compare")
    for wa, wb in pairs:
        print()
        title = wa["label"] if wa["label"] == wb["label"] \
            else "%s -> %s" % (wa["label"], wb["label"])
        print("== %s ==   before: %s   after: %s" % (
            title, args.before, args.after))
        ba, bb = wa["box"]["avg"], wb["box"]["avg"]
        print("box busy avg  %6.1f%% -> %6.1f%%   (%+.1f points of box)"
              % (ba, bb, bb - ba))
        print()
        print("%-34s %9s %9s %9s %8s" % ("process", "before", "after",
                                         "delta", "d%box"))
        pa, pb = wa["processes"], wb["processes"]
        labels = sorted(set(pa) | set(pb), key=lambda k: -abs(
            pb.get(k, {}).get("avg_core", 0.0)
            - pa.get(k, {}).get("avg_core", 0.0)))
        for lab in labels:
            x = pa.get(lab, {}).get("avg_core")
            y = pb.get(lab, {}).get("avg_core")
            d = (y or 0.0) - (x or 0.0)
            print("%-34s %9s %9s %+9.1f %+8.1f" % (
                lab[:34], "-" if x is None else "%.1f" % x,
                "-" if y is None else "%.1f" % y, d, d / ncpu))
            if args.threads:
                ga = pa.get(lab, {}).get("thread_groups") or {}
                gb = pb.get(lab, {}).get("thread_groups") or {}
                for comm in sorted(set(ga) | set(gb), key=lambda c: -abs(
                        gb.get(c, 0.0) - ga.get(c, 0.0))):
                    dd = gb.get(comm, 0.0) - ga.get(comm, 0.0)
                    if abs(dd) < args.min_delta:
                        continue
                    print("   %-31s %9.1f %9.1f %+9.1f" % (
                        comm[:31], ga.get(comm, 0.0), gb.get(comm, 0.0), dd))
    return 0


# ----------------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(
        prog="profile_cpu.py",
        description="Per-process and per-thread CPU of the IRIV processes, "
                    "read from /proc. 'diff A.json B.json' compares two runs.")
    p.add_argument("--interval", type=float, default=1.0,
                   help="seconds between samples (default 1)")
    p.add_argument("--duration", type=float, default=30.0,
                   help="seconds per timed window (default 30)")
    p.add_argument("--top", type=int, default=5,
                   help="threads shown per process (default 5)")
    p.add_argument("--json", metavar="FILE", help="also write the run here")
    p.add_argument("--label", default="run",
                   help="window label when no --scenario (default 'run')")
    p.add_argument("--scenario", choices=sorted(SCENARIOS),
                   help="timed windows with instructions between them")
    p.add_argument("--wait", type=float, default=None, metavar="N",
                   help="wait N seconds before each scenario window instead "
                        "of waiting for ENTER")
    p.add_argument("--settle", type=float, default=None, metavar="N",
                   help="seconds between ENTER and measuring (default 12 for "
                        "a scenario, 0 otherwise)")
    p.add_argument("--proc-root", default="/proc",
                   help="read this tree instead of /proc (testing)")
    p.add_argument("--hz", type=int, default=0,
                   help="clock ticks per second (default: SC_CLK_TCK, or 100)")
    p.add_argument("--readers-url", default=READERS_URL,
                   help="MediaMTX paths API (default %(default)s)")
    p.add_argument("--no-readers", action="store_true",
                   help="do not ask MediaMTX for reader counts")
    return p


def build_diff_parser():
    p = argparse.ArgumentParser(prog="profile_cpu.py diff",
                                description="Per-process CPU delta of two "
                                            "--json runs (after minus before).")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--threads", action="store_true",
                   help="also show per-thread-name deltas inside each process")
    p.add_argument("--min-delta", type=float, default=1.0,
                   help="hide thread-name deltas smaller than this "
                        "(%%core, default 1)")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "diff":
        return cmd_diff(build_diff_parser().parse_args(argv[1:]))
    return cmd_run(build_parser().parse_args(argv))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
