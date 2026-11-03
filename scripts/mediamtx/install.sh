#!/bin/sh
# Install the video gateway: MediaMTX binary, config, systemd unit.
#
# Idempotent -- safe to re-run on every deploy. There is NO per-board value in
# the config any more. Until 2026-10-04 this script stamped the board's LAN
# address into webrtcAdditionalHosts, and that address was the one the box had
# ON INSTALL DAY: the customer board installed as 192.168.1.5 was still
# advertising .5 as a candidate at .20 and .21, through a DHCP move and two
# fixed addresses set from the Network page. The browser's ICE check against
# the dead address just times out, and anything set once at install time
# goes stale the first time the Network page is used.
#
# The address the browser can actually reach is whatever the box has NOW, and
# webrtcIPsFromInterfaces (on, in mediamtx.yml) gathers exactly that, from
# every interface, at the start of every session -- eth0, eth1, the hotspot,
# a 169.254 fallback, all of them, with nothing to keep in step.
#
# The binary runs as a service holding every passthrough camera's password,
# installed by root, so it is not installed on trust (Pipi M2, 2026-10-11):
# the tarball must match the sha256 pinned below or nothing is installed.
# The pins are the release's own checksums.sha256 for v1.20.1, which agreed
# with GitHub's per-asset digests, and for arm64 (the CM5) with a hash taken
# of the downloaded file. Another MEDIAMTX_VERSION has no pin and is refused
# unless MEDIAMTX_SHA256 is given with it.
set -e
PINNED_VER=v1.20.1
SHA256_arm64=d1689f0bfefb1864e5ed3dcc8495eb2d7ec0a654f90bf3cd48980cb3bd08718a
SHA256_armv7=ee4dde41e04de75a237c7bb2733cf4102ca212db05efefbcbbcd1ea60bd77e33
SHA256_amd64=81b143f55a5d23d4a8c028d52869c14ea4a59919900528698fcc97a747fd69c6
VER="${MEDIAMTX_VERSION:-$PINNED_VER}"
HERE=$(cd "$(dirname "$0")" && pwd)

need_install=1
if [ -x /usr/local/bin/mediamtx ]; then
  have=$(/usr/local/bin/mediamtx --version 2>/dev/null || echo none)
  [ "$have" = "$VER" ] && need_install=0
fi

if [ "$need_install" = 1 ]; then
  case "$(uname -m)" in
    aarch64|arm64) ARCH=arm64 ;;
    armv7l)        ARCH=armv7 ;;
    x86_64)        ARCH=amd64 ;;
    *) echo "unsupported architecture $(uname -m)"; exit 1 ;;
  esac
  if [ "$VER" = "$PINNED_VER" ]; then
    eval "SHA=\$SHA256_${ARCH}"
  else
    SHA="${MEDIAMTX_SHA256:-}"
  fi
  if ! printf '%s' "$SHA" | grep -Eq '^[0-9a-f]{64}$'; then
    echo "no sha256 pinned for mediamtx ${VER} (${ARCH}) -- not installing an unchecked binary as root; set MEDIAMTX_SHA256"
    exit 1
  fi
  URL="https://github.com/bluenviron/mediamtx/releases/download/${VER}/mediamtx_${VER}_linux_${ARCH}.tar.gz"
  echo "installing mediamtx ${VER} (${ARCH})"
  TMP=$(mktemp -d)
  curl -fsSL -o "$TMP/m.tar.gz" "$URL"
  if ! echo "${SHA}  $TMP/m.tar.gz" | sha256sum -c - >/dev/null 2>&1; then
    echo "mediamtx ${VER} (${ARCH}): the download does not match its pinned sha256 -- NOT installed"
    rm -rf "$TMP"
    exit 1
  fi
  tar xzf "$TMP/m.tar.gz" -C "$TMP" mediamtx
  sudo install -m755 -o root -g root "$TMP/mediamtx" /usr/local/bin/mediamtx
  rm -rf "$TMP"
else
  echo "mediamtx ${VER} already installed"
fi

# The committed config, as is, root-owned: loopback-only listeners and auth,
# MoQ and every other unused protocol off (Pipi H1). The same bytes an update
# installs (scripts/root_steps.py, "upgrade" table). A re-run on a box
# installed before 2026-10-04 is also how its stale install-day address goes.
sudo mkdir -p /etc/mediamtx
sudo install -m 644 -o root -g root "$HERE/mediamtx.yml" /etc/mediamtx/mediamtx.yml
grep '^webrtcIPsFromInterfaces' /etc/mediamtx/mediamtx.yml

sudo cp "$HERE/irivai-mediamtx.service" /etc/systemd/system/irivai-mediamtx.service
sudo systemctl daemon-reload
sudo systemctl enable irivai-mediamtx >/dev/null 2>&1 || true
sudo systemctl restart irivai-mediamtx
sleep 2
systemctl is-active irivai-mediamtx
curl -fsS -m 5 http://127.0.0.1:9997/v3/config/global/get >/dev/null \
  && echo "gateway API answering on 127.0.0.1:9997" \
  || { echo "gateway did not come up"; sudo journalctl -u irivai-mediamtx -n 20 --no-pager; exit 1; }

# Nothing of the gateway's may listen off loopback but the WebRTC media port
# (UDP 8189). A listener a new version opened by default is exactly how H1
# happened; a gateway found doing it is STOPPED (pages fall back to MJPEG)
# rather than left serving cameras to the LAN. No ss here: said, not judged.
if command -v ss >/dev/null 2>&1; then
  open=$(sudo ss -Hlntup 2>/dev/null | grep '"mediamtx"' | awk '{print $1" "$5}' \
         | grep -Ev ' (127\.0\.0\.1|\[::1\]):[0-9]+$' | grep -Ev '^udp .*:8189$' || true)
  if [ -n "$open" ]; then
    echo "the gateway listens off loopback -- stopped:"
    echo "$open"
    sudo systemctl stop irivai-mediamtx
    exit 1
  fi
  echo "gateway listeners: loopback only, plus UDP 8189 for WebRTC media"
else
  echo "no ss on this box: the gateway's listeners were not checked"
fi
