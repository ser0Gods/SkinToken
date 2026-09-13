# GLB Bone Rename Tool — Design

## Background
SkinTokens-rigged models in `results\` carry index-named bones (`bone_0`…`bone_N`).
`mappings\` contains one flat JSON per convention (`{ "bone_<n>": "<target name>" , ... }`).
We want to rename the bones of the produced models according to a chosen convention,
producing new GLBs while leaving the originals untouched.

## Requirements
1. `docker compose run --rm rename <convention>` loops over **every** `*.glb` in `results\` and renames
   bones per `mappings\<convention>.json`. One convention per invocation.
2. Output per model: `results\<stem>__<convention>.glb` (same folder, new file).
3. Container is removed after all converted models are saved (`--rm`).
4. Coexists with existing `Dockerfile` (nvidia/cuda rigging image). The new one is a separate,
   lightweight `Dockerfile.rename` with no GPU and no third-party Python deps.
5. Every `bone_<n>` present in the mapping is renamed. Bones missing from the mapping
   stay untouched (with a warning).
6. The existing `Dockerfile` (and the `rig` compose service) must NOT be altered.
   The rename pipeline lives in a **new** `Dockerfile.rename`.
7. The conversion is runnable in one command via a new `rename_all.bat`.

## Approach (approved)
**Stdlib-only GLB rewrite.** The script parses the GLB container in pure Python (same style as
`show_skeleton.py`), mutates only `nodes[].name` in the JSON chunk, and copies the binary chunk
verbatim. Animation channels and `skins.joints` reference nodes by index, not by name, so renaming
node names touches no other structure. Meshes, materials, and buffers are byte-identical.

Rejected alternative: trimesh/pygltflib — heavier image and full asset re-serialization on save,
risking incidental changes to everything else in the file.

## Components

### `rename_bones.py` (project root, stdlib only)
- Arg: single positional `convention` (folder key, no `.json`).
- Fixed container paths: `/results` (rw), `/mappings` (ro).
- Load `/mappings/<convention>.json` once (error if missing, listing available conventions).
- For each `results\*.glb` (sorted, deterministic):
  1. Skip files whose name ends with `__<convention>.glb` (re-run idempotency).
  2. Parse GLB: magic `glTF`, version must be 2, locate JSON chunk (type `0x4E4F534A`).
  3. Detect duplicate mapping keys (`object_pairs_hook`) → warn, value resolves last-wins.
  4. Rename `node["name"]` for exact matches.
  5. Warn: mapping keys that matched no node in this model; model `bone_<n>` names missing from
      the mapping (e.g. `bone_10`…`bone_24` in current `mesh2motion.json`).
  6. If zero nodes were renamed → per-file error (likely wrong model or wrong convention).
  7. Re-serialize JSON (compact separators, UTF-8; JSON chunk needs no 4-byte padding), copy the
      BIN chunk (and any padding) verbatim, recompute chunk length and GLB total length.
  8. Write `/results/<stem>__<convention>.glb` atomically (temp file + rename).
- Non-zero exit if any file errored. Per-file summary line (renamed count, untouched count).

### `Dockerfile.rename`
```dockerfile
FROM python:3.12-alpine
WORKDIR /app
COPY rename_bones.py .
ENTRYPOINT ["python", "rename_bones.py"]
```
Convention passed as container command arg → `docker compose run --rm rename <convention>`.
No venv, no pip, no CUDA, no GPU, no named volumes.

### `docker-compose.yml` — add `rename:` service
```yaml
  rename:
    build:
      context: .
      dockerfile: Dockerfile.rename
    image: skintokens-rename
    container_name: skintokens-rename
    volumes:
      - ./results:/results
      - ./mappings:/mappings:ro
```

### `rename_all.bat` (one-command runner, symmetry with `rig_all.bat`)
- `cd /d "%~dp0"`
- If `%1` missing: print usage `rename_all.bat <convention>` + fail, `pause`.
- `docker compose run --rm rename %1`; report success/fail by errorlevel; `pause`.

## Naming
- Output: `<original stem>__<convention>.glb`, e.g.
  `results\Adept_1k_res_1m_target_4k_tex_rigged__mesh2motion.glb`.
- The double underscore avoids colliding with the existing trailing `_rigged` suffix.

## Error handling
| Situation | Behavior |
|---|---|
| Unknown convention (`mappings\<c>.json` missing) | Hard error, lists available conventions, non-zero exit |
| Duplicate mapping keys | Warn, last-wins (documented JSON behavior) |
| Mapping key not in model | Warn, continue |
| Model `bone_<n>` not in mapping | Warn, leave as-is |
| Zero bones would be renamed for a file | Per-file error, non-zero exit |
| GLB magic/version bad, or no JSON chunk | Per-file error, non-zero exit |
| Re-run on same convention | Generated outputs skipped (`__<convention>.glb` suffix) |

## Test plan
1. `docker compose build rename` — clean build, no network installs beyond base image.
2. `docker compose run --rm rename mesh2motion` — runs over both existing models in `results\`.
3. Verify with `python show_skeleton.py results\<stem>__mesh2motion.glb`:
   - expected renamed set: `bone_0`…`bone_9`, `bone_25`…`bone_51` renamed; `bone_10`…`bone_24`
     unchanged (known gap in `mesh2motion.json`, left as-is per user decision).
   - node count unchanged (54); non-`bone` nodes (`Armature`, `mesh_0`) unchanged.
4. Warnings printed for `bone_10`…`bone_24` and for never-matched template names.
5. Idempotency: run again — existing `__mesh2motion.glb` files skipped, no double suffix.
6. Unknown convention rejected with available list.
7. Spot-check the rewritten file opens (GLB header length matches actual file size).

## Out of scope
- Fixing `mappings/mesh2motion.json` (user chose to keep it as-is; `bone_10`…`bone_24` stay).
- Renaming anything besides bone node names (mesh names, animation names, materials).
- Batch mode / GUI / running multiple conventions in one invocation.
- GPU, checkpoint download, or reuse of the rigging pipeline.

## Risks / notes
- Re-serializing the JSON chunk may reorder keys/whitespace — semantically harmless (GLB JSON is
  data, not text); binary payload and padding are copied verbatim so file remains valid GLB.
- `mappings\` is bind-mounted (not baked into the image), so mapping edits don't require a rebuild.
- `results\` is excluded from the Docker build context by `.dockerignore`; both dirs are
  runtime binds, consistent with the existing `rig` service.
