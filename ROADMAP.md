# CNAP fMRI Prep Roadmap

This roadmap prioritizes reproducibility and operational reliability before
expanding the scientific scope of `cnapfmriprep`.

## Development principle

The immediate objective is:

> A fresh computer can clone the repository, configure a session, and complete
> preprocessing without hand-editing source code.

Testing a clean checkout on a second computer is the release gate for the next
development cycle. Problems found during that test should take priority over
new features.

## 0.3.2: Installation and portability hardening

Implementation status: complete in 0.3.2. Validation on a clean office computer
remains the release acceptance test.

- Test clean installation on supported macOS and Linux systems.
- Add automated GitHub tests for each commit and pull request.
- Expand `cnapfmriprep doctor` to detect and explain:
  - an unsourced FSL environment, including a missing `FSLOUTPUTTYPE`;
  - ANTs installed through Conda but absent from `PATH`;
  - MATLAB availability and license failures;
  - an unavailable or invalid NORDIC checkout;
  - incompatible Python or Pydra versions.
- Add shell-specific guidance, including exact `tcsh` commands.
- Consider `doctor --fix-shell tcsh` as a safe command generator that prints
  changes rather than modifying shell configuration automatically.
- Publish source archives and installation notes with tagged GitHub releases.

### Acceptance criteria

- A clean clone installs without editing package files.
- `doctor` identifies all missing external dependencies with actionable
  messages.
- The browser setup assistant and a mocked preprocessing run pass on macOS and
  Linux.

## 0.4.0: Job status, restart, and recovery

- Add `cnapfmriprep status --work-dir ...` with a per-run session summary.
- Distinguish completed, running, waiting, failed, cached, and unpublished
  stages.
- Add an explicit `resume` command while retaining safe same-command restart.
- Record which results were reused from cache and which were recomputed.
- Improve stale-lock detection without modifying active work directories.
- Retry temporary MATLAB license failures with bounded backoff.
- Support graceful shutdown that leaves a clean restart point.
- Estimate remaining time from completed stage and volume timings.

Example status display:

```text
Shared TOPUP       complete
BOLD run 01        complete
BOLD run 02        motion correction 180/420
BOLD run 03        waiting for motion reference
BOLD run 04        NORDIC complete
```

### Acceptance criteria

- Interrupting a run does not invalidate completed work.
- Restarting reports exactly what is reused.
- Temporary MATLAB license unavailability does not require manual cache
  surgery.
- Status output remains useful from another terminal while a job is running.

## 0.4.x: Session-level QC dashboard

- Create one session homepage linking every run report.
- Compare motion and framewise displacement across runs.
- Display the shared TOPUP field and before/after distortion overlays.
- Add image sliders or animations where they materially aid review.
- Flag unusually large motion, suspicious field magnitude, negative values,
  grid mismatches, and failed registrations.
- Export a session-level CSV of QC measurements.
- Use neutral labels such as `review recommended`; do not automatically make
  scientific inclusion or exclusion decisions.

### Acceptance criteria

- A reviewer can assess the complete session without opening reports manually
  from the filesystem.
- Every warning links to the evidence and thresholds that produced it.

## Setup assistant improvements

- Preview generated BIDS filenames before saving the YAML.
- Support several tasks in one session.
- Support multiple AP/PA pairs assigned to different run groups.
- Improve conservative suggestions for SBRef, phase, magnitude, no-RF,
  anatomical, and unrelated series.
- Warn about duplicate, overlapping, or incomplete selections.
- Compare a new session with a previously saved study template.
- Separate reusable study defaults from session-specific series UIDs.
- Run inventory-rule validation directly from the browser.

## 0.5.0: Batch and server operation

- Process multiple subjects and sessions from a study-level manifest.
- Add a dry-run command that displays the complete work plan.
- Add SLURM support for cluster execution.
- Set separate resource limits for NORDIC, TOPUP, ANTs registration, and final
  resampling.
- Account for available RAM, CPU count, and simultaneous MATLAB licenses in
  server execution profiles.
- Evaluate Apptainer or container support for Python, FSL, and ANTs while
  keeping MATLAB and the authorized NORDIC checkout externally configured.

### Acceptance criteria

- Batch execution isolates failures to individual subjects or sessions.
- Resource limits are explicit, logged, and reproducible.
- A failed job can be resumed without rerunning unrelated completed sessions.

## Scientific validation and reproducibility

- Maintain synthetic end-to-end regression data for orchestration tests.
- Add numerical regression tests for transform ordering and interpolation.
- Define a de-identified reference session for release validation when one is
  legally and ethically available.
- Record software versions, configuration, execution profile, and command-line
  invocation in derivative provenance.
- Document known scientific boundaries, including static susceptibility fields
  and the absence of slice-timing, anatomical alignment, normalization,
  smoothing, and response estimation.

## Recommended implementation order

1. Complete the clean desktop reproducibility test.
2. Fix installation and `doctor` issues discovered by that test.
3. Add status, graceful resume, and MATLAB-license retry.
4. Build the session-level QC dashboard.
5. Extend the setup assistant to multiple tasks and fieldmap groups.
6. Add multi-subject, multi-session, and cluster execution.
