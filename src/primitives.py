"""Primitive-family creation.

Each ``create_*`` function creates exactly one active mesh object using
Blender's built-in primitive operators, centers it at the world origin, and
returns ``(object, creation_parameters)``. These functions do not export
files, do not apply subdivision/quadify logic, and do not mutate run-level
state; see ``quadify.py`` and ``variations.py`` for the following pipeline
stages.
"""

from __future__ import annotations

import random
from typing import Any, Callable

import bmesh
import bpy

from config import PrimitiveBounds


def _uniform(rng: random.Random, lo: float, hi: float) -> float:
    return rng.uniform(lo, hi)


def _randint(rng: random.Random, lo: int, hi: int) -> int:
    return rng.randint(lo, hi)


def _active_object() -> "bpy.types.Object":
    obj = bpy.context.view_layer.objects.active
    if obj is None:
        raise RuntimeError("primitive creation operator did not set an active object")
    return obj


def normalize_object_origin(obj: "bpy.types.Object") -> None:
    """Center the object's origin/geometry on the world origin.

    Blender's primitive operators are created at ``bpy.context.scene.cursor``
    (world origin) by default and already set the object origin to the
    geometry origin, but this is re-asserted defensively so later stages can
    assume the object sits at ``(0, 0, 0)`` with no residual offset.
    """

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    obj.location = (0.0, 0.0, 0.0)
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")
    obj.location = (0.0, 0.0, 0.0)


def create_cube(rng: random.Random, bounds: PrimitiveBounds) -> tuple["bpy.types.Object", dict[str, Any]]:
    size = _uniform(rng, *bounds.cube_size_range)
    bevel_width = _uniform(rng, *bounds.cube_bevel_width_range)
    bevel_segments = _randint(rng, *bounds.cube_bevel_segments_range)
    # A zero-width or zero-segment bevel is a no-op; skip it outright so the
    # cube can remain natively all-quad (subdivision level 0 becomes valid).
    apply_bevel = bevel_width > 1e-6 and bevel_segments > 0

    bpy.ops.mesh.primitive_cube_add(size=size, location=(0.0, 0.0, 0.0))
    obj = _active_object()

    if apply_bevel:
        mesh = obj.data
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.bevel(
            bm,
            geom=list(bm.edges),
            offset=bevel_width,
            segments=bevel_segments,
            affect="EDGES",
            clamp_overlap=True,
            loop_slide=True,
        )
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
    else:
        bevel_width = 0.0
        bevel_segments = 0

    normalize_object_origin(obj)
    params = {
        "size": size,
        "bevel_width": bevel_width,
        "bevel_segments": bevel_segments,
    }
    return obj, params


def create_sphere(rng: random.Random, bounds: PrimitiveBounds) -> tuple["bpy.types.Object", dict[str, Any]]:
    segments = _randint(rng, *bounds.sphere_segments_range)
    rings = _randint(rng, *bounds.sphere_rings_range)
    radius = _uniform(rng, *bounds.sphere_radius_range)

    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=segments, ring_count=rings, radius=radius, location=(0.0, 0.0, 0.0)
    )
    obj = _active_object()
    normalize_object_origin(obj)
    params = {"segments": segments, "rings": rings, "radius": radius}
    return obj, params


def create_torus(rng: random.Random, bounds: PrimitiveBounds) -> tuple["bpy.types.Object", dict[str, Any]]:
    major_segments = _randint(rng, *bounds.torus_major_segments_range)
    minor_segments = _randint(rng, *bounds.torus_minor_segments_range)
    major_radius = _uniform(rng, *bounds.torus_major_radius_range)
    minor_ratio = _uniform(rng, *bounds.torus_minor_radius_ratio_range)
    minor_radius = major_radius * minor_ratio
    # Required invariant: major_radius > minor_radius > 0.
    if minor_radius <= 0.0 or minor_radius >= major_radius:
        minor_radius = major_radius * 0.25

    bpy.ops.mesh.primitive_torus_add(
        major_radius=major_radius,
        minor_radius=minor_radius,
        major_segments=major_segments,
        minor_segments=minor_segments,
        mode="MAJOR_MINOR",
        location=(0.0, 0.0, 0.0),
    )
    obj = _active_object()
    normalize_object_origin(obj)
    params = {
        "major_radius": major_radius,
        "minor_radius": minor_radius,
        "major_segments": major_segments,
        "minor_segments": minor_segments,
    }
    return obj, params


def create_cylinder(rng: random.Random, bounds: PrimitiveBounds) -> tuple["bpy.types.Object", dict[str, Any]]:
    vertices = _randint(rng, *bounds.cylinder_vertices_range)
    radius = _uniform(rng, *bounds.cylinder_radius_range)
    depth = _uniform(rng, *bounds.cylinder_depth_range)
    # Both fill types remain quad-safe once Catmull-Clark subdivision is
    # applied afterwards (an n-gon or triangle fan cap becomes a fan of
    # quads meeting at the cap center).
    cap_fill_type = rng.choice(("NGON", "TRIFAN"))

    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices,
        radius=radius,
        depth=depth,
        end_fill_type=cap_fill_type,
        location=(0.0, 0.0, 0.0),
    )
    obj = _active_object()
    normalize_object_origin(obj)
    params = {
        "vertices": vertices,
        "radius": radius,
        "depth": depth,
        "cap_fill_type": cap_fill_type,
    }
    return obj, params


def create_cone(rng: random.Random, bounds: PrimitiveBounds) -> tuple["bpy.types.Object", dict[str, Any]]:
    vertices = _randint(rng, *bounds.cone_vertices_range)
    radius1 = _uniform(rng, *bounds.cone_radius1_range)
    radius2 = _uniform(rng, *bounds.cone_radius2_range)
    depth = _uniform(rng, *bounds.cone_depth_range)
    cap_fill_type = rng.choice(("NGON", "TRIFAN"))

    bpy.ops.mesh.primitive_cone_add(
        vertices=vertices,
        radius1=radius1,
        radius2=radius2,
        depth=depth,
        end_fill_type=cap_fill_type,
        location=(0.0, 0.0, 0.0),
    )
    obj = _active_object()
    normalize_object_origin(obj)
    params = {
        "vertices": vertices,
        "radius1": radius1,
        "radius2": radius2,
        "depth": depth,
        "cap_fill_type": cap_fill_type,
    }
    return obj, params


def create_monkey(rng: random.Random, bounds: PrimitiveBounds) -> tuple["bpy.types.Object", dict[str, Any]]:
    # bpy's primitive_monkey_add has no radius/size argument; overall scale
    # is realized as a uniform object-scale creation parameter here (kept
    # separate from the shared geometric-variation scale sampled later).
    scale_factor = _uniform(rng, *bounds.monkey_scale_range)
    use_smooth = rng.random() < 0.5

    bpy.ops.mesh.primitive_monkey_add(location=(0.0, 0.0, 0.0))
    obj = _active_object()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    obj.scale = (scale_factor, scale_factor, scale_factor)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if use_smooth:
        bpy.ops.object.shade_smooth()
    normalize_object_origin(obj)
    params = {"scale": scale_factor, "shade_smooth": use_smooth}
    return obj, params


def create_plane(rng: random.Random, bounds: PrimitiveBounds) -> tuple["bpy.types.Object", dict[str, Any]]:
    size_x = _uniform(rng, *bounds.plane_size_range)
    size_y = _uniform(rng, *bounds.plane_size_range)
    x_subdivisions = _randint(rng, *bounds.plane_subdivisions_range)
    y_subdivisions = _randint(rng, *bounds.plane_subdivisions_range)

    bpy.ops.mesh.primitive_grid_add(
        x_subdivisions=x_subdivisions,
        y_subdivisions=y_subdivisions,
        size=1.0,
        location=(0.0, 0.0, 0.0),
    )
    obj = _active_object()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    obj.scale = (size_x, size_y, 1.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    normalize_object_origin(obj)
    params = {
        "size_x": size_x,
        "size_y": size_y,
        "x_subdivisions": x_subdivisions,
        "y_subdivisions": y_subdivisions,
    }
    return obj, params


PRIMITIVE_CREATORS: dict[
    str, Callable[[random.Random, PrimitiveBounds], tuple["bpy.types.Object", dict[str, Any]]]
] = {
    "cube": create_cube,
    "sphere": create_sphere,
    "torus": create_torus,
    "cylinder": create_cylinder,
    "monkey": create_monkey,
    "cone": create_cone,
    "plane": create_plane,
}


def create_primitive(
    name: str, rng: random.Random, bounds: PrimitiveBounds
) -> tuple["bpy.types.Object", dict[str, Any]]:
    try:
        creator = PRIMITIVE_CREATORS[name]
    except KeyError as exc:
        raise ValueError(f"unknown primitive family: {name!r}") from exc
    return creator(rng, bounds)
