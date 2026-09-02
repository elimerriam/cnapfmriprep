# CNAP fMRI Prep 0.3.2

This release focuses on making a fresh installation diagnosable and repeatable
without editing source code or shell startup files.

## Installation diagnostics

`cnapfmriprep doctor` now reports:

- compatibility of the active Python and Pydra versions;
- mismatches between the imported package and installed distribution;
- command and import availability;
- an unsourced or incomplete FSL environment, including `FSLOUTPUTTYPE` and
  TOPUP configuration files;
- ANTs binaries present in an active Conda environment but missing from `PATH`;
- MATLAB and the configured NORDIC checkout; and
- concise actions for every failed check.

Pass `--config config/my_study.yaml` to verify the study's NORDIC path. Pass
`--check-matlab-license` only when a real, bounded MATLAB license checkout is
desired.

`doctor --fix-shell tcsh --fsldir /path/to/fsl` prints copyable commands for the
current shell. It does not run the commands and never changes `.tcshrc` or other
startup files. `csh`, `bash`, `zsh`, and `fish` are supported as well.

## Continuous testing and release archives

Every push and pull request now runs lint and tests on macOS and Linux with
Python 3.11 and 3.12. CI also builds the source distribution and wheel. Pushing
a version tag runs the tests and publishes both installation artifacts in the
corresponding GitHub release.

## Compatibility

- Python 3.11 or 3.12
- Pydra 0.25.0 (do not upgrade to the incompatible 1.x alpha line)
- Existing 0.2 and 0.3 study YAML remains valid
- `seventprep` remains available as a temporary command alias

The scientific preprocessing behavior is unchanged from 0.3.1.
