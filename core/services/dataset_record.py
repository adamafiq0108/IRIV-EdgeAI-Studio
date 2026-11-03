# -*- coding: utf-8 -*-
"""
dataset_record.py -- the dataset CLIP recorder: the raw stream into an H.264
.mp4 under the system's dataset_rec folder, timed or START/STOP.

Split from dataset_capture.py (itself moved out of detector_control.py) so
neither file passes the line ceiling. The job table and the clip limits stay
in dataset_capture because dataset_list reports a running recording; this
module only imports from there, never the other way, so there is no cycle.
"""

import contextlib
import os
import shutil
import subprocess
import threading
import time

from core.services.dataset_capture import (
    MAX_DATASET_CLIP_S, MAX_DATASET_SESSION_S, NOT_RUNNING, _dataset_jobs,
    _safe_name, dataset_available, dataset_dir, open_raw, remember_camera,
    resolve_camera, tag_camera)


def _dataset_writer(cv2, path, fps, size):
    """(VideoWriter, wrote_h264) for a dataset clip.

    H.264, not MPEG-4 Part 2. `mp4v` produces a file named .mp4 that carries
    an mpeg4 stream: it downloads fine and then sits at 0:00 in Windows Media
    Player, QuickTime and every browser, which is what "the download is not
    really an mp4" means. avc1 is H.264 and plays everywhere. The fallback
    stays for a board whose OpenCV was built without an H.264 encoder -- there
    _to_h264 finishes the job, and if that is missing too an unplayable clip
    still beats losing the recording.
    """
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"avc1"), fps, size)
    if writer.isOpened():
        return writer, True
    writer.release()
    return cv2.VideoWriter(
        path, cv2.VideoWriter_fourcc(*"mp4v"), fps, size), False


def _to_h264(path):
    """Re-encode a clip in place to H.264 in an MP4 the players accept.

    Only reached when OpenCV had no H.264 encoder. ffmpeg is an install
    dependency, so this is the normal path on such a board rather than a
    long shot. `yuv420p` because 4:2:0 is what consumer players decode, and
    +faststart so the header is at the front and the file starts playing
    before it has finished downloading. A failure leaves the original: a
    clip that needs VLC beats no clip.
    """
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    tmp = path + ".h264.mp4"
    try:
        r = subprocess.run(
            [ff, "-y", "-loglevel", "error", "-i", path,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp],
            capture_output=True, timeout=600)
        if r.returncode == 0 and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    with contextlib.suppress(OSError):
        os.remove(tmp)
    return False


def dataset_record(systems_dir, slug, name, duration_s=None, camera=None):
    """Record the raw stream into the dataset clip folder, on a background
    thread. One recording per system at a time.

    TWO MODES, because a training set is not a fixed-length thing. A duration
    records exactly that many seconds and stops itself -- right when the
    subject is a conveyor cycle you can time. `duration_s` None (or 0) starts a
    START/STOP session instead: it runs until dataset_record_stop is called,
    which is what capturing "the parts coming through until I have enough"
    actually needs. A session is still capped (MAX_DATASET_SESSION_S) so a
    forgotten one cannot fill the drive.

    `camera` names which of the system's cameras to record (None: the first,
    as before). It is checked BEFORE the job starts, so a camera that is not
    running is an error the page shows now, not a failed clip found later --
    and the clip reads /raw?camera=<id> only, never the bare path.
    """
    if not dataset_available():
        raise ValueError("no NVMe storage mounted - dataset capture needs /mnt/nvme")
    job = _dataset_jobs.get(slug)
    if job and not job.get("done") and job.get("until", 0) > time.time():
        raise ValueError("a recording is already running for this system")
    manual = not duration_s
    if manual:
        duration = MAX_DATASET_SESSION_S
    else:
        try:
            duration = min(MAX_DATASET_CLIP_S,
                           max(1.0, float(duration_s)))
        except (TypeError, ValueError):
            duration = MAX_DATASET_CLIP_S
    cam = resolve_camera(systems_dir, slug, camera)
    from core.services.detector_control import stream_port  # noqa: PLC0415
    port = stream_port(systems_dir, slug)
    folder = dataset_dir("clip", slug)
    os.makedirs(folder, exist_ok=True)
    fname = f"{_safe_name(name)}_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    path = os.path.join(folder, fname)
    now = time.time()
    job = {"until": now + duration + 5, "file": fname,
           "error": None, "done": False, "stop": False, "manual": manual,
           "started_at": now, "frames": 0,
           "camera": cam["id"] if cam else None,
           "camera_name": cam["name"] if cam else None}
    _dataset_jobs[slug] = job

    def _work():
        try:
            import cv2
            import numpy as np
            req = open_raw(port, cam["id"] if cam else None, timeout=8)
            writer = None
            h264 = False
            buf = b""
            end = time.time() + duration
            try:
                while time.time() < end and not job["stop"]:
                    chunk = req.read(8192)
                    if not chunk:
                        break
                    buf += chunk
                    while True:
                        s = buf.find(b"\xff\xd8")
                        e = buf.find(b"\xff\xd9", s + 2) if s != -1 else -1
                        if s == -1 or e == -1:
                            break
                        frame = cv2.imdecode(
                            np.frombuffer(buf[s:e + 2], np.uint8), cv2.IMREAD_COLOR)
                        buf = buf[e + 2:]
                        if frame is None:
                            continue
                        if writer is None:
                            h, w = frame.shape[:2]
                            writer, h264 = _dataset_writer(cv2, path, 10, (w, h))
                        writer.write(frame)
                        job["frames"] += 1
                        # Checked per FRAME, not per read: Stop has to close
                        # the clip now, not after the next 8KB happens to
                        # arrive from a camera that may have gone quiet.
                        if job["stop"]:
                            break
            finally:
                req.close()
                if writer is not None:
                    writer.release()
            if writer is None:
                job["error"] = (NOT_RUNNING if cam else
                                "no frames arrived - is detection running?")
            elif not h264:
                # Written by the fallback codec, so it is an .mp4 that most
                # players refuse. Re-encode before anybody downloads it.
                _to_h264(path)
        except Exception as exc:            # noqa: BLE001 -- report, never crash
            job["error"] = str(exc)
        finally:
            job["done"] = True

    threading.Thread(target=_work, daemon=True).start()
    out = {"file": fname, "duration_s": None if manual else duration,
           "manual": manual}
    if cam:
        # Tagged now, not when the clip closes: the page lists the file while
        # it is still being written, and it should say which camera already.
        tag_camera("clip", slug, fname, cam)
        remember_camera(systems_dir, slug, cam["id"])
        out.update(camera=cam["id"], camera_name=cam["name"])
    return out


def dataset_record_stop(slug):
    """Stop the running START/STOP recording and keep what it captured.

    Not a cancel: the clip is closed and kept, because the frames up to now are
    exactly the training data the operator was collecting. Stopping something
    that is not running is not an error -- the page may have polled a stale
    state, and refusing would be noise rather than information.
    """
    job = _dataset_jobs.get(slug)
    if not job or job.get("done"):
        return {"stopped": False, "file": (job or {}).get("file")}
    job["stop"] = True
    return {"stopped": True, "file": job.get("file"),
            "frames": job.get("frames", 0)}
