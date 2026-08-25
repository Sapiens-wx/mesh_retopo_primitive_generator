"""Parameter sampling and topology-safe geometric deformations.

Deformations here never add, remove, or reorder vertices/edges/faces: scale
and rotation are plain object-transform properties, and taper/twist/bend/
displacement are implemented with Blender modifiers that only reposition
existing vertices (Simple Deform, Displace). All amplitude bounds are
expressed relative to the candidate's bounding-box diagonal so they scale
safely with object size (architecture.md section 8).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

import bpy

from config import DeformationBounds


def bbox_diagonal(obj: "bpy.types.Object") -> float:
    """World-space bounding-box diagonal length of ``obj``."""

    dims = obj.dimensions
    return math.sqrt(dims.x**2 + dims.y**2 + dims.z**2)


@dataclass
class VariationParams:
    uniform_scale: float
    axis_scale: tuple[float, float, float]
    rotation_euler_rad: tuple[float, float, float]
    taper_enabled: bool
    taper_factor: float
    taper_axis: str
    twist_enabled: bool
    twist_angle_deg: float
    twist_axis: str
    bend_enabled: bool
    bend_angle_deg: float
    bend_axis: str
    normal_displace_enabled: bool
    normal_displace_strength: float
    lattice_displace_enabled: bool
    lattice_displace_strength: float

    def to_metadata(self) -> dict[str, Any]:
        return {
            "uniform_scale": self.uniform_scale,
            "axis_scale": list(self.axis_scale),
            "rotation_euler_rad": list(self.rotation_euler_rad),
            "taper": {
                "enabled": self.taper_enabled,
                "factor": self.taper_factor,
                "axis": self.taper_axis,
            },
            "twist": {
                "enabled": self.twist_enabled,
                "angle_deg": self.twist_angle_deg,
                "axis": self.twist_axis,
            },
            "bend": {
                "enabled": self.bend_enabled,
                "angle_deg": self.bend_angle_deg,
                "axis": self.bend_axis,
            },
            "normal_displace": {
                "enabled": self.normal_displace_enabled,
                "strength": self.normal_displace_strength,
            },
            "lattice_displace": {
                "enabled": self.lattice_displace_enabled,
                "strength": self.lattice_displace_strength,
            },
        }


_AXES = ("X", "Y", "Z")

# Probability that each optional (topology-preserving but shape-altering)
# deformation is included for a given candidate. Kept independent so
# combinations are exercised across the dataset.
_TAPER_PROBABILITY = 0 # 0.5
_TWIST_PROBABILITY = 0 # 0.5
_BEND_PROBABILITY = 0.4
_NORMAL_DISPLACE_PROBABILITY = 0 # 0.5
_LATTICE_DISPLACE_PROBABILITY = 0 # 0.35


def sample_variation_params(rng: random.Random, bounds: DeformationBounds) -> VariationParams:
    uniform_scale = rng.uniform(*bounds.uniform_scale_range)
    axis_scale = (
        rng.uniform(*bounds.axis_proportion_range),
        rng.uniform(*bounds.axis_proportion_range),
        rng.uniform(*bounds.axis_proportion_range),
    )
    if bounds.rotation_enabled:
        rotation = (
            rng.uniform(0.0, 2.0 * math.pi),
            rng.uniform(0.0, 2.0 * math.pi),
            rng.uniform(0.0, 2.0 * math.pi),
        )
    else:
        rotation = (0.0, 0.0, 0.0)

    taper_enabled = rng.random() < _TAPER_PROBABILITY
    twist_enabled = rng.random() < _TWIST_PROBABILITY
    bend_enabled = rng.random() < _BEND_PROBABILITY
    normal_displace_enabled = rng.random() < _NORMAL_DISPLACE_PROBABILITY
    lattice_displace_enabled = rng.random() < _LATTICE_DISPLACE_PROBABILITY

    return VariationParams(
        uniform_scale=uniform_scale,
        axis_scale=axis_scale,
        rotation_euler_rad=rotation,
        taper_enabled=taper_enabled,
        taper_factor=rng.uniform(*bounds.taper_factor_range) if taper_enabled else 0.0,
        taper_axis=rng.choice(_AXES),
        twist_enabled=twist_enabled,
        twist_angle_deg=rng.uniform(*bounds.twist_angle_deg_range) if twist_enabled else 0.0,
        twist_axis=rng.choice(_AXES),
        bend_enabled=bend_enabled,
        bend_angle_deg=rng.uniform(*bounds.bend_angle_deg_range) if bend_enabled else 0.0,
        bend_axis=rng.choice(_AXES),
        normal_displace_enabled=normal_displace_enabled,
        normal_displace_strength=(
            rng.uniform(*bounds.normal_displace_relative_range) if normal_displace_enabled else 0.0
        ),
        lattice_displace_enabled=lattice_displace_enabled,
        lattice_displace_strength=(
            rng.uniform(*bounds.lattice_displace_relative_range) if lattice_displace_enabled else 0.0
        ),
    )


def apply_scale_and_rotation(obj: "bpy.types.Object", params: VariationParams) -> None:
    sx, sy, sz = params.axis_scale
    obj.scale = (
        params.uniform_scale * sx,
        params.uniform_scale * sy,
        params.uniform_scale * sz,
    )
    obj.rotation_euler = params.rotation_euler_rad


def _add_simple_deform(
    obj: "bpy.types.Object", name: str, deform_method: str, axis: str, *, angle_rad: float = 0.0, factor: float = 0.0
) -> None:
    modifier = obj.modifiers.new(name=name, type="SIMPLE_DEFORM")
    modifier.deform_method = deform_method
    modifier.deform_axis = axis
    if deform_method in ("TWIST", "BEND"):
        modifier.angle = angle_rad
    elif deform_method == "TAPER":
        modifier.factor = factor


def _add_displace(obj: "bpy.types.Object", name: str, strength: float, noise_scale: float) -> "bpy.types.Texture":
    texture = bpy.data.textures.new(name=f"{obj.name}_{name}_tex", type="CLOUDS")
    texture.noise_scale = noise_scale
    modifier = obj.modifiers.new(name=name, type="DISPLACE")
    modifier.texture = texture
    modifier.direction = "NORMAL"
    modifier.strength = strength
    modifier.mid_level = 0.5
    return texture


def add_deformation_modifiers(
    obj: "bpy.types.Object", params: VariationParams, diagonal: float
) -> list["bpy.types.Texture"]:
    """Add bounded, topology-preserving modifiers for later application.

    Returns any procedural textures created for Displace modifiers so callers
    can free them once the modifiers have been applied (they otherwise leak
    as orphaned datablocks across candidates).
    """

    textures: list["bpy.types.Texture"] = []

    if params.taper_enabled:
        _add_simple_deform(obj, "variation_taper", "TAPER", params.taper_axis, factor=params.taper_factor)
    if params.twist_enabled:
        _add_simple_deform(
            obj, "variation_twist", "TWIST", params.twist_axis, angle_rad=math.radians(params.twist_angle_deg)
        )
    if params.bend_enabled:
        _add_simple_deform(
            obj, "variation_bend", "BEND", params.bend_axis, angle_rad=math.radians(params.bend_angle_deg)
        )
    if params.normal_displace_enabled:
        strength = params.normal_displace_strength * diagonal
        # Small noise scale: high-frequency, low-amplitude surface detail.
        textures.append(_add_displace(obj, "variation_normal_displace", strength, noise_scale=0.5))
    if params.lattice_displace_enabled:
        strength = params.lattice_displace_strength * diagonal
        # Large noise scale: smooth, low-frequency global warp that reads as
        # a lattice-like bulk deformation rather than surface noise.
        textures.append(_add_displace(obj, "variation_lattice_displace", strength, noise_scale=4.0))

    return textures
