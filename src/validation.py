"""Geometry and scene-level validation (architecture.md section 10).

All checks operate on the in-memory mesh via a throwaway ``bmesh`` so the
active mesh datablock is never left in edit mode or otherwise mutated by
validation itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import bmesh
import bpy

from config import EDGE_LENGTH_EPS_RELATIVE, FACE_AREA_EPS_RELATIVE, MERGE_DISTANCE_ABSOLUTE_FLOOR, MERGE_DISTANCE_RELATIVE


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    stats: dict = field(default_factory=dict)


def validate_single_mesh_scene(expected_name: str = "mesh") -> ValidationResult:
    """Confirm exactly one mesh object exists in the scene for export."""

    mesh_objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if len(mesh_objects) != 1:
        return ValidationResult(
            False, f"expected exactly one mesh object, found {len(mesh_objects)}"
        )
    non_mesh = [o for o in bpy.context.scene.objects if o.type != "MESH"]
    if non_mesh:
        names = ", ".join(f"{o.name}:{o.type}" for o in non_mesh)
        return ValidationResult(False, f"unexpected non-mesh objects present: {names}")
    obj = mesh_objects[0]
    if obj.name != expected_name or obj.data.name != expected_name:
        return ValidationResult(
            False, f"mesh object/data must be named {expected_name!r}, found {obj.name!r}/{obj.data.name!r}"
        )
    return ValidationResult(True, stats={"object_name": obj.name})


def _edge_direction_consistent(bm: "bmesh.types.BMesh") -> bool:
    """Check that every manifold (2-face) edge has opposite winding on
    its two adjacent faces, i.e. normals are consistently oriented."""

    for edge in bm.edges:
        loops = edge.link_loops
        if len(loops) != 2:
            continue
        loop_a, loop_b = loops[0], loops[1]
        a_dir = (loop_a.vert.index, loop_a.link_loop_next.vert.index)
        b_dir = (loop_b.vert.index, loop_b.link_loop_next.vert.index)
        if a_dir[0] != b_dir[1] or a_dir[1] != b_dir[0]:
            return False
    return True


def validate_mesh(
    obj: "bpy.types.Object",
    primitive_name: str,
    *,
    is_closed_family: bool,
    max_polygon_count: int,
) -> ValidationResult:
    mesh = obj.data

    dims = obj.dimensions
    diagonal = math.sqrt(dims.x**2 + dims.y**2 + dims.z**2)
    if not math.isfinite(diagonal) or diagonal <= 0.0:
        return ValidationResult(False, "bounding-box diagonal is not finite/positive")

    edge_eps = max(diagonal * EDGE_LENGTH_EPS_RELATIVE, 1e-9)
    face_eps = max((diagonal**2) * FACE_AREA_EPS_RELATIVE, 1e-12)
    merge_tolerance = max(diagonal * MERGE_DISTANCE_RELATIVE, MERGE_DISTANCE_ABSOLUTE_FLOOR)

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    try:
        if len(bm.verts) == 0 or len(bm.faces) == 0:
            return ValidationResult(False, "mesh has no vertices or no faces")

        if len(bm.faces) > max_polygon_count:
            return ValidationResult(
                False, f"polygon count {len(bm.faces)} exceeds safety limit {max_polygon_count}"
            )

        for vert in bm.verts:
            co = vert.co
            if not (math.isfinite(co.x) and math.isfinite(co.y) and math.isfinite(co.z)):
                return ValidationResult(False, f"non-finite vertex coordinate at index {vert.index}")

        for face in bm.faces:
            if len(face.verts) == 4 and len({v.index for v in face.verts}) != 4:
                return ValidationResult(False, "degenerate quad face reuses a vertex")
            if face.calc_area() <= face_eps:
                return ValidationResult(False, "degenerate (near-zero-area) quad face found")
            normal = face.normal
            if not (math.isfinite(normal.x) and math.isfinite(normal.y) and math.isfinite(normal.z)):
                return ValidationResult(False, "non-finite face normal found")

        for edge in bm.edges:
            if edge.calc_length() <= edge_eps:
                return ValidationResult(False, "degenerate (near-zero-length) edge found")
            n_faces = len(edge.link_faces)
            if n_faces == 0:
                return ValidationResult(False, "loose edge with no adjacent faces found")
            if is_closed_family:
                if n_faces != 2:
                    return ValidationResult(
                        False, f"closed primitive is not watertight (edge with {n_faces} faces)"
                    )
            else:
                if n_faces > 2:
                    return ValidationResult(
                        False, f"non-manifold edge with {n_faces} adjacent faces found"
                    )

        for vert in bm.verts:
            if len(vert.link_faces) == 0:
                return ValidationResult(False, f"loose vertex with no adjacent faces at index {vert.index}")

        doubles = bmesh.ops.find_doubles(bm, verts=list(bm.verts), dist=merge_tolerance)
        if doubles["targetmap"]:
            return ValidationResult(
                False, f"{len(doubles['targetmap'])} duplicate vertices within merge tolerance"
            )

        if not _edge_direction_consistent(bm):
            return ValidationResult(False, "face normals are not consistently oriented")

        stats = {
            "vertex_count": len(bm.verts),
            "edge_count": len(bm.edges),
            "face_count": len(bm.faces),
            "bbox_diagonal": diagonal,
        }
        return ValidationResult(True, stats=stats)
    finally:
        bm.free()
