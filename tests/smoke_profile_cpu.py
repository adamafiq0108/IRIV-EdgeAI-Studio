# tools/profile_cpu.py against a fake /proc tree that changes between samples.
#
# The tool runs on the Pi, but its parsing and arithmetic are the part that
# can be wrong quietly, so they are pinned here on any OS: a tree is written
# to a temp dir and advanced one step per "sleep" (the tool's _sleep hook),
# with a pid that exits, a thread that exits, a pid number reused by a
# different process, a comm with a space and a ')' in it, a malformed stat,
# and the profiler's own process -- then the averages, peaks, labels, JSON,
# the diff subcommand and the scenario windows are checked against numbers
# worked out by hand. The MediaMTX readers call is answered by a local stub.
#
#     py -3 tests/smoke_profile_cpu.py
import builtins
import contextlib
import http.server
import io
import json
import os
import shutil
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import profile_cpu as pc                                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


TMP = tempfile.mkdtemp(prefix="profcpu_")
PROC = os.path.join(TMP, "proc")
SYS = os.path.join(TMP, "store", "systems", "people")
NVME = os.path.join(TMP, "nvme", "people", "data")
os.makedirs(os.path.join(SYS, "data"))
os.makedirs(NVME)

HZ, NCPU = 100, 2


def can_symlink():
    try:
        os.symlink(SYS, os.path.join(TMP, "probe"), target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


LINKS = can_symlink()


def stat_line(pid, comm, ppid, ticks, start):
    # utime carries every tick, stime 0; 9 fields between ppid and utime,
    # 6 between stime and starttime (proc(5)).
    return ("%d (%s) S %d 1 1 0 -1 4194304 0 0 0 0 %d 0 0 0 20 0 1 0 %d "
            "1000 100\n" % (pid, comm, ppid, ticks, start))


def proc_entry(pid, comm, ppid, ticks, start, argv, threads=None, cwd=None,
               fd1=None):
    d = os.path.join(PROC, str(pid))
    os.makedirs(os.path.join(d, "task"))
    with open(os.path.join(d, "stat"), "w", newline="\n") as f:
        f.write(stat_line(pid, comm, ppid, ticks, start))
    with open(os.path.join(d, "cmdline"), "w", newline="\n") as f:
        f.write("\0".join(argv) + ("\0" if argv else ""))
    for tid, (tcomm, tticks) in (threads or {pid: (comm, ticks)}).items():
        td = os.path.join(d, "task", str(tid))
        os.makedirs(td)
        with open(os.path.join(td, "stat"), "w", newline="\n") as f:
            f.write(stat_line(tid, tcomm, ppid, tticks, start))
    if LINKS and cwd:
        os.symlink(cwd, os.path.join(d, "cwd"), target_is_directory=True)
    if LINKS and fd1:
        os.makedirs(os.path.join(d, "fd"))
        os.symlink(fd1, os.path.join(d, "fd", "1"))


def write_tree(step, app_rate=60):
    """The box after `step` seconds. Per step: each core 100 jiffies, core0
    50 busy, core1 30 busy; the app 60 ticks (thread src:src 40, the odd
    thread 20 until it exits at step 2); hub 5; mediamtx 10 until it exits
    at step 3; master 5, whose pid 200 is reused at step 4 by another
    system's master; the app's child 5; ffmpeg 7."""
    if os.path.isdir(PROC):
        shutil.rmtree(PROC)
    os.makedirs(PROC)
    s = step
    c0b, c1b = 1000 + 50 * s, 2000 + 30 * s
    c0t, c1t = 10000 + 100 * s, 20000 + 100 * s
    # user nice system idle iowait irq softirq steal guest guest_nice
    lines = ["cpu  %d 0 0 %d 0 0 0 0 0 0" % (c0b + c1b,
                                              c0t + c1t - c0b - c1b),
             "cpu0 %d 0 0 %d 0 0 0 0 0 0" % (c0b, c0t - c0b),
             "cpu1 %d 0 0 %d 0 0 0 0 0 0" % (c1b, c1t - c1b),
             "intr 12345", "ctxt 999"]
    with open(os.path.join(PROC, "stat"), "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    proc_entry(100, "python3", 1, 500 + 5 * s, 10,
               ["python3", "/opt/cytron/hub_server.py"])
    if s < 4:
        proc_entry(200, "python3", 100, 300 + 5 * s, 20,
                   ["python3", "master_server.py", "--system", "people",
                    "--port", "5101"])
    else:
        proc_entry(200, "python3", 100, 9000 + 5 * s, 777,
                   ["python3", "master_server.py", "--system=other"])
    threads = {300: ("python3", 100), 301: ("src:src", 1000 + 40 * s)}
    if s < 2:
        threads[302] = ("enc ) odd x", 50 + 20 * s)
    proc_entry(300, "python3", 200, 2000 + app_rate * s, 30,
               ["/usr/bin/python3", "-m", "app.run"], threads, cwd=SYS,
               fd1=os.path.join(NVME, "app.log"))
    if s < 3:
        proc_entry(400, "mediamtx", 1, 700 + 10 * s, 40,
                   ["/usr/local/bin/mediamtx", "/etc/mediamtx.yml"])
    proc_entry(500, "hailort_service", 1, 50, 50, ["/usr/bin/hailort_service"])
    proc_entry(600, "ffmpeg", 300, 100 + 7 * s, 60, ["ffmpeg", "-i", "x"])
    proc_entry(610, "convert", 300, 10 + 5 * s, 61, ["convert", "a", "b"])
    proc_entry(700, "python3", 1, 5 + 2 * s, 70,
               ["python3", "tools/profile_cpu.py", "--json", "x"])
    proc_entry(800, "bash", 1, 999 * s, 80, ["-bash"])
    proc_entry(900, "(sd-pam)", 1, 3, 90, [])
    os.makedirs(os.path.join(PROC, "950"))
    with open(os.path.join(PROC, "950", "stat"), "w") as f:
        f.write("garbage")
    os.makedirs(os.path.join(PROC, "960"))       # vanished: no stat at all
    os.makedirs(os.path.join(PROC, "net"))      # non-pid entries ignored


with open(os.path.join(NVME, "state.json"), "w") as f:
    json.dump({"cameras": [{"id": "cam1", "fps": 14, "measured_fps": 15},
                           {"id": "cam2", "fps": 9, "measured_fps": 10}]}, f)
with open(os.path.join(SYS, "data", "state.json"), "w") as f:
    json.dump({"cameras": [{"id": "local", "fps": 1}]}, f)


class Steps:
    def __init__(self, app_rate=60):
        self.n, self.app_rate = 0, app_rate
        write_tree(0, app_rate)

    def __call__(self, _seconds):
        self.n += 1
        write_tree(self.n, self.app_rate)


class Stub(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"items": [
            {"name": "people__cam1", "readers": [{}, {}]},
            {"name": "people__cam2", "readers": []},
            {"name": "odd"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d/v3/paths/list" % srv.server_address[1]


def run(argv, app_rate=60, inputs=None):
    pc._sleep = Steps(app_rate)
    out = io.StringIO()
    real_input = builtins.input
    if inputs is not None:
        builtins.input = lambda prompt="": inputs.append(prompt) or ""
    try:
        with contextlib.redirect_stdout(out):
            rc = pc.main(argv)
    finally:
        builtins.input = real_input
    return rc, out.getvalue()


try:
    # --- parsing on its own
    st = pc.parse_stat("42 (enc ) odd x) R 7 1 1 0 -1 0 0 0 0 0 11 4 0 0 20 0 "
                       "3 0 555 0 0\n")
    check("stat with ') ' inside comm parses on the LAST ')'",
          st == {"comm": "enc ) odd x", "ppid": 7, "ticks": 15, "start": 555},
          st)
    check("malformed stat is None, not a crash",
          pc.parse_stat("garbage") is None and pc.parse_stat("1 (x) S") is None
          and pc.parse_stat(None) is None)
    cs = pc.parse_proc_stat("cpu  10 1 2 100 5 1 1 0 7 0\ncpu0 1 2 3 4\n")
    check("/proc/stat busy excludes idle+iowait, guest not double-counted",
          cs["cpu"] == (15, 120) and cs["cpu0"] == (6, 10), cs)
    check("classify: hub / master --system X / --system=X / app / mediamtx",
          [pc.classify(["python3", "/x/hub_server.py"], "python3"),
           pc.classify(["python3", "master_server.py", "--system", "a"], "p"),
           pc.classify(["python3", "master_server.py", "--system=b"], "p"),
           pc.classify(["python3", "-m", "app.run"], "p", "/s/systems/c/"),
           pc.classify(["/usr/local/bin/mediamtx"], "mediamtx"),
           pc.classify([], "hailort_service"),
           pc.classify(["gst-launch-1.0", "x"], "gst-launch-1.0"),
           pc.classify(["python3", "other.py"], "python3")]
          == ["hub", "master:a", "master:b", "app:c", "mediamtx",
              "hailort_service", "gst-launch-1.0", None])

    # --- a plain 5 s run
    j1 = os.path.join(TMP, "before.json")
    rc, text = run(["--proc-root", PROC, "--hz", "100", "--duration", "5",
                    "--json", j1, "--readers-url", URL, "--top", "3"])
    with open(j1) as f:
        doc = json.load(f)
    w = doc["windows"][0]
    P = w["processes"]
    app = "app:people" if LINKS else "app:pid300"
    check("ran, 5 samples, 2 cores from /proc/stat",
          rc == 0 and w["samples"] == 5 and doc["ncpu"] == 2, (rc, w["samples"]))
    check("elapsed taken from /proc/stat jiffies (5 s)",
          abs(w["duration_s"] - 5.0) < 1e-6, w["duration_s"])
    check("box busy 40%, core0 50%, core1 30%",
          w["box"]["avg"] == 40.0 and w["box"]["cores"]["cpu0"]["avg"] == 50.0
          and w["box"]["cores"]["cpu1"]["avg"] == 30.0, w["box"])
    check("app 60% of a core, 30% of the box", P.get(app, {}).get("avg_core")
          == 60.0 and P[app]["avg_box"] == 30.0 and P[app]["peak_core"] == 60.0,
          P.get(app))
    th = {t["tid"]: t for t in P[app]["threads"]}
    check("thread src:src 40%; the exited odd thread counts 1 of 5 intervals",
          th[301]["avg_core"] == 40.0 and th[301]["comm"] == "src:src"
          and th[302]["avg_core"] == 4.0 and th[302]["peak_core"] == 20.0
          and th[302]["comm"] == "enc ) odd x", th)
    check("mediamtx exits after 2 intervals: avg 4, peak 10",
          P["mediamtx"]["avg_core"] == 4.0 and P["mediamtx"]["peak_core"] == 10.0,
          P["mediamtx"])
    check("reused pid 200: 3 intervals of master:people, the swap interval "
          "dropped, then master:other",
          P["master:people"]["avg_core"] == 3.0
          and P["master:other"]["avg_core"] == 1.0
          and P["master:other"]["pids"] == [200], (P.get("master:people"),
                                                   P.get("master:other")))
    check("hub, hailort_service (idle), ffmpeg found",
          P["hub"]["avg_core"] == 5.0 and P["hailort_service"]["avg_core"] == 0
          and P["ffmpeg"]["avg_core"] == 7.0, sorted(P))
    check("an unnamed child of the app is the app's cost",
          P.get(app + "/convert", {}).get("avg_core") == 5.0, sorted(P))
    check("the profiler is its own row, never a product process",
          P["(profiler)"]["avg_core"] == 2.0
          and not any("profile_cpu" in P[k]["cmdline"]
                      for k in P if k != "(profiler)"), sorted(P))
    check("unrelated processes (bash, sd-pam) left out",
          not any("bash" in k or "sd-pam" in k for k in P), sorted(P))
    check("readers from the gateway API, path without a list left out",
          w["readers_end"] == {"people__cam1": 2, "people__cam2": 0},
          w["readers_end"])
    check("table printed with the app and its top thread",
          app in text and "src:src" in text and "box busy" in text, text)
    if LINKS:
        cams = w["apps_start"][0]["cameras"] if w["apps_start"] else []
        check("camera fps from state.json beside app.log (fd 1), not cwd/data",
              [c["id"] for c in cams] == ["cam1", "cam2"]
              and cams[0]["fps"] == 14 and "cam1: 14 fps processed" in text,
              w["apps_start"])
    else:
        print("skip symlink-based checks (cwd, fd/1): this OS user cannot "
              "create symlinks; app_state checked directly instead")
    st = pc.app_state(PROC, 99999, SYS)
    check("state.json fallback: <cwd>/data when fd 1 is not readable",
          st and st["cameras"] == [{"id": "local", "fps": 1,
                                    "measured_fps": None}], st)
    check("no state.json anywhere: None, silently",
          pc.app_state(PROC, 99999, os.path.join(TMP, "nowhere")) is None)

    # --- a second run with the app cheaper, then diff
    j2 = os.path.join(TMP, "after.json")
    rc, _ = run(["--proc-root", PROC, "--hz", "100", "--duration", "5",
                 "--json", j2, "--no-readers"], app_rate=30)
    with open(j2) as f:
        w2 = json.load(f)["windows"][0]
    check("--no-readers records readers as unknown", w2["readers_end"] is None)
    rc, text = run(["diff", j1, j2, "--threads"])
    line = [ln for ln in text.splitlines() if ln.startswith(app + " ")]
    check("diff: app 60.0 -> 30.0, delta -30.0 (-15.0 of box)",
          rc == 0 and line and "60.0" in line[0] and "30.0" in line[0]
          and "-30.0" in line[0] and "-15.0" in line[0], text)

    # --- scenario: three labelled windows, ENTER waited for each
    j3 = os.path.join(TMP, "scen.json")
    prompts = []
    rc, text = run(["--proc-root", PROC, "--hz", "100", "--duration", "2",
                    "--scenario", "viewer-webrtc", "--settle", "0",
                    "--no-readers", "--json", j3], inputs=prompts)
    with open(j3) as f:
        labels = [x["label"] for x in json.load(f)["windows"]]
    check("viewer-webrtc: windows idle, viewer-webrtc, idle-after",
          labels == ["idle", "viewer-webrtc", "idle-after"], labels)
    check("...each preceded by its instruction and an ENTER prompt",
          len(prompts) == 3 and "window 2/3: viewer-webrtc" in text
          and "Open ONE browser tab" in text, (prompts, text[:400]))
    rc, text = run(["--proc-root", PROC, "--hz", "100", "--duration", "1",
                    "--scenario", "viewer-mjpeg", "--wait", "0", "--settle",
                    "0", "--no-readers"], inputs=prompts)
    check("--wait 0 never prompts; viewer-mjpeg says to switch the system "
          "to MJPEG first", len(prompts) == 3 and 'stream = "mjpeg"' in text)
    rc, text = run(["diff", j1, j3])
    check("diff of differently-labelled runs pairs windows by position",
          rc == 0 and "run -> idle" in text, text[:300])

    # --- refusals
    if not sys.platform.startswith("linux"):
        try:
            pc.main(["--duration", "1"])
            check("non-Linux without --proc-root refuses", False)
        except SystemExit as exc:
            check("non-Linux without --proc-root refuses with a clear message",
                  "runs on the Linux box" in str(exc.code), exc.code)
    try:
        pc.main(["--proc-root", TMP, "--duration", "1"])
        check("a tree without stat refuses", False)
    except SystemExit as exc:
        check("a tree without stat refuses clearly",
              "not a /proc tree" in str(exc.code), exc.code)
    try:
        pc.main(["diff", j1, os.path.join(TMP, "nope.json")])
        check("diff of a missing file refuses", False)
    except SystemExit as exc:
        check("diff of a missing file refuses clearly",
              "cannot read" in str(exc.code), exc.code)
finally:
    srv.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)

print("smoke_profile_cpu: %s" % ("OK" if not bad else "%d FAILED" % bad))
sys.exit(1 if bad else 0)
