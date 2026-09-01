"""Static QC figures and a self-contained run summary page."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import nibabel as nb
import numpy as np
import pandas as pd

from .errors import ValidationError
from .utils import ensure_dir, read_json, write_json


def _data(path: str | Path) -> np.ndarray:
    return np.asanyarray(nb.load(str(path)).dataobj, dtype=np.float32)


def _mean_3d(path: str | Path) -> np.ndarray:
    value = _data(path)
    return np.nanmean(value, axis=3) if value.ndim == 4 else value


def _tstd_3d(path: str | Path) -> np.ndarray:
    value = _data(path)
    return np.nanstd(value, axis=3, ddof=1) if value.ndim == 4 else np.zeros(value.shape[:3])


def _slice(data: np.ndarray) -> np.ndarray:
    data = np.squeeze(data)
    if data.ndim != 3:
        raise ValidationError(f"QC expected a 3D image, got shape {data.shape}")
    return np.rot90(data[:, :, data.shape[2] // 2])


def _limits(data: np.ndarray, lower: float = 1, upper: float = 99) -> tuple[float, float]:
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return 0.0, 1.0
    low, high = np.percentile(finite, [lower, upper])
    if high <= low:
        low, high = float(np.min(finite)), float(np.max(finite))
    if high <= low:
        high = low + 1.0
    return float(low), float(high)


def _save_panels(
    panels: Sequence[tuple[str, np.ndarray]],
    output: Path,
    *,
    cmap: str = "gray",
    shared_limits: bool = False,
) -> Path:
    figure, axes = plt.subplots(1, len(panels), figsize=(5 * len(panels), 4), squeeze=False)
    limits = _limits(np.concatenate([panel.ravel() for _, panel in panels])) if shared_limits else None
    for axis, (title, panel) in zip(axes[0], panels, strict=True):
        low, high = limits or _limits(panel)
        image = axis.imshow(_slice(panel), cmap=cmap, vmin=low, vmax=high, interpolation="nearest")
        axis.set_title(title)
        axis.axis("off")
        figure.colorbar(image, ax=axis, fraction=0.046, pad=0.04)
    figure.tight_layout()
    figure.savefig(output, dpi=140, bbox_inches="tight")
    plt.close(figure)
    return output.resolve()


def _save_scalar(title: str, data: np.ndarray, output: Path, *, cmap: str = "viridis") -> Path:
    figure, axis = plt.subplots(figsize=(6, 5))
    low, high = _limits(data)
    image = axis.imshow(_slice(data), cmap=cmap, vmin=low, vmax=high, interpolation="nearest")
    axis.set_title(title)
    axis.axis("off")
    figure.colorbar(image, ax=axis, fraction=0.046, pad=0.04)
    figure.tight_layout()
    figure.savefig(output, dpi=140, bbox_inches="tight")
    plt.close(figure)
    return output.resolve()


def _save_motion_translation(motion: pd.DataFrame, output: Path) -> Path:
    figure, axis = plt.subplots(figsize=(10, 4))
    for column in ("trans_x_mm", "trans_y_mm", "trans_z_mm"):
        axis.plot(motion.index, motion[column], label=column)
    axis.set_xlabel("Volume")
    axis.set_ylabel("Translation (mm)")
    axis.set_title("Rigid translations")
    axis.legend(loc="best")
    axis.grid(True, alpha=0.25)
    figure.tight_layout()
    figure.savefig(output, dpi=140, bbox_inches="tight")
    plt.close(figure)
    return output.resolve()


def _save_motion_rotation_fd(motion: pd.DataFrame, output: Path) -> Path:
    figure, axis = plt.subplots(figsize=(10, 4))
    for column in ("rot_x_rad", "rot_y_rad", "rot_z_rad"):
        axis.plot(motion.index, motion[column], label=column)
    axis.set_xlabel("Volume")
    axis.set_ylabel("Rotation (radians)")
    axis.set_title("Rigid rotations")
    second = axis.twinx()
    second.plot(
        motion.index,
        motion["framewise_displacement_power_mm"],
        linestyle="--",
        label="Power FD",
    )
    second.set_ylabel("Power FD (mm)")
    handles, labels = axis.get_legend_handles_labels()
    handles2, labels2 = second.get_legend_handles_labels()
    axis.legend(handles + handles2, labels + labels2, loc="best")
    axis.grid(True, alpha=0.25)
    figure.tight_layout()
    figure.savefig(output, dpi=140, bbox_inches="tight")
    plt.close(figure)
    return output.resolve()


def _save_displacement(displacement: pd.DataFrame, output: Path) -> Path:
    figure, axis = plt.subplots(figsize=(10, 4))
    for column in (
        "absolute_displacement_median_mm",
        "absolute_displacement_p95_mm",
        "absolute_displacement_max_mm",
        "framewise_displacement_p95_mm",
    ):
        if column in displacement:
            axis.plot(displacement.index, displacement[column], label=column)
    axis.set_xlabel("Volume")
    axis.set_ylabel("Displacement (mm)")
    axis.set_title("Slab-aware voxel displacement")
    axis.legend(loc="best")
    axis.grid(True, alpha=0.25)
    figure.tight_layout()
    figure.savefig(output, dpi=140, bbox_inches="tight")
    plt.close(figure)
    return output.resolve()


def run_qc_stage(
    raw_bold: str,
    nordic_bold: str,
    corrected_bold: str,
    preview_bold: str,
    field_hz: str,
    displacement_mm: str,
    jacobian: str,
    topup_input: str,
    topup_corrected: str,
    motion_tsv: str,
    displacement_tsv: str,
    no_rf_stats_json: str,
    transform_order_json: str,
    output_dir: str,
) -> dict[str, Any]:
    """Generate nine deterministic PNG reportlets and an HTML index."""
    root = ensure_dir(output_dir)
    figures_dir = ensure_dir(root / "figures")
    motion = pd.read_csv(motion_tsv, sep="\t")
    displacement = pd.read_csv(displacement_tsv, sep="\t")
    no_rf = read_json(no_rf_stats_json)
    transform_order = read_json(transform_order_json)
    raw_mean = _mean_3d(raw_bold)
    nordic_mean = _mean_3d(nordic_bold)
    corrected_mean = _mean_3d(corrected_bold)
    preview_mean = _mean_3d(preview_bold)
    raw_tstd = _tstd_3d(raw_bold)
    nordic_tstd = _tstd_3d(nordic_bold)
    figures: list[Path] = []
    figures.append(
        _save_panels(
            [("Raw mean", raw_mean), ("NORDIC mean", nordic_mean), ("SDC + motion mean", corrected_mean)],
            figures_dir / "01_signal_stages.png",
            shared_limits=True,
        )
    )
    figures.append(
        _save_panels(
            [("Raw temporal SD", raw_tstd), ("NORDIC temporal SD", nordic_tstd), ("Raw minus NORDIC SD", raw_tstd - nordic_tstd)],
            figures_dir / "02_nordic_temporal_sd.png",
            cmap="magma",
        )
    )
    figures.append(
        _save_panels(
            [("TOPUP input mean", _mean_3d(topup_input)), ("TOPUP corrected mean", _mean_3d(topup_corrected)), ("BOLD SDC preview mean", preview_mean)],
            figures_dir / "03_topup_before_after.png",
            shared_limits=True,
        )
    )
    figures.append(_save_scalar("TOPUP field (Hz)", _mean_3d(field_hz), figures_dir / "04_field_hz.png"))
    figures.append(_save_scalar("Signed PE displacement (mm)", _mean_3d(displacement_mm), figures_dir / "05_displacement_mm.png", cmap="coolwarm"))
    figures.append(_save_scalar("Susceptibility Jacobian", _mean_3d(jacobian), figures_dir / "06_jacobian.png"))
    figures.append(_save_motion_translation(motion, figures_dir / "07_translations.png"))
    figures.append(_save_motion_rotation_fd(motion, figures_dir / "08_rotations_fd.png"))
    figures.append(_save_displacement(displacement, figures_dir / "09_displacement_timeseries.png"))
    summary = {
        "functional_volumes": int(nb.load(corrected_bold).shape[3]),
        "maximum_power_fd_mm": float(motion["framewise_displacement_power_mm"].max()),
        "maximum_absolute_displacement_mm": float(displacement["absolute_displacement_max_mm"].max()),
        "no_rf": no_rf,
        "transform_order": transform_order,
        "figures": [str(path) for path in figures],
    }
    summary_json = write_json(root / "qc_summary.json", summary)
    rows = "\n".join(
        f'<section><h2>{html.escape(path.stem.replace("_", " ").title())}</h2>'
        f'<img src="figures/{html.escape(path.name)}" alt="{html.escape(path.stem)}"></section>'
        for path in figures
    )
    report = root / "report.html"
    report.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>cnapfmriprep QC</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem}"
        "img{max-width:100%;border:1px solid #ccc}pre{white-space:pre-wrap;background:#f4f4f4;padding:1rem}"
        "section{margin:2rem 0}</style></head><body><h1>cnapfmriprep run QC</h1>"
        f"<p>Functional volumes: {summary['functional_volumes']}</p>"
        f"<p>Maximum Power FD: {summary['maximum_power_fd_mm']:.4g} mm</p>"
        f"<p>Maximum slab displacement: {summary['maximum_absolute_displacement_mm']:.4g} mm</p>"
        + rows
        + "<h2>No-RF statistics</h2><pre>"
        + html.escape(json.dumps(no_rf, indent=2))
        + "</pre><h2>Transform-order verification</h2><pre>"
        + html.escape(json.dumps(transform_order, indent=2))
        + "</pre></body></html>\n"
    )
    return {
        "report_html": str(report.resolve()),
        "summary_json": str(summary_json),
        "figures": [str(path) for path in figures],
    }
