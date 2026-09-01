# Migrating a SevenTPrep 0.1.x configuration to 0.2.0

## Replace one-run BOLD rules with one automatic multi-run rule

```yaml
- name: retinotopy_magnitude_runs
  kind: bold_with_norf
  match:
    SeriesDescription: '(?i)YOUR_SHARED_BOLD_PHRASE'
  task: retinotopy
  acquisition: hires7T
  run: auto
  run_start: 1
  run_sort_by: SeriesNumber
  b0_identifier: pepolar_session01
  expected_matches: one_or_more
```

An exact integer is also accepted, such as `expected_matches: 10`. Use the
integer when a session is required to contain exactly ten runs; use
`one_or_more` when the count may vary.

Do not set `expected_matches` above one while retaining `run: 1`. Version 0.2.0
rejects that combination because all matched series would otherwise target the
same BIDS filename.

## Give the shared AP/PA pair the same identifier

Use the same value in the BOLD rule and both fieldmap rules:

```yaml
b0_identifier: pepolar_session01
```

The AP and PA rules should each retain:

```yaml
expected_matches: 1
```

## Add the multi-run section

```yaml
multi_run:
  shared_topup: true
  shared_motion_reference: true
  reference_task: retinotopy
  reference_run: 1
```

The explicit task/run pair selects the robust reference source. Setting both
fields to `null` selects the first run among the requested targets, but explicit
values are safer when the session contains several tasks.

## Recheck automatic run assignment

```bash
seventprep inventory XNAT_export.zip \
  --output-dir work/inventory-matched \
  --config config/my_study.yaml
```

Inspect `series_match_plan.tsv`. Confirm that `assigned_run` follows acquisition
order before running ingestion. Then preprocess the session without `--run` so
all selected runs are included in the shared graph.
