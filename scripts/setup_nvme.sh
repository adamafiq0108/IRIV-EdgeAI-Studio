#!/usr/bin/env bash
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
#
# setup_nvme.sh -- put the event database and the detection snapshots on the
# NVMe, not the eMMC. The eMMC wears out under the DB's write traffic and a busy
# snapshot directory; the NVMe is the right home for both (iriv-edge-ai-board).
#
#   sudo ./scripts/setup_nvme.sh [/mnt/nvme]
#
# Two independent stores:
#   - PostgreSQL data directory -> <nvme>/postgresql/<ver>/<cluster>
#     (moved with the cluster stopped; the old copy is KEPT as a backup)
#   - snapshots / app data       -> <nvme>/cytron/<slug>/data
#     (you set [app].data_dir in the system's config.local.toml -- printed below)
#
# Safe to re-run: it skips a store already on the NVMe. The Postgres move keeps
# the original data until you remove it by hand, so a bad move is recoverable.
set -euo pipefail

NVME="${1:-/mnt/nvme}"
#: Who the services run as (install.sh's SERVICE_USER). Everything this script
#: creates on the drive is handed to them -- a folder root can write and the
#: service cannot is a folder the product cannot use. Asked of the hub unit
#: when not given (upgrade_steps.sh does the same); cytron when unknown.
SVC_USER="${SERVICE_USER:-$(sed -n 's/^User=//p' /etc/systemd/system/irivai-hub.service 2>/dev/null | head -1 || true)}"
SVC_USER="${SVC_USER:-cytron}"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ -d "$NVME" ] || die "NVMe mount '$NVME' not found -- mount the drive first (lsblk / mount)."
touch "$NVME/.cytron_write_test" 2>/dev/null && rm -f "$NVME/.cytron_write_test" \
    || die "'$NVME' is not writable by this user."

# ---------------------------------------------------------------------------
# 1. Snapshots / app data
# ---------------------------------------------------------------------------
say "Snapshot / app data directory"
# The box's folders on the drive, owned by the SERVICE user.
#
# A freshly mounted drive is root-owned and 0755, so a service running as
# `cytron` cannot create anything at the top of it. It does not need to --
# it needs its own folders (cytron/, dataset_image/, dataset_rec/, model/)
# -- but nothing created them, so the Dataset page reported "No NVMe drive
# mounted" on a board with the drive mounted and 111 GB free, and pointed
# at the one thing that was already right.
#
# ONE implementation: the root helper's "repair" (scripts/storage_setup.py,
# APP_DIRS), the same step the dashboard runs by itself when the folders are
# not writable. Each folder only, never chown -R. A mount point other than
# /mnt/nvme is a bench setup the helper does not serve; it gets the folders
# the old way.
HELPER=/usr/local/lib/irivai/storage-setup.py
[ -f "$HELPER" ] || HELPER="$(dirname "$0")/storage_setup.py"
if [ "$NVME" = "/mnt/nvme" ] && [ "$(id -u)" -eq 0 ]; then
    printf '{"action":"repair"}' | SUDO_USER="$SVC_USER" python3 -I "$HELPER" >/dev/null \
        || die "could not prepare the folders on $NVME (see the line above)."
else
    install -d -o "$SVC_USER" -g "$SVC_USER" "$NVME/cytron" \
        "$NVME/dataset_image" "$NVME/dataset_rec" "$NVME/model"
fi
echo "  The box's folders on $NVME are ready (owner $SVC_USER): cytron/,"
echo "  dataset_image/ and dataset_rec/ (dataset capture), model/."
echo
echo "  Point each system's app at the NVMe. In systems/<slug>/config.local.toml:"
echo
echo "      [app]"
echo "      data_dir = \"$NVME/cytron/<slug>/data\""
echo
echo "  The app creates it on start; events.jsonl and snapshots/ then live on the"
echo "  NVMe. Existing data under systems/<slug>/data stays where it is -- move it"
echo "  once if you want the history: mv systems/<slug>/data/* $NVME/cytron/<slug>/data/"

# ---------------------------------------------------------------------------
# 2. PostgreSQL data directory
# ---------------------------------------------------------------------------
say "PostgreSQL data directory"
if ! command -v pg_lsclusters >/dev/null 2>&1; then
    echo "  PostgreSQL not installed -- skipping (the event store is optional; the"
    echo "  master degrades to no-history without it)."
    exit 0
fi

read -r VER CLUSTER <<<"$(pg_lsclusters -h 2>/dev/null | awk 'NR==1{print $1, $2}')"
[ -n "${VER:-}" ] || die "no PostgreSQL cluster found (pg_lsclusters empty)."

OLD="/var/lib/postgresql/${VER}/${CLUSTER}"
NEW="${NVME}/postgresql/${VER}/${CLUSTER}"
CONF="/etc/postgresql/${VER}/${CLUSTER}/postgresql.conf"

echo "  cluster ${VER}/${CLUSTER}"
echo "  ${OLD}  ->  ${NEW}"

if [ -f "${NEW}/PG_VERSION" ]; then
    echo "  already on the NVMe -- nothing to do."
    exit 0
fi
[ "$(id -u)" -eq 0 ] || die "moving the cluster needs root -- re-run with sudo."
[ -f "${CONF}" ] || die "config not found: ${CONF}"

echo "  stopping postgresql..."
systemctl stop postgresql

install -d -o postgres -g postgres "$(dirname "$NEW")"
echo "  copying data to the NVMe (rsync -a)..."
rsync -a "${OLD}/" "${NEW}/"
chown -R postgres:postgres "$NEW"

# Keep a copy of the conf line we change, then repoint the cluster.
cp -a "$CONF" "${CONF}.pre-nvme"
if grep -qE "^[#[:space:]]*data_directory" "$CONF"; then
    sed -i "s|^[#[:space:]]*data_directory.*|data_directory = '${NEW}'|" "$CONF"
else
    printf "\ndata_directory = '%s'\n" "$NEW" >> "$CONF"
fi

echo "  starting postgresql..."
systemctl start postgresql
sleep 2

ACTIVE="$(sudo -u postgres psql -tAc 'SHOW data_directory;' 2>/dev/null || true)"
if [ "$ACTIVE" = "$NEW" ]; then
    echo "  OK -- cluster now runs from ${ACTIVE}"
    echo "  the old data is KEPT at ${OLD}; remove it once you've verified:"
    echo "      sudo rm -rf ${OLD}"
else
    die "cluster did not come up on the new path (SHOW data_directory = '${ACTIVE:-?}').
       Restore: sudo systemctl stop postgresql &&
                sudo cp ${CONF}.pre-nvme ${CONF} &&
                sudo systemctl start postgresql"
fi
