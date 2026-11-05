# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
hailo.py -- Hailo accelerator support: discovery, arch, and a Detector.

Runs on Hailo-8, Hailo-8L (AI Kit / AI HAT) and Hailo-10H (AI HAT+). The three
differ for our purposes in exactly one way: **which compiled HEF they accept**.
A HEF built for hailo8 will not load on hailo10h, so the architecture is
detected at runtime and used to pick the model file -- that is why models live
in `models/<arch>/<name>.hef` rather than one flat folder.

Everything degrades. On a laptop with no accelerator, `probe()` returns
`{"present": false}` and `HailoDetector` raises a readable error instead of an
ImportError traceback, so a developer can build against this file without the
hardware in front of them.

Requires on-device: HailoRT + its Python bindings (`hailo_platform`), installed
by the Hailo runtime packages. Nothing here pins a HailoRT version -- the API
used (`VDevice.create_infer_model`) is the stable one from HailoRT 4.17
onward, including 5.x.
"""

import glob
import logging
import os
import re
import stat
import subprocess
import tempfile
import time

logger = logging.getLogger(__name__)

#: Accelerators, newest first. The names match the Hailo Model Zoo's compiled
#: directories, so a HEF path is `models/<arch>/<name>.hef` with no mapping.
ARCHS = ("hailo10h", "hailo8", "hailo8l")

_PCI_HINTS = (
    (re.compile(r"hailo-?10", re.I), "hailo10h"),
    (re.compile(r"hailo-?8l", re.I), "hailo8l"),
    (re.compile(r"hailo-?8", re.I), "hailo8"),
)


def _run(argv, timeout=8):
    """Run a probe tool; a missing tool is a normal answer, not an error."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def detect_arch():
    """Return "hailo10h" | "hailo8" | "hailo8l" | None.

    Three sources, cheapest last-resort first: hailortcli (authoritative when
    HailoRT is installed), then lspci (works even if HailoRT is not), then the
    PCIe driver's own sysfs entries.
    """
    text = _run(["hailortcli", "fw-control", "identify"]) or _run(["hailortcli", "scan"])
    for pattern, arch in _PCI_HINTS:
        if text and pattern.search(text):
            return arch

    lspci = _run(["lspci", "-mm"])
    for line in lspci.splitlines():
        if "hailo" in line.lower():
            for pattern, arch in _PCI_HINTS:
                if pattern.search(line):
                    return arch
            return "hailo8"              # a Hailo PCIe device of unknown model

    for path in glob.glob("/sys/class/hailo_chardev/hailo*/device/device"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                if f.read().strip():
                    return "hailo8"
        except OSError:
            continue
    return None


def probe():
    """Inventory for telemetry and the Monitoring page.

    Safe on every host: a machine with no accelerator answers
    {"present": False, "arch": None} and nothing raises.
    """
    arch = detect_arch()
    info = {"present": bool(arch), "arch": arch, "runtime": None, "devices": []}
    try:
        import hailo_platform                                   # noqa: PLC0415

        info["runtime"] = getattr(hailo_platform, "__version__", "unknown")
        try:
            info["devices"] = [str(d) for d in
                               hailo_platform.Device.scan()]    # type: ignore[attr-defined]
        except Exception:                                       # noqa: BLE001
            pass
    except ImportError:
        info["runtime"] = None
    identify = _run(["hailortcli", "fw-control", "identify"])
    if identify:
        info["identify"] = [l.strip() for l in identify.splitlines() if l.strip()]
    temp = _run(["hailortcli", "measure-temp"])
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*C", temp or "")
    if match:
        info["temperature_c"] = float(match.group(1))
    return info


def resolve_hef(models_dir, name, arch=None):
    """models/<arch>/<name>.hef, falling back to models/<name>.hef.

    When the CONFIGURED models_dir holds nothing, the same layout is retried
    under every known model root (CYTRON_MODEL_DIR, the NVMe's /mnt/nvme/model,
    the legacy /home/pi/model -- app/config.py model_roots()), both as
    <root>/<basename(models_dir)>/... and directly under <root>. A saved
    config.local.toml carries ABSOLUTE model paths; when the operator moves
    the model folder to the NVMe those stored paths go stale, and refusing to
    look in the new home would brick a detector over a path the dashboard
    wrote itself. The fallback is logged, never silent.

    Raises FileNotFoundError naming every path tried -- "model not found" with
    no path is the least useful message an edge deployment can produce.
    """
    from app import config as _config_mod                   # noqa: PLC0415

    arch = arch or detect_arch()
    name = name if name.endswith(".hef") else f"{name}.hef"

    def _layout(base):
        out = []
        if arch:
            out.append(os.path.join(base, arch, name))
        out.append(os.path.join(base, name))
        return out

    candidates = _layout(models_dir)
    for path in candidates:
        if os.path.exists(path):
            return path

    folder = os.path.basename(os.path.normpath(str(models_dir)))
    fallbacks = []
    for root in _config_mod.model_roots():
        if os.path.realpath(root) == os.path.realpath(str(models_dir)):
            continue
        if folder and folder != os.path.basename(os.path.normpath(root)):
            fallbacks.extend(_layout(os.path.join(root, folder)))
        fallbacks.extend(_layout(root))
    for path in fallbacks:
        if os.path.exists(path):
            logger.info("model: %s not at the configured %r; using %s",
                        name, models_dir, path)
            return path
    raise FileNotFoundError(
        f"no HEF for {name!r} (arch={arch}); tried: "
        + ", ".join(candidates + fallbacks))


class HailoDetector:
    """Contract-side Detector backed by HailoRT.

    Config (systems/<slug>/config.toml):

        [detector]
        kind = "hailo"
        model = "yolov8n"          # models/<arch>/yolov8n.hef
        models_dir = "models"      # relative to the system package
        labels = "models/coco.txt" # one class name per line
        confidence = 0.45
        classes = ["person"]       # empty = every class

    Assumes NMS-on-chip HEFs (the Model Zoo default for the YOLO family), whose
    output is already grouped per class -- decoding raw feature maps on the CPU
    would give back the latency the accelerator just saved.
    """

    name = "hailo"

    def __init__(self, config=None, vdevice=None):
        self.config = dict(config or {})
        self.min_confidence = float(self.config.get("confidence", 0.45))
        self.classes = set(self.config.get("classes") or ())
        self.arch = detect_arch()
        self._infer = self._vdevice = None
        # A caller (MultiHailoDetector) can pass ONE VDevice to share across
        # several models in this process. When shared, we don't own it and must
        # not release it on close.
        self._shared_vd = vdevice
        self._owns_vd = vdevice is None
        # Whether this detector's VDevice is a client of hailort_service
        # (multi_process). Such a client must not be released in place on
        # Hailo-8L + HailoRT 4.23: the release takes the service down
        # (asri_test_4 on .49, 2026-10-09: every Stop crashed hailort_service
        # and the app with it), so run.py re-executes the app instead -- see
        # release_by_exec there.
        self.via_service = False
        self.labels = self._load_labels(self.config.get("labels"))
        self._open()

    def _load_labels(self, path):
        """Class names from the configured labels file.

        No labels configured at all -> [] (numeric class ids; the documented
        behaviour of an unconfigured system). A CONFIGURED file that is
        missing is different: the config points at data the operator relied
        on, so after retrying the same filename in the places it may have
        moved to (the current data dir after a storage migration, the model
        folder), the detector REFUSES to start, naming the path. Starting
        with no labels quietly renumbers every class -- a detector that runs
        wrong is worse than one that says why it will not.
        """
        if not path:
            return []
        candidates = [path]
        base = os.path.basename(str(path))
        for folder in (self.config.get("_data_dir"),
                       self.config.get("models_dir")):
            if folder:
                candidates.append(os.path.join(str(folder), base))
        for candidate in candidates:
            if not os.path.exists(candidate):
                continue
            if candidate != path:
                logger.info("labels: %s is missing; using %s",
                            path, candidate)
            with open(candidate, "r", encoding="utf-8") as f:
                return [line.strip() for line in f if line.strip()]
        raise FileNotFoundError(
            f"the labels file is not at {path!r} (also tried: "
            + ", ".join(candidates[1:] or ["nothing else"])
            + ") - re-select the model on the AI Models page or fix "
              "[detector].labels")

    def _open(self):
        try:
            from hailo_platform import FormatType             # noqa: PLC0415
        except ImportError as e:
            raise RuntimeError(
                "HailoRT Python bindings not installed (hailo_platform). Install "
                "the HailoRT runtime for this device, or set detector.kind to "
                "\"mock\" for development."
            ) from e
        if not self.arch:
            raise RuntimeError("no Hailo accelerator found (hailortcli scan is empty)")

        models_dir = self.config.get("models_dir") or "models"
        hef_path = resolve_hef(models_dir, self.config.get("model") or "yolov8n",
                               self.arch)
        # A SHARED, scheduled VDevice: the HailoRT scheduler time-slices
        # several models on the one accelerator instead of the first one
        # owning it exclusively ("VDevice released while CIM in use"), and --
        # see _open_vdevice -- shares it across PROCESSES too, so a second
        # system on this box is not refused with error 74. A shared VDevice
        # passed in (multi-model in one process) is reused rather than
        # re-opened.
        if self._shared_vd is not None:
            self._vdevice = self._shared_vd
        else:
            # Explicit beats default, both ways: a `false` written on purpose
            # must stay false even on a Hailo-8. `.get` (not `dict.get(k, d)`)
            # is what makes an ABSENT key different from a present, falsy one.
            configured = self.config.get("multi_process")
            multi_process = (bool(configured) if configured is not None
                             else _default_multi_process(self.arch))
            opened = {}
            self._vdevice = _open_vdevice(
                self.config.get("hailo_group"), multi_process, opened)
            self.via_service = bool(opened.get("service"))
        self._model = self._vdevice.create_infer_model(hef_path)
        # [detector.hailo] batch = N, else by what the HEF is: a multi-context
        # model is fed 8 at a time (see DEFAULT_MULTI_CONTEXT_BATCH), a
        # single-context one 1 -- it gains nothing from a batch and would only
        # pay the latency. Decided BEFORE configure(); the size is fixed after.
        configured = self.config.get("batch")
        self.contexts = hef_contexts(hef_path)
        if configured is not None:
            try:
                self.batch = max(1, min(MAX_BATCH, int(configured)))
            except (TypeError, ValueError):
                self.batch = 1
        else:
            self.batch = _auto_batch(self.contexts,
                                     self.config.get("_cameras"))
        self._model.set_batch_size(self.batch)

        # HailoRT 4.17+/5.x: the buffer FORMATS must be declared before
        # configure(), and an NMS-on-chip output takes its score/IoU thresholds
        # here. Skipping this is why run() later fails with "output not
        # configured as a view" -- the buffer has no declared type to write into.
        self._model.input().set_format_type(FormatType.UINT8)
        self._output_names = [o.name for o in self._model.outputs]
        for name in self._output_names:
            out = self._model.output(name)
            out.set_format_type(FormatType.FLOAT32)
            try:
                out.set_nms_score_threshold(self.min_confidence)
                out.set_nms_iou_threshold(0.45)
            except Exception:                                  # noqa: BLE001
                pass                                           # not an NMS output

        self._infer = self._model.configure()
        shape = self._model.input().shape                      # (h, w, c)
        self.input_size = (int(shape[1]), int(shape[0]))
        # How many inferences the pipeline will hold at once. This is what
        # decides whether detect_many overlaps anything: a depth of 1 makes
        # the async path behave exactly like the synchronous one.
        try:
            self.async_depth = max(1, int(self._infer.get_async_queue_size()))
        except Exception:                                      # noqa: BLE001
            self.async_depth = 1
        # [detector.hailo] async = true opts INTO the pipelined path; the
        # default is the synchronous run(). MEASURED on a Hailo-8 (HailoRT
        # 4.23, yolov8m, exclusive, apps stopped): sync 14.7 frames/s at
        # 67.8 ms a run, async with 2 in flight 14.7, with 4 in flight 14.8 --
        # the queue was 8 deep and it changed nothing, because this chip is
        # simply saturated at that model. Through the service the async path
        # also raised transient HAILO_RPC_FAILED(77) and cost ~25% more CPU
        # for the same rate. So it is here for a runtime or a chip where it
        # helps, and off until someone measures that it does.
        self._async = self.config.get("async", False) is True
        # [detector.hailo] priority = <int>: when the chip is SHARED through
        # the service, the scheduler favours the higher number. A production
        # system next to a test system should not be sharing 50/50.
        prio = self.config.get("priority")
        if prio is not None:
            try:
                self._infer.set_scheduler_priority(int(prio))
            except Exception as exc:                           # noqa: BLE001
                logger.warning("hailo: scheduler priority %r not applied: %s",
                               prio, exc)
        logger.info("[HAILO] %s on %s, input %s, queue %d, %s",
                    os.path.basename(hef_path), self.arch, self.input_size,
                    self.async_depth, "async" if self._async else "sync")
        # Said in the app log like everything else that decides a rate: this
        # is the line that explains why a model runs at 32 and not 15.
        logger.info("hailo: %s -- %s, batch %d",
                    os.path.basename(hef_path),
                    ("%d contexts" % self.contexts if self.contexts > 1
                     else "single context" if self.contexts == 1
                     else "contexts unknown"),
                    self.batch)

    def detect(self, frame):
        """Return [{label, confidence, box}] in FRAME pixel coordinates."""
        return self.detect_many([frame])[0]

    def detect_many(self, frames):
        """Several frames, ALL in flight before any is waited for.

        Measured on a Hailo-8 with yolov8m: one inference at a time -- submit,
        wait ~65 ms, parse, next camera -- caps a two-camera system at ~7 fps
        per camera while the chip could do four times that. Its DMA-in,
        compute and DMA-out only overlap when more than one frame is queued,
        and a synchronous run() never queues more than one.

        So: prepare and submit every frame first (wait_for_async_ready gates
        each on the pipeline's queue), then wait for each job in order and
        parse. A None frame answers [] in its slot, so callers index results
        by camera without counting. Every buffer a job reads or writes is held
        in `pending` until that job is waited on -- HailoRT DMAs straight
        into them, and a buffer garbage-collected mid-flight is a corrupted
        frame or worse.

        [detector.hailo] async = false takes the synchronous path, one frame
        at a time, exactly as before.
        """
        if self._infer is None:
            return [[] for _ in frames]
        out = [[] for _ in frames]
        real = [(i, f) for i, f in enumerate(frames)
                if f is not None and hasattr(f, "shape")]
        if not real:
            return out
        size = max(1, int(getattr(self, "batch", 1) or 1))
        # A configured batch is EXACTLY how many bindings a run() takes. A
        # chunk short of it is padded with its own last frame -- inferred and
        # discarded, cheaper than a run that the runtime refuses. Chunks are
        # walked in order so results land on their frames.
        # A batched run waits ~size frames on the chip: the timeout scales
        # with it, or a legitimately long batch reads as a hung chip.
        budget = 2000 + 500 * size
        # Two phases, so the async path keeps its contract: every chunk is on
        # the chip before the first is waited for. The sync path runs each
        # chunk in place -- there is nothing to overlap.
        pending = []
        for start in range(0, len(real), size):
            chunk = real[start:start + size]
            preps = [self._prepare(f) for _i, f in chunk]
            pad = preps[-1]
            while len(preps) < size:
                preps.append(self._prepare(pad["frame"]))
            bindings = [p["bindings"] for p in preps]
            job = None
            if self._async:
                self._infer.wait_for_async_ready(timeout_ms=budget, frames_count=size)
                job = self._infer.run_async(bindings)
            else:
                self._infer.run(bindings, timeout=budget)
            pending.append((chunk, preps, job))
        for chunk, preps, job in pending:
            if job is not None:
                job.wait(budget)
            for (i, _f), prep in zip(chunk, preps):
                out[i] = self._parse(prep)
        return out

    def _prepare(self, frame):
        """Letterbox one frame and bind its buffers. Returns everything
        _parse needs to put the boxes back into FRAME pixel coordinates.

        The model sees a letterboxed copy; boxes come back normalised, so they
        are scaled to the original frame again in _parse. Returning
        model-space boxes is the classic bug that puts every drawn rectangle
        in the wrong place.
        """
        import numpy as np                                     # noqa: PLC0415

        height, width = frame.shape[:2]
        target_w, target_h = self.input_size
        # The letterbox pads right/bottom only (see _letterbox): the image sits
        # at [0..w*scale, 0..h*scale] of the model input, so one scale undoes it.
        scale = min(target_w / width, target_h / height) or 1.0
        resized = np.ascontiguousarray(self._letterbox(frame, self.input_size))
        bindings = self._infer.create_bindings()
        bindings.input().set_buffer(resized)

        # Allocate an output buffer per output and bind it BEFORE the run:
        # HailoRT writes results into these. Without a bound buffer the call
        # fails with "output not configured as a view" -- the exact error a
        # device on 5.3.0 raises otherwise.
        out_buffers = {}
        for name in self._output_names:
            buf = np.empty(self._model.output(name).shape, dtype=np.float32)
            bindings.output(name).set_buffer(buf)
            out_buffers[name] = buf
        # `input` is kept on purpose: nothing else references the letterboxed
        # array once set_buffer returns, and the chip reads it asynchronously.
        return {"bindings": bindings, "input": resized, "out": out_buffers,
                "frame": frame,
                "scale": scale, "width": width, "height": height,
                "target_w": target_w, "target_h": target_h}

    def _parse(self, prep):
        """The finished job's output, as detections in frame pixels."""
        import numpy as np                                     # noqa: PLC0415
        width, height = prep["width"], prep["height"]
        target_w, target_h, scale = prep["target_w"], prep["target_h"], prep["scale"]
        out_buffers = prep["out"]

        # HailoRT on-chip NMS returns a FLAT, ragged float32 buffer, not a fixed
        # (classes, boxes, 5) grid: for each class in turn it is [count, then
        # count * (ymin, xmin, ymax, xmax, score)]. Parsing it as a grid yields
        # scalars, not rows ("numpy.float32 object is not iterable"). Coordinates
        # are normalised to the letterboxed input; unletterbox to frame pixels.
        flat = np.ascontiguousarray(
            out_buffers[self._output_names[0]], dtype=np.float32).ravel()
        num_classes = max(len(self.labels), 1)
        out = []
        idx = 0
        total = flat.size
        for class_id in range(num_classes):
            if idx >= total:
                break
            count = int(flat[idx])
            idx += 1
            label = self.labels[class_id] if class_id < len(self.labels) else str(class_id)
            keep_label = not self.classes or label in self.classes
            for _ in range(count):
                if idx + 5 > total:
                    break
                ymin, xmin, ymax, xmax, score = (float(v) for v in flat[idx:idx + 5])
                idx += 5
                if score < self.min_confidence or not keep_label:
                    continue
                x1 = min(width, max(0, (xmin * target_w) / scale))
                y1 = min(height, max(0, (ymin * target_h) / scale))
                x2 = min(width, max(0, (xmax * target_w) / scale))
                y2 = min(height, max(0, (ymax * target_h) / scale))
                out.append({"label": label, "confidence": round(score, 2),
                            "box": [int(x1), int(y1), int(x2), int(y2)]})
        return out

    @staticmethod
    def _letterbox(frame, size):
        """Resize preserving aspect ratio, padding the remainder."""
        import cv2                                             # noqa: PLC0415
        import numpy as np                                     # noqa: PLC0415

        target_w, target_h = size
        h, w = frame.shape[:2]
        scale = min(target_w / w, target_h / h)
        new_w, new_h = int(w * scale), int(h * scale)
        canvas = np.full((target_h, target_w, 3), 114, dtype=np.uint8)
        canvas[:new_h, :new_w] = cv2.resize(frame, (new_w, new_h))
        return canvas

    def close(self):
        # Release this model's infer handle always; release the VDevice only if
        # we own it (a shared one is released by its owner).
        objs = [self._infer] + ([self._vdevice] if self._owns_vd else [])
        for obj in objs:
            try:
                if obj is not None and hasattr(obj, "release"):
                    obj.release()
            except Exception:                                  # noqa: BLE001
                pass
        self._infer = None
        if self._owns_vd:
            self._vdevice = None


def _device_holders():
    """Which processes currently have /dev/hailo* open, as
    [(pid, name)] -- the system slug where it can be worked out from the
    process's working directory, else the command.

    Read from /proc rather than asked of HailoRT, because the whole point is
    to answer when HailoRT has just refused to talk to us. Best effort: on a
    box where /proc cannot be read the list is simply empty, and the caller
    still says something true, just less specific.
    """
    out = []
    for pid in os.listdir("/proc") if os.path.isdir("/proc") else []:
        if not pid.isdigit():
            continue
        try:
            fds = os.listdir("/proc/%s/fd" % pid)
        except OSError:
            continue                        # gone, or not ours to look at
        holds = False
        for fd in fds:
            try:
                if os.readlink("/proc/%s/fd/%s" % (pid, fd)).startswith("/dev/hailo"):
                    holds = True
                    break
            except OSError:
                continue
        if not holds:
            continue
        name = ""
        try:
            # A detection app runs with its system package as the working
            # directory, so the folder name IS the system slug -- the name the
            # operator knows it by, which "pid 43007" is not.
            name = os.path.basename(os.readlink("/proc/%s/cwd" % pid))
        except OSError:
            pass
        if not name:
            try:
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    name = f.read().replace(b"\x00", b" ").decode(
                        errors="replace").strip()[:60]
            except OSError:
                pass
        out.append((int(pid), name or "?"))
    return out


def _busy_message(exc):
    """A readable reason for HAILO_OUT_OF_PHYSICAL_DEVICES, or None.

    Error 74 means every accelerator is already claimed. On a Hailo-8 that is
    ONE device, so it means "something else is using the chip" -- and the
    message HailoRT gives says nothing about what, which leaves an operator
    with an error code and no next step.
    """
    if "74" not in str(exc) and "OUT_OF_PHYSICAL_DEVICES" not in str(exc):
        return None
    mine = os.getpid()
    others = [(pid, name) for pid, name in _device_holders() if pid != mine]
    who = ", ".join("%s (pid %d)" % (name, pid) for pid, name in others)
    return ("the AI accelerator is already in use"
            + (" by %s" % who if who else " by another system on this box")
            + ". Without sharing, one accelerator runs one system at a time. "
              "To run both on this chip, set [detector.hailo].multi_process = "
              "true on EVERY system here that uses it - all of them or none, "
              "because a system holding the accelerator exclusively stops the "
              "HailoRT service serving the others. Otherwise stop the other "
              "system, or fit a second accelerator.")


def _vdevice_params(group=None, service=False):
    from hailo_platform import VDevice, HailoSchedulingAlgorithm  # noqa: PLC0415
    params = VDevice.create_params()
    params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN
    params.group_id = group or "SHARED"
    # `service` routes this VDevice through hailort.service, which is the only
    # way one accelerator can serve SEVERAL PROCESSES -- group_id above shares
    # it between models within one process, and two detection apps are two
    # processes.
    #
    # ALL OR NOTHING. Measured on HailoRT 4.23 (Hailo-8, CM5):
    #
    #   both apps through the service   -- both get a detector and share the
    #                                      chip; the scheduler time-slices them
    #                                      (7.4 and 3.7 fps on one Hailo-8)
    #   one exclusive + one service     -- the service can never obtain a
    #                                      device somebody already owns: it
    #                                      goes down with SIGSEGV and the
    #                                      client dies of SIGBUS as its shared
    #                                      memory is pulled away
    #
    # That second row is why this is off by default rather than on: SIGBUS is a
    # signal, not an exception, so nothing in this file can rescue an app that
    # meets it, and a box with a mix of configured and unconfigured systems is
    # exactly the mix that produces it. Turn it on for EVERY system on the box
    # that uses the accelerator, or for none:
    #
    #     [detector.hailo]
    #     multi_process = true
    if service and hasattr(params, "multi_process_service"):
        params.multi_process_service = True
    return params


def hef_contexts(hef_path):
    """How many contexts a HEF was compiled into, or 0 when it cannot be told.

    A single-context model lives on the chip whole; a multi-context one has
    part of its weights streamed from host memory for EVERY frame, which is
    why yolov8m runs at a seventh of yolov8s's rate on a Hailo-8 and why
    batching it pays (the stream happens once per batch). hailortcli parses
    the file without touching the device.
    """
    try:
        out = _run(["hailortcli", "parse-hef", str(hef_path)], timeout=20)
    except Exception:                                          # noqa: BLE001
        return 0
    m = re.search(r"Number of contexts:\s*(\d+)", out or "")
    if m:
        return int(m.group(1))
    return 1 if "Single Context" in (out or "") else 0


#: The batch a multi-context model defaults to. Measured on a Hailo-8 at
#: PCIe Gen 2 with yolov8m: 14.7 frames/s at 1, 23.0 at 4, 31.7 at 8 through
#: this API. Eight is where the curve flattens; the price is eight frames of
#: latency before the boxes appear, which on two 25 fps cameras is ~160 ms.
DEFAULT_MULTI_CONTEXT_BATCH = 8
MAX_BATCH = 16


def _auto_batch(contexts, cameras=None):
    """How many frames to take at once, when nobody has said.

    A single-context HEF gains nothing from a batch and would only pay the
    latency, so it takes one. A multi-context one is rewarded for batching
    -- on Hailo-10H, yolov8s measured 49.8 frames/s one at a time against
    134 at eight -- so it takes DEFAULT_MULTI_CONTEXT_BATCH.

    But never MORE FRAMES THAN THERE ARE CAMERAS. A batch wider than the
    cameras cannot be filled from one pass, so the loop waits for each
    camera to produce another frame before it infers anything: with three
    cameras and a batch of eight it collected three rounds, and py-spy put
    20% of the whole loop inside that wait. Everything downstream --
    zones, counters, the drawn overlay -- then arrives in bursts a batch
    apart rather than as each camera is read. The frames that were missing
    from the batch were padded with a copy of the last one anyway
    (detect_many), so the chip was doing eight inferences to answer three.

    `cameras` unknown (None: a caller that has not said, a probe, a test)
    keeps the old full batch -- the number is a bound, not a guess.
    """
    if contexts <= 1:
        return 1
    try:
        n = int(cameras)
    except (TypeError, ValueError):
        return DEFAULT_MULTI_CONTEXT_BATCH
    if n < 1:
        return DEFAULT_MULTI_CONTEXT_BATCH
    return max(1, min(DEFAULT_MULTI_CONTEXT_BATCH, n))


def _default_multi_process(arch):
    """Share the accelerator when nobody said, on the hardware where that is
    the useful default.

    Hailo-8 and 8L are the one-chip platform this product runs several
    systems on -- see MAX_RUNNING_APPS and _busy_message above, both written
    for exactly that shape. A system opening the chip EXCLUSIVELY by default
    means the second one on the box refuses with error 74 until an operator
    finds this one setting; sharing by default means it works the first time,
    and an operator who wants exclusive access can still ask for it.

    Hailo-10H is left OFF. Different hardware, and turning its systems into
    the shared service by default is not this function's call to make --
    unlike Hailo-8/8L, nothing here has established that it should be.
    """
    return arch in ("hailo8", "hailo8l")


#: When the HailoRT service will not serve a multi_process client. It
#: segfaults now and then (14 times since boot on .164, 2026-10-11) and systemd
#: has it back within seconds -- but an app that opened the chip EXCLUSIVELY
#: during those seconds locked every other system on the box out
#: (HAILO_OUT_OF_PHYSICAL_DEVICES) until somebody restarted them all, which is
#: the all-or-nothing rule in _vdevice_params broken by the fallback itself.
#:
#: So: a few quick tries in the call (SERVICE_RETRY_WAITS_S, ~6 s, short
#: enough that an app's startup is not held up), and while the first failure
#: is younger than SERVICE_GRACE_S the open RAISES, so run.py's detector retry
#: ladder (8, 16, 32 s...) tries again later instead of anything going
#: exclusive. Only past the grace, with no other system on this box a service
#: client, does today's exclusive fallback happen -- and never while one is.
SERVICE_RETRY_WAITS_S = (1.0, 2.0, 3.0)
SERVICE_GRACE_S = 45.0
#: The unit systemd restarts the service as.
SERVICE_UNIT = "hailort.service"
#: Monotonic time of this process's first service failure since the service
#: last served it; None while it serves. Reset by execv, which is right: a
#: re-executed app starts a fresh grace.
_service_failed_at = None
#: Seams for the smoke (tests/smoke_hailoservice.py): no real waiting there.
_sleep = time.sleep
_now = time.monotonic


def _service_active():
    """True / False from systemd, None when it cannot be asked (no systemctl,
    not Linux). "activating" is not active yet."""
    out = _run(["systemctl", "is-active", SERVICE_UNIT], timeout=3)
    if not out:
        return None
    return out.strip() == "active"


#: The service-client markers live in a PER-UID directory under the temp dir
#: that only this account may own (Pipi S1-L1, 2026-10-11). A fixed shared
#: name in world-writable /tmp let a second local account pre-create it:
#: plant symlinks the app's open() followed (clobbering e.g. users.local.toml
#: as the service account), plant a marker for a live pid (never exclusive),
#: or make it unwritable (all-or-nothing defeated). Every app runs as the one
#: service user, so per-uid is enough. Not $XDG_RUNTIME_DIR: it comes and goes
#: with login sessions, and two apps disagreeing on the place is worse.
#: Seams for the smoke: the uid, and the stat calls the checks read.
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_lstat = os.lstat
_fstat = os.fstat
_marker_warned = []


def _uid():
    """This process's effective uid, None where there is none (Windows)."""
    return os.geteuid() if hasattr(os, "geteuid") else None


def _marker_dir():
    uid = _uid()
    name = "iriv-hailo-service" if uid is None else "iriv-hailo-service-%d" % uid
    return os.path.join(tempfile.gettempdir(), name)


def _trusted_dir(path):
    """True when `path` is a real directory (not a symlink) owned by this uid
    that nobody else can write (0700 / 0755). Without uids, any directory."""
    try:
        st = _lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False
    uid = _uid()
    return uid is None or (st.st_uid == uid and not st.st_mode & 0o022)


def _distrust(path):
    """Log once that the marker dir is not ours to trust."""
    if not _marker_warned:
        _marker_warned.append(path)
        logger.warning("hailo: %s is not a private directory of this account "
                       "(symlink, other owner or writable by others); not "
                       "trusting its service-client markers -- remove it",
                       path)


def _start_time(pid):
    """The kernel's start time of `pid` (/proc/<pid>/stat field 22), or "".
    Kept in the marker so a recycled pid is not taken for a service client;
    execv keeps it, so a re-executed app still matches its own marker."""
    try:
        with open("/proc/%d/stat" % int(pid), "rb") as f:
            text = f.read().decode(errors="replace")
        return text.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError, ValueError):
        return ""


def _mark_service_client(on):
    """Say (or unsay) on this box that this process is a service client, so
    another app deciding whether it may go exclusive can see it. Best effort:
    a box where it cannot be written behaves as before. Only ever inside a
    directory _trusted_dir accepts; the marker is unlinked first and created
    O_EXCL|O_NOFOLLOW, so a planted symlink is removed, never followed."""
    folder = _marker_dir()
    path = os.path.join(folder, str(os.getpid()))
    try:
        if not on:
            if _trusted_dir(folder):
                os.remove(path)
            return
        try:
            os.mkdir(folder, 0o700)
        except FileExistsError:
            pass
        if not _trusted_dir(folder):
            _distrust(folder)
            return
        try:
            os.remove(path)                     # a stale one, or a planted link
        except FileNotFoundError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW,
                     0o644)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            uid = _uid()
            if uid is not None and _fstat(f.fileno()).st_uid != uid:
                raise OSError("marker not owned by this account")
            f.write(_start_time(os.getpid()))
    except OSError:
        pass


def _other_service_clients():
    """Pids of OTHER live processes that marked themselves service clients.
    Empty where /proc does not exist (the marker means nothing there).

    None when the marker directory exists but is not this account's own: then
    nobody can say whether a peer is a client, and the caller must not go
    exclusive (that lock-out is what the markers exist to prevent). A marker
    that is a symlink, not a regular file, or owned by another uid is not
    counted."""
    if not os.path.isdir("/proc"):
        return []
    folder = _marker_dir()
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    if not _trusted_dir(folder):
        _distrust(folder)
        return None
    uid = _uid()
    for name in names:
        if not name.isdigit() or int(name) == os.getpid():
            continue
        if os.path.islink(os.path.join(folder, name)):
            continue                     # O_NOFOLLOW says the same on Linux
        try:
            fd = os.open(os.path.join(folder, name),
                         os.O_RDONLY | _O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
        except OSError:
            continue
        with os.fdopen(fd, "r", encoding="ascii", errors="replace") as f:
            st = _fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or (uid is not None
                                                and st.st_uid != uid):
                continue
            started = f.read(64).strip()
        now = _start_time(name)
        if now and now == started:
            out.append(int(name))
    return sorted(out)


def _service_vdevice(group):
    """A service VDevice after up to len(SERVICE_RETRY_WAITS_S) + 1 tries,
    each failure logged; (vdevice or None, the last error)."""
    from hailo_platform import VDevice                        # noqa: PLC0415
    last = None
    for attempt, wait in enumerate((0.0,) + tuple(SERVICE_RETRY_WAITS_S)):
        if wait:
            _sleep(wait)
            if _service_active() is False:
                logger.warning("hailo: %s is not active yet (try %d); waiting",
                               SERVICE_UNIT, attempt + 1)
                continue
        try:
            return VDevice(_vdevice_params(group, service=True)), None
        except Exception as exc:                              # noqa: BLE001
            last = exc
            logger.warning("hailo: the HailoRT service would not serve this "
                           "client (try %d): %s", attempt + 1, str(exc)[:120])
    return None, last


def _open_vdevice(group=None, multi_process=False, opened=None):
    """A VDevice for this process, shared across processes only when asked.

    Raises a RuntimeError naming the other system when the chip is already
    claimed, rather than passing HailoRT's error 74 up untranslated -- an
    error code with no next step is what an operator was left with before.
    `opened`, a dict when given, is told {"service": True} when the VDevice
    really is a hailort_service client (the exclusive fallback says False).
    """
    global _service_failed_at
    from hailo_platform import VDevice                        # noqa: PLC0415
    if opened is not None:
        opened["service"] = False
    if multi_process:
        vd, exc = _service_vdevice(group)
        if vd is not None:
            _service_failed_at = None
            _mark_service_client(True)
            if opened is not None:
                opened["service"] = True
            return vd
        now = _now()
        if _service_failed_at is None:
            _service_failed_at = now
        waited = now - _service_failed_at
        reason = str(exc)[:120]
        peers = _other_service_clients()
        if peers is None:
            raise RuntimeError(
                "the HailoRT service would not serve this system (%s) and "
                "the service-client markers in %s cannot be trusted; not "
                "taking the accelerator exclusively, retrying the service"
                % (reason, _marker_dir())) from exc
        if peers:
            # Never exclusive while another system here is a service client:
            # that is the mix that crashes the service and SIGBUSes them.
            raise RuntimeError(
                "the HailoRT service would not serve this system (%s) and "
                "other systems here use it (pid %s); not taking the "
                "accelerator exclusively, retrying the service"
                % (reason, ", ".join(str(p) for p in peers))) from exc
        if waited < SERVICE_GRACE_S:
            raise RuntimeError(
                "the HailoRT service would not serve this system (%s); "
                "retrying it for up to %.0f s more before opening the "
                "accelerator exclusively"
                % (reason, SERVICE_GRACE_S - waited)) from exc
        logger.warning("hailo: multi_process asked for but the HailoRT "
                       "service has not served this system for %.0f s (%s) "
                       "and no other system here is its client; opening the "
                       "accelerator exclusively", waited, reason)
        _mark_service_client(False)
    try:
        return VDevice(_vdevice_params(group, service=False))
    except Exception as exc:                                  # noqa: BLE001
        busy = _busy_message(exc)
        if busy:
            raise RuntimeError(busy) from exc
        raise


def _shared_vdevice():
    """One scheduled VDevice for several models to share in this process."""
    return _open_vdevice()


class MultiHailoDetector:
    """Run SEVERAL HEFs on ONE camera and ONE shared VDevice, in one process.

    This is how multiple models run on a single camera and accelerator reliably:
    the camera is read once (by the loop), each model runs on the frame, and the
    HailoRT scheduler time-slices them on the chip. Every detection is tagged
    with the model that produced it, so analytics can split results by model.

    Config (systems/<slug>/config.toml):

        [detector]
        kind = "hailo"
        models_dir = "..."         # shared by all entries unless overridden
        labels = "models/coco.txt"

        [[detector.models]]
        model = "yolov8n"
        classes = ["person"]
        confidence = 0.45

        [[detector.models]]
        model = "yolov8s"
        classes = ["car", "truck"]
    """

    name = "hailo-multi"

    def __init__(self, config=None):
        self.config = dict(config or {})
        specs = self.config.get("models") or []
        if not specs:
            raise RuntimeError("detector.models is empty")
        self._vdevice = _shared_vdevice()
        self._detectors = []
        for spec in specs:
            sub = dict(self.config)
            sub.pop("models", None)
            sub.update(spec)                                   # per-model model/classes/confidence
            det = HailoDetector(sub, vdevice=self._vdevice)
            self._detectors.append((spec.get("model") or "model", det))

    def detect(self, frame):
        out = []
        for model_name, det in self._detectors:
            for d in det.detect(frame):
                d["model"] = model_name                        # tag by producing model
                out.append(d)
        return out

    def close(self):
        for _name, det in self._detectors:
            try:
                det.close()
            except Exception:                                  # noqa: BLE001
                pass
        try:
            if self._vdevice is not None and hasattr(self._vdevice, "release"):
                self._vdevice.release()
        except Exception:                                      # noqa: BLE001
            pass
        self._vdevice = None
