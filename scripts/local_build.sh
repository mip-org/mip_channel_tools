#!/usr/bin/env bash
#
# Install this tooling and run the local-build pipeline. This is the shared
# half of the local build: a channel's thin scripts/local_build.sh clones this
# repo into the channel and then execs this script, so the install + invoke
# logic lives here once rather than being duplicated per channel.
#
# Can also be run directly from a channel checkout that already has
# mip_channel_tools/ alongside:
#
#   mip_channel_tools/scripts/local_build.sh packages/<name>/<release> [flags...]
#
# Builds and tests by default; add --publish to upload + reindex. All flags
# after the package path are forwarded to `mip-channel local-build` (e.g.
# --architecture, --force, --no-test, --publish, --no-reindex, --matlab,
# --mip-dir). $PYTHON overrides the interpreter (default python3);
# $MIP_RUNTIME_REF picks a mip branch/tag (default: its default branch).
set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"

if [ "$#" -lt 1 ]; then
  echo "usage: $0 packages/<name>/<release> [mip-channel local-build flags...]" >&2
  exit 2
fi

# Mip runtime. CI checks out mip-org/mip fresh and addpaths it; do the same into
# the channel's gitignored ./mip rather than assume mip is on the MATLAB path
# (it usually isn't under `matlab -batch`). --mip-dir below points the build at
# it; a trailing --mip-dir in "$@" overrides. Leaves an existing non-git ./mip
# (e.g. a hand-placed checkout) untouched.
MIP_REF="${MIP_RUNTIME_REF:-}"
if [ -d mip/.git ]; then
  echo "Updating mip runtime (./mip) ..."
  git -C mip fetch -q --depth 1 origin "${MIP_REF:-HEAD}"
  git -C mip checkout -q FETCH_HEAD
elif [ ! -e mip ]; then
  echo "Cloning mip runtime into ./mip ..."
  git clone -q --depth 1 ${MIP_REF:+--branch "$MIP_REF"} \
    https://github.com/mip-org/mip.git mip
fi

echo "Installing channel tooling from $TOOLS_DIR ..."
"$PY" -m pip install -q -e "$TOOLS_DIR"

pkg="$1"; shift
exec "$PY" -m mip_channel_tools local-build \
  --tools-dir "$TOOLS_DIR" \
  --mip-dir "$PWD/mip" \
  --package-path "$pkg" \
  "$@"
