"""BIDS discovery, official validation, and pipeline-specific semantic checks."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import nibabel as nb

from .errors import ExternalCommandError, ValidationError
from .utils import assert_same_nifti_grid, read_json, write_json

_ENTITY_PATTERN = re.compile(r"(?:^|_)(sub|ses|task|acq|run|dir|echo|part)-([^_]+)")
_VALID_PE = {"i", "i-", "j", "j-", "k", "k-"}


def _normalize_entity(value: str | None, prefix: str) -> str | None:
    if value is None:
        return None
    return value[len(prefix) + 1 :] if value.startswith(prefix + "-") else value


def _strip_nifti_suffix(path: Path) -> str:
    if path.name.endswith(".nii.gz"):
        return path.name[:-7]
    if path.name.endswith(".nii"):
        return path.name[:-4]
    return path.stem


def sidecar_for(path: str | Path) -> Path:
    source = Path(path)
    return source.with_name(_strip_nifti_suffix(source) + ".json")


def parse_entities(path: str | Path) -> dict[str, str]:
    return {key: value for key, value in _ENTITY_PATTERN.findall(_strip_nifti_suffix(Path(path)))}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _nifti_files(directory: Path, suffix: str) -> list[Path]:
    return sorted(list(directory.glob(f"*_{suffix}.nii.gz")) + list(directory.glob(f"*_{suffix}.nii")))


def _session_root(bids_root: Path, subject: str, session: str | None) -> Path:
    path = bids_root / f"sub-{subject}"
    return path / f"ses-{session}" if session else path


def _resolve_noise_file(bids_root: Path, bold_file: Path, metadata: dict[str, Any]) -> Path:
    declared = metadata.get("NORDICNoiseFile")
    if declared:
        candidate = (bids_root / str(declared)).resolve()
        try:
            candidate.relative_to(bids_root.resolve())
        except ValueError as error:
            raise ValidationError(f"NORDICNoiseFile escapes the BIDS dataset in {sidecar_for(bold_file)}") from error
        if not candidate.is_file():
            raise ValidationError(f"NORDICNoiseFile does not exist: {candidate}")
        return candidate
    entities = parse_entities(bold_file)
    session_root = bold_file.parents[1]
    candidates: list[Path] = []
    for datatype in ("fmap", "func"):
        directory = session_root / datatype
        if not directory.exists():
            continue
        for candidate in _nifti_files(directory, "noRF"):
            candidate_entities = parse_entities(candidate)
            if entities.get("run") and candidate_entities.get("run") != entities.get("run"):
                continue
            candidates.append(candidate)
    if len(candidates) != 1:
        raise ValidationError(
            f"Could not resolve exactly one no-RF file for {bold_file}; found {candidates}. "
            "Set NORDICNoiseFile in the BOLD JSON sidecar."
        )
    return candidates[0].resolve()


def _resolve_fieldmaps(
    bids_root: Path,
    session_root: Path,
    bold_file: Path,
    metadata: dict[str, Any],
) -> tuple[list[Path], list[Path]]:
    identifiers = set(_as_list(metadata.get("B0FieldSource")))
    fmap_dir = session_root / "fmap"
    candidates = _nifti_files(fmap_dir, "epi") if fmap_dir.exists() else []
    selected: list[Path] = []
    selected_json: list[Path] = []
    intended_candidates = {
        bold_file.relative_to(bids_root).as_posix(),
        bold_file.relative_to(session_root).as_posix(),
    }
    for candidate in candidates:
        sidecar = sidecar_for(candidate)
        if not sidecar.is_file():
            continue
        fmap_metadata = read_json(sidecar)
        fmap_identifiers = set(_as_list(fmap_metadata.get("B0FieldIdentifier")))
        intended_for = set(_as_list(fmap_metadata.get("IntendedFor")))
        if identifiers and identifiers.intersection(fmap_identifiers):
            selected.append(candidate.resolve())
            selected_json.append(sidecar.resolve())
        elif not identifiers and intended_candidates.intersection(intended_for):
            selected.append(candidate.resolve())
            selected_json.append(sidecar.resolve())
    if not selected and not identifiers and len(candidates) == 2:
        selected = [path.resolve() for path in candidates]
        selected_json = [sidecar_for(path).resolve() for path in candidates]
    return selected, selected_json


def _run_number(entities: dict[str, str]) -> int:
    try:
        return int(entities.get("run", "0"))
    except (TypeError, ValueError):
        return 0


def bold_run_sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    """Stable ordering for selecting a session reference run."""
    entities = record.get("entities", {})
    return (
        str(entities.get("task", "")),
        str(entities.get("acq", "")),
        _run_number(entities),
        str(record.get("bold", "")),
    )


def discover_bold_runs(
    bids_dir: str | Path,
    *,
    subject: str,
    session: str | None = None,
    task: str | None = None,
    run: int | None = None,
) -> list[dict[str, Any]]:
    """Discover BOLD/no-RF/reverse-PE groups without requiring PyBIDS."""
    root = Path(bids_dir).expanduser().resolve()
    subject = _normalize_entity(subject, "sub") or ""
    session = _normalize_entity(session, "ses")
    session_root = _session_root(root, subject, session)
    func_dir = session_root / "func"
    if not func_dir.is_dir():
        raise ValidationError(f"Functional directory does not exist: {func_dir}")
    output: list[dict[str, Any]] = []
    for bold in _nifti_files(func_dir, "bold"):
        entities = parse_entities(bold)
        if task is not None and entities.get("task") != task:
            continue
        if run is not None:
            if _run_number(entities) != run:
                continue
        bold_json = sidecar_for(bold)
        if not bold_json.is_file():
            raise ValidationError(f"Missing BOLD sidecar: {bold_json}")
        metadata = read_json(bold_json)
        noise = _resolve_noise_file(root, bold, metadata)
        fieldmaps, fieldmap_jsons = _resolve_fieldmaps(root, session_root, bold, metadata)
        output.append(
            {
                "bold": bold.resolve(),
                "bold_json": bold_json.resolve(),
                "noise": noise,
                "noise_json": sidecar_for(noise).resolve(),
                "fieldmaps": fieldmaps,
                "fieldmap_jsons": fieldmap_jsons,
                "entities": entities,
                "metadata": metadata,
            }
        )
    if not output:
        raise ValidationError(
            f"No matching BOLD runs found for subject={subject!r}, session={session!r}, task={task!r}, run={run!r}"
        )
    return sorted(output, key=bold_run_sort_key)


def _validate_pe_metadata(metadata: dict[str, Any], sidecar: Path) -> tuple[str, float]:
    direction = str(metadata.get("PhaseEncodingDirection", ""))
    if direction not in _VALID_PE:
        raise ValidationError(f"Invalid or missing PhaseEncodingDirection in {sidecar}")
    try:
        readout = float(metadata.get("TotalReadoutTime", 0))
    except (TypeError, ValueError) as error:
        raise ValidationError(f"Invalid TotalReadoutTime in {sidecar}") from error
    if readout <= 0:
        raise ValidationError(f"Missing or non-positive TotalReadoutTime in {sidecar}")
    return direction, readout


def semantic_validate(
    bids_dir: str | Path,
    *,
    subject: str | None = None,
    session: str | None = None,
    expected_no_rf_volumes: int = 2,
) -> dict[str, Any]:
    """Validate relationships required by this pipeline beyond BIDS syntax."""
    root = Path(bids_dir).expanduser().resolve()
    description = root / "dataset_description.json"
    if not description.is_file():
        raise ValidationError(f"Missing dataset_description.json in {root}")
    read_json(description)
    subjects = (
        [_normalize_entity(subject, "sub") or ""]
        if subject is not None
        else [path.name[4:] for path in sorted(root.glob("sub-*")) if path.is_dir()]
    )
    if not subjects:
        raise ValidationError(f"No sub-* directories found in {root}")
    report_runs: list[dict[str, Any]] = []
    for subject_label in subjects:
        subject_root = root / f"sub-{subject_label}"
        sessions: Iterable[str | None]
        if session is not None:
            sessions = [_normalize_entity(session, "ses")]
        else:
            session_dirs = [path for path in sorted(subject_root.glob("ses-*")) if path.is_dir()]
            sessions = [path.name[4:] for path in session_dirs] if session_dirs else [None]
        for session_label in sessions:
            try:
                runs = discover_bold_runs(root, subject=subject_label, session=session_label)
            except ValidationError as error:
                if "No matching BOLD runs" in str(error):
                    continue
                raise
            for run_record in runs:
                bold = Path(run_record["bold"])
                bold_image = nb.load(str(bold))
                if len(bold_image.shape) != 4 or bold_image.shape[3] < 1:
                    raise ValidationError(f"BOLD image must be non-empty and 4D: {bold}")
                bold_pe, bold_readout = _validate_pe_metadata(
                    run_record["metadata"], Path(run_record["bold_json"])
                )
                try:
                    repetition_time = float(run_record["metadata"].get("RepetitionTime", 0))
                except (TypeError, ValueError):
                    repetition_time = 0
                if repetition_time <= 0:
                    raise ValidationError(f"Missing or invalid RepetitionTime in {run_record['bold_json']}")
                noise = Path(run_record["noise"])
                noise_image = nb.load(str(noise))
                if len(noise_image.shape) != 4 or noise_image.shape[3] != expected_no_rf_volumes:
                    raise ValidationError(
                        f"Expected {expected_no_rf_volumes} no-RF volumes in {noise}, found shape {noise_image.shape}"
                    )
                assert_same_nifti_grid(bold, noise, context="BOLD and no-RF images")
                fieldmaps = [Path(path) for path in run_record["fieldmaps"]]
                fieldmap_jsons = [Path(path) for path in run_record["fieldmap_jsons"]]
                if len(fieldmaps) < 2 or len(fieldmaps) != len(fieldmap_jsons):
                    raise ValidationError(f"BOLD run {bold} must resolve to at least two reverse-PE EPI images")
                directions: list[str] = []
                readouts: list[float] = []
                for fmap, fmap_json in zip(fieldmaps, fieldmap_jsons, strict=True):
                    assert_same_nifti_grid(bold, fmap, context="BOLD and reverse-PE images")
                    direction, readout = _validate_pe_metadata(read_json(fmap_json), fmap_json)
                    directions.append(direction)
                    readouts.append(readout)
                opposite = any(
                    first[0] == second[0] and first.endswith("-") != second.endswith("-")
                    for first in directions for second in directions
                )
                if not opposite:
                    raise ValidationError(f"Reverse-PE images associated with {bold} are not opposite: {directions}")
                if any(direction[0] != bold_pe[0] for direction in directions):
                    raise ValidationError(
                        f"BOLD and reverse-PE acquisitions use different PE axes: BOLD={bold_pe}, fieldmaps={directions}"
                    )
                report_runs.append(
                    {
                        "bold": str(bold),
                        "functional_volumes": int(bold_image.shape[3]),
                        "noise": str(noise),
                        "no_rf_volumes": int(noise_image.shape[3]),
                        "fieldmaps": [str(path) for path in fieldmaps],
                        "phase_encoding_directions": directions,
                        "bold_total_readout_time": bold_readout,
                        "fieldmap_total_readout_times": readouts,
                    }
                )
    if not report_runs:
        raise ValidationError("No BOLD runs were available for semantic validation")
    return {"bids_dir": str(root), "expected_no_rf_volumes": expected_no_rf_volumes, "runs": report_runs}


def run_official_validator(
    bids_dir: str | Path,
    *,
    output_json: str | Path,
    required: bool = True,
) -> Path | None:
    """Run an installed official BIDS validator and retain its JSON output."""
    executable = shutil.which("bids-validator-deno") or shutil.which("bids-validator")
    if executable is None:
        if required:
            raise ExternalCommandError(
                "No official BIDS validator was found (bids-validator-deno or bids-validator)"
            )
        return None
    root = Path(bids_dir).expanduser().resolve()
    output = Path(output_json).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [executable, "--json", str(root)],
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        parsed = json.loads(completed.stdout) if completed.stdout.strip() else {}
        payload: dict[str, Any] = parsed if isinstance(parsed, dict) else {"result": parsed}
    except json.JSONDecodeError:
        payload = {"stdout": completed.stdout}
    payload["stderr"] = completed.stderr
    payload["returncode"] = completed.returncode
    payload["command"] = [executable, "--json", str(root)]
    write_json(output, payload)
    if completed.returncode != 0:
        raise ValidationError(f"The official BIDS validator reported errors for {root}; see {output}")
    return output
