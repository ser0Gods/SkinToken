# Rename Bones (GLB) — Implementation Plan

- **Spec:** `docs\superpowers\specs\2026-09-13-rename-bones-design.md` (committed)
- **Working branch:** `development`
- **Test command (local, no Docker needed):** `python -m unittest test_rename_bones -v`
- **Python available locally:** 3.13.5, `docker compose` v2.39.4 available on this machine.

## Global Constraints (copied from spec)

1. Existing `Dockerfile` and the `rig` compose service must NOT be altered.
2. New lightweight `Dockerfile.rename` (Python stdlib only — no GPU, no venv, no CUDA).
3. One-command batch runner over every `*.glb` in `results\`; container is removed after all files are saved (`--rm`).
4. Output: `results\<stem>__<convention>.glb`, same folder, new file; originals never modified; re-runs are safe (idempotent).
5. Bones missing from the mapping stay `bone_<n>` and are WARNED about (with exact names); mapping file is used as-is.
6. Warnings, never silent: duplicate mapping keys (last value wins), mapping keys matching no node, unmapped bones.
7. Hard error (non-zero exit) when a GLB yields zero renamed bones.
8. Exit codes: 0 success, 2 usage / unknown convention (print available conventions), 1 per-file failure or bad mapping.
9. File/function names: `rename_bones.py`, `Dockerfile.rename`, compose service `rename`, `rename_all.bat`, tests in `test_rename_bones.py`.
10. GLB rewrite mechanics: validate header (magic `glTF`, version 2, total length == file size, first chunk type `JSON` at offset 12); rename `nodes[].name` exact-match (node indices untouched — animations/`skins.joints` reference indices, so they keep working); re-encode JSON compact (`separators=(",", ":")`, `ensure_ascii=True`) and pad the JSON chunk with `0x20` spaces to a multiple of 4; append EVERY byte after the original JSON chunk verbatim (BIN chunk untouched); recompute chunk length — payload-only: it counts payload bytes only, NOT the 8-byte chunk header, per the GLB spec — and the GLB total length; atomic write via temp file + `os.replace` in the same directory.
11. `RESULTS_DIR`/`MAPPINGS_DIR` are `/results` and `/mappings` (module-level names, read at call time so tests can monkeypatch them).

---

## Task 1 — GLB parse / rebuild / rename core (tests first)

**Files:**
- Create: `test_rename_bones.py` (repo root)
- Create: `rename_bones.py` (repo root)

**Step 1.1 — Write the failing tests.** Create `test_rename_bones.py` with EXACTLY this content (full module code for Task 1 tests; main-loop tests are added in Task 2):

```python
"""Tests for rename_bones.py.

Run:
    python -m unittest test_rename_bones -v
"""
import json
import os
import struct
import tempfile
import unittest

import rename_bones as rb


def make_glb(gltf, bin_body=b"BINPAYLOAD"):
    """Build a structurally valid GLB v2 file in memory (JSON + BIN chunks)."""
    js = json.dumps(gltf).encode("ascii")
    js += b" " * ((4 - len(js) % 4) % 4)
    body = bin_body + b"\x00" * ((4 - len(bin_body) % 4) % 4)
    chunks = struct.pack("<II", len(js), rb.JSON_CHUNK_TYPE) + js
    chunks += struct.pack("<II", len(body), 0x004E4942) + body
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def sample_gltf(names=("bone_0", "bone_1")):
    return {
        "asset": {"version": "2.0"},
        "scene": 0,
        "nodes": [
            {"name": n, "translation": [0.0, float(i), 0.0]}
            for i, n in enumerate(names)
        ],
    }


class ParseGlbTests(unittest.TestCase):
    def test_returns_gltf_and_binary_tail(self):
        gltf = sample_gltf()
        data = make_glb(gltf, bin_body=b"BINARYDATA")
        parsed, rest = rb.parse_glb(data)
        self.assertEqual(parsed, gltf)
        chunk_len, chunk_type = struct.unpack_from("<II", rest, 0)
        self.assertEqual(chunk_type, 0x004E4942)
        self.assertEqual(rest[8:18], b"BINARYDATA")

    def test_bad_magic(self):
        with self.assertRaises(rb.GlbError):
            rb.parse_glb(b"XXXX" + struct.pack("<II", 2, 12))

    def test_wrong_version(self):
        data = make_glb(sample_gltf())
        bad = data[:8] + struct.pack("<I", 3) + data[12:]
        with self.assertRaises(rb.GlbError):
            rb.parse_glb(bad)

    def test_header_length_mismatch(self):
        with self.assertRaises(rb.GlbError):
            rb.parse_glb(make_glb(sample_gltf())[:-1])

    def test_first_chunk_must_be_json(self):
        gltf = sample_gltf()
        js = json.dumps(gltf).encode("ascii")
        js += b" " * ((4 - len(js) % 4) % 4)
        body = b"\x00" * 4
        chunks = struct.pack("<II", len(body), 0x004E4942) + body
        chunks += struct.pack("<II", len(js), rb.JSON_CHUNK_TYPE) + js
        data = struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks
        with self.assertRaises(rb.GlbError):
            rb.parse_glb(data)

    def test_json_chunk_overruns_file(self):
        js = json.dumps(sample_gltf()).encode("ascii")
        js += b" " * ((4 - len(js) % 4) % 4)
        total = 12 + 8 + len(js)
        data = (
            struct.pack("<4sII", b"glTF", 2, total)
            + struct.pack("<II", 10**6, rb.JSON_CHUNK_TYPE)
            + js
        )
        with self.assertRaises(rb.GlbError):
            rb.parse_glb(data)


class RebuildGlbTests(unittest.TestCase):
    def test_round_trip(self):
        gltf = sample_gltf()
        data = make_glb(gltf, bin_body=b"BINARYDATA")
        parsed, rest = rb.parse_glb(data)
        rebuilt = rb.rebuild_glb(parsed, rest)
        self.assertEqual(rb.parse_glb(rebuilt)[0], gltf)
        self.assertIn(b"BINARYDATA", rb.parse_glb(rebuilt)[1])

    def test_total_length_matches_bytes(self):
        data = rb.rebuild_glb(sample_gltf(), b"\x00\x00\x00\x00")
        magic, version, total = struct.unpack_from("<4sII", data, 0)
        self.assertEqual(magic, b"glTF")
        self.assertEqual(version, 2)
        self.assertEqual(total, len(data))

    def test_json_chunk_padded_to_4_bytes(self):
        data = rb.rebuild_glb(sample_gltf(), b"")
        chunk_len, chunk_type = struct.unpack_from("<II", data, 12)
        self.assertEqual(chunk_type, rb.JSON_CHUNK_TYPE)
        self.assertEqual(chunk_len % 4, 0)

    def test_rebuilt_json_chunk_length_is_payload_only(self):
        gltf = sample_gltf()
        parsed, rest = rb.parse_glb(make_glb(gltf, bin_body=b"BINARYDATA"))
        out = rb.rebuild_glb(parsed, rest)
        jlen, _ = struct.unpack_from("<II", out, 12)
        # Per the GLB spec the length field counts payload bytes only, so the
        # BIN chunk header sits right after the 20-byte prelude + jlen bytes.
        self.assertEqual(
            out[20 + jlen : 24 + jlen], struct.pack("<I", len(rest[8:]))
        )
        self.assertEqual(out[24 + jlen : 28 + jlen], b"BIN\x00")


class RenameTests(unittest.TestCase):
    def test_only_exact_name_matches(self):
        gltf = sample_gltf(("bone_0", "bone_1", "pelvis", "Mesh0"))
        renamed = rb.rename_bones(gltf, {"bone_1": "spine_01"})
        self.assertEqual(renamed, ["bone_1"])
        self.assertEqual(
            [n["name"] for n in gltf["nodes"]],
            ["bone_0", "spine_01", "pelvis", "Mesh0"],
        )

    def test_all_mapped_bones_renamed(self):
        gltf = sample_gltf()
        renamed = rb.rename_bones(gltf, {"bone_0": "a", "bone_1": "b"})
        self.assertEqual(renamed, ["bone_0", "bone_1"])
        self.assertEqual(rb.remaining_bones(gltf), [])

    def test_unmapped_bones_reported(self):
        gltf = sample_gltf(("bone_0", "bone_1", "bone_2"))
        rb.rename_bones(gltf, {"bone_1": "x"})
        self.assertEqual(rb.remaining_bones(gltf), ["bone_0", "bone_2"])

    def test_nodes_without_names_untouched(self):
        gltf = {"nodes": [{"translation": [0, 0, 0]}, {"name": "bone_0"}]}
        renamed = rb.rename_bones(gltf, {"bone_0": "a"})
        self.assertEqual(renamed, ["bone_0"])


class MappingTests(unittest.TestCase):
    def _write_mapping(self, text):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        self.addCleanup(os.remove, path)
        return path

    def test_basic_mapping(self):
        path = self._write_mapping('{"bone_0": "pelvis", "bone_1": "spine_01"}')
        mapping, dups = rb.load_mapping(path)
        self.assertEqual(mapping, {"bone_0": "pelvis", "bone_1": "spine_01"})
        self.assertEqual(dups, [])

    def test_duplicate_keys_last_wins_and_reported(self):
        path = self._write_mapping('{"bone_1": "a", "bone_0": "x", "bone_1": "b"}')
        mapping, dups = rb.load_mapping(path)
        self.assertEqual(mapping["bone_1"], "b")
        self.assertEqual(dups, ["bone_1"])
```

**Step 1.2 — Run, confirm RED.** Run: `python -m unittest test_rename_bones -v`
Expect: collection error / failure (`ModuleNotFoundError: rename_bones`).

**Step 1.3 — Implement** `rename_bones.py` (repo root) with EXACTLY this content:

```python
"""Rename bone_<n> nodes in SkinTokens-rigged GLB files.

Pure-stdlib tool. Reads every *.glb in RESULTS_DIR and renames nodes whose
name matches a key in MAPPINGS_DIR/<convention>.json, writing a new file
<stem>__<convention>.glb next to each original (originals are never
modified). Only the JSON chunk of each GLB is rewritten; every byte from
the end of the JSON chunk to the end of the file (the binary chunk) is
copied verbatim.
"""
import json
import os
import re
import struct
import sys
import tempfile

RESULTS_DIR = "/results"
MAPPINGS_DIR = "/mappings"

MAGIC = b"glTF"
JSON_CHUNK_TYPE = 0x4E4F534A  # "JSON"
BIN_CHUNK_TYPE = 0x004E4942   # "BIN\0"

_BONE_RE = re.compile(r"^bone_\d+$")


class GlbError(ValueError):
    """Raised when a GLB file is structurally invalid."""


def parse_glb(data):
    """Parse a GLB v2 byte string into (gltf, rest).

    rest is the verbatim slice from the end of the JSON chunk to the end
    of the file (i.e. the binary chunk including its chunk header).
    Raises GlbError when the file is not a valid GLB v2.
    """
    if len(data) < 12:
        raise GlbError("file too short to be a GLB")
    magic, version, total_len = struct.unpack_from("<4sII", data, 0)
    if magic != MAGIC:
        raise GlbError("bad magic: not a GLB file")
    if version != 2:
        raise GlbError(f"unsupported GLB version {version} (need 2)")
    if total_len != len(data):
        raise GlbError(f"header length {total_len} != file size {len(data)}")
    chunk_len, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != JSON_CHUNK_TYPE:
        raise GlbError("first chunk is not JSON")
    json_end = 20 + chunk_len
    if json_end > len(data):
        raise GlbError("JSON chunk runs past end of file")
    gltf = json.loads(data[20:json_end])
    return gltf, data[json_end:]


def rebuild_glb(gltf, rest):
    """Rebuild a full GLB v2 byte string from parsed parts.

    The JSON chunk is re-encoded (compact, ASCII) and padded with 0x20
    spaces to a multiple of 4 bytes; rest is appended verbatim.
    """
    payload = json.dumps(gltf, separators=(",", ":"), ensure_ascii=True)
    payload = payload.encode("ascii")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    total = 12 + 8 + len(payload) + len(rest)
    return (
        struct.pack("<4sII", MAGIC, 2, total)
        + struct.pack("<II", len(payload), JSON_CHUNK_TYPE)
        + payload
        + rest
    )


def name_matches_bone(name):
    return isinstance(name, str) and bool(_BONE_RE.match(name))


def rename_bones(gltf, mapping):
    """Rename nodes whose name exactly matches a key of mapping (in place).

    Returns the list of OLD names that were renamed, in node order.
    Node order/indices are never changed, so animations and skins.joints
    (which index nodes) keep working.
    """
    renamed = []
    for node in gltf.get("nodes", []):
        name = node.get("name")
        if name is not None and name in mapping:
            node["name"] = mapping[name]
            renamed.append(name)
    return renamed


def remaining_bones(gltf):
    """Names of nodes still matching bone_<n> after a rename pass."""
    return [
        n["name"]
        for n in gltf.get("nodes", [])
        if name_matches_bone(n.get("name"))
    ]


def load_mapping(path):
    """Load a {bone_name: new_name} mapping JSON file from path.

    Returns (mapping, duplicates). duplicates lists every key that appears
    more than once, in the order the duplicate was seen; the LAST value
    wins (JSON last-wins, surfaced as a warning by the caller).
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    pairs = json.loads(raw, object_pairs_hook=lambda p: p)
    mapping = {}
    duplicates = []
    for key, value in pairs:
        if key in mapping:
            duplicates.append(key)
        mapping[key] = value
    return mapping, duplicates


def atomic_write(path, data):
    """Write bytes to path atomically (temp file in same dir + os.replace)."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".rename-", suffix=".glb.tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

**Step 1.4 — Run, confirm GREEN for these tests.** Run: `python -m unittest test_rename_bones -v`
Expect: all 13 tests above PASS.

**Step 1.5 — Commit.** `git add rename_bones.py test_rename_bones.py` then commit with message:
`feat: add rename_bones GLB core (parse/rebuild/rename/load_mapping)`

---

## Task 2 — main loop + atomic write tests (tests first)

**Files:**
- Modify: `test_rename_bones.py` (append test classes)
- Modify: `rename_bones.py` (append `main` + `__main__` guard)

**Step 2.1 — Append failing tests** to `test_rename_bones.py` (full content of the appended classes):

```python
class AtomicWriteTests(unittest.TestCase):
    def test_writes_data_and_leaves_no_temp_files(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = os.path.join(d.name, "out.glb")
        rb.atomic_write(path, b"1234")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"1234")
        self.assertEqual(os.listdir(d.name), ["out.glb"])


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.results = os.path.join(self.tmp.name, "results")
        self.mappings = os.path.join(self.tmp.name, "mappings")
        os.makedirs(self.results)
        os.makedirs(self.mappings)
        self._results = rb.RESULTS_DIR
        self._mappings = rb.MAPPINGS_DIR
        rb.RESULTS_DIR = self.results
        rb.MAPPINGS_DIR = self.mappings
        with open(os.path.join(self.mappings, "conv.json"), "w", encoding="utf-8") as f:
            f.write('{"bone_0": "pelvis"}')

    def tearDown(self):
        rb.RESULTS_DIR = self._results
        rb.MAPPINGS_DIR = self._mappings

    def _write_model(self, name="model.glb"):
        path = os.path.join(self.results, name)
        with open(path, "wb") as f:
            f.write(make_glb(sample_gltf(("bone_0", "bone_1"))))
        return path

    def _list_glbs(self):
        return sorted(os.listdir(self.results))

    def test_no_args_exits_2(self):
        self.assertEqual(rb.main([]), 2)

    def test_unknown_convention_exits_2(self):
        self.assertEqual(rb.main(["nope"]), 2)

    def test_renames_and_writes_new_file(self):
        self._write_model()
        self.assertEqual(rb.main(["conv"]), 0)
        self.assertEqual(self._list_glbs(), ["model.glb", "model__conv.glb"])
        with open(os.path.join(self.results, "model__conv.glb"), "rb") as f:
            gltf, _ = rb.parse_glb(f.read())
        self.assertEqual([n["name"] for n in gltf["nodes"]], ["pelvis", "bone_1"])

    def test_originals_untouched(self):
        src = self._write_model()
        with open(src, "rb") as f:
            before = f.read()
        self.assertEqual(rb.main(["conv"]), 0)
        with open(src, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_second_run_is_idempotent(self):
        self._write_model()
        self.assertEqual(rb.main(["conv"]), 0)
        with open(os.path.join(self.results, "model__conv.glb"), "rb") as f:
            once = f.read()
        self.assertEqual(rb.main(["conv"]), 0)
        self.assertEqual(self._list_glbs(), ["model.glb", "model__conv.glb"])
        with open(os.path.join(self.results, "model__conv.glb"), "rb") as f:
            self.assertEqual(f.read(), once)

    def test_no_matching_bones_exits_1_no_output(self):
        self._write_model()
        with open(os.path.join(self.mappings, "conv2.json"), "w", encoding="utf-8") as f:
            f.write('{"bone_99": "nope"}')
        self.assertEqual(rb.main(["conv2"]), 1)
        self.assertEqual(self._list_glbs(), ["model.glb"])

    def test_no_glbs_exits_0(self):
        self.assertEqual(rb.main(["conv"]), 0)

    def test_missing_results_dir_exits_1(self):
        rb.RESULTS_DIR = os.path.join(self.tmp.name, "does-not-exist")
        self.assertEqual(rb.main(["conv"]), 1)

    def test_invalid_mapping_json_exits_1(self):
        self._write_model()
        with open(os.path.join(self.mappings, "bad.json"), "w", encoding="utf-8") as f:
            f.write("this is not json")
        self.assertEqual(rb.main(["bad"]), 1)
```

**Step 2.2 — Run, confirm RED.** Run: `python -m unittest test_rename_bones -v`
Expect: new tests fail with `AttributeError: module 'rename_bones' has no attribute 'main'`.

**Step 2.3 — Append to `rename_bones.py`:**

```python
def _conventions(directory):
    try:
        return sorted(fn[:-5] for fn in os.listdir(directory) if fn.endswith(".json"))
    except OSError:
        return []


def warn(message):
    print(message, file=sys.stderr)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not argv[0]:
        warn("usage: rename_bones.py <convention>")
        return 2

    convention = argv[0]
    mapping_path = os.path.join(MAPPINGS_DIR, convention + ".json")
    if not os.path.isfile(mapping_path):
        warn(f"error: convention '{convention}' not found in {MAPPINGS_DIR}")
        available = _conventions(MAPPINGS_DIR)
        if available:
            warn("available conventions: " + ", ".join(available))
        return 2

    try:
        mapping, duplicates = load_mapping(mapping_path)
    except (OSError, ValueError) as exc:
        warn(f"error: cannot read mapping {mapping_path}: {exc}")
        return 1
    for key in duplicates:
        warn(f"warning: duplicate key '{key}' in mapping; last value wins")

    suffix = f"__{convention}.glb"
    try:
        files = sorted(
            fn
            for fn in os.listdir(RESULTS_DIR)
            if fn.lower().endswith(".glb") and not fn.endswith(suffix)
        )
    except OSError as exc:
        warn(f"error: cannot list {RESULTS_DIR}: {exc}")
        return 1
    if not files:
        print(f"no .glb files to rename in {RESULTS_DIR}; nothing to do")
        return 0

    failed = 0
    for fn in files:
        src = os.path.join(RESULTS_DIR, fn)
        stem = fn[: -len(".glb")]
        dst_name = stem + suffix
        dst = os.path.join(RESULTS_DIR, dst_name)
        try:
            with open(src, "rb") as f:
                data = f.read()
            gltf, rest = parse_glb(data)
            renamed = rename_bones(gltf, mapping)
            if not renamed:
                raise GlbError("no node names matched the mapping")
            left = remaining_bones(gltf)
            if left:
                warn(
                    f"warning: {fn}: {len(left)} bone(s) not in mapping, left as-is: "
                    + ", ".join(left)
                )
            unused = sorted(set(mapping) - set(renamed))
            if unused:
                warn(
                    f"warning: {fn}: {len(unused)} mapping key(s) matched no node: "
                    + ", ".join(unused)
                )
            atomic_write(dst, rebuild_glb(gltf, rest))
        except (OSError, GlbError, ValueError) as exc:
            warn(f"error: {fn}: {exc}")
            failed += 1
        else:
            print(f"ok: {fn} -> {dst_name} ({len(renamed)} bones renamed)")

    if failed:
        warn(f"done with errors: {failed}/{len(files)} file(s) failed")
        return 1
    print(f"done: {len(files)} file(s) renamed with convention '{convention}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Step 2.4 — Run, confirm GREEN.** Run: `python -m unittest test_rename_bones -v`
Expect: ALL tests pass (13 + 10 = 23 total… i.e. `OK (23 tests)` — the exact count may be 23: 6+3+4+2+1+9 minus none skipped; whatever it is, `OK`).

**Step 2.5 — Commit.** `git add rename_bones.py test_rename_bones.py` then commit:
`feat: add rename_bones main loop with warnings, atomic write, exit codes`

---

## Task 3 — Dockerfile.rename + compose `rename` service

**Files:**
- Create: `Dockerfile.rename` (repo root)
- Modify: `docker-compose.yml` (append `rename:` service ONLY — leave the `rig` service and top-level `volumes:` exactly as-is)

**Step 3.1 — Create `Dockerfile.rename`:**

```dockerfile
# Standalone GLB bone-renamer. Python stdlib only - no GPU, no venv, no CUDA.
FROM python:3.12-alpine

WORKDIR /app

COPY rename_bones.py .

ENTRYPOINT ["python", "rename_bones.py"]
```

**Step 3.2 — Modify `docker-compose.yml`:** insert this block between the `rig:` service block and the top-level `volumes:` key (do NOT touch anything else):

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

**Step 3.3 — Verify YAML + build.** Run BOTH:
- `docker compose config --services` → output lists `rename` (and `rig`); no YAML error.
- `docker compose build rename` → build succeeds, image `skintokens-rename` exists.

**Step 3.4 — Smoke-run in container (no rename yet).** Run: `docker compose run --rm rename mesh2motion`
Expect: BOTH models processed — one `ok:` line per model, ending `done: 2 file(s) renamed with convention 'mesh2motion'`, exit code 0. This is the real deliverable run; verify in Task 5. (If you want to isolate Task 3, any successful 2-file run here is fine.)

**Step 3.5 — Commit.** `git add Dockerfile.rename docker-compose.yml` then commit:
`feat: add Dockerfile.rename and compose rename service`

> Note: the actual rename outputs in `results\` are untracked and NOT committed (per constraint, only tool files are committed).

---

## Task 4 — rename_all.bat one-command runner

**Files:**
- Create: `rename_all.bat` (repo root) — MUST have CRLF line endings.

**Step 4.1 — Create `rename_all.bat` with EXACTLY this content (CRLF-terminated):**

```bat
@echo off
setlocal
cd /d "%~dp0"
set EXIT_CODE=0

if "%~1"=="" (
    echo Usage: rename_all.bat ^<convention^>
    echo.
    echo Available conventions:
    for %%f in ("mappings\*.json") do echo   %%~nf
    echo.
    echo   e.g.: rename_all.bat mesh2motion
    set EXIT_CODE=1
    goto :end
)

echo.
echo Renaming bone_ names in all *.glb under results\ using mapping "%~1"
echo Output: results\<source>__%~1.glb   ^(originals are not modified^)
echo.

docker compose run --rm rename "%~1"
set EXIT_CODE=%ERRORLEVEL%

if not %EXIT_CODE%==0 (
    echo.
    echo !!! Rename run reported errors - check output above.
) else (
    echo.
    echo Done. New GLBs are in results\ next to the originals.
)

:end
endlocal
exit /b %EXIT_CODE%
```

Then normalize to CRLF (the editor may write LF). Run:
`python -c "import pathlib; p = pathlib.Path('rename_all.bat'); b = p.read_bytes(); p.write_bytes(b.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))"`
Verify: `python -c "import pathlib; b = pathlib.Path('rename_all.bat').read_bytes(); assert b'\r\n' in b and b.count(b'\r\n') == b.count(b'\n'); print('CRLF ok')"`

**Step 4.2 — Verify usage path.** Run: `.\rename_all.bat`
Expect: prints `Usage: rename_all.bat <convention>`, lists `mesh2motion` under `Available conventions:`, exits with code 1. (Check with `echo $LASTEXITCODE` after running, or `cmd /c rename_all.bat` then `echo errorlevel`.)

**Step 4.3 — Verify run path.** Run: `.\rename_all.bat mesh2motion`
Expect: same 2 `ok:` lines + `Done.` banner, exit code 0.

**Step 4.4 — Commit.** `git add rename_all.bat` then commit:
`feat: add rename_all.bat one-command GLB renamer runner`

---

## Task 5 — End-to-end verification on the real models (no commit)

**No new files.** The two real inputs: `Adept_1k_res_1m_target_4k_tex_rigged.glb` and `Hoplite_1k_res_1m_target_4k_tex_rigged.glb` (both already produce outputs from Task 3.4 — this task VERIFIES them and the edge cases).

**Step 5.1 — Verify outputs exist for BOTH models.** Run:
`python -c "import glob, os; [print(f, os.path.getsize(f)) for f in glob.glob(r'results/*__mesh2motion.glb')]"`
Expect: exactly 2 files, names `Adept_1k_res_1m_target_4k_tex_rigged__mesh2motion.glb` and `Hoplite_1k_res_1m_target_4k_tex_rigged__mesh2motion.glb`, each close to the original size (~135–145 MB).

**Step 5.2 — Verify skeleton structure of each output with the existing checker.** Run for BOTH outputs:
`python show_skeleton.py results\Adept_1k_res_1m_target_4k_tex_rigged__mesh2motion.glb`
`python show_skeleton.py results\Hoplite_1k_res_1m_target_4k_tex_rigged__mesh2motion.glb`
Expect: still `total nodes: 54`; ZERO names matching `bone_N`; renamed names follow `mappings\mesh2motion.json` (root `pelvis`, `spine_01`…`head`, `head_leaf`, `clavicle_l`/`upperarm_l`/`lowerarm_l`/`hand_l`, fingers, right side, `thigh_l/calf_l/foot_l/ball_l`, `thigh_r/calf_r/foot_r/ball_r`). Also run the same on BOTH originals to confirm originals still show `bone_N` names (untouched).

**Step 5.3 — Verify idempotency at container scale.** Run: `docker compose run --rm rename mesh2motion`
Expect: re-processes the 2 originals, skips nothing, exits 0, and still exactly 4 `.glb` files in `results\` (2 originals + 2 outputs; NO `*__mesh2motion__mesh2motion.glb`).

**Step 5.4 — Verify unknown-convention error through Docker.** Run: `docker compose run --rm rename nope`
Expect: `error: convention 'nope' not found in /mappings` + `available conventions: mesh2motion`, exit code 2.

**Step 5.5 — Final test run + git hygiene.** Run:
- `python -m unittest test_rename_bones -v` → all pass.
- `git status --short` → only the 5 intended tool files are staged/committed; `results\`, `mappings\`, `input\`, `show_skeleton.py`, logs remain untracked (do NOT stage them).

**Step 5.6 — No commit in this task** (outputs are untracked on purpose).

---

## Done criteria

- `python -m unittest test_rename_bones -v` → all tests pass locally (no Docker needed).
- `.\rename_all.bat mesh2motion` → both originals in `results\` produce `__ mesh2motion.glb` outputs with renamed bones, originals byte-identical, container gone (`--rm`).
- Re-running is a no-op; unknown convention → exit 2 with available list; no-match / missing dirs → exit 1; usage → exit 2.
- `git status` shows only: `rename_bones.py`, `test_rename_bones.py`, `Dockerfile.rename`, `docker-compose.yml` (modified), `rename_all.bat` — all committed; nothing else touched.
