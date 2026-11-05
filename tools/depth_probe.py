# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
depth_probe.py -- Read one depth frame from the Orbbec Gemini and say what it
saw. Proves the camera works, and which of the two ways in is live right now.

The camera on .63 is a Gemini 336: ACTIVE STEREO, not time-of-flight. Two IR
cameras 50mm apart with an IR projector throwing a pattern, so a blank wall
still has something to match on, and depth comes from the disparity between
the two eyes. Range 0.1m to 20m+, best between 0.26m and 3m. Values are
MILLIMETRES; 0 means no reading -- too close, too far, too dark, or hidden
from one of the two eyes.

TWO WAYS IN, AND THEY ARE MUTUALLY EXCLUSIVE:

  --v4l2   The camera is a plain UVC device: one node hands out 16-bit depth
           (Z16) with no SDK at all. This is the path the existing capture
           code could take, since camera.py already opens V4L2 nodes and gst
           pipelines.

  --sdk    pyorbbecsdk2 (module `pyorbbecsdk`, installed at ~/orbbec/venv on
           .63). Needed for the depth SCALE, alignment to the colour camera,
           point clouds, the IMU, per-sensor controls.

Running --sdk RECONFIGURES the device: the Z16 node disappears until the USB
device is reset (--reset does it, or unplug it). Verified on .63. So pick one
path per deployment rather than mixing them.

Node numbers also move -- the Orbbec took video2..6, then video2..5 after the
SDK touched it -- so nothing here hard-codes a number: the depth node is the
one that offers Z16 behind an Orbbec by-id link.

Run on the box (192.168.1.63):
    python3 tools/depth_probe.py                  # V4L2, the default
    sudo python3 tools/depth_probe.py --reset     # after the SDK has been used
    ~/orbbec/venv/bin/python tools/depth_probe.py --sdk
"""

import argparse
import array
import fcntl
import glob
import os
import re
import subprocess
import tempfile
import time

ORBBEC_VID = "2bc5"
NO_READING = 0
#: Saturation, not a 65-metre wall. The spec tops out at 20m+, and a frame of
#: an ordinary room comes back with a scatter of 0xFFFF where the stereo
#: match failed outright. Counting those as distances put "max 65535mm" on
#: every reading this tool took.
UNMEASURED = 0xFFFF
#: v4l2_fourcc('Z','1','6',' ') -- the trailing space is part of it, and is
#: why the string form has to be quoted everywhere it is typed.
Z16_FOURCC = "Z16 "


def run(cmd, timeout=30):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def depth_node():
    """The node that offers Z16. Found by asking, never by number.

    Not by-id either, tempting as those links look: after a USB reset this box
    had /dev/v4l/by-id/*Orbbec*-index0 pointing at video9, which enumerates no
    formats at all, while the live depth node was video2. The links survive
    the re-enumeration; what is behind them does not.

    So: ask every node what it offers, and take the one that says Z16. There
    is exactly one on a box with one depth camera, and the question costs a
    few milliseconds.
    """
    nodes = sorted(glob.glob("/dev/video*"),
                   key=lambda p: int(re.sub(r"\D", "", p) or 0))
    for node in nodes:
        if "Z16" in run(["v4l2-ctl", "-d", node, "--list-formats"]).stdout:
            return node
    return None


def usb_reset():
    """Put the device back in its default UVC mode, where depth is a node.

    The SDK leaves it configured otherwise, and then nothing in /dev has Z16
    on it. A reset is what an unplug would do, without the walk to the box.
    """
    usbdevfs_reset = ord("U") << 8 | 20
    for vid in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        if open(vid).read().strip() != ORBBEC_VID:
            continue
        base = os.path.dirname(vid)
        bus = int(open(os.path.join(base, "busnum")).read())
        dev = int(open(os.path.join(base, "devnum")).read())
        path = "/dev/bus/usb/%03d/%03d" % (bus, dev)
        fd = os.open(path, os.O_WRONLY)
        try:
            fcntl.ioctl(fd, usbdevfs_reset, 0)
        finally:
            os.close(fd)
        return path
    return None


def stats(values, scale=1.0):
    """What a depth frame amounts to, in one line.

    The median rather than the mean: one window at the end of a room drags a
    mean to nonsense, and the question here is "what distance is this camera
    mostly looking at".
    """
    seen = sorted(v * scale for v in values
                  if v != NO_READING and v != UNMEASURED)
    if not seen:
        return "no valid pixels -- lens covered, or nothing in range"
    return ("valid {:.1f}%  min {:.0f}mm  median {:.0f}mm  max {:.0f}mm"
            .format(100.0 * len(seen) / len(values), seen[0],
                    seen[len(seen) // 2], seen[-1]))


def negotiated(node):
    """What the driver actually settled on. Asking for a size is not getting
    it, and a frame read at the wrong size is silently half a picture."""
    out = run(["v4l2-ctl", "-d", node, "--get-fmt-video"]).stdout
    size = re.search(r"Width/Height\s*:\s*(\d+)/(\d+)", out)
    fmt = re.search(r"Pixel Format\s*:\s*.(....).", out)
    if not size:
        raise SystemExit("could not read the format back from " + node)
    return int(size.group(1)), int(size.group(2)), (fmt.group(1) if fmt else "?")


def read_v4l2(node, width, height):
    """Frames through the kernel, no SDK. Z16 is little-endian uint16."""
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp:
        raw = tmp.name
    try:
        run(["v4l2-ctl", "-d", node,
             "--set-fmt-video=width={},height={},pixelformat={}".format(
                 width, height, Z16_FOURCC)])
        wide, high, fmt = negotiated(node)
        if fmt.strip() != "Z16":
            raise SystemExit(
                "{} is in {}, not Z16. The SDK reconfigures the device and the "
                "depth node goes with it -- run with --reset first."
                .format(node, fmt.strip()))
        # Three frames, and the LAST one: the first buffer after a format
        # change can come back short.
        run(["v4l2-ctl", "-d", node, "--stream-mmap", "--stream-count=3",
             "--stream-to=" + raw])
        buf = open(raw, "rb").read()
        size = wide * high * 2
        if len(buf) < size:
            raise SystemExit("short capture: {} bytes, wanted {} -- is "
                             "something else holding {}?"
                             .format(len(buf), size, node))
        frame = array.array("H")
        frame.frombytes(buf[-size:])
        return frame, wide, high
    finally:
        os.unlink(raw)


def report(where, values, wide, high, scale=1.0, extra=""):
    raw = values[(high // 2) * wide + wide // 2]
    print("{}  {}x{}{}".format(where, wide, high, extra))
    print("  " + stats(values, scale))
    print("  centre " + ("(no reading)" if raw in (NO_READING, UNMEASURED)
                         else "{:.0f}mm".format(raw * scale)))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sdk", action="store_true",
                        help="read through pyorbbecsdk")
    parser.add_argument("--v4l2", action="store_true",
                        help="read through the V4L2 node (the default)")
    parser.add_argument("--reset", action="store_true",
                        help="USB-reset the camera first (needs root); "
                             "restores the Z16 node after the SDK has run")
    parser.add_argument("--node", default="", help="depth node; found by default")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    args = parser.parse_args()
    if not (args.sdk or args.v4l2):
        args.v4l2 = True

    if args.reset:
        print("usb reset:", usb_reset() or "no Orbbec found")
        time.sleep(6)          # it re-enumerates; the nodes come back after

    if args.v4l2:
        node = args.node or depth_node()
        if not node:
            raise SystemExit("no Z16 node -- the camera is not in UVC depth "
                             "mode. Run with --reset (as root), or use --sdk.")
        frame, wide, high = read_v4l2(node, args.width, args.height)
        report("V4L2 " + node, frame, wide, high)

    if args.sdk:
        try:
            from pyorbbecsdk import Config, Context, OBSensorType, Pipeline
        except ImportError:
            raise SystemExit("pyorbbecsdk not installed -- use the venv that "
                             "has it:\n    ~/orbbec/venv/bin/python "
                             "tools/depth_probe.py --sdk")
        # The Context has to stay in a NAME. Written as
        # Context().query_devices().get_device_by_index(0) the context is a
        # temporary, freed the moment the expression moves on, and the device
        # list it handed back is left pointing at a device manager that no
        # longer exists: OBError, NULL pointer passed for argument "deviceMgr".
        context = Context()
        devices = context.query_devices()
        if devices.get_count() < 1:
            raise SystemExit("no Orbbec camera found -- check lsusb for 2bc5")
        info = devices.get_device_by_index(0).get_device_info()
        print("SDK  {}  sn {}  fw {}".format(info.get_name(),
                                             info.get_serial_number(),
                                             info.get_firmware_version()))
        pipe = Pipeline()
        profile = (pipe.get_stream_profile_list(OBSensorType.DEPTH_SENSOR)
                   .get_default_video_stream_profile())
        config = Config()
        config.enable_stream(profile)
        pipe.start(config)
        try:
            # The first frames after a start are often empty while the stream
            # settles; asking twenty times costs under a second.
            for _ in range(20):
                frames = pipe.wait_for_frames(1000)
                depth = frames.get_depth_frame() if frames else None
                if not depth:
                    continue
                pixels = array.array("H")
                pixels.frombytes(bytes(depth.get_data()))
                report("", pixels, depth.get_width(), depth.get_height(),
                       depth.get_depth_scale(),
                       "  @{}fps  scale {:.3f}mm/unit".format(
                           profile.get_fps(), depth.get_depth_scale()))
                break
            else:
                print("  no depth frame in 20 tries")
        finally:
            pipe.stop()


if __name__ == "__main__":
    main()
