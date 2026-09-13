"""Rename bone_<n> nodes in rigged GLB files per a convention JSON mapping.

Reads every .glb under results/, renames nodes whose name exactly matches a
mapping key, and writes <stem>__<convention>.glb alongside. The input files
are never modified. Only stdlib is used.
"""

import json
import os
import struct
import sys
import tempfile
from pathlib import Path

MAGIC = b"glTF"
GLB_VERSION = 2
JSON_CHUNK_TYPE = 0x4E4F534A  # little-endian 'jSON'
RESULTS_DIR = Path("/results")
MAPPINGS_DIR = Path("/mappings")


class GlbError(Exception):
    pass


class RenameError(Exception):
    pass


def _pad4(n):
    return (4 - n % 4) % 4


def parse_glb(data, name):
    """Parse a GLB file's bytes. Returns (gltf dict, rest bytes).

    `rest` is everything from the end of the JSON chunk to the end of the
    file, copied verbatim — typically the BIN chunk plus padding, which must
    be preserved exactly.
    """
    if hasattr(data, "read"):
        data = data.read()
    size = len(data)
    if size < 12:
        raise GlbError(f"{name}: too short ({size} bytes)")
    magic, version, total, _ = struct.unpack_from("<4sIII", data, 0)
    if magic != MAGIC:
        raise GlbError(f"{name}: bad magic {magic!r}")
    if version != GLB_VERSION:
        raise GlbError(f"{name}: glTF version {version} != {GLB_VERSION}")
    if total != size:
        raise GlbError(
            f"{name}: header length {total} != file size {size}"
        )
    if size < 20:
        raise GlbError(f"{name}: truncated chunk header")
    json_len = struct.unpack_from("<I", data, 12)[0]
    json_type = struct.unpack_from("<I", data, 16)[0]
    # Per the GLB spec the chunk length field counts only the payload bytes,
    # not the 8-byte chunk header, so the payload starts at 20 and ends at
    # 20 + json_len.
    payload_end = 20 + json_len
    if json_type != JSON_CHUNK_TYPE:
        raise GlbError(f"{name}: first chunk is not JSON (type {json_type:#x})")
    if payload_end > size:
        raise GlbError(f"{name}: JSON chunk overruns file")
    try:
        gltf = json.loads(data[20:payload_end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise GlbError(f"{name}: bad JSON chunk: {e}") from e
    rest = data[payload_end:]
    return gltf, rest


def rebuild_glb(gltf, rest):
    """Rebuild a GLB file's bytes from a parsed glTF dict and the verbatim
    trailing bytes (BIN chunk & co). Returns bytes."""
    js = json.dumps(gltf, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    payload = js + b"\x20" * _pad4(len(js))
    # Chunk length field = payload bytes only (header not included).
    json_chunk = (
        struct.pack("<I", len(payload))
        + struct.pack("<I", JSON_CHUNK_TYPE)
        + payload
    )
    total = 12 + len(json_chunk) + len(rest)
    return (
        struct.pack("<4sII", MAGIC, GLB_VERSION, total) + json_chunk + rest
    )


def rename_bones(gltf, mapping):
    """Rename matching node names in place. Returns number renamed."""
    changed = 0
    for node in gltf.get("nodes", []):
        nm = node.get("name")
        if nm in mapping:
            node["name"] = mapping[nm]
            changed += 1
    if changed == 0:
        raise RenameError(
            f"no node names matched the mapping ({len(mapping)} keys)"
        )
    return changed


def _is_bone_name(nm):
    return nm and nm.startswith("bone_") and nm[len("bone_"):].isdigit()


def remaining_bones(gltf):
    """Return node names still matching bone_<number> after a rename."""
    return [
        node["name"]
        for node in gltf.get("nodes", [])
        if _is_bone_name(node.get("name", ""))
    ]


def load_mapping_pairs(pairs):
    """Build (dict, sorted-duplicate-keys) from a list of key/value pairs in
    document order. Later duplicates win."""
    mapping = {}
    seen = {}
    for k, v in pairs:
        seen[k] = seen.get(k, 0) + 1
        mapping[k] = v
    return mapping, sorted(k for k, c in seen.items() if c > 1)


def load_mapping(path):
    """Load a convention JSON file. Returns (mapping, duplicate_keys)."""
    p = Path(path)
    if not p.exists():
        raise GlbError(f"mapping not found: {p}")
    try:
        raw = json.loads(
            p.read_bytes(), object_pairs_hook=lambda p: p
        )
    except json.JSONDecodeError as e:
        raise GlbError(f"{p.name} is not valid JSON: {e}") from e
    return load_mapping_pairs(raw)


def atomic_write(path, data):
    """Write bytes to a temp file in the same directory, then atomic rename."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(
        prefix=".rename-", suffix=".glb.tmp", dir=str(path.parent)
    )
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


def warn(msg):
    print(f"WARNING: {msg}", file=sys.stderr)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        warn("usage: rename_bones <convention>")
        return 2
    convention = argv[0]
    mapping_path = MAPPINGS_DIR / f"{convention}.json"
    if not mapping_path.exists():
        warn(f"unknown convention '{convention}'")
        print("available conventions:", file=sys.stderr)
        for p in sorted(MAPPINGS_DIR.glob("*.json")):
            print(f"  {p.stem}")
        return 2
    mapping, duplicates = load_mapping(mapping_path)
    for d in duplicates:
        warn(f"{mapping_path.name}: duplicate key '{d}' (last value wins)")
    if not RESULTS_DIR.exists():
        warn(f"results directory not found: {RESULTS_DIR}")
        return 1
    files = sorted(
        p
        for p in RESULTS_DIR.glob("*.glb")
        if p.is_file() and not p.name.endswith(f"__{convention}.glb")
    )
    if not files:
        warn(
            f"nothing to do: no .glb files to process in {RESULTS_DIR} "
            f"(convention: {convention})"
        )
        return 0
    failures = 0
    for path in files:
        data = path.read_bytes()
        try:
            gltf, rest = parse_glb(data, path.name)
            original_names = {n.get("name") for n in gltf.get("nodes", [])}
            changed = rename_bones(gltf, mapping)
        except (GlbError, RenameError) as e:
            warn(f"{path.name}: {e}")
            failures += 1
            continue
        unmapped_keys = sorted(set(mapping) - original_names)
        if unmapped_keys:
            warn(
                f"{path.name}: mapping keys with no matching node: "
                f"{', '.join(unmapped_keys)}"
            )
        leftover = remaining_bones(gltf)
        if leftover:
            warn(
                f"{path.name}: {len(leftover)} bone_<n> nodes have no "
                f"mapping and were left unchanged"
            )
        out = path.with_name(f"{path.stem}__{convention}.glb")
        atomic_write(out, rebuild_glb(gltf, rest))
        print(f"OK {path.name} -> {out.name} ({changed} bones renamed)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
