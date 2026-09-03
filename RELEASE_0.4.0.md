# CNAP fMRI Prep 0.4.0

Version 0.4.0 adds operational visibility and restart safety for long multi-run
preprocessing jobs. Scientific preprocessing behavior is unchanged.

## Live status

Inspect a job from another terminal without changing its work directory:

```bash
cnapfmriprep status --work-dir work/preprocess-sub001-ses01
```

The summary reports the shared TOPUP task, each BOLD run, volume progress,
completed-but-unpublished outputs, cache integrity, and an ETA when timing data
is available. `--json` emits the same snapshot as structured data.

## Resume and cache provenance

Each preprocessing directory now contains `job.json`, which records the exact
resolved invocation and an append-only attempt history. Resume it with:

```bash
cnapfmriprep resume --work-dir work/preprocess-sub001-ses01
```

The original preprocess command remains restart-safe. Every attempt records
which named stages were reused from Pydra cache and which were recomputed.

## Interruption and locks

- Ctrl-C and SIGTERM mark the attempt interrupted and preserve completed cache
  entries.
- A single-writer `job.lock` prevents two processes from using one work
  directory.
- Locks owned by a live process, or whose ownership is uncertain, are never
  changed.
- Demonstrably stale Pydra locks and invalid results are archived under
  `interrupted-cache-backups`; stale job leases are retained under
  `WORK_DIR/stale-locks`. Nothing is silently deleted.

## MATLAB license retry

Recognized temporary MATLAB license checkout failures use bounded exponential
backoff. Defaults are three retries, 30 seconds initially, and a maximum delay
of 300 seconds. Non-license MATLAB failures are not retried.

## Progress terminology

The former per-run `distortion field` progress stage is now named `SDC warp
preparation`. TOPUP still estimates the susceptibility field once when shared
TOPUP is enabled; the per-run stage only converts that estimate into the ANTs
displacement representation required for final resampling.
