# SevenTPrep 0.3.0 quick start

Version 0.3.0 supports variable session run names/counts, one shared TOPUP
estimate, one shared robust motion reference, live terminal progress, execution
profiles, and safe recovery from interrupted Pydra cache entries.

```bash
python -m pip uninstall -y seventprep
unzip seventprep-0.3.0-source.zip
cd seventprep-0.3.0
conda env create -f environment.yml
conda activate seventprep
python -m pip install -e .
seventprep version
seventprep doctor
```

Create the study configuration with the local browser assistant:

```bash
seventprep setup /absolute/path/experiment.zip \
  --template config/example_study.yaml \
  --output config/my_study.yaml \
  --work-dir work/setup
```

Review every suggested series role, select exactly one normal/AP and one
reversed/PA series, choose the motion-reference BOLD run, verify phase encoding,
and confirm. The page runs only on this Mac and writes exact series-UID rules.

If an inventory already exists, use it directly:

```bash
seventprep setup \
  --inventory work/inventory/dicom_series.tsv \
  --template config/example_study.yaml \
  --output config/my_study.yaml
```

The equivalent variable-count BOLD rule in the generated YAML is:

```yaml
- name: retinotopy_magnitude_runs
  kind: bold_with_norf
  match:
    SeriesDescription: "(?i)retino.*nordic"
  task: retinotopy
  acquisition: hires7T
  run: auto
  run_start: 1
  run_sort_by: SeriesNumber
  b0_identifier: pepolar_session01
  expected_matches: 10       # generated from this session's selection
```

Use `b0_identifier: pepolar_session01` on both AP and PA rules as well. If
dcm2niix omits `PhaseEncodingDirection` for a custom EPI sequence, add
`phase_encoding_direction` to the BOLD and fieldmap rules. Use BIDS NIfTI-axis
values such as `j-` and `j`, not the AP/PA filename labels. Enable session
sharing:

```yaml
multi_run:
  shared_topup: true
  shared_motion_reference: true
  reference_task: retinotopy
  reference_run: 1
```

Test the generated mappings and inspect `assigned_run`:

```bash
seventprep inventory /absolute/path/experiment.zip \
  --output-dir work/inventory-matched \
  --config config/my_study.yaml

column -t -s $'\t' work/inventory-matched/series_match_plan.tsv | less -S
```

Then ingest and preprocess:

```bash
seventprep ingest /absolute/path/experiment.zip ./bids \
  --config config/my_study.yaml \
  --subject 001 --session 01 \
  --work-dir work/ingest-sub001-ses01

seventprep preprocess ./bids ./derivatives/seventprep \
  --config config/my_study.yaml \
  --subject 001 --session 01 \
  --work-dir work/preprocess-sub001-ses01
```

The default example uses the memory-conservative laptop profile. On a larger
machine, inspect and select a bounded parallel profile:

```bash
seventprep profiles

seventprep preprocess ./bids ./derivatives/seventprep \
  --config config/my_study.yaml \
  --subject 001 --session 01 \
  --work-dir work/preprocess-sub001-ses01 \
  --execution-profile workstation
```

Progress appears in the terminal and is saved in
`work/preprocess-sub001-ses01/progress.jsonl`.

After a crash or forced stop, rerun the same command with the same work
directory. SevenTPrep automatically quarantines only invalid empty Pydra
results and preserves completed work. Manual recovery is also available:

```bash
seventprep recover-cache --work-dir work/preprocess-sub001-ses01
```

The extractor also accepts `.tgz`, `.tar.gz`, and `.tar` archives.
