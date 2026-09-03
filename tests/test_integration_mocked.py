from __future__ import annotations

import json
import os
from pathlib import Path

import nibabel as nb

from cnapfmriprep.config import load_config
from cnapfmriprep.preprocess import preprocess_dataset


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def test_mocked_external_tools_complete_shared_multirun_graph(
    tmp_path: Path, synthetic_bids_multi_run: Path, monkeypatch
) -> None:
    python = os.environ.get("VIRTUAL_ENV")
    if python:
        python = str(Path(python) / "bin" / "python")
    else:
        python = os.sys.executable

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_nordic = tmp_path / "NORDIC_Raw"
    fake_nordic.mkdir()
    (fake_nordic / "NIFTI_NORDIC.m").write_text("% fake for integration test\n")
    topup_counter = tmp_path / "topup-call-count.txt"

    _write_executable(
        fake_bin / "matlab",
        f"""#!{python}
import json, re, shutil, sys
from pathlib import Path
import nibabel as nb
import numpy as np
expr = sys.argv[-1]
match = re.search(r"run_nordic_job\\('([^']+)'\\)", expr)
if not match:
    raise SystemExit('job path not found')
job = json.loads(Path(match.group(1)).read_text())
out = Path(job['output_directory'])
out.mkdir(parents=True, exist_ok=True)
source = Path(job['magnitude_file'])
shutil.copy2(source, out / (job['output_prefix'] + '.nii.gz'))
img = nb.load(source)
nb.Nifti1Image(np.ones(img.shape[:3], dtype='float32'), img.affine).to_filename(
    out / ('gfactor_' + job['output_prefix'] + '.nii.gz')
)
(out / (job['output_prefix'] + '.mat')).write_bytes(b'MATLAB 5.0 MAT-file mock')
""",
    )

    _write_executable(
        fake_bin / "topup",
        f"""#!{python}
import os, shutil, sys
from pathlib import Path
import nibabel as nb
import numpy as np
counter = Path(os.environ['CNAPFMRIPREP_TEST_TOPUP_COUNTER'])
count = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(count + 1))
args = {{item.split('=', 1)[0]: item.split('=', 1)[1] for item in sys.argv[1:] if '=' in item}}
imain = Path(args['--imain'])
out_prefix = Path(args['--out'])
fout = Path(args['--fout'])
iout = Path(args['--iout'])
img = nb.load(imain)
nb.Nifti1Image(np.zeros(img.shape[:3], dtype='float32'), img.affine).to_filename(str(fout) + '.nii.gz')
shutil.copy2(imain, str(iout) + '.nii.gz')
nb.Nifti1Image(np.zeros(img.shape[:3], dtype='float32'), img.affine).to_filename(
    str(out_prefix) + '_fieldcoef.nii.gz'
)
Path(str(out_prefix) + '_movpar.txt').write_text('0 0 0 0 0 0\\n' * img.shape[3])
""",
    )

    _write_executable(
        fake_bin / "antsApplyTransforms",
        f"""#!{python}
import shutil, sys
from pathlib import Path
args = sys.argv[1:]
source = Path(args[args.index('-i') + 1])
out = Path(args[args.index('-o') + 1])
out.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(source, out)
""",
    )

    _write_executable(
        fake_bin / "antsRegistration",
        f"""#!{python}
import re, shutil, sys
from pathlib import Path
args = sys.argv[1:]
output = args[args.index('--output') + 1]
match = re.match(r'\\[(.*?),(.*?)\\]$', output)
if not match:
    raise SystemExit('output spec not found')
prefix = Path(match.group(1))
warped = Path(match.group(2))
prefix.parent.mkdir(parents=True, exist_ok=True)
affine = Path(str(prefix) + '0GenericAffine.mat')
affine.write_text('''#Insight Transform File V1.0
# Transform 0
Transform: MatrixOffsetTransformBase_double_3_3
Parameters: 1 0 0 0 1 0 0 0 1 0 0 0
FixedParameters: 0 0 0
''')
metric = args[args.index('--metric') + 1]
parts = metric[metric.index('[') + 1:metric.index(']')].split(',')
moving = Path(parts[1])
shutil.copy2(moving, warped)
""",
    )

    fsldir = tmp_path / "fsl"
    config_dir = fsldir / "etc" / "flirtsch"
    config_dir.mkdir(parents=True)
    for suffix in ("1", "2", "4"):
        (config_dir / f"b02b0_{suffix}.cnf").write_text("# mock\n")

    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FSLDIR", str(fsldir))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib-cache"))
    monkeypatch.setenv("PYDRA_HASH_CACHE", str(tmp_path / "pydra-hash-cache"))
    monkeypatch.setenv("CNAPFMRIPREP_TEST_TOPUP_COUNTER", str(topup_counter))

    root = Path(__file__).parents[1]
    config = load_config(root / "config" / "example_study.yaml")
    config.multi_run.reference_task = "demo"
    config.multi_run.reference_run = 1
    config.nordic.checkout = fake_nordic
    config.nordic.matlab_command = str(fake_bin / "matlab")
    config.execution.pydra_plugin = "serial"
    config.execution.n_procs = 1
    config.execution.volume_workers = 1
    config.execution.threads_per_ants = 1

    result = preprocess_dataset(
        synthetic_bids_multi_run,
        tmp_path / "derivatives",
        config=config,
        subject="001",
        session="01",
        work_dir=tmp_path / "work",
    )

    assert result["shared_topup"] is True
    assert result["shared_motion_reference"] is True
    assert result["workflow_run_count"] == 3
    assert len(result["runs"]) == 3
    assert topup_counter.read_text() == "1"

    references: list[bytes] = []
    reference_sources: list[str] = []
    for run_result in result["runs"]:
        derivatives = run_result["derivatives"]
        corrected = Path(derivatives["corrected_bold"])
        assert corrected.is_file()
        assert nb.load(corrected).shape == (8, 9, 6, 7)
        assert Path(derivatives["qc_report"]).is_file()
        assert nb.load(derivatives["nordic_no_rf"]).shape[3] == 2
        references.append(Path(derivatives["bold_reference"]).read_bytes())

        sidecar = corrected.with_name(corrected.name[:-7] + ".json")
        metadata = json.loads(sidecar.read_text())
        reference_sources.append(metadata["MotionReferenceSource"])

    assert references[0] == references[1] == references[2]
    assert reference_sources[0] == reference_sources[1] == reference_sources[2]
    assert reference_sources[0].endswith("run-000/nordic/desc-nordic_bold.nii.gz")

    resumed = preprocess_dataset(
        synthetic_bids_multi_run,
        tmp_path / "derivatives",
        config=config,
        subject="001",
        session="01",
        work_dir=tmp_path / "work",
    )
    assert topup_counter.read_text() == "1"
    assert len(resumed["cache_usage"]["reused"]) == 13
    assert resumed["cache_usage"]["recomputed"] == []
    manifest = json.loads((tmp_path / "work" / "job.json").read_text())
    assert manifest["attempt"] == 2
    assert manifest["state"] == "completed"
