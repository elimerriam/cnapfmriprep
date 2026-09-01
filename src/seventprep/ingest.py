"""XNAT archive inventory and study-configured DICOM-to-BIDS ingestion."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

import nibabel as nb
import numpy as np

from .archive import safe_extract_archive
from .bids import run_official_validator, semantic_validate, sidecar_for
from .config import SeriesRule, StudyConfig
from .dicom import inventory_dicom_series, match_series
from .errors import ValidationError
from .utils import ensure_dir, read_json, require_executable, run_command, write_json

_LABEL = re.compile(r"^[A-Za-z0-9]+$")


def _check_label(value: str, name: str) -> str:
    value = value.removeprefix(f"{name}-")
    if not _LABEL.fullmatch(value):
        raise ValidationError(
            f"Invalid BIDS {name} label {value!r}; labels must contain only letters and digits"
        )
    return value


def _row_for_tsv(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    output["Files"] = json.dumps(output.get("Files", []))
    return output


def _write_tsv(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in materialized:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in materialized:
            writer.writerow(_row_for_tsv(row))
    return path.resolve()


def _write_match_plan(path: Path, plan: list[dict[str, Any]]) -> Path:
    rows = []
    for entry in plan:
        rule = entry["rule"]
        series = entry["series"]
        rows.append(
            {
                "rule_name": rule["name"],
                "kind": rule["kind"],
                "match_index": entry["match_index"],
                "assigned_run": entry.get("assigned_run", ""),
                "SeriesNumber": series.get("SeriesNumber", ""),
                "SeriesDescription": series.get("SeriesDescription", ""),
                "ProtocolName": series.get("ProtocolName", ""),
                "SeriesInstanceUID": series.get("SeriesInstanceUID", ""),
                "NumberOfFiles": series.get("NumberOfFiles", ""),
            }
        )
    return _write_tsv(path, rows)


def inventory_archive(
    archive: str | Path,
    output_dir: str | Path,
    *,
    config: StudyConfig | None = None,
) -> dict[str, Any]:
    """Safely extract an archive and produce a DICOM series inventory."""
    root = ensure_dir(output_dir)
    extracted = root / "extracted"
    if extracted.exists():
        shutil.rmtree(extracted)
    extraction = safe_extract_archive(archive, extracted)
    rows = inventory_dicom_series(extracted)
    inventory_tsv = _write_tsv(root / "dicom_series.tsv", rows)
    result: dict[str, Any] = {
        "extraction": extraction,
        "series_count": len(rows),
        "inventory_tsv": str(inventory_tsv),
    }
    if config is not None:
        plan = match_series(rows, config.ingest.series_rules)
        plan_tsv = _write_match_plan(root / "series_match_plan.tsv", plan)
        result["matched_series"] = len(plan)
        result["match_plan_tsv"] = str(plan_tsv)
    write_json(root / "inventory_result.json", result)
    return result


def _stage_series_files(series: dict[str, Any], destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    files = [Path(path) for path in series.get("Files", [])]
    if not files:
        raise ValidationError(
            f"DICOM inventory entry has no files: {series.get('SeriesInstanceUID', '')}"
        )
    for index, source in enumerate(files):
        target = destination / f"{index:08d}_{source.name}"
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
    return destination


def _convert_one_series(
    entry: dict[str, Any],
    root: Path,
    *,
    compression: str,
) -> tuple[Path, Path]:
    rule_name = str(entry["rule_name"])
    match_index = int(entry.get("match_index", 0))
    series_key = f"{rule_name}_match-{match_index:03d}"
    source_target = root / "dicoms" / series_key
    converted_target = root / "converted" / series_key
    if source_target.exists():
        shutil.rmtree(source_target)
    if converted_target.exists():
        shutil.rmtree(converted_target)
    source_dir = _stage_series_files(entry["series"], source_target)
    output_dir = ensure_dir(converted_target)
    dcm2niix = require_executable("dcm2niix")
    run_command(
        [
            dcm2niix,
            "-b", "y",
            "-ba", "y",
            "-z", compression,
            "-f", "converted",
            "-o", str(output_dir),
            str(source_dir),
        ],
        log_file=output_dir / "dcm2niix.log",
    )
    nifti_files = sorted(output_dir.glob("*.nii.gz")) + sorted(output_dir.glob("*.nii"))
    unique: dict[str, Path] = {}
    for path in nifti_files:
        key = path.name.removesuffix(".gz").removesuffix(".nii")
        unique.setdefault(key, path)
    nifti_files = list(unique.values())
    if len(nifti_files) != 1:
        raise ValidationError(
            f"Rule {rule_name!r} produced {len(nifti_files)} NIfTI files. "
            f"Refine the DICOM rule so one intended series/component is converted: {nifti_files}"
        )
    nifti = nifti_files[0].resolve()
    sidecar = sidecar_for(nifti)
    if not sidecar.is_file():
        raise ValidationError(f"dcm2niix did not create a JSON sidecar for {nifti}")
    return nifti, sidecar.resolve()


def _entity_prefix(subject: str, session: str | None) -> str:
    return f"sub-{subject}" + (f"_ses-{session}" if session else "")


def _run_entity(run: int | None) -> str:
    return f"_run-{run:02d}" if run is not None else ""


def _acq_entity(acquisition: str | None) -> str:
    return f"_acq-{_check_label(acquisition, 'acq')}" if acquisition else ""


def _save_image(data: np.ndarray, source: nb.spatialimages.SpatialImage, path: Path) -> Path:
    header = source.header.copy()
    header.set_data_shape(data.shape)
    nb.Nifti1Image(data, source.affine, header).to_filename(path)
    return path.resolve()


def _split_bold_and_noise(
    converted: Path,
    bold_target: Path,
    noise_target: Path,
    *,
    noise_volumes: int,
) -> tuple[Path, Path]:
    image = nb.load(str(converted))
    if len(image.shape) != 4 or image.shape[3] <= noise_volumes:
        raise ValidationError(
            f"Expected a 4D BOLD acquisition with more than {noise_volumes} frames: "
            f"{converted} has shape {image.shape}"
        )
    signal = np.asanyarray(image.dataobj[..., :-noise_volumes])
    noise = np.asanyarray(image.dataobj[..., -noise_volumes:])
    bold_target.parent.mkdir(parents=True, exist_ok=True)
    noise_target.parent.mkdir(parents=True, exist_ok=True)
    return _save_image(signal, image, bold_target), _save_image(noise, image, noise_target)


def _copy_nifti_and_json(
    image: Path,
    sidecar: Path,
    target: Path,
    metadata_updates: dict[str, Any],
    *,
    rule: SeriesRule | None = None,
) -> tuple[Path, Path]:
    target.parent.mkdir(parents=True, exist_ok=True)
    converted_image = nb.load(str(image))
    converted_image.to_filename(target)
    output_sidecar = sidecar_for(target)
    metadata = read_json(sidecar)
    if rule is not None:
        _apply_phase_encoding_direction(metadata, rule, sidecar=sidecar)
    metadata.update(metadata_updates)
    write_json(output_sidecar, metadata)
    return target.resolve(), output_sidecar.resolve()


def _apply_phase_encoding_direction(
    metadata: dict[str, Any],
    rule: SeriesRule,
    *,
    sidecar: Path,
) -> None:
    """Fill missing PE metadata from a rule without hiding a real conflict."""
    configured = rule.phase_encoding_direction
    if configured is None:
        return
    converted = metadata.get("PhaseEncodingDirection")
    if converted not in (None, "") and str(converted) != configured:
        raise ValidationError(
            f"Rule {rule.name!r} configures PhaseEncodingDirection={configured!r}, "
            f"but dcm2niix wrote {converted!r} in {sidecar}"
        )
    metadata["PhaseEncodingDirection"] = configured


def _bids_session_root(staging: Path, subject: str, session: str | None) -> Path:
    subject_root = staging / f"sub-{subject}"
    return subject_root / f"ses-{session}" if session else subject_root


def _convert_plan_to_bids(
    plan: list[dict[str, Any]],
    staging: Path,
    conversion_root: Path,
    *,
    config: StudyConfig,
    subject: str,
    session: str | None,
) -> list[dict[str, Any]]:
    session_root = _bids_session_root(staging, subject, session)
    prefix = _entity_prefix(subject, session)
    products: list[dict[str, Any]] = []
    for entry in plan:
        rule = SeriesRule.model_validate(entry["rule"])
        assigned_run = entry.get("assigned_run")
        if assigned_run == "auto":
            raise ValidationError(f"Internal error: unresolved automatic run in rule {rule.name}")
        converted, converted_json = _convert_one_series(
            entry, conversion_root, compression=config.ingest.dcm2niix_compression
        )
        if rule.kind == "bold_with_norf":
            task = _check_label(rule.task or "", "task")
            acq = _acq_entity(rule.acquisition)
            run_entity = _run_entity(assigned_run)
            bold_name = f"{prefix}_task-{task}{acq}{run_entity}_bold.nii.gz"
            noise_acq = _check_label(rule.acquisition or task, "acq")
            noise_name = f"{prefix}_acq-{noise_acq}{run_entity}_mod-bold_noRF.nii.gz"
            bold_target = session_root / "func" / bold_name
            noise_target = session_root / config.ingest.no_rf_datatype / noise_name
            bold_path, noise_path = _split_bold_and_noise(
                converted,
                bold_target,
                noise_target,
                noise_volumes=config.ingest.trailing_no_rf_volumes,
            )
            metadata = read_json(converted_json)
            _apply_phase_encoding_direction(metadata, rule, sidecar=converted_json)
            metadata.update(
                {
                    "TaskName": task,
                    "B0FieldSource": rule.b0_identifier,
                    "NORDICNoiseFile": noise_path.relative_to(staging).as_posix(),
                }
            )
            write_json(sidecar_for(bold_path), metadata)
            noise_metadata = dict(metadata)
            for key in ("TaskName", "B0FieldSource", "NORDICNoiseFile"):
                noise_metadata.pop(key, None)
            noise_metadata["Modality"] = "bold"
            noise_metadata["NumberOfVolumes"] = config.ingest.trailing_no_rf_volumes
            write_json(sidecar_for(noise_path), noise_metadata)
            products.append(
                {
                    "rule": rule.name,
                    "kind": rule.kind,
                    "run": assigned_run,
                    "bold": str(bold_path),
                    "noise": str(noise_path),
                }
            )
        elif rule.kind == "fmap_epi":
            direction = _check_label(rule.direction or "", "dir")
            name = f"{prefix}{_acq_entity(rule.acquisition)}_dir-{direction}{_run_entity(assigned_run)}_epi.nii.gz"
            target = session_root / "fmap" / name
            image, sidecar = _copy_nifti_and_json(
                converted,
                converted_json,
                target,
                {"B0FieldIdentifier": rule.b0_identifier},
                rule=rule,
            )
            products.append({"rule": rule.name, "kind": rule.kind, "image": str(image), "sidecar": str(sidecar)})
        elif rule.kind == "anat":
            suffix = rule.suffix or "T1w"
            if not re.fullmatch(r"[A-Za-z0-9]+", suffix):
                raise ValidationError(f"Invalid anatomical suffix in rule {rule.name}: {suffix}")
            target = session_root / "anat" / f"{prefix}{_acq_entity(rule.acquisition)}{_run_entity(assigned_run)}_{suffix}.nii.gz"
            image, sidecar = _copy_nifti_and_json(converted, converted_json, target, {})
            products.append({"rule": rule.name, "kind": rule.kind, "image": str(image), "sidecar": str(sidecar)})
    return products


def _publish_staging(
    staging: Path,
    bids_root: Path,
    *,
    subject: str,
    session: str | None,
    overwrite: bool,
) -> Path:
    bids_root.mkdir(parents=True, exist_ok=True)
    destination_description = bids_root / "dataset_description.json"
    if not destination_description.exists():
        shutil.copy2(staging / "dataset_description.json", destination_description)
    source_subject = staging / f"sub-{subject}"
    destination_subject = bids_root / f"sub-{subject}"
    if session:
        source = source_subject / f"ses-{session}"
        destination_subject.mkdir(parents=True, exist_ok=True)
        destination = destination_subject / f"ses-{session}"
    else:
        source = source_subject
        destination = destination_subject
    if destination.exists():
        if not overwrite:
            raise ValidationError(
                f"BIDS destination already exists: {destination}. Use --overwrite deliberately."
            )
        shutil.rmtree(destination)
    temporary = destination.with_name(f".{destination.name}.incoming-{os.getpid()}")
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(source, temporary)
    temporary.replace(destination)
    return destination.resolve()


def ingest_archive(
    archive: str | Path,
    bids_dir: str | Path,
    *,
    config: StudyConfig,
    subject: str,
    session: str | None,
    work_dir: str | Path,
    run_validator: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Convert a configured XNAT archive in staging and publish only after validation."""
    subject = _check_label(subject, "sub")
    session = _check_label(session, "ses") if session else None
    work = ensure_dir(work_dir)
    extracted = work / "extracted"
    if extracted.exists():
        shutil.rmtree(extracted)
    extraction = safe_extract_archive(archive, extracted)
    series = inventory_dicom_series(extracted)
    inventory_tsv = _write_tsv(work / "dicom_series.tsv", series)
    plan = match_series(series, config.ingest.series_rules)
    plan_tsv = _write_match_plan(work / "series_match_plan.tsv", plan)
    staging = work / "bids-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    write_json(
        staging / "dataset_description.json",
        {
            "Name": config.ingest.dataset_name,
            "BIDSVersion": "1.10.1",
            "DatasetType": "raw",
            "GeneratedBy": [{"Name": "seventprep", "Description": "DICOM-to-BIDS staging"}],
        },
    )
    products = _convert_plan_to_bids(
        plan,
        staging,
        work / "conversion",
        config=config,
        subject=subject,
        session=session,
    )
    semantic_staging = semantic_validate(
        staging,
        subject=subject,
        session=session,
        expected_no_rf_volumes=config.ingest.trailing_no_rf_volumes,
    )
    validator_staging = (
        run_official_validator(staging, output_json=work / "bids-validator-staging.json", required=True)
        if run_validator else None
    )
    destination = _publish_staging(
        staging,
        Path(bids_dir).expanduser().resolve(),
        subject=subject,
        session=session,
        overwrite=overwrite,
    )
    semantic_final = semantic_validate(
        bids_dir,
        subject=subject,
        session=session,
        expected_no_rf_volumes=config.ingest.trailing_no_rf_volumes,
    )
    validator_final = (
        run_official_validator(bids_dir, output_json=work / "bids-validator-final.json", required=True)
        if run_validator else None
    )
    if not config.ingest.retain_extracted_dicoms:
        shutil.rmtree(extracted, ignore_errors=True)
    result = {
        "archive": extraction,
        "inventory_tsv": str(inventory_tsv),
        "match_plan_tsv": str(plan_tsv),
        "products": products,
        "staging_semantic_validation": semantic_staging,
        "final_semantic_validation": semantic_final,
        "staging_official_validator": str(validator_staging) if validator_staging else None,
        "final_official_validator": str(validator_final) if validator_final else None,
        "published": str(destination),
    }
    write_json(work / "ingest_result.json", result)
    return result
