# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
The demo system's detection app.

This is the half that runs ON THE DEVICE, next to the camera. It is a complete,
dependency-light reference: capture -> detect -> decide -> store, plus an MJPEG
stream. Copy this folder to start a real system.

    app/config.py     load config.toml (+ gitignored config.local.toml)
    app/store.py      durable events + snapshots; the cursor the contract needs
    app/detector.py   Detector interface, registry, MockDetector
    app/camera.py     frame capture, JPEG encoding, synthetic fallback
    app/runtime.py    pid/state file; start/stop the app as a process
    app/run.py        the loop, and the MJPEG server

Nothing here imports the dashboard. The dashboard reaches it only through
`systems/demo/adapter.py`, which is a read-only window (docs/contract-v1.md).
That direction is the whole architecture: the app never knows a master exists,
so a master outage cannot stop detection.
"""
