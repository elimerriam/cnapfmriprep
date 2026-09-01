# SevenTPrep 0.2.1 verification report

Date: 2026-08-30

## Startup correction

Version 0.2.0 imported `seventprep.preprocess` while importing the CLI module.
That transitively imported Pydra before Typer selected a command, so a missing or
incompatible Pydra installation could prevent even `version` or `inventory` from
starting. Version 0.2.1 makes command-specific imports lazy and changes the
console entry point to a minimal diagnostic wrapper.

A new `seventprep doctor` command reports:

- the active Python executable and version;
- the imported SevenTPrep package path;
- installed dependency versions;
- the installed Pydra version;
- whether Pydra exposes the 0.25 `Submitter`, `Workflow`, and `mark` API;
- `CONDA_PREFIX` and the executables selected from `PATH`.

## Checks completed in the artifact environment

```text
Python source compilation: passed
CLI version without Pydra/NiBabel/Pydicom installed: passed
CLI doctor in an intentionally incomplete environment: passed
Package-completeness test: passed
Focused CLI tests: 3 passed
```

The startup test deliberately runs where Pydra, NiBabel, Pydicom, and
NiTransforms are unavailable. `seventprep version` still succeeds and
`seventprep doctor` emits structured diagnostics without a traceback.

The multi-run source and tests from 0.2.0 are retained. The artifact environment
does not contain the complete neuroimaging dependency stack or external FSL,
ANTs, MATLAB, NORDIC, and BIDS-validator programs, so scientific integration on
actual 7 T data remains a pilot qualification step.
