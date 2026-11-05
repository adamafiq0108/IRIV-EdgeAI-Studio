#!/usr/bin/env bash
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
#
# iriv-ctl -- stop, disable, enable or check IRIV EdgeAI Studio on this box.
#
#   sudo iriv-ctl disable   stop everything now and keep it from starting at boot
#   sudo iriv-ctl enable    start at boot again, and start now
#   sudo iriv-ctl stop      stop now (it starts again at the next boot)
#   sudo iriv-ctl start     start now
#   iriv-ctl status         what is running
#   iriv-ctl about          product, version and developer
#
# Only switches the services. Nothing is deleted: systems, data, licenses and
# the installed software stay as they are, so `enable` brings the box back
# exactly as it was.

set -uo pipefail

# The hub, one master per system, the video gateway and the Modbus redirect.
# Masters are templated units (irivai-master@<system>), found by name.
fixed=(irivai-hub.service irivai-mediamtx.service irivai-modbus-redirect.service)

masters() {
    systemctl list-units --all --no-legend --plain 'irivai-master@*.service' 2>/dev/null | awk '{print $1}'
}

need_root() {
    [[ $EUID -eq 0 ]] || { echo "run with sudo: sudo iriv-ctl $1" >&2; exit 1; }
}

case "${1:-}" in
    disable)
        need_root disable
        systemctl disable --now "${fixed[@]}" 2>/dev/null
        for m in $(masters); do systemctl disable --now "$m" 2>/dev/null; done
        echo "IRIV EdgeAI Studio is stopped and will not start at boot. Re-enable with: sudo iriv-ctl enable"
        ;;
    enable)
        need_root enable
        systemctl enable --now "${fixed[@]}" 2>/dev/null
        for m in $(masters); do systemctl enable --now "$m" 2>/dev/null; done
        echo "IRIV EdgeAI Studio is enabled and starting."
        ;;
    stop)
        need_root stop
        for m in $(masters); do systemctl stop "$m" 2>/dev/null; done
        systemctl stop "${fixed[@]}" 2>/dev/null
        echo "IRIV EdgeAI Studio is stopped (it starts again at the next boot)."
        ;;
    start)
        need_root start
        systemctl start "${fixed[@]}" 2>/dev/null
        for m in $(masters); do systemctl start "$m" 2>/dev/null; done
        echo "IRIV EdgeAI Studio is starting."
        ;;
    about)
        for d in /opt/irivai-systor /opt/irivai-releases/*; do
            [[ -f "$d/core/about.py" ]] && last="$d"
        done
        (cd "${last:-/opt/irivai-systor}" && .venv/bin/python -c "from core import about; print(about.text())" 2>/dev/null             || python3 -c "from core import about; print(about.text())")
        ;;
    status)
        systemctl --no-pager --no-legend list-units --all 'irivai-*' 2>/dev/null | cut -c1-110
        ;;
    *)
        sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac
