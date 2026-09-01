#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./scripts/example_xnat_archive.sh /path/from/xnat/experiment.zip 001 01
# ZIP is the usual XNAT format; .tgz, .tar.gz, and .tar are also accepted.

ARCHIVE=${1:?"Pass the XNAT .zip or tar archive as argument 1"}
SUBJECT=${2:-001}
SESSION=${3:-01}
CONFIG=${CONFIG:-config/my_study.yaml}
ROOT=${ROOT:-$PWD/example-run}

mkdir -p "$ROOT"

# 1. Inventory first. A BOLD rule may intentionally match several series when
#    it uses run: auto; AP and PA rules should normally match once each.
cnapfmriprep inventory "$ARCHIVE" \
  --output-dir "$ROOT/inventory" \
  --config "$CONFIG"

# 2. Check the external environment before an expensive run.
cnapfmriprep check-deps --config "$CONFIG"

# 3. Convert into a staging dataset, run both validators, then publish.
cnapfmriprep ingest "$ARCHIVE" "$ROOT/bids" \
  --config "$CONFIG" \
  --subject "$SUBJECT" \
  --session "$SESSION" \
  --work-dir "$ROOT/work/ingest"

# 4. Run one NORDIC task per run and one shared TOPUP task, build a robust
#    reference from the configured first run, and resample every run to it.
cnapfmriprep preprocess "$ROOT/bids" "$ROOT/derivatives/cnapfmriprep" \
  --config "$CONFIG" \
  --subject "$SUBJECT" \
  --session "$SESSION" \
  --work-dir "$ROOT/work/preprocess"

printf '\nDerivatives: %s\n' "$ROOT/derivatives/cnapfmriprep"
printf 'QC reports:  %s\n' "$ROOT/derivatives/cnapfmriprep/sub-$SUBJECT/ses-$SESSION/figures"
