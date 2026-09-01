"""Publish stable, BIDS-like derivatives from one completed run graph."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from .bids import parse_entities, sidecar_for
from .errors import ValidationError
from .utils import ensure_dir, read_json, write_json


def _stem(path: Path) -> str:
    if path.name.endswith(".nii.gz"):
        return path.name[:-7]
    if path.name.endswith(".nii"):
        return path.name[:-4]
    return path.stem


def _copy(source: str | Path | None, destination: Path) -> Path | None:
    if source is None:
        return None
    source_path = Path(source)
    if not source_path.is_file():
        raise ValidationError(f"Expected derivative source does not exist: {source_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)
    return destination.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_description(root: Path, version: str) -> Path:
    path = root / "dataset_description.json"
    if not path.exists():
        write_json(
            path,
            {
                "Name": "cnapfmriprep native-resolution fMRI derivatives",
                "BIDSVersion": "1.10.1",
                "DatasetType": "derivative",
                "GeneratedBy": [
                    {
                        "Name": "cnapfmriprep",
                        "Version": version,
                        "Description": (
                            "Magnitude-only NORDIC followed by TOPUP/ANTs susceptibility "
                            "and rigid-motion correction"
                        ),
                    }
                ],
            },
        )
    return path.resolve()


def publish_run_derivatives(
    *,
    raw_bold: str,
    nordic_outputs: dict[str, Any],
    topup_outputs: dict[str, Any],
    field_outputs: dict[str, Any],
    motion_outputs: dict[str, Any],
    qc_outputs: dict[str, Any],
    derivatives_root: str,
    resolved_config: dict[str, Any],
    pipeline_version: str,
) -> dict[str, Any]:
    """Copy one run's deliverables out of the cache and record provenance."""
    root = ensure_dir(derivatives_root)
    _dataset_description(root, pipeline_version)
    source = Path(raw_bold).resolve()
    entities = parse_entities(source)
    subject = entities.get("sub")
    if not subject:
        raise ValidationError(f"Could not parse subject from raw BOLD filename: {source}")
    session = entities.get("ses")
    session_root = root / f"sub-{subject}"
    if session:
        session_root /= f"ses-{session}"
    func_dir = ensure_dir(session_root / "func")
    fmap_dir = ensure_dir(session_root / "fmap")
    figures_dir = ensure_dir(session_root / "figures")
    prefix = _stem(source).removesuffix("_bold")
    outputs: dict[str, Any] = {}
    outputs["nordic_bold"] = str(_copy(nordic_outputs["nordic_bold"], func_dir / f"{prefix}_desc-nordic_bold.nii.gz"))
    outputs["corrected_bold"] = str(_copy(motion_outputs["corrected_bold"], func_dir / f"{prefix}_desc-preproc_bold.nii.gz"))
    outputs["bold_reference"] = str(_copy(motion_outputs["bold_reference"], func_dir / f"{prefix}_desc-preproc_boldref.nii.gz"))
    outputs["brain_mask"] = str(_copy(motion_outputs["brain_mask"], func_dir / f"{prefix}_desc-brain_mask.nii.gz"))
    outputs["motion_tsv"] = str(_copy(motion_outputs["motion_tsv"], func_dir / f"{prefix}_desc-motion_timeseries.tsv"))
    outputs["displacement_tsv"] = str(_copy(motion_outputs["displacement_tsv"], func_dir / f"{prefix}_desc-displacement_timeseries.tsv"))
    outputs["nordic_no_rf"] = str(_copy(nordic_outputs["nordic_no_rf"], fmap_dir / f"{prefix}_mod-bold_desc-nordic_noRF.nii.gz"))
    optional_gfactor = _copy(nordic_outputs.get("gfactor"), fmap_dir / f"{prefix}_desc-nordicGfactor_map.nii.gz")
    if optional_gfactor:
        outputs["nordic_gfactor"] = str(optional_gfactor)
    outputs["topup_field_hz"] = str(_copy(topup_outputs["field_hz"], fmap_dir / f"{prefix}_desc-topup_fieldmap.nii.gz"))
    outputs["topup_corrected"] = str(_copy(topup_outputs["topup_corrected"], fmap_dir / f"{prefix}_desc-topup_epi.nii.gz"))
    outputs["field_warp"] = str(_copy(field_outputs["field_warp"], fmap_dir / f"{prefix}_desc-sdc_warp.nii.gz"))
    outputs["displacement_map"] = str(_copy(field_outputs["displacement_mm"], fmap_dir / f"{prefix}_desc-sdc_displacement.nii.gz"))
    outputs["voxel_shift_map"] = str(_copy(field_outputs["voxel_shift_map"], fmap_dir / f"{prefix}_desc-sdc_voxelshift.nii.gz"))
    outputs["jacobian_map"] = str(_copy(field_outputs["jacobian"], fmap_dir / f"{prefix}_desc-sdc_jacobian.nii.gz"))
    xfm_target = func_dir / f"{prefix}_desc-rigid_xfms"
    if xfm_target.exists():
        shutil.rmtree(xfm_target)
    shutil.copytree(motion_outputs["rigid_xfms_dir"], xfm_target)
    outputs["rigid_xfms_dir"] = str(xfm_target.resolve())
    source_qc_root = Path(qc_outputs["report_html"]).resolve().parent
    target_qc_root = figures_dir / f"{prefix}_desc-qc"
    if target_qc_root.exists():
        shutil.rmtree(target_qc_root)
    shutil.copytree(source_qc_root, target_qc_root)
    outputs["qc_report"] = str((target_qc_root / "report.html").resolve())
    raw_json = sidecar_for(source)
    metadata = read_json(raw_json) if raw_json.is_file() else {}
    metadata.update(
        {
            "Sources": [source.as_uri()],
            "Description": (
                "Magnitude-only NORDIC; static TOPUP field; ANTs rigid motion to a "
                "shared undistorted session reference when configured; one final combined "
                "spatial interpolation per functional frame"
            ),
            "SpatialReference": (
                "Shared native-resolution undistorted BOLD reference"
                if motion_outputs.get("reference_mode") == "shared_session_reference"
                else "Native undistorted BOLD reference created from this run"
            ),
            "MotionReferenceMode": motion_outputs.get("reference_mode"),
            "MotionReferenceSource": motion_outputs.get("reference_source_bold"),
            "PipelineName": "cnapfmriprep",
            "PipelineVersion": pipeline_version,
            "Interpolation": resolved_config["resampling"]["interpolation"],
            "JacobianModulation": resolved_config["resampling"]["jacobian_modulation"],
            "NumberOfVolumes": motion_outputs["functional_volume_count"],
        }
    )
    write_json(sidecar_for(Path(outputs["corrected_bold"])), metadata)
    provenance = {
        "pipeline": {"name": "cnapfmriprep", "version": pipeline_version},
        "source_bold": str(source),
        "resolved_config": resolved_config,
        "nordic": nordic_outputs,
        "topup": topup_outputs,
        "field": field_outputs,
        "motion": motion_outputs,
        "qc": qc_outputs,
    }
    provenance_path = write_json(func_dir / f"{prefix}_desc-provenance.json", provenance)
    outputs["provenance"] = str(provenance_path)
    manifest_files: list[Path] = []
    for value in outputs.values():
        path = Path(value)
        if path.is_file():
            manifest_files.append(path)
        elif path.is_dir():
            manifest_files.extend(candidate for candidate in path.rglob("*") if candidate.is_file())
    manifest = {
        "files": [
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in sorted(set(manifest_files))
        ]
    }
    manifest_path = write_json(func_dir / f"{prefix}_desc-manifest.json", manifest)
    outputs["manifest"] = str(manifest_path)
    return outputs
