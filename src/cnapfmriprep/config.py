"""Typed study configuration loaded from YAML."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ValidationError

PhaseEncodingDirection = Literal["i", "i-", "j", "j-", "k", "k-"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SeriesRule(_Model):
    """One DICOM-series-to-BIDS mapping rule.

    ``run: auto`` permits one rule to match several BOLD series. Matches are
    sorted by ``run_sort_by`` and assigned consecutive BIDS run numbers
    beginning at ``run_start``.
    """

    name: str
    kind: Literal["bold_with_norf", "fmap_epi", "anat"]
    match: dict[str, str]
    task: str | None = None
    acquisition: str | None = None
    direction: str | None = None
    run: int | Literal["auto"] | None = None
    run_start: int = Field(default=1, ge=1)
    run_sort_by: Literal["SeriesNumber", "AcquisitionTime"] = "SeriesNumber"
    b0_identifier: str | None = None
    phase_encoding_direction: PhaseEncodingDirection | None = None
    suffix: str | None = None
    expected_matches: int | Literal["one_or_more"] = 1

    @field_validator("match")
    @classmethod
    def match_is_not_empty(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("A series rule must contain at least one match expression")
        return value

    @field_validator("run")
    @classmethod
    def validate_run(cls, value: int | str | None) -> int | str | None:
        if isinstance(value, int) and value < 1:
            raise ValueError("run must be at least 1")
        return value

    @field_validator("expected_matches")
    @classmethod
    def validate_expected_matches(cls, value: int | str) -> int | str:
        if isinstance(value, int) and value < 1:
            raise ValueError("expected_matches must be at least 1")
        return value

    @model_validator(mode="after")
    def validate_kind_fields(self) -> SeriesRule:
        if self.kind == "bold_with_norf":
            if not self.task:
                raise ValueError("bold_with_norf rules require task")
            if not self.b0_identifier:
                raise ValueError("bold_with_norf rules require b0_identifier")
        elif self.kind == "fmap_epi":
            if not self.direction:
                raise ValueError("fmap_epi rules require direction")
            if not self.b0_identifier:
                raise ValueError("fmap_epi rules require b0_identifier")
        elif self.kind == "anat":
            if self.phase_encoding_direction is not None:
                raise ValueError(
                    "phase_encoding_direction is supported only for BOLD and EPI fieldmaps"
                )
            if not self.suffix:
                object.__setattr__(self, "suffix", "T1w")

        multiple_expected = self.expected_matches == "one_or_more" or (
            isinstance(self.expected_matches, int) and self.expected_matches > 1
        )
        if self.run == "auto" and self.kind != "bold_with_norf":
            raise ValueError("run: auto is currently supported only for bold_with_norf rules")
        if multiple_expected and self.kind != "bold_with_norf":
            raise ValueError(
                "Multiple matches are currently supported only for bold_with_norf rules; "
                "use one explicit rule per fieldmap or anatomical series"
            )
        if self.kind == "bold_with_norf" and multiple_expected and self.run != "auto":
            raise ValueError(
                "A BOLD rule that can match multiple series must use run: auto so every "
                "series receives a unique BIDS run number"
            )
        return self


class IngestConfig(_Model):
    dataset_name: str
    trailing_no_rf_volumes: int = Field(default=2, ge=1)
    no_rf_datatype: Literal["func"] = "func"
    dcm2niix_compression: Literal["y", "n"] = "y"
    retain_extracted_dicoms: bool = False
    series_rules: list[SeriesRule]

    @field_validator("no_rf_datatype", mode="before")
    @classmethod
    def migrate_legacy_no_rf_datatype(cls, value: Any) -> Any:
        # BIDS 1.10 introduced BOLD no-RF scans under func. Accept the former
        # cnapfmriprep value so existing study YAML migrates without breaking.
        return "func" if value == "fmap" else value

    @model_validator(mode="after")
    def unique_rule_names(self) -> IngestConfig:
        names = [rule.name for rule in self.series_rules]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(
                "DICOM series rule names must be unique: " + ", ".join(duplicates)
            )
        return self


class NordicConfig(_Model):
    checkout: Path
    matlab_command: str = "matlab"
    magnitude_only: bool = True
    noise_volume_last: int = Field(default=2, ge=1)
    factor_error: float = Field(default=1.0, gt=0)
    save_gfactor_map: bool = True
    save_additional_info: bool = True
    matlab_license_retries: int = Field(default=3, ge=0, le=20)
    matlab_license_retry_initial_seconds: float = Field(default=30.0, ge=0, le=3600)
    matlab_license_retry_max_seconds: float = Field(default=300.0, ge=0, le=3600)

    @model_validator(mode="after")
    def magnitude_only_required(self) -> NordicConfig:
        if not self.magnitude_only:
            raise ValueError("This pipeline release supports magnitude-only NORDIC")
        return self


class TopupConfig(_Model):
    volumes_per_direction: int = Field(default=1, ge=1)
    config: str = "auto"
    fallback_total_readout_time: float | None = Field(default=None, gt=0)


class AntsConfig(_Model):
    two_pass: bool = True
    metric: Literal["Mattes", "MI", "CC"] = "Mattes"
    sampling_percentage: float = Field(default=0.2, gt=0, le=1)
    preview_interpolation: str = "Linear"
    use_registration_mask: bool = True
    seed: int = 20260829
    fd_radius_mm: float = Field(default=50.0, gt=0)


class MultiRunConfig(_Model):
    """Session-level sharing of TOPUP and the motion target."""

    shared_topup: bool = True
    shared_motion_reference: bool = True
    reference_task: str | None = None
    reference_run: int | None = Field(default=None, ge=1)


class ResamplingConfig(_Model):
    interpolation: str = "LanczosWindowedSinc"
    allow_negative_values: bool = False
    jacobian_modulation: bool = False
    maximum_transform_order_nrmse: float = Field(default=0.25, gt=0)


class QcConfig(_Model):
    enabled: bool = True


class ExecutionConfig(_Model):
    profile: Literal["custom", "laptop", "workstation", "server", "auto"] = "custom"
    resolved_profile: Literal["custom", "laptop", "workstation", "server"] = "custom"
    pydra_plugin: str = "cf"
    n_procs: int = Field(default=2, ge=1)
    volume_workers: int = Field(default=4, ge=1)
    threads_per_ants: int = Field(default=2, ge=1)
    show_progress: bool = True
    progress_interval_percent: int = Field(default=10, ge=1, le=100)


class StudyConfig(_Model):
    ingest: IngestConfig
    nordic: NordicConfig
    topup: TopupConfig
    ants: AntsConfig
    multi_run: MultiRunConfig = Field(default_factory=MultiRunConfig)
    resampling: ResamplingConfig
    qc: QcConfig
    execution: ExecutionConfig

    @model_validator(mode="after")
    def validate_noise_index(self) -> StudyConfig:
        if self.nordic.noise_volume_last > self.ingest.trailing_no_rf_volumes:
            raise ValueError(
                "nordic.noise_volume_last cannot exceed ingest.trailing_no_rf_volumes"
            )
        return self


def _expand_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser()


def load_config(path: str | Path) -> StudyConfig:
    """Load and validate a study YAML configuration."""
    source = Path(path).expanduser().resolve()
    try:
        raw: Any = yaml.safe_load(source.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise ValidationError(f"Could not read configuration {source}: {error}") from error
    if not isinstance(raw, dict):
        raise ValidationError(f"Configuration must contain a YAML mapping: {source}")
    raw = dict(raw)
    from .execution import resolve_execution_mapping

    raw["execution"] = resolve_execution_mapping(dict(raw.get("execution") or {}))
    nordic = dict(raw.get("nordic") or {})
    environment_root = os.environ.get("NORDIC_ROOT")
    if environment_root:
        nordic["checkout"] = environment_root
    elif "checkout" in nordic:
        nordic["checkout"] = _expand_path(nordic["checkout"])
    raw["nordic"] = nordic
    try:
        return StudyConfig.model_validate(raw)
    except Exception as error:
        raise ValidationError(f"Invalid configuration {source}: {error}") from error
