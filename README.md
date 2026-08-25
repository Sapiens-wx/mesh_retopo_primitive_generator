# Primitive Dataset Generator

Blender `bpy`/`bmesh` background-mode tool that generates a validated,
strictly quad-topology dataset of primitive meshes (cube, sphere, torus,
cylinder, monkey, plane) and exports each accepted sample as
`clean.fbx`. See `architecture.md` for the full design.

## Prerequisites

- Blender (with the `bpy`/`bmesh` Python API and the bundled FBX import/export
  add-on enabled — true by default on stock Blender installs).
- No third-party Python packages; standard library only.

## Run (Windows PowerShell)

```powershell
& "C:\Program Files\Blender Foundation\Blender\blender.exe" --background --factory-startup --python src\main.py -- `
  --output "C:\datasets\primitive_clean" `
  --samples-per-primitive 100 `
  --seed 12345
```

`--factory-startup` prevents user preferences/add-ons from affecting imports,
modifiers, units, or FBX export behavior.

## Key CLI options

| Option | Default | Description |
|---|---|---|
| `-o`, `--output` | *(required)* | Dataset output root; created if absent. |
| `--samples-per-primitive` | `100` | Accepted samples per enabled family. |
| `--primitives` | all | Comma-separated subset of `cube,sphere,torus,cylinder,monkey,plane`. |
| `--seed` | `0` | Root seed for deterministic generation. |
| `--start-index` | `0` | First index per primitive family. |
| `--subdivision-min` / `--subdivision-max` | `1` / `3` | Flat per-face subdivision range; no smoothing modifier is used. |
| `--max-attempts` | `20` | Retry attempts per requested sample. |
| `--filename` | `clean.fbx` | Exported mesh filename (keep default for Stage 2). |
| `--overwrite` | off | Replace an existing sample directory (mutually exclusive with `--resume`). |
| `--resume` | off | Skip already-valid samples and continue an interrupted run. |
| `--fail-fast` | off | Stop after the first rejected/failed sample. |
| `--keep-failed` | off | Save `.blend` snapshots of failed attempts under `_failed_snapshots/`. |

Exit code is `0` only if every requested sample was generated (or already
valid on resume); non-zero otherwise.

## Output example

```
primitive_clean/
├── cube_000000/
│   ├── clean.fbx
│   └── metadata.json
├── sphere_000000/
│   ├── clean.fbx
│   └── metadata.json
├── manifest.json
└── run.log
```

Every exported mesh contains exactly one object named `mesh`, with only
non-degenerate quad faces, finite/no-loose geometry, and (except for the
intentionally open `plane` family) manifold/watertight topology.

`run.log` contains one line per requested primitive index: either
`<sample_id>: succeeded` or `<sample_id>: failed: <error>`.
Blender operator and FBX import/export diagnostics are suppressed.

## Feeding Stage 2 (Animation_Mesh_Pipeline)

The output root can be passed directly to Stage 2, which recursively
discovers every `clean.fbx`:

```powershell
& "C:\Program Files\Blender Foundation\Blender\blender.exe" --background --python ..\Animation_Mesh_Pipeline\Animation_Mesh_Pipeline\stage2_dirty\batch_apply_dirty.py -- `
  --input_dir "C:\datasets\primitive_clean"
```

## Layout

```
primitive_dataset_generator/
├── architecture.md
├── README.md
└── src/
    ├── main.py         # CLI parsing and orchestration (entry point)
    ├── config.py       # Config dataclasses, validation, deterministic seeding
    ├── primitives.py   # Primitive-family creation
    ├── variations.py   # Parameter sampling and safe deformations
    ├── quadify.py      # Quad topology conversion helpers
    ├── validation.py   # Geometry and scene-level validation
    ├── exporter.py     # FBX export and atomic-ish output handling
    └── manifest.py     # Run/per-sample metadata serialization
```

## Known limitations

- Not runnable/tested in this environment (no Blender executable available);
  verified via Python syntax compilation (`py_compile`) and lint (`pyflakes`)
  only. Validate end-to-end on a machine with Blender before production use.
- Self-intersection checking is not implemented (architecture.md marks it
  optional); deformation bounds are kept conservative instead.
- The "lattice-like smooth deformation" is implemented as a low-frequency
  Displace modifier (large noise scale) rather than a dedicated Lattice
  object, to keep per-candidate scene cleanup simple and safe.
