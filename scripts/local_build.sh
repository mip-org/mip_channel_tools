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
# All flags after the package path are forwarded to `mip-channel local-build`
# (e.g. --architecture, --force, --no-test, --no-publish, --no-reindex,
# --matlab, --mip-dir). $PYTHON overrides the interpreter (default python3).
set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"

if [ "$#" -lt 1 ]; then
  echo "usage: $0 packages/<name>/<release> [mip-channel local-build flags...]" >&2
  exit 2
fi

echo "Installing channel tooling from $TOOLS_DIR ..."
"$PY" -m pip install -q -e "$TOOLS_DIR"

pkg="$1"; shift
exec "$PY" -m mip_channel_tools local-build \
  --tools-dir "$TOOLS_DIR" \
  --package-path "$pkg" \
  "$@"
