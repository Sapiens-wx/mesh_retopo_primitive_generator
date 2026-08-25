"""Configuration dataclasses, CLI validation, and deterministic seed derivation.

This module has no dependency on ``bpy``/``bmesh`` so it can be imported (and
its validation logic exercised) outside of Blender.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

ALL_PRIMITIVES: tuple[str, ...] = (
    "cube",
    "sphere",
    "torus",
    "cylinder",
    "monkey",
    "cone",
    "plane",
)

#: Closed primitive families that must pass manifold/watertight validation.
CLOSED_PRIMITIVES: frozenset[str] = frozenset(
    {"cube", "sphere", "torus", "cylinder", "monkey", "cone"}
)

#: Primitive families that Blender may create with triangles or n-gons and
#: therefore require at least one Catmull-Clark subdivision level to become
#: strictly quad-only. Kept for documentation; the generator also performs a
#: runtime check of the intermediate mesh rather than trusting this list
#: blindly (e.g. a beveled cube also needs forced subdivision).
NON_QUAD_NATIVE_PRIMITIVES: frozenset[str] = frozenset(
    {"sphere", "cylinder", "cone", "monkey"}
)

SCHEMA_VERSION = 1

# Safety limits shared by all primitive families. These are intentionally not
# exposed as CLI flags (see architecture.md section 15): raising them is a
# deliberate code change, not an accidental one.
MAX_POLYGON_COUNT = 20000
MERGE_DISTANCE_RELATIVE = 1e-5
MERGE_DISTANCE_ABSOLUTE_FLOOR = 1e-6
EDGE_LENGTH_EPS_RELATIVE = 1e-6
FACE_AREA_EPS_RELATIVE = 1e-6


class ConfigError(ValueError):
    """Raised for any invalid CLI configuration. Fatal, non-retryable."""


@dataclass(frozen=True)
class DeformationBounds:
    """Bounded ranges for topology-safe geometric variation.

    All ranges are either absolute multipliers/angles or expressed relative
    to the candidate's bounding-box diagonal, so amplitude scales safely with
    object size regardless of primitive family.
    """

    uniform_scale_range: tuple[float, float] = (0.7, 1.5)
    axis_proportion_range: tuple[float, float] = (0.75, 1.35)
    taper_factor_range: tuple[float, float] = (-0.35, 0.35)
    twist_angle_deg_range: tuple[float, float] = (-60.0, 60.0)
    bend_angle_deg_range: tuple[float, float] = (-45.0, 45.0)
    normal_displace_relative_range: tuple[float, float] = (0.0, 0.02)
    lattice_displace_relative_range: tuple[float, float] = (0.0, 0.015)
    rotation_enabled: bool = True


@dataclass(frozen=True)
class PrimitiveBounds:
    """Sampling ranges for each primitive family's creation parameters."""

    # sphere
    sphere_segments_range: tuple[int, int] = (8, 32)
    sphere_rings_range: tuple[int, int] = (6, 24)
    sphere_radius_range: tuple[float, float] = (0.5, 1.5)
    # torus
    torus_major_segments_range: tuple[int, int] = (12, 48)
    torus_minor_segments_range: tuple[int, int] = (6, 24)
    torus_major_radius_range: tuple[float, float] = (0.8, 1.6)
    torus_minor_radius_ratio_range: tuple[float, float] = (0.15, 0.4)
    # cylinder
    cylinder_vertices_range: tuple[int, int] = (8, 32)
    cylinder_radius_range: tuple[float, float] = (0.4, 1.2)
    cylinder_depth_range: tuple[float, float] = (0.8, 2.4)
    # cone
    cone_vertices_range: tuple[int, int] = (8, 32)
    cone_radius1_range: tuple[float, float] = (0.5, 1.3)
    cone_radius2_range: tuple[float, float] = (0.0, 0.4)
    cone_depth_range: tuple[float, float] = (0.8, 2.4)
    # cube
    cube_size_range: tuple[float, float] = (1.0, 2.0)
    cube_bevel_width_range: tuple[float, float] = (0.0, 0.12)
    cube_bevel_segments_range: tuple[int, int] = (0, 3)
    # plane / grid
    plane_size_range: tuple[float, float] = (1.0, 2.5)
    # Blender's grid operator interprets these as vertex counts per axis;
    # at least two are required to create a face.
    plane_subdivisions_range: tuple[int, int] = (2, 12)
    # monkey
    monkey_scale_range: tuple[float, float] = (0.8, 1.4)


@dataclass
class GenerationConfig:
    """Fully validated, immutable-in-practice run configuration."""

    output: Path
    samples_per_primitive: int = 100
    primitives: tuple[str, ...] = ALL_PRIMITIVES
    seed: int = 0
    start_index: int = 0
    subdivision_min: int = 1
    subdivision_max: int = 3
    max_attempts: int = 20
    filename: str = "clean.fbx"
    overwrite: bool = False
    resume: bool = False
    fail_fast: bool = False
    keep_failed: bool = False

    deformation_bounds: DeformationBounds = field(default_factory=DeformationBounds)
    primitive_bounds: PrimitiveBounds = field(default_factory=PrimitiveBounds)
    max_polygon_count: int = MAX_POLYGON_COUNT


def parse_primitive_subset(raw: str | None) -> tuple[str, ...]:
    """Parse ``--primitives`` into a validated, order-preserving tuple."""

    if raw is None or raw.strip() == "":
        return ALL_PRIMITIVES
    names = [chunk.strip() for chunk in raw.split(",")]
    names = [name for name in names if name != ""]
    if not names:
        raise ConfigError("--primitives must not resolve to an empty list")
    unknown = [name for name in names if name not in ALL_PRIMITIVES]
    if unknown:
        raise ConfigError(
            f"Unknown primitive name(s): {', '.join(unknown)}. "
            f"Valid names: {', '.join(ALL_PRIMITIVES)}"
        )
    deduped = tuple(dict.fromkeys(names))
    return deduped


def validate_config(config: GenerationConfig) -> None:
    """Validate a fully constructed :class:`GenerationConfig`.

    Raises :class:`ConfigError` on any violation. Must be called before any
    Blender scene work begins (architecture.md section 5/15).
    """

    if not config.primitives:
        raise ConfigError("--primitives must not be empty")
    unknown = [name for name in config.primitives if name not in ALL_PRIMITIVES]
    if unknown:
        raise ConfigError(f"Unknown primitive name(s): {', '.join(unknown)}")

    if config.samples_per_primitive <= 0:
        raise ConfigError("--samples-per-primitive must be a positive integer")

    if config.start_index < 0:
        raise ConfigError("--start-index must be non-negative")

    if config.subdivision_min < 0 or config.subdivision_max < 0:
        raise ConfigError("subdivision levels must be non-negative")
    if config.subdivision_min > config.subdivision_max:
        raise ConfigError("--subdivision-min must not exceed --subdivision-max")
    if config.subdivision_max > 6:
        raise ConfigError(
            "--subdivision-max above 6 is rejected: polygon counts grow "
            "exponentially with Catmull-Clark subdivision level"
        )

    if config.max_attempts <= 0:
        raise ConfigError("--max-attempts must be a positive integer")

    if config.overwrite and config.resume:
        raise ConfigError("--overwrite and --resume are mutually exclusive")

    filename = config.filename
    basename = Path(filename).name
    if not filename or basename != filename:
        raise ConfigError("--filename must be a bare basename, not a path")
    if not basename.lower().endswith(".fbx"):
        raise ConfigError("--filename must end with .fbx")
    if any(sep in basename for sep in ("..", "/", "\\", ":")):
        raise ConfigError(f"unsafe --filename value: {filename!r}")

    # Requested sample counts cannot produce duplicate sample IDs: indexes are
    # start_index .. start_index + samples_per_primitive - 1 per primitive,
    # which are unique by construction, so nothing further to check here
    # beyond the bounds already validated above.

    bounds = config.primitive_bounds
    _validate_range(bounds.sphere_segments_range, "sphere_segments_range", minimum=3)
    _validate_range(bounds.sphere_rings_range, "sphere_rings_range", minimum=3)
    _validate_range(bounds.torus_major_segments_range, "torus_major_segments_range", minimum=3)
    _validate_range(bounds.torus_minor_segments_range, "torus_minor_segments_range", minimum=3)
    _validate_range(bounds.cylinder_vertices_range, "cylinder_vertices_range", minimum=3)
    _validate_range(bounds.cone_vertices_range, "cone_vertices_range", minimum=3)
    _validate_range(bounds.plane_subdivisions_range, "plane_subdivisions_range", minimum=2)
    _validate_range(bounds.cube_bevel_segments_range, "cube_bevel_segments_range", minimum=0)

    if config.max_polygon_count <= 0:
        raise ConfigError("max_polygon_count must be positive")


def _validate_range(rng: Sequence[float], name: str, minimum: float) -> None:
    lo, hi = rng
    if lo > hi:
        raise ConfigError(f"{name}: minimum ({lo}) exceeds maximum ({hi})")
    if lo < minimum:
        raise ConfigError(f"{name}: minimum ({lo}) is below the required floor ({minimum})")


def derive_seed(root_seed: int, primitive: str, index: int, attempt: int) -> int:
    """Derive a deterministic per-candidate seed with a stable hash.

    Uses BLAKE2b (not Python's process-randomized ``hash()``) so that results
    are reproducible across processes and Python versions, and so that adding
    or removing another primitive family does not change the seed sequence of
    an existing family.
    """

    payload = f"{root_seed}|{primitive}|{index}|{attempt}".encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


def sample_id_for(primitive: str, index: int) -> str:
    return f"{primitive}_{index:06d}"
