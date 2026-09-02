# CNAP fMRI Prep 0.3.2 verification report

Date: 2026-09-01

## Local verification

- Ruff: passed for `src` and `tests`.
- Pytest: 69 passed, 2 environment-dependent tests skipped.
- ANTs-enabled transform tests: 8 passed after adding the installed Conda ANTs
  binaries to `PATH`.
- Isolated source and wheel build: passed.
- Wheel installation in a new temporary virtual environment: passed.
- Primary and compatibility commands both reported version 0.3.2.
- Source-archive inspection confirmed that `config/my_study.yaml` is excluded;
  the reusable example, diagnostics module, roadmap, tests, and release notes are
  included.

The mocked multi-run integration test is part of the full passing suite. It
executes fake NORDIC, TOPUP, and ANTs commands and verifies shared-TOPUP session
orchestration without requiring research data.

## Diagnostic smoke check

Against the current Mac installation, `doctor --config config/my_study.yaml`
correctly reported:

- compatible Python 3.11 and Pydra 0.25;
- matching package and distribution version 0.3.2;
- successful imports of the ingest and preprocessing graph;
- valid FSL, TOPUP configurations, Conda ANTs, and NORDIC checkout; and
- the two tools omitted from the test command's controlled `PATH`: MATLAB and
  the official BIDS validator.

The MATLAB license check was not run during this verification because it is an
explicit opt-in operation.

## Continuous integration

The repository workflow runs the full suite on macOS and Linux with Python
3.11 and 3.12 and builds both distribution formats. These remote checks begin
after the commit is pushed. A separate tag workflow retests the package before
publishing the source and wheel archives to GitHub Releases.

The remaining release acceptance test is installation and pilot processing on
the clean office computer.
