# CNAP fMRI Prep 0.4.0 verification report

Date: 2026-09-02

## Local verification

- Ruff: passed for `src` and `tests`.
- Pytest: 80 passed with ANTs available on `PATH`.
- Mocked three-run restart: passed; the second attempt reused all 13 scientific
  Pydra tasks and did not execute TOPUP again.
- Read-only status smoke test: correctly summarized the completed nine-run pilot
  session produced by 0.3.2, including one shared TOPUP and 38 valid cache
  entries.
- Wheel build: passed with pip's local PEP 517 builder.
- Wheel contents: include the job/status modules and MATLAB wrapper.
- Primary command reports version 0.4.0.
- `git diff --check`: passed.

The three warnings in the full suite are upstream pydicom deprecation warnings
from the synthetic Part 10 DICOM fixture. They do not represent test failures.

## Reliability coverage

Tests cover active work-directory exclusion, stale job-lock archival, refusal to
recover an active cache, stale Pydra-lock archival, durable interrupted state,
saved-invocation replay, status categories, cache-hit provenance, timing-based
status estimates, and bounded temporary MATLAB-license retry.

## Release acceptance

The 0.3.2 clean-computer installation and pilot-preprocessing gate passed on
2026-09-02. The remaining 0.4.0 acceptance test is to interrupt and resume a real
pilot job, inspect it from a second terminal, and confirm the reported cache
reuse against the derivative outputs.
