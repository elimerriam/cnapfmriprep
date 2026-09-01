"""DICOM series inventory and strict regular-expression matching."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence

import pydicom
from pydicom.datadict import tag_for_keyword
from pydicom.errors import InvalidDicomError

from .config import SeriesRule
from .errors import ValidationError


# These are DICOM data-element keywords, not human-readable tag names. Keep
# this list validated because pydicom resolves every item in ``specific_tags``
# before it opens a dataset. One misspelled keyword would otherwise make every
# file look unreadable.
_DICOM_FIELDS = (
    "SOPClassUID",
    "SOPInstanceUID",
    "SeriesInstanceUID",
    "StudyInstanceUID",
    "SeriesNumber",
    "SeriesDescription",
    "ProtocolName",
    "SequenceName",
    "ImageType",
    "EchoNumbers",
    "AcquisitionNumber",
    "AcquisitionTime",
    "Modality",
    "Manufacturer",
    "ManufacturerModelName",
    "MagneticFieldStrength",
    "Rows",
    "Columns",
)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\\".join(str(item) for item in value)
    return str(value)


@lru_cache(maxsize=1)
def validated_dicom_fields() -> tuple[str, ...]:
    """Return inventory fields after checking every pydicom keyword.

    Validation occurs once, before scanning files. This prevents a programming
    error in the field list from being swallowed as one "unreadable" result per
    DICOM file.
    """
    invalid = [keyword for keyword in _DICOM_FIELDS if tag_for_keyword(keyword) is None]
    if invalid:
        raise ValidationError(
            "CNAP fMRI Prep has invalid internal pydicom keyword(s): "
            + ", ".join(sorted(invalid))
        )
    return _DICOM_FIELDS


def _read_header(path: Path, specific_tags: list[str]) -> tuple[Any, str]:
    """Read a DICOM header, accepting datasets without a Part-10 preamble.

    XNAT normally stores DICOM Part-10 files. Some scanner exports omit the
    128-byte preamble and ``DICM`` prefix while retaining a valid dataset. In
    that limited case, retry with ``force=True`` and still require a valid
    SeriesInstanceUID before accepting the file.
    """
    try:
        dataset = pydicom.dcmread(
            str(path),
            stop_before_pixels=True,
            force=False,
            specific_tags=specific_tags,
        )
        return dataset, "part10"
    except InvalidDicomError:
        dataset = pydicom.dcmread(
            str(path),
            stop_before_pixels=True,
            force=True,
            specific_tags=specific_tags,
        )
        return dataset, "forced"


def _extension_summary(paths: Sequence[Path], *, maximum: int = 8) -> str:
    counts = Counter(path.suffix.lower() or "<none>" for path in paths)
    return ", ".join(f"{suffix}={count}" for suffix, count in counts.most_common(maximum))


def inventory_dicom_series(root: str | Path) -> list[dict[str, Any]]:
    """Recursively inventory readable DICOM files, grouped by SeriesInstanceUID."""
    source = Path(root).resolve()
    candidates = sorted(candidate for candidate in source.rglob("*") if candidate.is_file())
    specific_tags = list(validated_dicom_fields())

    groups: dict[str, dict[str, Any]] = {}
    files_by_uid: dict[str, list[str]] = defaultdict(list)
    read_modes_by_uid: dict[str, Counter[str]] = defaultdict(Counter)
    unreadable = 0
    forced = 0

    for path in candidates:
        try:
            dataset, read_mode = _read_header(path, specific_tags)
        except (InvalidDicomError, OSError, ValueError, EOFError, TypeError):
            unreadable += 1
            continue

        uid = _stringify(getattr(dataset, "SeriesInstanceUID", "")).strip()
        if not uid:
            unreadable += 1
            continue

        # A forced parse of an arbitrary non-DICOM file can occasionally yield
        # nonsense. Requiring at least one additional standard UID makes the
        # fallback conservative while allowing anonymized datasets.
        sop_uid = _stringify(getattr(dataset, "SOPInstanceUID", "")).strip()
        study_uid = _stringify(getattr(dataset, "StudyInstanceUID", "")).strip()
        if read_mode == "forced" and not (sop_uid or study_uid):
            unreadable += 1
            continue

        if read_mode == "forced":
            forced += 1

        if uid not in groups:
            row = {field: _stringify(getattr(dataset, field, "")) for field in _DICOM_FIELDS}
            row["SeriesInstanceUID"] = uid
            groups[uid] = row
        files_by_uid[uid].append(str(path.resolve()))
        read_modes_by_uid[uid][read_mode] += 1

    rows: list[dict[str, Any]] = []
    for uid, row in groups.items():
        files = sorted(files_by_uid[uid])
        modes = read_modes_by_uid[uid]
        row = dict(row)
        row["NumberOfFiles"] = len(files)
        row["Part10Files"] = modes.get("part10", 0)
        row["ForcedReadFiles"] = modes.get("forced", 0)
        row["Files"] = files
        row["UnreadableFilesInArchive"] = unreadable
        row["ForcedReadFilesInArchive"] = forced
        rows.append(row)

    rows.sort(
        key=lambda row: (
            int(row["SeriesNumber"]) if str(row.get("SeriesNumber", "")).isdigit() else 10**9,
            str(row.get("SeriesDescription", "")),
            str(row.get("SeriesInstanceUID", "")),
        )
    )

    if not rows:
        extension_text = _extension_summary(candidates) or "none"
        raise ValidationError(
            f"No readable DICOM series were found under {source}. "
            f"Inspected {len(candidates)} regular file(s); extension summary: {extension_text}. "
            "Confirm that the XNAT download contains the DICOM resource rather than only "
            "NIfTI, snapshots, an XML catalog, or another nested archive."
        )
    return rows


def _rule_matches(row: dict[str, Any], rule: SeriesRule) -> bool:
    for field, expression in rule.match.items():
        value = _stringify(row.get(field, ""))
        try:
            if re.search(expression, value) is None:
                return False
        except re.error as error:
            raise ValidationError(
                f"Invalid regular expression in rule {rule.name!r}, field {field!r}: {error}"
            ) from error
    return True


def _numeric_sort_value(value: Any) -> tuple[int, float | str]:
    text = _stringify(value).strip()
    try:
        return (0, float(text))
    except ValueError:
        return (1, text)


def _run_sort_key(row: dict[str, Any], field: str) -> tuple[Any, ...]:
    secondary = "AcquisitionTime" if field == "SeriesNumber" else "SeriesNumber"
    return (
        _numeric_sort_value(row.get(field, "")),
        _numeric_sort_value(row.get(secondary, "")),
        _stringify(row.get("SeriesInstanceUID", "")),
    )


def match_series(
    rows: Sequence[dict[str, Any]],
    rules: Iterable[SeriesRule],
) -> list[dict[str, Any]]:
    """Match configured rules and assign unique BIDS run numbers.

    A rule with ``run: auto`` may match several BOLD series. Those series are
    sorted deterministically and assigned consecutive run numbers beginning at
    ``run_start``. All other rules remain strict one-series mappings unless an
    exact integer ``expected_matches`` says otherwise.
    """
    plan: list[dict[str, Any]] = []
    claimed: dict[str, str] = {}
    assigned_targets: dict[tuple[str, str, int], str] = {}
    for rule in rules:
        matches = [row for row in rows if _rule_matches(row, rule)]
        if rule.run == "auto":
            matches = sorted(matches, key=lambda row: _run_sort_key(row, rule.run_sort_by))

        expected = rule.expected_matches
        count_ok = len(matches) >= 1 if expected == "one_or_more" else len(matches) == expected
        if not count_ok:
            descriptions = [
                f"{row.get('SeriesNumber', '')}:{row.get('SeriesDescription', '')}"
                for row in matches
            ]
            expectation = "one or more" if expected == "one_or_more" else str(expected)
            raise ValidationError(
                f"Series rule {rule.name!r} expected {expectation} match(es), "
                f"found {len(matches)}: {descriptions}"
            )

        for match_index, row in enumerate(matches):
            uid = str(row["SeriesInstanceUID"])
            if uid in claimed:
                raise ValidationError(
                    f"DICOM series {uid} was matched by both {claimed[uid]!r} and {rule.name!r}"
                )
            claimed[uid] = rule.name
            assigned_run = (
                rule.run_start + match_index
                if rule.run == "auto"
                else rule.run
            )
            if rule.kind == "bold_with_norf" and isinstance(assigned_run, int):
                target = (
                    rule.task or "",
                    rule.acquisition or "",
                    assigned_run,
                )
                previous = assigned_targets.get(target)
                if previous is not None:
                    raise ValidationError(
                        f"Rules {previous!r} and {rule.name!r} both assign BIDS "
                        f"task/acquisition/run {target}. Adjust run_start or make the "
                        "matching expressions non-overlapping."
                    )
                assigned_targets[target] = rule.name
            plan.append(
                {
                    "rule": rule.model_dump(mode="json"),
                    "rule_name": rule.name,
                    "match_index": match_index,
                    "assigned_run": assigned_run,
                    "series": row,
                }
            )
    return plan
