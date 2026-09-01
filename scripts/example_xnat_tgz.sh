#!/usr/bin/env bash
set -euo pipefail

# Backward-compatible script name. ZIP and tar archives are both accepted.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$SCRIPT_DIR/example_xnat_archive.sh" "$@"
