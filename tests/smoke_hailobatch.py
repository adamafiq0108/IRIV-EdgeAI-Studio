"""A multi-context model is fed in batches, because that is what it rewards.

Measured on 192.168.1.234 (Hailo-8, HailoRT 4.23, PCIe Gen 2): yolov8m is a
3-context HEF, and through our InferModel path it ran 14.7 frames/s at batch
1, 23.0 at 4, 31.7 at 8 -- the part of its weights streamed from host memory
per frame is streamed once per batch instead. yolov8s is single-context and
already does 100+ at batch 1, so it gains nothing and keeps batch 1.

Pinned with a FAKE ConfiguredInferModel: a run() always receives exactly
`batch` bindings (the runtime refuses otherwise), a short chunk is padded with
its own last frame and the pad's result discarded, results land on their
frames in order, and None frames answer [] without reaching the chip.

    python tests/smoke_hailobatch.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


hailo_src = read("systems", "demo", "app", "hailo.py")
run_src = read("systems", "demo", "app", "run.py")
det_src = read("systems", "demo", "app", "detector.py")

# -- 1. the shape --------------------------------------------------------------
check("every detector says how many frames it wants; the base wants one",
      "    batch = 1" in det_src)
check("the Hailo detector reads the HEF's context count without the device",
      "def hef_contexts(hef_path):" in hailo_src and '"parse-hef"' in hailo_src)
check("...multi-context defaults to 8, single-context to 1",
      "DEFAULT_MULTI_CONTEXT_BATCH = 8" in hailo_src
      and "def _auto_batch(contexts, cameras=None):" in hailo_src
      and "return DEFAULT_MULTI_CONTEXT_BATCH" in hailo_src)
# ...and never wider than there are cameras to fill it. A batch of eight on a
# three-camera system cannot be filled from one pass, so the loop waited for
# every camera to produce two more frames before it inferred anything -- 20%
# of the loop inside that wait on the live box, and everything downstream
# arriving in bursts a batch apart instead of as each camera was read. The
# short chunk was padded with a copy of the last frame anyway, so the chip did
# eight inferences to answer three.
import sys as _sys                                              # noqa: E402
_sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import hailo as _hailo                                 # noqa: E402
check("...and never wider than the cameras that must fill it",
      (_hailo._auto_batch(3, 3), _hailo._auto_batch(3, 2),
       _hailo._auto_batch(3, 1)) == (3, 2, 1),
      "a batch nothing can fill in one pass is a wait, not a batch")
check("...with the full batch still the ceiling",
      _hailo._auto_batch(3, 99) == _hailo.DEFAULT_MULTI_CONTEXT_BATCH)
check("...a single-context model still takes one",
      _hailo._auto_batch(1, 8) == 1,
      "it gains nothing from a batch and would only pay the latency")
check("...and an unknown camera count is not guessed at",
      _hailo._auto_batch(3, None) == _hailo.DEFAULT_MULTI_CONTEXT_BATCH
      and _hailo._auto_batch(3, 0) == _hailo.DEFAULT_MULTI_CONTEXT_BATCH,
      "the count is a BOUND on the batch; absent, there is nothing to bound it "
      "with and the old default stands")
check("...and the loop tells the detector how many cameras it has",
      "_cameras=len(_camera_list(cfg)) or 1" in run_src)
check("...an explicit [detector.hailo] batch wins, clamped",
      'configured = self.config.get("batch")' in hailo_src
      and "max(1, min(MAX_BATCH, int(configured)))" in hailo_src)
check("...and it is fixed BEFORE configure()",
      hailo_src.index("self._model.set_batch_size(self.batch)") < hailo_src.index("self._infer = self._model.configure()"))
check("the run loop collects ceil(batch / cameras) rounds per pass",
      "rounds = max(1, -(-want // max(1, len(live))))" in run_src)
check("...each extra round waits for a FRESH grab on every camera at once, bounded by the slowest period",
      "p.cam.latest()[1] != seqs[p.id]" in run_src and "longest * 1.5" in run_src,
      "a batch of the same picture eight times is eight wasted inferences; "
      "an unbounded wait is a stalled camera stalling every camera")
check("...and the HUD rate is frames per camera per second, not passes",
      "inst = per_pass / dt" in run_src)

# -- the batch overlaps the loop --------------------------------------------------
check("a batched detector runs on its own thread while the loop collects and draws",
      "def _infer_submit(frames):" in run_src and "def _infer_collect():" in run_src
      and run_src.index("ready = _infer_collect()") < run_src.rindex("_infer_submit(frames)"),
      "profiled in series: 56% waiting for frames, 36% waiting on the chip, rate unchanged")
check("...batch 1 keeps the synchronous path",
      "ready = (frames, _infer_batch([f for _p, f in frames]))" in run_src)
check("...and the detector is drained before every close or rebuild",
      run_src.count("_infer_drain()") >= 3 and "def _infer_drain():" in run_src,
      "a VDevice released under a running batch is a crash, not an error")
check("...with one ladder for both paths", "def _infer_failed(exc):" in run_src
      and run_src.count("_infer_failed(") >= 3)

# -- a client the service dropped is replaced, never released --------------------
check("an RPC failure is told apart from a chip failure",
      "def _rpc_failure(exc):" in run_src and '"RPC_FAILED" in text' in run_src)
check("...and the dead handle is NOT closed before the fallback",
      "if _rpc_failure(exc):" in run_src
      and run_src.index("if _rpc_failure(exc):", run_src.index("def _infer_failed(exc):"))
        < run_src.index("det = detector_mod.fallback(det_cfg)", run_src.index("def _infer_failed(exc):")),
      "releasing a handle the service no longer knows faults inside the "
      "runtime and the process dies with no traceback -- seen three times in a day")
check("shutdown drains the batch in flight before releasing the accelerator",
      run_src.index("_infer_drain()\n        except Exception")
        < run_src.index("det.close()\n        except Exception as exc:")
      and run_src.index("det.close()\n        except Exception as exc:")
        < run_src.index("runtime.write(data_dir, running=False, pid=None)"),
      "a release under a running batch is a fault, and a fault there skipped the stopped-state write")

# -- a dropped service client restarts the app --------------------------------------
check("repeated RPC failures restart the app in place rather than climb the ladder",
      "def _restart_self(why, fault=True):" in run_src
      and 'os.execv(sys.executable, [sys.executable, "-m", "app.run"] + sys.argv[1:])' in run_src
      and "RPC_RESTART_AFTER, RPC_RESTART_WINDOW_S = 3, 10.0" in run_src,
      "tested: even without releasing the dead handle the survivor died natively; "
      "there is no in-process recovery from a dropped service client")
check("...one RPC failure is a transient, not a restart",
      "len(rpc_hits) >= RPC_RESTART_AFTER" in run_src)
check("...and what must not be lost is flushed first, with the reason on record",
      run_src.index("flush_daily(force=True)", run_src.index("def _restart_self"))
        < run_src.index("os.execv(", run_src.index("def _restart_self"))
      and 'runtime.write(data_dir, error=f"{why}; restarting")' in run_src)

# -- the one-click catalogue stops at "small" ---------------------------------------
# Batching lifts a multi-context model a long way (3.7 to 9.9 fps per camera
# with four cameras) but not to where a single-context model starts, and an
# operator who picks "balanced" off a menu is not choosing to quarter their
# frame rate -- they are trusting the menu. So yolov8m is off the menu; a
# heavier HEF stays available by hand-filled upload for anyone who means it.
catalogue = read("systems", "demo", "models.toml")
presets = read("core", "services", "models.py")
check("no multi-context model is offered as a one-click download",
      "[models.yolov8m]" not in catalogue,
      "yolov8m at 14.7 fps/batch-1 reads as the product being slow")
check("...nor as an upload preset",
      '{"name": "yolov8m"' not in presets)
check("...and detection offers exactly two models, nano and small",
      [k for k in catalogue.split(chr(10)) if k.startswith("[models.")]
      and sum(1 for line in catalogue.split(chr(10))
              if line.startswith("[models.")
              and "_seg" not in line and "_pose" not in line
              and "depth" not in line) == 2
      and presets.count('{"name": "yolo') == 2,
      "a third preset in the same speed class (yolov6n) only asks the "
      "operator to research a choice that does not change their result")
check("...while small and nano stay",
      "[models.yolov8s]" in catalogue and "[models.yolov8n]" in catalogue
      and '{"name": "yolov8s"' in presets)

# -- the balancer counts CAMERAS, not frames -----------------------------------
# The batching above made per_cam one entry per FRAME (rounds x cameras). Two
# call sites still read len(per_cam) as a camera count: the HUD rate, and the
# chip balancer's weight. The second one bit -- a two-camera system on batch 8
# heartbeated a weight of 8, _fps_share saw "more than four cameras" and
# returned the 10 fps floor, and the balancer paced both cameras at 10. On the
# box: chip ~90% idle, 21 inferences/s against a measured ceiling of 134+, and
# the loop parked 88% of its samples waiting for a grab thread it had itself
# told to go slowly. Measured on 192.168.1.63 with py-spy.
check("one helper answers how many cameras a pass touched",
      "def cams_this_pass():" in run_src
      and run_src.count("cams_this_pass()") >= 3,
      "the HUD rate and the balancer weight must not be able to disagree")
check("...and the balancer weight is that, not the batch size",
      "live_cams = max(1, cams_this_pass())" in run_src
      and "live_cams = max(1, len(per_cam))" not in run_src,
      "len(per_cam) is the batch size once the loop batches")
check("...and the HUD divides by it too",
      "per_pass = max(1, len(per_cam)) / max(1, cams_this_pass())" in run_src)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
