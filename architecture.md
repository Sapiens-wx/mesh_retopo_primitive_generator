# Primitive Dataset Generator Architecture

## 1. Purpose

Primitive Dataset Generator is a Python application executed by Blender. It
creates a configurable, deterministic dataset of varied primitive meshes and
exports each valid sample as an FBX file.

The generator has three primary goals:

1. Produce broad geometric variation from a small set of primitive families.
2. Guarantee that every exported mesh contains only non-degenerate quad faces.
3. Emit a directory layout that can be consumed directly by Stage 2 of
   `Animation_Mesh_Pipeline`.

This project generates static clean geometry only. Animation sampling, dirty
topology generation, feature extraction, labels, and train/validation splitting
are outside its scope.

## 2. Runtime and Dependencies

- Python version: the Python version bundled with the supported Blender release.
- Required runtime: Blender with the `bpy` and `bmesh` modules.
- External Python packages: none.
- Command-line parsing: Python `argparse`.
- Primary output format: binary FBX.

The application must run in Blender background mode and must not depend on a
saved `.blend` file, an active selection, or UI state.

## 3. Entry Point

`main.py` is the only executable entry point:

```python
if __name__ == "__main__":
    main()
```

Blender-specific arguments are separated from application arguments by `--`.
The application reads only arguments following that separator.

Example:

```powershell
Blender --background --factory-startup --python main.py -- `
  --output "C:\datasets\primitive_clean" `
  --samples-per-primitive 100 `
  --seed 12345
```

`--factory-startup` is recommended so user preferences and add-ons cannot
affect imports, modifiers, units, or FBX export behavior.

## 4. Repository Layout

The implementation should use the following module boundaries:

```text
primitive_dataset_generator/
├── architecture.md
├── main.py                   # CLI parsing and application orchestration
├── config.py                 # Configuration dataclasses and validation
├── primitives.py             # Primitive-family creation
├── variations.py             # Parameter sampling and safe deformations
├── quadify.py                # Conversion to and validation of quad topology
├── validation.py             # Geometry and scene-level validation
├── exporter.py               # FBX export and atomic output handling
└── manifest.py               # Run and per-sample metadata serialization
```

The modules should not hold mutable Blender scene objects in global state.
Configuration is passed explicitly, and a sample is completely processed
before the next sample begins.

## 5. Command-Line Interface

### Required arguments

| Argument | Type | Description |
|---|---:|---|
| `-o`, `--output` | path | Dataset output root. Created when absent. |

### Generation arguments

| Argument | Type | Default | Description |
|---|---:|---:|---|
| `--samples-per-primitive` | positive integer | `100` | Number of accepted samples per enabled primitive family. |
| `--primitives` | comma-separated names | all | Subset of `cube,sphere,torus,cylinder,monkey,cone,plane`. |
| `--seed` | integer | `0` | Root random seed for reproducible generation. |
| `--start-index` | non-negative integer | `0` | First index generated for each primitive family. |
| `--subdivision-min` | integer | `1` | Minimum applied Catmull-Clark subdivision level. |
| `--subdivision-max` | integer | `3` | Maximum applied Catmull-Clark subdivision level. |
| `--max-attempts` | positive integer | `20` | Candidate attempts allowed for each requested sample. |
| `--filename` | string | `clean.fbx` | Exported mesh filename. Use `clean.fbx` for Stage 2 compatibility. |

### Output and execution arguments

| Argument | Type | Default | Description |
|---|---:|---:|---|
| `--overwrite` | flag | off | Replace an existing sample directory. |
| `--resume` | flag | off | Skip already valid samples and continue an interrupted run. |
| `--fail-fast` | flag | off | Stop the run after the first rejected or failed sample. |
| `--keep-failed` | flag | off | Preserve failed `.blend` snapshots for debugging. |

`--overwrite` and `--resume` are mutually exclusive. Unknown primitive names,
invalid ranges, an empty primitive list, and unsafe filenames must fail during
argument validation before Blender scene work begins.

## 6. Output Contract

### Directory structure

Each accepted mesh is stored in one directory directly beneath the output root:

```text
primitive_clean/                         # --output
├── cube_000000/
│   ├── clean.fbx
│   └── metadata.json
├── cube_000001/
│   ├── clean.fbx
│   └── metadata.json
├── sphere_000000/
│   ├── clean.fbx
│   └── metadata.json
├── torus_000000/
│   ├── clean.fbx
│   └── metadata.json
├── manifest.json
└── run.log
```

Sample directory names are `<primitive>_<index>`, where `index` is zero-padded
to six digits. Indexes are scoped to a primitive family and remain stable
across resumed runs.

The default `clean.fbx` name is intentional. Stage 2 recursively searches for
that exact filename, so the complete output root can be passed directly to:

```powershell
Blender --background --python stage2_dirty\batch_apply_dirty.py -- `
  --input_dir "C:\datasets\primitive_clean"
```

Stage 2 writes `dirty10.fbx`, `dirty20.fbx`, and the other requested strengths
beside each `clean.fbx`.

### FBX content

Every exported FBX must contain exactly one object:

- object name: `mesh`
- object type: `MESH`
- no armature, camera, light, animation, or material dependency
- transforms applied before export
- location at the world origin
- consistent Blender units and coordinate convention
- no unapplied modifiers

FBX export uses selection-only export with:

- object types: mesh only
- animation baking: disabled
- leaf bones: disabled
- forward axis: `-Y`
- up axis: `Z`
- unit scaling enabled

### Per-sample metadata

`metadata.json` records enough information to reproduce and audit a sample:

```json
{
  "schema_version": 1,
  "sample_id": "cube_000000",
  "primitive": "cube",
  "index": 0,
  "seed": 746805015404516437,
  "generator_parameters": {},
  "variation_parameters": {},
  "subdivision_level": 2,
  "vertex_count": 98,
  "edge_count": 192,
  "face_count": 96,
  "bbox_dimensions": [1.2, 1.2, 1.2],
  "filename": "clean.fbx"
}
```

The run-level `manifest.json` contains the CLI configuration, Blender version,
root seed, start/end timestamps, accepted and failed counts, and the ordered
list of accepted sample IDs. JSON is UTF-8, uses stable key ordering, and does
not contain machine-specific absolute paths except the requested output root.

## 7. Supported Primitive Families

All parameter ranges below are bounded to avoid degenerate faces and excessive
polygon counts. Exact numeric ranges belong in `config.py` and are recorded in
the manifest.

| Family | Sampled creation parameters | Notes |
|---|---|---|
| Cube | base size, bevel width, bevel segments | Bevel is optional and must preserve a closed surface. |
| Sphere | segments, rings, radius | Blender UV sphere is allowed as an intermediate mesh only; pole triangles must be quadified. |
| Torus | major/minor segments, major/minor radius | Require `major_radius > minor_radius > 0`. Native side topology is already quad-based. |
| Cylinder | vertices, radius, depth, cap treatment | Caps must be converted to quad patches; an n-gon cap is not exportable. |
| Monkey | radius/scale, smoothing, subdivision | Suzanne is an intermediate source and must pass the same final validation. |
| Plane | X/Y subdivisions, dimensions | Generate a quad grid rather than a single fixed face when subdivisions are greater than one. |

Primitive creation functions return one active mesh object and the exact
parameters used. They do not export files or mutate run-level state.

## 8. Variation Model

Variation is split into topology parameters chosen during primitive creation
and geometry-only deformations applied afterward.

### Topology-safe geometric variation

The following operations may be sampled independently or combined:

- uniform global scale
- arbitrary 3D rotation
- axis proportions chosen before final normalization
- bounded per-axis taper
- bounded twist around a principal axis
- bounded bend
- low-amplitude displacement along vertex normals
- smooth lattice-like deformation

Geometry-only variation must not add or remove vertices, edges, or faces.
Deformations are bounded relative to the object's bounding-box diagonal to
prevent self-intersection and collapsed faces.

Non-uniform proportions, taper, twist, and bend may change geometry while
preserving topology. Object transforms are applied before export, so the final
FBX object always has identity rotation and unit scale.

### Randomness and reproducibility

Use a dedicated `random.Random` instance; do not use ambient global randomness.
Each candidate receives a deterministic seed derived from:

```text
root seed + primitive name + sample index + attempt number
```

Use a stable hash such as BLAKE2 rather than Python's process-randomized
`hash()`. Adding or removing another primitive family must not change the
samples generated for an existing family.

Every sampled value is written to `metadata.json`. Running the same Blender
version with the same configuration and seed should reproduce the same mesh.

## 9. Quad Topology Strategy

“Quad mesh” is a strict exported-data invariant, not merely a preferred
creation mode.

Some Blender primitives contain triangles or n-gons by default. The pipeline
therefore uses the following sequence:

1. Create the intermediate primitive.
2. Apply primitive-specific cap or tip construction where needed.
3. Apply at least one Catmull-Clark subdivision level when the source contains
   triangles or n-gons; applied subdivision converts source polygons into
   quads.
4. Apply optional topology-safe geometric variations.
5. Apply all remaining modifiers.
6. Remove duplicate vertices within a small, scale-relative tolerance.
7. Recalculate normals consistently.
8. Validate the final mesh.

Subdivision level zero may only be accepted if the intermediate mesh already
passes strict quad validation. The default minimum is one because sphere,
cylinder, cone, and monkey source meshes cannot otherwise be assumed to be
quad-only.

Do not triangulate during generation or FBX export. Do not treat two adjacent
triangles as a quad unless they are explicitly dissolved into one valid
four-sided face.

## 10. Validation Rules

A candidate is accepted only if all of the following are true:

- exactly one mesh object exists for export
- the mesh has at least one vertex and one face
- every polygon has exactly four distinct vertices
- every edge has non-zero length above a scale-relative epsilon
- every face has non-zero area above a scale-relative epsilon
- there are no duplicate vertices within the merge tolerance
- there are no loose vertices or loose edges
- normals are finite and consistently oriented
- all vertex coordinates are finite
- the bounding-box diagonal is finite and non-zero
- polygon count is below the configured safety limit

Closed families (`cube`, `sphere`, `torus`, `cylinder`, `monkey`, and `cone`)
must also be manifold and watertight. `plane` is intentionally open, but each
boundary edge must belong to exactly one face and each interior edge to exactly
two faces.

Self-intersection checking may be enabled as an additional expensive validation
step, but it is not required for the initial implementation. When disabled,
deformation bounds must remain conservative.

After export, the integration test path re-imports the FBX into an empty scene
and reruns the structural checks. This catches export settings that alter
topology or include unintended objects.

## 11. Generation Pipeline

For each enabled primitive family and requested index:

1. Derive the candidate seed.
2. Clear the scene and purge orphaned data.
3. Create the primitive with sampled creation parameters.
4. Normalize its base dimensions around the world origin.
5. Quadify and apply the selected subdivision level.
6. Apply sampled topology-safe deformations.
7. Apply modifiers and object transforms.
8. Rename the object and mesh datablock to `mesh`.
9. Recalculate normals and remove near-duplicate vertices.
10. Validate the in-memory mesh.
11. Export the FBX and write metadata to a temporary sample directory.
12. Optionally re-import and validate the exported artifact.
13. Atomically rename the temporary directory to its final sample ID.
14. Append the accepted sample to the run manifest.

If validation rejects a candidate, retry the same sample index with the next
attempt seed. A sample index is never silently omitted. Exhausting
`--max-attempts` records a failure and either continues or exits according to
`--fail-fast`.

## 12. Scene Isolation and Resource Management

Each candidate begins from a clean scene. Cleanup includes objects, meshes,
materials, images, collections, and other orphaned datablocks created by the
previous candidate.

To keep long background runs stable:

- release temporary BMesh instances with `bm.free()`
- unlink and remove rejected objects and mesh datablocks
- avoid retaining Blender objects in lists after a sample completes
- periodically purge orphaned datablocks
- log progress after every accepted or failed sample

The generator is single-process. Multiple generator processes may run against
different output roots, but concurrent writers to the same output root are not
supported.

## 13. Existing Output, Resume, and Atomicity

Default behavior is conservative:

- If the output root exists and is non-empty, fail unless `--resume` or
  `--overwrite` is specified.
- `--overwrite` replaces only sample directories that the current run owns; it
  must not recursively delete an arbitrary output root.
- `--resume` considers a sample complete only when its FBX and metadata both
  exist and metadata matches the sample ID and requested filename.
- A partial `.tmp` sample directory is removed or regenerated during resume.

Export and metadata are first written beneath
`.<sample_id>.tmp-<process-id>`. The directory is renamed to `<sample_id>` only
after all writes and validations succeed. This prevents interrupted runs from
presenting incomplete samples as valid data.

## 14. Logging and Error Handling

Status is written to stdout and `run.log`. There is exactly one line per
requested primitive index: `<sample_id>: succeeded` or
`<sample_id>: failed: <error>`. Individual retries and run-level progress are
not logged.

Errors must not be converted into success-shaped empty samples. Invalid CLI
configuration and output-root safety failures are fatal. Candidate-specific
geometry failures are retryable.

The process exits with:

- `0` when all requested samples are generated or already valid during resume
- non-zero when configuration is invalid, a fatal runtime error occurs, or one
  or more requested samples remain ungenerated

## 15. Configuration Invariants

Configuration validation occurs before generation:

- minimum values do not exceed maximum values
- segment and ring counts satisfy primitive-specific lower bounds
- subdivision levels and polygon limits are safe
- all scale, radius, depth, and deformation bounds are positive where required
- deformation amplitudes are bounded relative to mesh size
- requested sample counts cannot produce duplicate sample IDs
- the output filename is a basename ending in `.fbx`

Runtime defaults should be conservative enough for unattended background
execution. Raising subdivision or segment limits should be an explicit user
choice because face count grows rapidly.
