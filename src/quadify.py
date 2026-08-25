"""Conversion to, and low-level checks of, primarily quad topology.

This module implements architecture.md section 9 (Quad Topology Strategy):
face components without triangles are subdivided into quads using edge
midpoints and a face center. Components containing triangles are preserved
to avoid T-junctions, and existing vertex positions are never smoothed.
"""

from __future__ import annotations

import bmesh


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
    sampled so every non-triangular output face is a quad.
    """

    if requested_level > 0:
        return requested_level
    if mesh_is_all_quads(obj.data):
        return 0
    return 1


def subdivide_faces_to_quads(obj: "bpy.types.Object", level: int) -> None:
    """Subdivide triangle-free face components into quads without smoothing.

    Each original edge receives one shared midpoint. Each face receives one
    center vertex and produces one quad per original corner:

    ``corner -> next edge midpoint -> face center -> previous edge midpoint``.

    This is the topology-only equivalent of subdividing selected faces in
    Edit Mode. Original vertices remain fixed, edge points are exact
    midpoints, and face points are arithmetic centers, so surfaces do not
    acquire Catmull-Clark smoothing. A connected component containing a
    triangle passes through unchanged because subdividing an adjacent face
    would split their shared edge and create a T-junction.
    """

    mesh = obj.data
    for _ in range(max(0, level)):
        coordinates = [tuple(vertex.co) for vertex in mesh.vertices]
        polygon_vertices = [tuple(polygon.vertices) for polygon in mesh.polygons]
        faces_by_edge: dict[tuple[int, int], list[int]] = {}
        for face_index, vertices in enumerate(polygon_vertices):
            for corner, vertex_index in enumerate(vertices):
                next_vertex = vertices[(corner + 1) % len(vertices)]
                edge = (
                    (vertex_index, next_vertex)
                    if vertex_index < next_vertex
                    else (next_vertex, vertex_index)
                )
                faces_by_edge.setdefault(edge, []).append(face_index)

        preserved_faces = {
            face_index
            for face_index, vertices in enumerate(polygon_vertices)
            if len(vertices) == 3
        }
        pending_faces = list(preserved_faces)
        while pending_faces:
            face_index = pending_faces.pop()
            vertices = polygon_vertices[face_index]
            for corner, vertex_index in enumerate(vertices):
                next_vertex = vertices[(corner + 1) % len(vertices)]
                edge = (
                    (vertex_index, next_vertex)
                    if vertex_index < next_vertex
                    else (next_vertex, vertex_index)
                )
                for adjacent_face in faces_by_edge[edge]:
                    if adjacent_face not in preserved_faces:
                        preserved_faces.add(adjacent_face)
                        pending_faces.append(adjacent_face)

        edge_midpoints: dict[tuple[int, int], int] = {}
        subdivided_faces: list[tuple[int, ...]] = []
        smooth_flags: list[bool] = []

        def midpoint_index(first: int, second: int) -> int:
            key = (first, second) if first < second else (second, first)
            existing = edge_midpoints.get(key)
            if existing is not None:
                return existing

            a = coordinates[first]
            b = coordinates[second]
            index = len(coordinates)
            coordinates.append(
                (
                    (a[0] + b[0]) * 0.5,
                    (a[1] + b[1]) * 0.5,
                    (a[2] + b[2]) * 0.5,
                )
            )
            edge_midpoints[key] = index
            return index

        for face_index, polygon in enumerate(mesh.polygons):
            vertices = polygon_vertices[face_index]
            if len(vertices) < 3:
                raise RuntimeError(
                    f"cannot subdivide face with {len(vertices)} vertices"
                )
            if face_index in preserved_faces:
                subdivided_faces.append(vertices)
                smooth_flags.append(polygon.use_smooth)
                continue

            center = [0.0, 0.0, 0.0]
            for vertex_index in vertices:
                coordinate = coordinates[vertex_index]
                center[0] += coordinate[0]
                center[1] += coordinate[1]
                center[2] += coordinate[2]
            inverse_count = 1.0 / len(vertices)
            center_index = len(coordinates)
            coordinates.append(
                (
                    center[0] * inverse_count,
                    center[1] * inverse_count,
                    center[2] * inverse_count,
                )
            )

            for corner, vertex_index in enumerate(vertices):
                previous_vertex = vertices[corner - 1]
                next_vertex = vertices[(corner + 1) % len(vertices)]
                previous_midpoint = midpoint_index(previous_vertex, vertex_index)
                next_midpoint = midpoint_index(vertex_index, next_vertex)
                subdivided_faces.append(
                    (
                        vertex_index,
                        next_midpoint,
                        center_index,
                        previous_midpoint,
                    )
                )
                smooth_flags.append(polygon.use_smooth)

        mesh.clear_geometry()
        mesh.from_pydata(coordinates, [], subdivided_faces)
        for polygon, use_smooth in zip(mesh.polygons, smooth_flags):
            polygon.use_smooth = use_smooth
        mesh.update(calc_edges=True)


def merge_by_distance(obj: "bpy.types.Object", bbox_diagonal: float, relative_tolerance: float) -> int:
    """Remove near-duplicate vertices within a scale-relative tolerance.

    Call this before index-based topology operations so coincident vertices
    from separate faces share the same generated edge topology.

    Returns the number of vertices removed.
    """

    from config import MERGE_DISTANCE_ABSOLUTE_FLOOR

    distance = max(bbox_diagonal * relative_tolerance, MERGE_DISTANCE_ABSOLUTE_FLOOR)
    mesh = obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=distance)
        after = len(bm.verts)

        remaining = bmesh.ops.find_doubles(
            bm,
            verts=list(bm.verts),
            dist=distance,
        )
        if remaining["targetmap"]:
            raise RuntimeError(
                f"failed to merge {len(remaining['targetmap'])} near-duplicate vertices"
            )

        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update(calc_edges=True)
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
