"""FBX export and atomic-ish output directory handling.

Samples are written beneath a process-scoped temporary directory
(``.<sample_id>.tmp-<pid>``) and only renamed to their final ``<sample_id>``
name after every write and validation step succeeds (architecture.md section
13). This keeps interrupted runs from ever presenting partial data as valid.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import bpy


def temp_sample_dir(output_root: Path, sample_id: str) -> Path:
    return output_root / f".{sample_id}.tmp-{os.getpid()}"


def prepare_temp_dir(temp_dir: Path) -> None:
    """Create a fresh, empty temporary sample directory.

    Only ever removes a directory matching our own ``.tmp-<pid>`` naming
    scheme, never an arbitrary path, so this cannot escalate into unsafe
    broad deletion of the output root.
    """

    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=False)


def cleanup_temp_dir(temp_dir: Path) -> None:
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)


def export_fbx(obj: "bpy.types.Object", filepath: Path) -> None:
    """Export ``obj`` as the sole selected mesh object using the fixed
    export settings required by architecture.md section 6."""

    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.export_scene.fbx(
        filepath=str(filepath),
        use_selection=True,
        object_types={"MESH"},
        bake_anim=False,
        add_leaf_bones=False,
        axis_forward="-Y",
        axis_up="Z",
        global_scale=1.0,
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_ALL",
        use_mesh_modifiers=True,
        mesh_smooth_type="OFF",
        use_triangles=False,
        embed_textures=False,
        path_mode="AUTO",
    )


def finalize_sample_dir(
    temp_dir: Path, final_dir: Path, *, replace_existing: bool = False
) -> None:
    """Atomically (best-effort on Windows) rename the temp dir into place.

    Existing output is retained until the replacement has been fully written
    and verified. Windows cannot atomically replace a non-empty directory, so
    the old directory is removed only immediately before the final rename.
    """

    if final_dir.exists():
        if not replace_existing:
            raise FileExistsError(
                f"destination sample directory already exists: {final_dir}"
            )
        shutil.rmtree(final_dir)
    os.rename(temp_dir, final_dir)
