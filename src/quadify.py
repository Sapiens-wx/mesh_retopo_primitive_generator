"""Conversion to, and low-level checks of, strict quad topology.

This module implements architecture.md section 9 (Quad Topology Strategy):
Catmull-Clark subdivision is the only mechanism used to convert triangles or
n-gons into quads. Nothing here triangulates or heuristically merges
triangle pairs into quads.
"""

from __future__ import annotations

import bmesh
import bpy


def mesh_is_all_quads(mesh: "bpy.types.Mesh") -> bool:
    """Cheap check using the mesh's own polygon loop counts (no bmesh)."""

    if len(mesh.polygons) == 0:
        return False
    return all(poly.loop_total == 4 for poly in mesh.polygons)


def resolve_subdivision_level(obj: "bpy.types.Object", requested_level: int) -> int:
    """Return the subdivision level actually required for this candidate.

    Subdivision level 0 is only valid when the intermediate mesh already
    passes strict quad validation (architecture.md section 9). Any other
    intermediate mesh is forced to at least level 1 regardless of what was
    sampled, since Catmull-Clark subdivision is the only quadification
    mechanism used.
    """

    if requested_level > 0:
        return requested_level
    if mesh_is_all_quads(obj.data):
        return 0
    return 1


def apply_subdivision(obj: "bpy.types.Object", level: int) -> None:
    """Add and immediately apply a Catmull-Clark subdivision modifier."""

    if level <= 0:
        return

    view_layer = bpy.context.view_layer
    view_layer.objects.active = obj
    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)

    modifier = obj.modifiers.new(name="quadify_subsurf", type="SUBSURF")
    modifier.subdivision_type = "CATMULL_CLARK"
    modifier.levels = level
    modifier.render_levels = level
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def merge_by_distance(obj: "bpy.types.Object", bbox_diagonal: float, relative_tolerance: float) -> int:
    """Remove near-duplicate vertices within a scale-relative tolerance.

    Returns the number of vertices removed.
    """

    from config import MERGE_DISTANCE_ABSOLUTE_FLOOR

    distance = max(bbox_diagonal * relative_tolerance, MERGE_DISTANCE_ABSOLUTE_FLOOR)
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=distance)
    after = len(bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return before - after


def recalculate_normals(obj: "bpy.types.Object") -> None:
    """Recompute consistently outward-facing normals."""

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def finalize_topology(obj: "bpy.types.Object", bbox_diagonal: float, relative_tolerance: float) -> int:
    """Merge near-duplicate vertices and recalculate normals.

    This is the last topology step before validation (architecture.md
    section 11, step 9), run after variations, modifiers, and object
    transforms have already been applied.
    """

    removed = merge_by_distance(obj, bbox_diagonal, relative_tolerance)
    recalculate_normals(obj)
    return removed


def rename_to_mesh(obj: "bpy.types.Object") -> None:
    """Rename the object and its mesh datablock to the required ``mesh``."""

    obj.name = "mesh"
    obj.data.name = "mesh"
