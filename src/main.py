"""CLI parsing and application orchestration.

This is the sole executable entry point (architecture.md section 3). Run
under Blender in background mode:

    Blender --background --factory-startup --python src\\main.py -- ^
        --output "C:\\datasets\\primitive_clean" --samples-per-primitive 100

Blender-specific arguments are separated from application arguments by a
literal ``--``; only arguments after it are parsed here.
"""

from __future__ import annotations

import argparse
import datetime
import random
import shutil
import sys
import traceback
from pathlib import Path

# Allow "import config", "import primitives", etc. as plain top-level modules
# regardless of Blender's working directory, matching the existing pipeline
# convention (see Animation_Mesh_Pipeline/stage2_dirty/batch_apply_dirty.py).
_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import bpy  # noqa: E402  (must follow sys.path setup for local imports below)

import exporter  # noqa: E402
import primitives  # noqa: E402
import quadify  # noqa: E402
import validation  # noqa: E402
import variations  # noqa: E402
from config import (  # noqa: E402
    ALL_PRIMITIVES,
    CLOSED_PRIMITIVES,
    ConfigError,
    GenerationConfig,
    MERGE_DISTANCE_RELATIVE,
    derive_seed,
    parse_primitive_subset,
    sample_id_for,
    validate_config,
)
from manifest import (  # noqa: E402
    ManifestWriter,
    append_error,
    build_sample_metadata,
    sample_is_complete_for_resume,
    setup_logging,
    write_json,
)


class SampleGenerationFailed(Exception):
    """Raised when a sample exhausts --max-attempts without acceptance."""


class FailFastStop(Exception):
    """Raised internally to unwind the generation loop on --fail-fast."""


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _extract_app_args(argv: list[str]) -> list[str]:
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return argv[1:]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="primitive_dataset_generator",
        description="Generate a validated, quad-only primitive mesh dataset.",
    )
    parser.add_argument("-o", "--output", required=True, type=str, help="Dataset output root.")
    parser.add_argument("--samples-per-primitive", type=int, default=100)
    parser.add_argument(
        "--primitives", type=str, default=None, help="Comma-separated subset of: " + ",".join(ALL_PRIMITIVES)
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--subdivision-min", type=int, default=1)
    parser.add_argument("--subdivision-max", type=int, default=3)
    parser.add_argument("--max-attempts", type=int, default=20)
    parser.add_argument("--filename", type=str, default="clean.fbx")

    exclusive = parser.add_mutually_exclusive_group()
    exclusive.add_argument("--overwrite", action="store_true")
    exclusive.add_argument("--resume", action="store_true")

    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--keep-failed", action="store_true")
    return parser


def build_config(args: argparse.Namespace) -> GenerationConfig:
    primitives_subset = parse_primitive_subset(args.primitives)
    config = GenerationConfig(
        output=Path(args.output),
        samples_per_primitive=args.samples_per_primitive,
        primitives=primitives_subset,
        seed=args.seed,
        start_index=args.start_index,
        subdivision_min=args.subdivision_min,
        subdivision_max=args.subdivision_max,
        max_attempts=args.max_attempts,
        filename=args.filename,
        overwrite=args.overwrite,
        resume=args.resume,
        fail_fast=args.fail_fast,
        keep_failed=args.keep_failed,
    )
    validate_config(config)
    return config


def ensure_output_root(config: GenerationConfig) -> Path:
    output_root = config.output.resolve()
    if output_root.exists():
        if any(output_root.iterdir()) and not (config.resume or config.overwrite):
            raise ConfigError(
                f"output root {output_root} exists and is not empty; "
                "pass --resume or --overwrite"
            )
    else:
        output_root.mkdir(parents=True)
    return output_root


# ---------------------------------------------------------------------------
# Scene management
# ---------------------------------------------------------------------------


def reset_scene() -> None:
    """Clear all objects and purge orphaned datablocks (section 12)."""

    if bpy.context.selected_objects or bpy.context.scene.objects:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
    for _ in range(3):
        try:
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
        except Exception:
            break


def apply_all_modifiers_and_transform(obj: "bpy.types.Object") -> None:
    bpy.context.view_layer.objects.active = obj
    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)

    while obj.modifiers:
        bpy.ops.object.modifier_apply(modifier=obj.modifiers[0].name)

    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def save_failed_snapshot(output_root: Path, sample_id: str, attempt: int) -> None:
    failed_dir = output_root / "_failed_snapshots"
    failed_dir.mkdir(parents=True, exist_ok=True)
    path = failed_dir / f"{sample_id}_attempt{attempt:02d}.blend"
    try:
        bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    except Exception:
        pass


def verify_exported_fbx(filepath: Path, primitive: str, max_polygon_count: int) -> None:
    """Re-import the exported FBX into an empty scene and re-run structural
    validation (architecture.md section 10, final paragraph)."""

    reset_scene()
    try:
        bpy.ops.import_scene.fbx(filepath=str(filepath))
        mesh_objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        if len(mesh_objects) != 1:
            raise RuntimeError(f"re-imported FBX has {len(mesh_objects)} mesh objects, expected 1")
        obj = mesh_objects[0]
        result = validation.validate_mesh(
            obj,
            primitive,
            is_closed_family=primitive in CLOSED_PRIMITIVES,
            max_polygon_count=max_polygon_count,
        )
        if not result.ok:
            raise RuntimeError(f"re-imported FBX failed validation: {result.reason}")
    finally:
        reset_scene()


# ---------------------------------------------------------------------------
# Per-sample generation
# ---------------------------------------------------------------------------


def generate_sample(
    primitive: str,
    index: int,
    config: GenerationConfig,
    output_root: Path,
    logger,
    errors_path: Path,
) -> str:
    sample_id = sample_id_for(primitive, index)
    final_dir = output_root / sample_id

    if config.resume and sample_is_complete_for_resume(final_dir, sample_id, config.filename):
        logger.info(f"{sample_id}: already valid, skipping (resume)")
        return sample_id

    # Remove any stale temp directories left behind by a previous crashed
    # run for this exact sample ID (architecture.md section 13). Only ever
    # matches our own ".<sample_id>.tmp-<pid>" naming scheme.
    for stale in output_root.glob(f".{sample_id}.tmp-*"):
        shutil.rmtree(stale, ignore_errors=True)

    last_reason = "unknown"
    for attempt in range(1, config.max_attempts + 1):
        seed = derive_seed(config.seed, primitive, index, attempt)
        rng = random.Random(seed)
        phase = "create"
        temp_dir = exporter.temp_sample_dir(output_root, sample_id)
        try:
            reset_scene()

            obj, creation_params = primitives.create_primitive(primitive, rng, config.primitive_bounds)

            phase = "quadify"
            requested_level = rng.randint(config.subdivision_min, config.subdivision_max)
            subdivision_level = quadify.resolve_subdivision_level(obj, requested_level)
            quadify.apply_subdivision(obj, subdivision_level)

            variation_params = variations.sample_variation_params(rng, config.deformation_bounds)
            variations.apply_scale_and_rotation(obj, variation_params)
            diagonal = variations.bbox_diagonal(obj)
            textures = variations.add_deformation_modifiers(obj, variation_params, diagonal)

            apply_all_modifiers_and_transform(obj)
            for texture in textures:
                if texture.users == 0:
                    bpy.data.textures.remove(texture)

            quadify.rename_to_mesh(obj)
            final_diagonal = variations.bbox_diagonal(obj)
            quadify.finalize_topology(obj, final_diagonal, MERGE_DISTANCE_RELATIVE)

            phase = "validate"
            scene_result = validation.validate_single_mesh_scene()
            if not scene_result.ok:
                raise RuntimeError(scene_result.reason)
            mesh_result = validation.validate_mesh(
                obj,
                primitive,
                is_closed_family=primitive in CLOSED_PRIMITIVES,
                max_polygon_count=config.max_polygon_count,
            )
            if not mesh_result.ok:
                raise RuntimeError(mesh_result.reason)

            phase = "export"
            exporter.prepare_temp_dir(temp_dir)
            fbx_path = temp_dir / config.filename
            exporter.export_fbx(obj, fbx_path)

            metadata = build_sample_metadata(
                sample_id=sample_id,
                primitive=primitive,
                index=index,
                seed=seed,
                generator_parameters=creation_params,
                variation_parameters=variation_params.to_metadata(),
                subdivision_level=subdivision_level,
                vertex_count=mesh_result.stats["vertex_count"],
                edge_count=mesh_result.stats["edge_count"],
                face_count=mesh_result.stats["face_count"],
                bbox_dimensions=tuple(obj.dimensions),
                filename=config.filename,
            )
            write_json(temp_dir / "metadata.json", metadata)

            phase = "verify"
            verify_exported_fbx(fbx_path, primitive, config.max_polygon_count)

            exporter.finalize_sample_dir(
                temp_dir,
                final_dir,
                replace_existing=config.overwrite or config.resume,
            )
            logger.info(f"{sample_id}: accepted attempt={attempt} seed={seed}")
            return sample_id

        except Exception as exc:  # noqa: BLE001 - candidate failures are retried
            last_reason = str(exc)
            logger.warning(f"{sample_id}: attempt={attempt} phase={phase} failed: {exc}")
            append_error(
                errors_path,
                {
                    "timestamp": _utc_now_iso(),
                    "sample_id": sample_id,
                    "seed": seed,
                    "attempt": attempt,
                    "phase": phase,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
            if config.keep_failed:
                save_failed_snapshot(output_root, sample_id, attempt)
            exporter.cleanup_temp_dir(temp_dir)
            reset_scene()

    raise SampleGenerationFailed(f"{sample_id}: exhausted {config.max_attempts} attempts ({last_reason})")


# ---------------------------------------------------------------------------
# Top-level run
# ---------------------------------------------------------------------------


def run(config: GenerationConfig) -> int:
    output_root = ensure_output_root(config)
    logger = setup_logging(output_root / "run.log")
    errors_path = output_root / "errors.jsonl"

    logger.info(
        f"starting run: primitives={list(config.primitives)} "
        f"samples_per_primitive={config.samples_per_primitive} seed={config.seed}"
    )

    manifest = ManifestWriter(
        output_root=output_root,
        config=config,
        blender_version=bpy.app.version_string,
        start_time_iso=_utc_now_iso(),
    )

    any_missing = False
    fatal_error: Exception | None = None
    try:
        for primitive in config.primitives:
            for offset in range(config.samples_per_primitive):
                index = config.start_index + offset
                try:
                    sample_id = generate_sample(primitive, index, config, output_root, logger, errors_path)
                    manifest.record_accepted(sample_id)
                except SampleGenerationFailed as exc:
                    logger.error(str(exc))
                    manifest.record_failed()
                    any_missing = True
                    if config.fail_fast:
                        logger.error("--fail-fast: stopping run after first rejected/failed sample")
                        raise FailFastStop() from exc
    except FailFastStop:
        pass
    except Exception as exc:  # noqa: BLE001 - fatal, non-retryable runtime error
        fatal_error = exc
        logger.error(f"fatal error: {exc}\n{traceback.format_exc()}")
    finally:
        manifest.write(end_time_iso=_utc_now_iso())

    logger.info(f"run complete: accepted={manifest.accepted_count} failed={manifest.failed_count}")

    if fatal_error is not None:
        return 1
    if any_missing:
        return 1
    return 0


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(_extract_app_args(sys.argv))

    try:
        config = build_config(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    try:
        exit_code = run(config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
