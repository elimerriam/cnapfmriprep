# SevenTPrep 0.3.0

This release adds a local browser setup assistant, live terminal progress,
portable execution profiles, and automatic recovery from interrupted Pydra
cache records.

## Compatibility

- Python 3.11 or 3.12
- Pydra remains pinned to 0.25.0; do not upgrade to the incompatible 1.x alpha
  line for this release.
- Existing 0.2 YAML remains valid. `execution.profile` is optional; without it,
  existing explicit execution fields retain their behavior.
- The package directory may retain an older local name when installed editable;
  `seventprep version` is the authoritative package version.

## New commands

```text
seventprep setup
seventprep profiles
seventprep recover-cache
```

See `QUICKSTART.md` for the normal setup, ingest, and preprocessing sequence.
