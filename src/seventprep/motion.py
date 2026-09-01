"""ANTs rigid-motion estimation and one-call SDC/HMC resampling per volume."""

from __future__ import annotations

import math
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar

import nibabel as nb
import numpy as np
import pandas as pd
from scipy import ndimage
from scipy.spatial.transform import Rotation

from .errors import ValidationError
from .utils import ensure_dir, require_executable, run_command, same_nifti_grid, write_json

_T = TypeVar("_T")
_U = TypeVar("_U")


def _parallel_map(
    function: Callable[[_T], _U],
    values: Sequence[_T],
    workers: int,
    progress: Callable[[int, int], None] | None = None,
) -> list[_U]:
    total = len(values)
    if workers <= 1:
        outputs = []
        for completed, value in enumerate(values, 1):
            outputs.append(function(value))
            if progress:
                progress(completed, total)
        return outputs
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outputs = []
        for completed, output in enumerate(pool.map(function, values), 1):
            outputs.append(output)
            if progress:
                progress(completed, total)
        return outputs


def _split_4d(source: Path, output_dir: Path) -> list[Path]:
    image = nb.load(str(source))
    if len(image.shape) != 4 or image.shape[3] < 1:
        raise ValidationError(f"Expected a non-empty 4D BOLD image: {source}")
    output_dir = ensure_dir(output_dir)
    outputs: list[Path] = []
    for index in range(image.shape[3]):
        data = np.asanyarray(image.dataobj[..., index], dtype=np.float32)
        header = image.header.copy()
        header.set_data_shape(data.shape)
        header.set_data_dtype(np.float32)
        path = output_dir / f"vol-{index:05d}.nii.gz"
        nb.Nifti1Image(data, image.affine, header).to_filename(path)
        outputs.append(path.resolve())
    return outputs


def _merge_3d(volumes: Sequence[Path], source_4d: Path, output: Path) -> Path:
    if not volumes:
        raise ValidationError("Cannot merge an empty volume list")
    for volume in volumes[1:]:
        if not same_nifti_grid(volumes[0], volume):
            raise ValidationError("Volume grids differ during 4D merge")
    source = nb.load(str(source_4d))
    data = np.stack(
        [np.asanyarray(nb.load(str(path)).dataobj, dtype=np.float32) for path in volumes], axis=3
    )
    header = source.header.copy()
    header.set_data_shape(data.shape)
    header.set_data_dtype(np.float32)
    output.parent.mkdir(parents=True, exist_ok=True)
    merged = nb.Nifti1Image(data, nb.load(str(volumes[0])).affine, header)
    merged.set_qform(merged.affine, code=max(int(source.header["qform_code"]), 1))
    merged.set_sform(merged.affine, code=max(int(source.header["sform_code"]), 1))
    merged.to_filename(output)
    return output.resolve()


def _mean_reference(volumes: Sequence[Path], output: Path) -> Path:
    if not volumes:
        raise ValidationError("Cannot build a reference from an empty volume list")
    first = nb.load(str(volumes[0]))
    accumulator = np.zeros(first.shape[:3], dtype=np.float64)
    count = np.zeros(first.shape[:3], dtype=np.uint32)
    for path in volumes:
        if not same_nifti_grid(volumes[0], path):
            raise ValidationError("Reference inputs are not on a common grid")
        data = np.asanyarray(nb.load(str(path)).dataobj, dtype=np.float32)
        finite = np.isfinite(data)
        accumulator[finite] += data[finite]
        count[finite] += 1
    reference = np.divide(
        accumulator, np.maximum(count, 1), out=np.zeros_like(accumulator), where=count > 0
    ).astype(np.float32)
    header = first.header.copy()
    header.set_data_shape(reference.shape)
    header.set_data_dtype(np.float32)
    output.parent.mkdir(parents=True, exist_ok=True)
    nb.Nifti1Image(reference, first.affine, header).to_filename(output)
    return output.resolve()


def _brain_mask(reference_file: Path, output: Path) -> Path:
    image = nb.load(str(reference_file))
    data = np.asanyarray(image.dataobj, dtype=np.float32)
    finite = np.isfinite(data)
    positive = data[finite & (data > 0)]
    if positive.size:
        mask = finite & (data > float(np.percentile(positive, 20)))
    else:
        mask = finite & (data != 0)
    if mask.any():
        mask = ndimage.binary_closing(mask, iterations=2)
        mask = ndimage.binary_fill_holes(mask)
        labels, count = ndimage.label(mask)
        if count:
            sizes = np.bincount(labels.ravel())
            sizes[0] = 0
            mask = labels == int(np.argmax(sizes))
        mask = ndimage.binary_dilation(mask, iterations=1)
    else:
        mask = np.ones(data.shape, dtype=bool)
    header = image.header.copy()
    header.set_data_shape(mask.shape)
    header.set_data_dtype(np.uint8)
    output.parent.mkdir(parents=True, exist_ok=True)
    nb.Nifti1Image(mask.astype(np.uint8), image.affine, header).to_filename(output)
    return output.resolve()


def _ants_environment(threads: int) -> dict[str, str]:
    return {
        "ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS": str(max(1, threads)),
        "OMP_NUM_THREADS": str(max(1, threads)),
    }


def _apply_transforms(
    moving: Path,
    reference: Path,
    output: Path,
    transforms: Sequence[Path],
    *,
    interpolation: str,
    threads: int,
    log_file: Path,
) -> Path:
    command = [
        require_executable("antsApplyTransforms"),
        "-d", "3",
        "-i", str(moving),
        "-r", str(reference),
        "-o", str(output),
        "-n", interpolation,
        "--float", "1",
    ]
    for transform in transforms:
        command.extend(["-t", str(transform)])
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command(command, env=_ants_environment(threads), log_file=log_file)
    if not output.is_file():
        raise ValidationError(f"antsApplyTransforms did not create {output}")
    return output.resolve()


def _metric_name(value: str) -> str:
    return "Mattes" if value in {"Mattes", "MI"} else value


def _register_rigid(
    moving: Path,
    fixed: Path,
    output_dir: Path,
    *,
    index: int,
    config: dict[str, Any],
    threads: int,
    fixed_mask: Path | None,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / f"vol-{index:05d}_"
    warped = output_dir / f"vol-{index:05d}_warped.nii.gz"
    metric = _metric_name(str(config.get("metric", "Mattes")))
    sampling = float(config.get("sampling_percentage", 0.2))
    command = [
        require_executable("antsRegistration"),
        "--dimensionality", "3",
        "--float", "1",
        "--output", f"[{prefix},{warped}]",
        "--interpolation", "Linear",
        "--winsorize-image-intensities", "[0.005,0.995]",
        "--use-histogram-matching", "0",
        "--initial-moving-transform", f"[{fixed},{moving},1]",
        "--transform", "Rigid[0.1]",
        "--metric", f"{metric}[{fixed},{moving},1,32,Regular,{sampling:.8g}]",
        "--convergence", "[100x50x20,1e-6,10]",
        "--shrink-factors", "4x2x1",
        "--smoothing-sigmas", "2x1x0vox",
        "--random-seed", str(int(config.get("seed", 20260829)) + index),
        "--write-composite-transform", "0",
    ]
    if fixed_mask is not None:
        command.extend(["--masks", f"[{fixed_mask},NULL]"])
    run_command(
        command,
        env=_ants_environment(threads),
        log_file=output_dir / f"vol-{index:05d}_registration.log",
    )
    affine = Path(str(prefix) + "0GenericAffine.mat")
    if not affine.is_file() or not warped.is_file():
        raise ValidationError(
            f"ANTs rigid registration did not create the expected files for volume {index}"
        )
    return affine.resolve(), warped.resolve()


def _load_itk_affine_ras(path: Path) -> np.ndarray:
    try:
        from nitransforms.io.itk import ITKLinearTransform
        return np.asarray(ITKLinearTransform.from_filename(path).to_ras(), dtype=np.float64)
    except Exception:
        text = path.read_text(errors="replace")
        parameters_line = next(
            (line for line in text.splitlines() if line.strip().startswith("Parameters:")), None
        )
        fixed_line = next(
            (line for line in text.splitlines() if line.strip().startswith("FixedParameters:")), None
        )
        if parameters_line is None:
            raise ValidationError(f"Could not parse ITK affine transform: {path}")
        values = np.fromstring(parameters_line.split(":", 1)[1], sep=" ")
        if values.size != 12:
            raise ValidationError(f"Expected 12 affine parameters in {path}")
        center = (
            np.fromstring(fixed_line.split(":", 1)[1], sep=" ")
            if fixed_line is not None else np.zeros(3)
        )
        linear = values[:9].reshape(3, 3)
        translation = values[9:]
        effective = translation + center - linear @ center
        lps = np.eye(4)
        lps[:3, :3] = linear
        lps[:3, 3] = effective
        reflection = np.diag([-1.0, -1.0, 1.0, 1.0])
        return reflection @ lps @ reflection


def _matrix_parameters(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    u, _, vt = np.linalg.svd(matrix[:3, :3])
    rotation_matrix = u @ vt
    if np.linalg.det(rotation_matrix) < 0:
        u[:, -1] *= -1
        rotation_matrix = u @ vt
    rotations = Rotation.from_matrix(rotation_matrix).as_euler("xyz", degrees=False)
    return matrix[:3, 3].astype(float), rotations.astype(float)


def _world_mask_points(mask_file: Path) -> np.ndarray:
    image = nb.load(str(mask_file))
    indices = np.argwhere(np.asanyarray(image.dataobj) > 0)
    if indices.size == 0:
        raise ValidationError(f"Motion mask contains no voxels: {mask_file}")
    return nb.affines.apply_affine(image.affine, indices).astype(np.float64)


def _transform_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return points @ matrix[:3, :3].T + matrix[:3, 3]


def write_motion_metrics(
    affine_files: Sequence[str | Path],
    mask_file: str | Path,
    motion_out: str | Path,
    displacement_out: str | Path,
    *,
    fd_radius_mm: float = 50.0,
) -> tuple[Path, Path]:
    """Write conventional motion parameters and slab-aware voxel displacement."""
    paths = [Path(path).resolve() for path in affine_files]
    if not paths:
        raise ValidationError("No rigid transforms were provided for motion metrics")
    matrices = [_load_itk_affine_ras(path) for path in paths]
    points = _world_mask_points(Path(mask_file))
    translations: list[np.ndarray] = []
    rotations: list[np.ndarray] = []
    transformed: list[np.ndarray] = []
    for matrix in matrices:
        translation, rotation = _matrix_parameters(matrix)
        translations.append(translation)
        rotations.append(rotation)
        transformed.append(_transform_points(points, matrix))
    translation_array = np.stack(translations)
    rotation_array = np.stack(rotations)
    differences_t = np.vstack([np.zeros(3), np.diff(translation_array, axis=0)])
    differences_r = np.vstack([np.zeros(3), np.diff(rotation_array, axis=0)])
    fd = np.abs(differences_t).sum(axis=1) + fd_radius_mm * np.abs(differences_r).sum(axis=1)
    motion = pd.DataFrame(
        {
            "trans_x_mm": translation_array[:, 0],
            "trans_y_mm": translation_array[:, 1],
            "trans_z_mm": translation_array[:, 2],
            "rot_x_rad": rotation_array[:, 0],
            "rot_y_rad": rotation_array[:, 1],
            "rot_z_rad": rotation_array[:, 2],
            "framewise_displacement_power_mm": fd,
        }
    )
    displacement_rows: list[dict[str, float]] = []
    for index, current in enumerate(transformed):
        absolute = np.linalg.norm(current - points, axis=1)
        framewise = (
            np.zeros_like(absolute)
            if index == 0 else np.linalg.norm(current - transformed[index - 1], axis=1)
        )
        displacement_rows.append(
            {
                "absolute_displacement_median_mm": float(np.percentile(absolute, 50)),
                "absolute_displacement_p95_mm": float(np.percentile(absolute, 95)),
                "absolute_displacement_max_mm": float(np.max(absolute)),
                "framewise_displacement_median_mm": float(np.percentile(framewise, 50)),
                "framewise_displacement_p95_mm": float(np.percentile(framewise, 95)),
                "framewise_displacement_max_mm": float(np.max(framewise)),
            }
        )
    displacement = pd.DataFrame(displacement_rows)
    motion_path = Path(motion_out).expanduser().resolve()
    displacement_path = Path(displacement_out).expanduser().resolve()
    motion_path.parent.mkdir(parents=True, exist_ok=True)
    displacement_path.parent.mkdir(parents=True, exist_ok=True)
    motion.to_csv(motion_path, sep="\t", index=False, float_format="%.10g")
    displacement.to_csv(displacement_path, sep="\t", index=False, float_format="%.10g")
    return motion_path, displacement_path


def _nrmse(candidate: Path, oracle: Path) -> float:
    candidate_data = np.asanyarray(nb.load(str(candidate)).dataobj, dtype=np.float64)
    oracle_data = np.asanyarray(nb.load(str(oracle)).dataobj, dtype=np.float64)
    finite = np.isfinite(candidate_data) & np.isfinite(oracle_data)
    if not finite.any():
        return math.inf
    denominator = max(float(np.std(oracle_data[finite])), float(np.finfo(float).eps))
    return float(np.sqrt(np.mean((candidate_data[finite] - oracle_data[finite]) ** 2)) / denominator)


def _select_transform_order(
    volumes: Sequence[Path],
    affines: Sequence[Path],
    field_warp: Path,
    reference: Path,
    output_dir: Path,
    *,
    interpolation: str,
    threads: int,
    maximum_nrmse: float,
) -> tuple[str, Path]:
    sample_indices = sorted({0, len(volumes) // 2, len(volumes) - 1})
    scores: dict[str, list[float]] = {"affine_then_sdc": [], "sdc_then_affine": []}
    for index in sample_indices:
        sample_dir = ensure_dir(output_dir / f"vol-{index:05d}")
        sdc = _apply_transforms(
            volumes[index], volumes[index], sample_dir / "sequential_sdc.nii.gz", [field_warp],
            interpolation="Linear", threads=threads, log_file=sample_dir / "sequential_sdc.log"
        )
        oracle = _apply_transforms(
            sdc, reference, sample_dir / "sequential_sdc_then_motion.nii.gz", [affines[index]],
            interpolation=interpolation, threads=threads, log_file=sample_dir / "sequential_motion.log"
        )
        candidates = {
            "affine_then_sdc": [affines[index], field_warp],
            "sdc_then_affine": [field_warp, affines[index]],
        }
        for label, transforms in candidates.items():
            candidate = _apply_transforms(
                volumes[index], reference, sample_dir / f"candidate_{label}.nii.gz", transforms,
                interpolation=interpolation, threads=threads, log_file=sample_dir / f"candidate_{label}.log"
            )
            scores[label].append(_nrmse(candidate, oracle))
    mean_scores = {key: float(np.mean(value)) for key, value in scores.items()}
    selected = min(mean_scores, key=mean_scores.get)
    sorted_scores = sorted(mean_scores.values())
    ambiguous = len(sorted_scores) > 1 and abs(sorted_scores[1] - sorted_scores[0]) < 1e-6
    if mean_scores[selected] > maximum_nrmse:
        raise ValidationError(
            "Neither one-call ANTs transform ordering reproduced the sequential SDC-then-motion "
            f"oracle within threshold {maximum_nrmse}: {mean_scores}"
        )
    payload = {
        "selected": selected,
        "ambiguous": ambiguous,
        "sample_indices": sample_indices,
        "normalized_rmse": scores,
        "mean_normalized_rmse": mean_scores,
        "maximum_allowed_nrmse": maximum_nrmse,
    }
    return selected, write_json(output_dir / "transform_order.json", payload)


def _clip_negative(path: Path) -> Path:
    image = nb.load(str(path))
    data = np.asanyarray(image.dataobj, dtype=np.float32)
    if np.any(data < 0):
        data[data < 0] = 0
        nb.Nifti1Image(data, image.affine, image.header).to_filename(path)
    return path


def run_motion_stage(
    nordic_bold: str,
    field_warp: str,
    output_dir: str,
    ants_config: dict[str, Any],
    resampling_config: dict[str, Any],
    execution_config: dict[str, Any],
    reference_outputs: dict[str, Any] | None = None,
    progress_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate rigid motion and resample originals once.

    When ``reference_outputs`` is ``None``, this run creates a robust two-pass
    reference and becomes the reference run. When it is supplied, every volume
    is registered directly to that already-undistorted reference. This makes
    absolute pose estimates and corrected voxel grids comparable across runs.
    """
    if bool(resampling_config.get("jacobian_modulation", False)):
        raise ValidationError(
            "Jacobian intensity modulation is not implemented in this release; set it to false"
        )
    require_executable("antsRegistration")
    require_executable("antsApplyTransforms")
    root = ensure_dir(output_dir)
    bold = Path(nordic_bold).resolve()
    warp = Path(field_warp).resolve()
    workers = int(execution_config.get("volume_workers", 1))
    threads = int(execution_config.get("threads_per_ants", 1))
    preview_interpolation = str(ants_config.get("preview_interpolation", "Linear"))
    final_interpolation = str(resampling_config.get("interpolation", "LanczosWindowedSinc"))
    volumes = _split_4d(bold, root / "volumes")
    from .progress import emit_progress, milestone_callback

    def make_preview(item: tuple[int, Path]) -> Path:
        index, volume = item
        return _apply_transforms(
            volume,
            volume,
            root / "previews" / f"vol-{index:05d}.nii.gz",
            [warp],
            interpolation=preview_interpolation,
            threads=threads,
            log_file=root / "previews" / f"vol-{index:05d}.log",
        )

    previews = _parallel_map(
        make_preview,
        list(enumerate(volumes)),
        workers,
        milestone_callback(
            progress_context, "motion correction and resampling", "SDC previews"
        ),
    )
    preview_bold = _merge_3d(previews, bold, root / "preview_sdc_bold.nii.gz")

    if reference_outputs is None:
        reference_mode = "created_from_this_run"
        initial_reference = _mean_reference(previews, root / "reference" / "initial.nii.gz")
        initial_mask = _brain_mask(
            initial_reference, root / "reference" / "initial_mask.nii.gz"
        )

        def pass1(item: tuple[int, Path]) -> tuple[Path, Path]:
            index, preview = item
            return _register_rigid(
                preview,
                initial_reference,
                root / "registration_pass1",
                index=index,
                config=ants_config,
                threads=threads,
                fixed_mask=(
                    initial_mask if ants_config.get("use_registration_mask", True) else None
                ),
            )

        first_pass = _parallel_map(
            pass1,
            list(enumerate(previews)),
            workers,
            milestone_callback(
                progress_context,
                "motion correction and resampling",
                "motion-reference pass 1",
            ),
        )
        first_affines = [item[0] for item in first_pass]
        first_warped = [item[1] for item in first_pass]
        pass1_reference = _mean_reference(
            first_warped, root / "reference" / "pass1.nii.gz"
        )
        pass1_mask = _brain_mask(
            pass1_reference, root / "reference" / "pass1_mask.nii.gz"
        )
        if bool(ants_config.get("two_pass", True)):

            def pass2(item: tuple[int, Path]) -> tuple[Path, Path]:
                index, preview = item
                return _register_rigid(
                    preview,
                    pass1_reference,
                    root / "registration_pass2",
                    index=index,
                    config=ants_config,
                    threads=threads,
                    fixed_mask=(
                        pass1_mask if ants_config.get("use_registration_mask", True) else None
                    ),
                )

            second_pass = _parallel_map(
                pass2,
                list(enumerate(previews)),
                workers,
                milestone_callback(
                    progress_context,
                    "motion correction and resampling",
                    "motion-reference pass 2",
                ),
            )
            final_affines = [item[0] for item in second_pass]
            bold_reference = _mean_reference(
                [item[1] for item in second_pass],
                root / "reference" / "bold_reference.nii.gz",
            )
        else:
            final_affines = first_affines
            bold_reference = pass1_reference
        brain_mask = _brain_mask(
            bold_reference, root / "reference" / "brain_mask.nii.gz"
        )
        reference_source_bold = str(bold)
    else:
        reference_mode = "shared_session_reference"
        try:
            bold_reference = Path(reference_outputs["bold_reference"]).resolve()
            brain_mask = Path(reference_outputs["brain_mask"]).resolve()
        except (KeyError, TypeError) as error:
            raise ValidationError(
                "Shared-reference motion outputs are missing bold_reference or brain_mask"
            ) from error
        if not bold_reference.is_file() or not brain_mask.is_file():
            raise ValidationError(
                f"Shared motion reference is incomplete: {bold_reference}, {brain_mask}"
            )
        if not same_nifti_grid(previews[0], bold_reference):
            raise ValidationError(
                "All runs sharing a motion reference must use the same BOLD grid. "
                f"Preview {previews[0]} differs from reference {bold_reference}."
            )

        def register_to_shared(item: tuple[int, Path]) -> tuple[Path, Path]:
            index, preview = item
            return _register_rigid(
                preview,
                bold_reference,
                root / "registration_to_shared_reference",
                index=index,
                config=ants_config,
                threads=threads,
                fixed_mask=(
                    brain_mask if ants_config.get("use_registration_mask", True) else None
                ),
            )

        registered = _parallel_map(
            register_to_shared,
            list(enumerate(previews)),
            workers,
            milestone_callback(
                progress_context,
                "motion correction and resampling",
                "registration to shared reference",
            ),
        )
        final_affines = [item[0] for item in registered]
        reference_source_bold = str(
            reference_outputs.get("reference_source_bold", bold_reference)
        )

    emit_progress(
        progress_context,
        "motion correction and resampling",
        "progress",
        message="validating transform order",
        completed=1,
        total=1,
    )
    selected_order, transform_order_json = _select_transform_order(
        volumes,
        final_affines,
        warp,
        bold_reference,
        root / "transform_order",
        interpolation=final_interpolation,
        threads=threads,
        maximum_nrmse=float(
            resampling_config.get("maximum_transform_order_nrmse", 0.25)
        ),
    )

    def final_resample(item: tuple[int, Path]) -> Path:
        index, volume = item
        transforms = (
            [final_affines[index], warp]
            if selected_order == "affine_then_sdc"
            else [warp, final_affines[index]]
        )
        output = _apply_transforms(
            volume,
            bold_reference,
            root / "corrected_volumes" / f"vol-{index:05d}.nii.gz",
            transforms,
            interpolation=final_interpolation,
            threads=threads,
            log_file=root / "corrected_volumes" / f"vol-{index:05d}.log",
        )
        if not bool(resampling_config.get("allow_negative_values", False)):
            _clip_negative(output)
        return output

    corrected_volumes = _parallel_map(
        final_resample,
        list(enumerate(volumes)),
        workers,
        milestone_callback(
            progress_context,
            "motion correction and resampling",
            "final one-step resampling",
        ),
    )
    corrected_bold = _merge_3d(
        corrected_volumes, bold, root / "desc-preproc_bold.nii.gz"
    )
    stable_xfm_dir = ensure_dir(root / "rigid_xfms")
    stable_affines: list[Path] = []
    for index, source in enumerate(final_affines):
        target = stable_xfm_dir / (
            f"vol-{index:05d}_from-distorted_to-sharedReference_mode-image.mat"
        )
        shutil.copy2(source, target)
        stable_affines.append(target.resolve())
    motion_tsv, displacement_tsv = write_motion_metrics(
        stable_affines,
        brain_mask,
        root / "motion_timeseries.tsv",
        root / "displacement_timeseries.tsv",
        fd_radius_mm=float(ants_config.get("fd_radius_mm", 50.0)),
    )
    return {
        "preview_bold": str(preview_bold),
        "corrected_bold": str(corrected_bold),
        "bold_reference": str(bold_reference),
        "brain_mask": str(brain_mask),
        "rigid_affines": [str(path) for path in stable_affines],
        "rigid_xfms_dir": str(stable_xfm_dir),
        "motion_tsv": str(motion_tsv),
        "displacement_tsv": str(displacement_tsv),
        "transform_order_json": str(transform_order_json),
        "functional_volume_count": len(volumes),
        "reference_mode": reference_mode,
        "reference_source_bold": reference_source_bold,
    }
