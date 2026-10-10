#!/usr/bin/env bash
#
# install.sh
#
# Fetches the upstream packages this project builds on, each pinned to the
# exact commit the modifications in overlay/ were written against, then copies
# those modifications into place.
#
# The upstream packages are the work of Jared Rhodenizer and are not
# redistributed here. This script clones them from his repositories. See
# README.md, Credits.
#
# Usage, from the repository root:
#   ./install.sh           fetch what is missing, reapply the overlay
#   ./install.sh --force    delete and re-clone everything
#
# The face widget is Windows only. Everything else runs on macOS and Linux.

set -euo pipefail
cd "$(dirname "$0")"

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

# Upstream repositories, pinned. These hashes are the commits the files in
# overlay/ were derived from. Moving a pin forward without re-deriving the
# overlay is how you get a silently broken merge.
PACKAGES="
backtalk        https://github.com/jaredrhod/backtalk        17353c66b21ef31b5e7d990153e81c717d80771d
ai-visualizer   https://github.com/jaredrhod/ai-visualizer   dd109df74fda8a14ee93828fc2313b9045a9f1b4
barehands       https://github.com/jaredrhod/barehands       bdd8df505b290287dc5483844eb43d61fa1b74af
fullstack-agent https://github.com/jaredrhod/fullstack-agent d3c7374c89680628f38e1e63aa9d64a0a7170f7b
ai-memory-vault https://github.com/jaredrhod/ai-memory-vault d16ba5a6c705456c0f53692f3be0819c2cfe98b3
"

command -v git >/dev/null 2>&1 || {
  echo "git is not on PATH. Install git, then run this again." >&2
  exit 1
}

while read -r name url commit; do
  [ -z "$name" ] && continue

  if [ "$FORCE" = "1" ] && [ -d "$name" ]; then
    echo "  $name : removing existing checkout (--force)"
    rm -rf "./${name:?}"
  fi

  if [ -d "$name" ]; then
    echo "  $name : already present, leaving it alone"
  else
    echo "  $name : cloning"
    git clone --quiet "$url" "$name"
    echo "  $name : pinning to ${commit:0:10}"
    git -C "$name" -c advice.detachedHead=false checkout --quiet "$commit"
  fi
done <<< "$PACKAGES"

# Apply the modified files. Each one carries Jared Rhodenizer's copyright
# notice and the AGPL header from the file it was derived from.
echo
echo "Applying overlay:"

[ -d overlay ] || { echo "overlay/ is missing. Run this from the repository root." >&2; exit 1; }

find overlay -type f | while read -r src; do
  rel="${src#overlay/}"
  mkdir -p "$(dirname "$rel")"
  cp -f "$src" "$rel"
  echo "  $rel"
done

cat <<'DONE'

Upstream fetched and overlay applied.

Next:
  1. cd backtalk && uv sync
  2. Copy each file in config-examples/ to the component that reads it and edit it.
  3. Edit CLAUDE.md and replace the {OWNER_*} placeholders.

See README.md for the full walkthrough.
DONE
