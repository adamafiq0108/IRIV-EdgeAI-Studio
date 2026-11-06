#!/usr/bin/env bash
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
#
# publish_update.sh -- put the latest manual-update file on the `updates`
# branch, where anyone can download it.
#
#     # bump VERSION first: a box refuses a version it is already running
#     bash scripts/publish_update.sh
#
# Builds the dashboard, builds dist/irivai-<version>.tar.gz
# (scripts/make_release.py), and pushes a branch holding ONLY that file, its
# SHA-256 and a README with the download and install steps.
#
# The branch is ONE commit, replaced on every release (force-pushed): a 5 MB
# archive committed per version would grow every clone of the repository for
# ever, and the branch's job is "the latest update", not an archive. Older
# versions are in the main branch's history and can be rebuilt from there.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${UPDATES_BRANCH:-updates}"
REMOTE="${UPDATES_REMOTE:-origin}"
cd "$ROOT"

VERSION="$(tr -d '[:space:]' < VERSION)"
URL="$(git remote get-url "$REMOTE")"
REPO_PATH="$(printf '%s' "$URL" | sed -E 's#^(https://github.com/|git@github.com:)##; s#\.git$##')"

echo "== building the dashboard"
(cd frontend && npm run build >/dev/null)
echo "== building dist/irivai-${VERSION}.tar.gz"
python scripts/make_release.py
FILE="irivai-${VERSION}.tar.gz"
[[ -f "dist/${FILE}" ]] || { echo "dist/${FILE} was not built" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp "dist/${FILE}" "$WORK/"
(cd "$WORK" && sha256sum "$FILE" > "${FILE}.sha256")
SHA="$(cut -d' ' -f1 "$WORK/${FILE}.sha256")"
SIZE="$(du -h "$WORK/${FILE}" | cut -f1)"
COMMIT="$(git rev-parse --short HEAD)"
RAW="https://github.com/${REPO_PATH}/raw/${BRANCH}/${FILE}"

cat > "$WORK/README.md" <<EOF
# IRIV EdgeAI Studio - latest update

| | |
|---|---|
| **Version** | \`${VERSION}\` |
| **File** | [\`${FILE}\`](${FILE}) (${SIZE}) |
| **SHA-256** | \`${SHA}\` |
| **Built from** | commit \`${COMMIT}\` on \`main\` |

This branch holds only the latest manual-update file. Your settings,
recordings, models and systems are kept when you update.

## Update a board

1. **Download** [\`${FILE}\`](${RAW}) to your computer.
2. Open the dashboard and sign in with an **admin** account.
3. Open the account menu (top right) and choose **About**.
4. In the **Software update** card, press **Choose update file**, pick the
   file, then press **Install**. The board checks every file against its
   manifest, installs the new version beside the old one, and restarts.

The previous version is kept: the same card offers **Go back to <version>**
if you need it.

## Check the download (optional)

\`\`\`bash
sha256sum ${FILE}
# must print: ${SHA}
\`\`\`

## Download from the board itself

\`\`\`bash
curl -LO ${RAW}
\`\`\`
EOF

echo "== publishing ${FILE} to ${REMOTE}/${BRANCH}"
(
  cd "$WORK"
  git init -q -b "$BRANCH"
  git add "$FILE" "${FILE}.sha256" README.md
  git -c user.name="$(git -C "$ROOT" config user.name)" \
      -c user.email="$(git -C "$ROOT" config user.email)" \
      commit -q -m "Update ${VERSION} (from ${COMMIT})"
  git push -q --force "$URL" "${BRANCH}:${BRANCH}"
)
echo "Done: ${RAW}"
