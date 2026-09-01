# CNAP fMRI Prep 0.3.1

This release renames SevenTPrep to CNAP fMRI Prep. The distribution, Python
package, primary terminal command, browser setup text, QC reports, and BIDS
provenance now consistently use `cnapfmriprep`.

The browser setup assistant, live terminal progress, portable execution
profiles, and interrupted-cache recovery introduced in 0.3.0 are unchanged.

## Compatibility

- Python 3.11 or 3.12
- Pydra remains pinned to 0.25.0; do not upgrade to the incompatible 1.x alpha
  line for this release.
- Existing 0.2 YAML remains valid. `execution.profile` is optional; without it,
  existing explicit execution fields retain their behavior.
- The legacy `seventprep` terminal command is installed as a temporary alias.
  Python imports should use `cnapfmriprep`.
- The package directory may retain an older local name when installed editable;
  `cnapfmriprep version` is the authoritative package version.

## New commands

```text
cnapfmriprep setup
cnapfmriprep profiles
cnapfmriprep recover-cache
```

See `QUICKSTART.md` for the normal setup, ingest, and preprocessing sequence.
