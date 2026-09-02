# CNAP fMRI Prep

CNAP fMRI Prep (`cnapfmriprep`) is an alpha, study-configured Pydra pipeline for high-resolution 7 T
fMRI. It stops before anatomical alignment and response estimation so that the
native-resolution outputs can be used with mrAlign and mrTools.

## Release 0.3.2: portable installation diagnostics

Version 0.3.2 hardens fresh-computer setup. `cnapfmriprep doctor` now checks
the supported Python and pinned Pydra versions, package imports, FSL shell
variables and TOPUP configuration files, Conda-installed ANTs commands, MATLAB,
the BIDS validator, and the configured NORDIC checkout. An optional bounded
MATLAB license probe distinguishes installation problems from license checkout
failures.

The release also adds macOS and Linux tests for Python 3.11 and 3.12, package
build checks, and automatic source/wheel assets for tagged GitHub releases.

## Release 0.3.1: cnapfmriprep rename

The installable distribution, Python package, provenance metadata, and primary
command are now named `cnapfmriprep`. The former `seventprep` terminal command
remains as a temporary compatibility alias, but new scripts should use
`cnapfmriprep`.

## Release 0.3.0: guided setup, live progress, safer restarts

Version 0.3.0 keeps the shared multi-run processing introduced in 0.2 and adds:

- `cnapfmriprep setup`, a local browser assistant that inventories an archive (or
  reads an existing inventory), shows every DICOM series, and lets the user
  select BOLD runs, the AP/PA pair, anatomical scans, and the shared motion
  reference. It writes exact UID-based rules, so run names and run counts may
  vary between sessions.
- Live stage and volume progress in the terminal for NORDIC, shared TOPUP,
  field construction, motion correction, final resampling, and QC.
- Named `laptop`, `workstation`, `server`, and `auto` execution profiles. The
  laptop profile is sequential to limit application-memory pressure.
- Automatic quarantine of interrupted Pydra cache results that are marked as
  successful but contain no output. Completed cache entries are preserved.
- A clear workflow error if Pydra nevertheless returns an empty result, instead
  of the former `NoneType` attribute traceback.

The setup website binds only to `127.0.0.1`, uses a random access token, and
does not upload DICOM metadata.

## Shared multi-run processing

The 0.2 series adds the session behavior needed for a typical fMRI experiment:

- One DICOM rule can match every BOLD run and assign consecutive BIDS run
  numbers automatically.
- A single AP/PA reverse-phase-encoded pair can be associated with every run.
- TOPUP is executed once for the shared AP/PA pair.
- NORDIC remains run-specific because each run has its own two trailing no-RF
  volumes.
- A robust undistorted reference is built from a configurable reference run,
  normally run 1.
- Every volume of every other run is registered directly to that same reference.
- Each delivered functional volume is generated from the original NORDIC volume
  with one combined susceptibility-plus-rigid resampling operation.

Earlier ZIP, packaging, and DICOM-keyword corrections are retained.

Version 0.2.1 also fixes command-line startup: `version`, `doctor`, and
`inventory` no longer import the Pydra preprocessing graph before command
selection. This prevents an incompatible Pydra installation from blocking DICOM
inventory and replaces launcher-only tracebacks with an actionable dependency
message.

### Resolving the “expected 1 match, found 10” error

That message came from the 0.1.x assumption that one DICOM rule represented one
BIDS run. In 0.2.x, the ten matches are intentional. Configure the BOLD rule
with `run: auto` and either `expected_matches: 10` or
`expected_matches: one_or_more`. Do **not** only change `expected_matches` while
leaving `run: 1`, because every series would otherwise target the same BIDS
filename.

## Processing graph

```text
XNAT ZIP/tar archive
  -> safe extraction and DICOM series inventory
  -> one multi-match BOLD rule assigns run-01, run-02, ...
  -> dcm2niix conversion and BOLD/no-RF splitting
  -> official BIDS validation plus semantic validation
  -> NORDIC independently for every BOLD run
  -> one TOPUP estimate from the session AP/PA pair
  -> one BOLD-grid susceptibility warp per run
  -> robust undistorted reference from configured reference run
  -> all volumes registered to that shared reference
  -> one final SDC plus motion interpolation per functional volume
  -> native-resolution BOLD, transforms, motion TSVs, and HTML QC
```

The pipeline does **not** perform slice-timing correction, anatomical
registration, normalization, smoothing, temporal filtering, nuisance regression,
or response estimation.

## Scientific and implementation boundaries

- All BOLD runs sharing one motion reference must use the same spatial grid.
- The BOLD runs and reverse-PE images must have compatible grids; the pipeline
  aborts instead of inserting an undocumented fieldmap registration.
- The final two acquired volumes of each BOLD series must be RF-off noise images.
- NORDIC is magnitude-only. With `noise_volume_last: 2`, the upstream-compatible
  procedure uses the penultimate volume for its measured-noise estimate; QC
  reports both RF-off volumes separately.
- The TOPUP field is static. It does not model susceptibility changes caused by
  large pose changes between or within runs.
- Jacobian maps are generated for QC. Intensity modulation remains disabled.
- NORDIC_Raw is not included. Supply an authorized local checkout.

## Requirements

Python 3.11 or 3.12 is recommended. Python dependencies are listed in
`pyproject.toml`; Pydra 0.25.0 is pinned for this release.

External programs required on `PATH`:

```text
dcm2niix
topup
antsRegistration
antsApplyTransforms
matlab
bids-validator-deno or bids-validator
```

`fslmerge` is used when available; a NiBabel fallback is included for assembling
TOPUP inputs. `FSLDIR` must point to an FSL installation containing the stock
TOPUP configurations. Set the NORDIC checkout in YAML or with `NORDIC_ROOT`.

## Installation

```bash
git clone https://github.com/elimerriam/cnapfmriprep.git
cd cnapfmriprep

conda env create -f environment.yml
conda activate cnapfmriprep

cnapfmriprep version
cnapfmriprep doctor --config config/my_study.yaml
```

`doctor` exits successfully only when the required installation checks pass and
prints a JSON report with specific remedies. The MATLAB license probe is
explicit because it launches MATLAB:

```bash
cnapfmriprep doctor --config config/my_study.yaml --check-matlab-license
```

For `tcsh`, print commands that configure the current terminal without changing
`.tcshrc` or any other startup file:

```tcsh
cnapfmriprep doctor --fix-shell tcsh --fsldir /absolute/path/to/fsl
```

Review the printed commands, run them in the terminal, and then rerun `doctor`.
The command generator also accepts `csh`, `bash`, `zsh`, and `fish`.

Expected version:

```text
0.3.2
```

## Inventory an XNAT archive

```bash
cnapfmriprep inventory /absolute/path/experiment.zip \
  --output-dir work/inventory
```

Inspect:

```text
work/inventory/dicom_series.tsv
```

Useful columns include `SeriesNumber`, `AcquisitionTime`,
`SeriesDescription`, `ProtocolName`, `ImageType`, and `SeriesInstanceUID`.

## Generate the configuration in a browser

The setup assistant can inventory an archive itself:

```bash
cnapfmriprep setup /absolute/path/experiment.zip \
  --template config/example_study.yaml \
  --output config/my_study.yaml \
  --work-dir work/setup
```

Or reuse an existing inventory to avoid extracting the archive again:

```bash
cnapfmriprep setup \
  --inventory work/inventory/dicom_series.tsv \
  --template config/example_study.yaml \
  --output config/my_study.yaml
```

The browser shows conservative role suggestions. Review every row, choose the
motion-reference radio button beside one selected BOLD run, verify the BIDS
phase-encoding directions, and confirm the selections. The generated YAML uses
the exact `SeriesInstanceUID` values of that session and sets the actual BOLD
run count automatically. Existing output is never replaced unless
`--overwrite` is supplied.

## Configure all BOLD runs with one rule

Copy the example:

```bash
cp config/example_study.yaml config/my_study.yaml
```

A multi-run BOLD rule should resemble:

```yaml
ingest:
  series_rules:
    - name: retinotopy_magnitude_runs
      kind: bold_with_norf
      match:
        SeriesDescription: "(?i)retino.*nordic"
        # Add ProtocolName or ImageType only when needed to exclude other series.
      task: retinotopy
      acquisition: hires7T
      run: auto
      run_start: 1
      run_sort_by: SeriesNumber
      b0_identifier: pepolar_session01
      expected_matches: 10
```

`expected_matches` can instead be:

```yaml
expected_matches: one_or_more
```

With `run: auto`, matching series are sorted numerically by `SeriesNumber` by
default and assigned `run-01`, `run-02`, and so forth. Set
`run_sort_by: AcquisitionTime` when acquisition time is the preferred ordering.
The other field remains a deterministic tie-breaker.

Do not merely set `expected_matches: 10` in a 0.1.x configuration while keeping
`run: 1`; that would not provide unique output names. Multiple BOLD matches
require `run: auto` in 0.2.x.

## Configure one shared AP/PA pair

Use the same `b0_identifier` for all BOLD runs and both reverse-PE series:

```yaml
    - name: reverse_pe_ap_session01
      kind: fmap_epi
      match:
        SeriesDescription: "(?i)^RETINO_TOPUP_AP$"
      acquisition: bold
      direction: AP
      b0_identifier: pepolar_session01
      expected_matches: 1

    - name: reverse_pe_pa_session01
      kind: fmap_epi
      match:
        SeriesDescription: "(?i)^RETINO_TOPUP_PA$"
      acquisition: bold
      direction: PA
      b0_identifier: pepolar_session01
      expected_matches: 1
```

`direction: AP` and `direction: PA` are filename labels. The actual BIDS
`PhaseEncodingDirection` values are normally read from the dcm2niix JSON
sidecars and validated independently.

Some custom scanner sequences do not expose the polarity metadata that
dcm2niix needs. In that case, set an explicit override on each affected BOLD
and EPI fieldmap rule:

```yaml
phase_encoding_direction: j-  # valid values: i, i-, j, j-, k, k-
```

CNAP fMRI Prep adds the configured value only when dcm2niix omits it or reports the
same value. A conflicting dcm2niix value is treated as an error so scanner
metadata cannot be silently replaced. The BIDS direction is relative to the
converted NIfTI axes; it is not necessarily the same text as the `dir-AP` or
`dir-PA` filename label.

## Configure the shared reference

```yaml
multi_run:
  shared_topup: true
  shared_motion_reference: true
  reference_task: retinotopy
  reference_run: 1
```

When `reference_task` and `reference_run` are both `null`, the first selected
run in stable BIDS ordering is used. Specifying both is safer when a session
contains multiple tasks.

The reference is not simply the first volume. CNAP fMRI Prep applies the shared SDC
to temporary previews from the reference run, performs two-pass rigid
realignment, and averages the aligned previews to create a robust undistorted
reference. All other runs are then registered directly to that fixed image.

## Test the rules without conversion

```bash
cnapfmriprep inventory /absolute/path/experiment.zip \
  --output-dir work/inventory-matched \
  --config config/my_study.yaml
```

Review:

```text
work/inventory-matched/series_match_plan.tsv
```

For a multi-run rule, this file contains one row per DICOM series and an
`assigned_run` column. Confirm that run ordering matches acquisition order.

## Ingest and validate

```bash
cnapfmriprep ingest /absolute/path/experiment.zip ./bids \
  --config config/my_study.yaml \
  --subject 001 \
  --session 01 \
  --work-dir work/ingest-sub001-ses01
```

The BIDS dataset will contain, for example:

```text
sub-001/ses-01/func/
  sub-001_ses-01_task-retinotopy_acq-hires7T_run-01_bold.nii.gz
  sub-001_ses-01_task-retinotopy_acq-hires7T_run-02_bold.nii.gz
  sub-001_ses-01_task-retinotopy_acq-hires7T_run-01_mod-bold_noRF.nii.gz
  sub-001_ses-01_task-retinotopy_acq-hires7T_run-02_mod-bold_noRF.nii.gz
  ...

sub-001/ses-01/fmap/
  sub-001_ses-01_acq-bold_dir-AP_epi.nii.gz
  sub-001_ses-01_acq-bold_dir-PA_epi.nii.gz
```

Each BOLD sidecar points to its own no-RF file and to the common B0 identifier.
For custom 3D sequences where dcm2niix reports an inner excitation interval as
`RepetitionTime`, ingestion detects a value shorter than the phase-encode readout
and derives the volume TR from the partition count and
`ParallelReductionFactorOutOfPlane`. The corrected value is also written to the
NIfTI temporal pixel dimension.

## Preprocess all runs

```bash
cnapfmriprep preprocess ./bids ./derivatives/cnapfmriprep \
  --config config/my_study.yaml \
  --subject 001 \
  --session 01 \
  --work-dir work/preprocess-sub001-ses01
```

The session Pydra graph schedules one shared TOPUP task and one NORDIC task per
run. NORDIC tasks can execute concurrently. Non-reference motion tasks depend on
the completed reference-run motion task but not on one another.

The terminal now reports live stage transitions and throttled volume counts.
The same structured events are retained at `WORK_DIR/progress.jsonl`.

### Execution profiles

The YAML can select a portable resource profile:

```yaml
execution:
  profile: laptop
  show_progress: true
  progress_interval_percent: 10
```

Inspect the presets and automatic selection with:

```bash
cnapfmriprep profiles
```

Override a YAML profile for one run without editing the file:

```bash
cnapfmriprep preprocess ./bids ./derivatives/cnapfmriprep \
  --config config/my_study.yaml \
  --subject 001 --session 01 \
  --work-dir work/preprocess-sub001-ses01 \
  --execution-profile workstation
```

Use `laptop` on a memory-constrained Mac. `workstation` and `server` enable
bounded parallelism and should be used only where RAM and simultaneous MATLAB
licenses are available. `auto` makes a conservative choice from installed RAM
and logical CPU count.

### Restarting after an interruption

Restart the same command with the same work directory. Before launching Pydra,
CNAP fMRI Prep checks the cache and moves only invalid empty results into
`pydra-cache/interrupted-cache-backups/`; valid completed work remains reusable.
It refuses to modify a cache containing an active lock.

To inspect and perform that recovery without starting preprocessing:

```bash
cnapfmriprep recover-cache --work-dir work/preprocess-sub001-ses01
```

To publish only one target run while retaining the shared reference behavior:

```bash
cnapfmriprep preprocess ./bids ./derivatives/cnapfmriprep \
  --config config/my_study.yaml \
  --subject 001 --session 01 \
  --task retinotopy --run 5 \
  --work-dir work/preprocess-sub001-ses01-run05
```

When run 5 is requested but run 1 is configured as the shared reference, run 1
is processed internally as a dependency. Only the requested target run is
published by that invocation.

## One-shot command

After confirming the inventory rules:

```bash
cnapfmriprep run /absolute/path/experiment.zip ./bids ./derivatives/cnapfmriprep \
  --config config/my_study.yaml \
  --subject 001 --session 01 \
  --work-dir work/sub001-ses01
```

## Outputs

For every run, derivatives include:

```text
*_desc-nordic_bold.nii.gz
*_desc-preproc_bold.nii.gz
*_desc-preproc_boldref.nii.gz
*_desc-brain_mask.nii.gz
*_desc-motion_timeseries.tsv
*_desc-displacement_timeseries.tsv
*_desc-rigid_xfms/
*_desc-provenance.json
*_desc-manifest.json
```

All `desc-preproc_bold` images in a shared-reference session have the same shape,
affine, and voxel grid. Their motion transforms map into the same target space.
Framewise displacement resets at the first frame of each run, while absolute
slab displacement is measured relative to the common reference.

The derivative `fmap` and `figures` directories contain NORDIC no-RF products,
TOPUP/SDC maps, and per-run HTML QC. The same TOPUP estimate may be copied under
run-prefixed derivative names for explicit provenance; the session processing
result records the single shared TOPUP task output.

## One-interpolation guarantee

Temporary linearly unwarped previews are used only to estimate motion. The
published BOLD volumes are sampled directly from the corresponding original
NORDIC functional volumes. Each final `antsApplyTransforms` call contains the
susceptibility warp and the volume-specific rigid transform.

Representative volumes are also compared against a sequential SDC-then-motion
oracle to determine the correct ANTs transform-list ordering. The run aborts if
neither candidate ordering is within the configured normalized error threshold.

## Resources

```yaml
execution:
  pydra_plugin: cf
  n_procs: 4
  volume_workers: 4
  threads_per_ants: 2
```

`n_procs` limits Pydra task concurrency. `volume_workers` and
`threads_per_ants` govern volume-level registration. Avoid CPU oversubscription:

```text
volume_workers * threads_per_ants <= allocated CPU cores
```

## Tests

```bash
python -m pip install -e '.[test]'
pytest
```

Tests cover safe ZIP/tar extraction, DICOM inventory, regex matching,
automatic run assignment, BIDS semantic validation, shared-session graph
construction, NORDIC splitting, TOPUP metadata, all six BIDS PE directions,
ITK displacement conversion, motion metrics, derivative QC, and mocked external
command execution.
