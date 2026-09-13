import contextlib
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

import rename_bones

MAGIC = b"glTF"


def make_glb(gltf, bin_body=b"\x00\x01\x02\x03"):
    """Build a minimal GLB (v2) from a JSON-able glTF dict and a BIN body."""
    js = json.dumps(gltf, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    jpad = (4 - len(js) % 4) % 4
    json_section = (
        struct.pack("<II", 8 + len(js) + jpad, 0x4E4F534A)
        + js
        + b" " * jpad
    )
    bpad = (4 - len(bin_body) % 4) % 4
    bin_section = (
        struct.pack("<II", 8 + len(bin_body) + bpad, 0x4E4942)
        + bin_body
        + b"\x00" * bpad
    )
    total = 12 + len(json_section) + len(bin_section)
    return struct.pack("<4sII", MAGIC, 2, total) + json_section + bin_section


def sample_gltf(names):
    return {
        "asset": {"version": "2.0"},
        "nodes": [
            {"name": n, "translation": [0, 0, 0]} for n in names
        ],
    }


class ParseGlbTests(unittest.TestCase):
    def test_round_trip_preserving_json_and_rest(self):
        names = ["bone_0", "bone_1", "bone_2"]
        data = make_glb(sample_gltf(names))
        gltf, rest = rename_bones.parse_glb(
            io.BytesIO(data), "fake.glb"
        )
        self.assertEqual(
            [n["name"] for n in gltf["nodes"]], ["bone_0", "bone_1", "bone_2"]
        )
        # rest holds the original BIN chunk: its type bytes must be in there
        body = b"\x00\x01\x02\x03"
        self.assertIn(b"BIN\x00" + body, rest)

    def test_bad_magic_raises(self):
        with self.assertRaises(rename_bones.GlbError):
            rename_bones.parse_glb(
                io.BytesIO(b"XXXX" + b"\x00" * 100), "bad.glb"
            )

    def test_zero_length_raises(self):
        with self.assertRaises(rename_bones.GlbError):
            rename_bones.parse_glb(io.BytesIO(b""), "empty.glb")


class RebuildGlbTests(unittest.TestCase):
    def test_output_is_valid_glb_with_same_payload(self):
        gltf, rest = rename_bones.parse_glb(
            io.BytesIO(make_glb(sample_gltf(["bone_0", "bone_1"]))),
            "x.glb",
        )
        out = rename_bones.rebuild_glb(gltf, rest)
        self.assertEqual(out[:4], b"glTF")
        self.assertEqual(struct.unpack_from("<I", out, 8)[0], len(out))
        gltf2, _ = rename_bones.parse_glb(io.BytesIO(out), "out.glb")
        self.assertEqual(gltf, gltf2)

    def test_padding_is_spaces(self):
        gltf, rest = rename_bones.parse_glb(
            io.BytesIO(make_glb(sample_gltf(["bone_0", "bone_1"]))),
            "x.glb",
        )
        out = rename_bones.rebuild_glb(gltf, rest)
        jlen = struct.unpack_from("<I", out, 12)[0]
        payload = out[20 : 12 + jlen]
        self.assertEqual(len(payload) % 4, 0)
        trailing = payload[payload.rfind(b"}") + 1 :]
        self.assertTrue(all(b == 0x20 for b in trailing))

    def test_json_and_bin_chunks_reconstructed(self):
        body = b"\xAB\xCD\xEF\x01\x02"
        data = make_glb(sample_gltf(["bone_0"]), bin_body=body)
        gltf, rest = rename_bones.parse_glb(io.BytesIO(data), "x.glb")
        out = rename_bones.rebuild_glb(gltf, rest)
        self.assertIn(b"BIN\x00" + body, out)
        # original trailing bytes preserved verbatim
        self.assertTrue(out.endswith(rest))


class RenameTests(unittest.TestCase):
    def test_all_bones_renamed_exactly(self):
        names = ["bone_0", "bone_1", "bone_2_other", "bone_10"]
        gltf, rest = rename_bones.parse_glb(
            io.BytesIO(make_glb(sample_gltf(names))), "x.glb"
        )
        mapping = {
            "bone_0": "pelvis",
            "bone_1": "spine_01",
            "bone_10": "thumb_02_l",
        }
        changed = rename_bones.rename_bones(gltf, mapping)
        self.assertEqual(changed, 3)
        self.assertEqual(
            [n["name"] for n in gltf["nodes"]],
            ["pelvis", "spine_01", "bone_2_other", "thumb_02_l"],
        )

    def test_no_keys_matching_any_node_raises(self):
        gltf, _ = rename_bones.parse_glb(
            io.BytesIO(make_glb(sample_gltf(["other_0", "other_1"]))),
            "x.glb",
        )
        with self.assertRaises(rename_bones.RenameError):
            rename_bones.rename_bones(gltf, {"bone_0": "pelvis"})

    def test_partial_mapping_keeps_unmapped_bones(self):
        gltf, _ = rename_bones.parse_glb(
            io.BytesIO(make_glb(sample_gltf(["bone_0", "bone_1"]))),
            "x.glb",
        )
        changed = rename_bones.rename_bones(gltf, {"bone_0": "pelvis"})
        self.assertEqual(changed, 1)
        self.assertEqual(
            [n["name"] for n in gltf["nodes"]], ["pelvis", "bone_1"]
        )

    def test_duplicate_mapping_keys_warn_and_last_wins(self):
        pairs = [("bone_0", "a"), ("bone_0", "b"), ("bone_1", "c")]
        mapping, duplicates = rename_bones.load_mapping_pairs(pairs)
        self.assertEqual(mapping, {"bone_0": "b", "bone_1": "c"})
        self.assertEqual(duplicates, ["bone_0"])

    def test_remaining_bones_reports_unrenamed(self):
        gltf, _ = rename_bones.parse_glb(
            io.BytesIO(make_glb(sample_gltf(["bone_0", "bone_1", "head"]))),
            "x.glb",
        )
        rename_bones.rename_bones(gltf, {"bone_0": "pelvis"})
        self.assertEqual(
            sorted(rename_bones.remaining_bones(gltf)), ["bone_1"]
        )


class MappingTests(unittest.TestCase):
    def test_load_mapping_reads_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m.json"
            p.write_text('{"bone_0": "pelvis"}')
            mapping, duplicates = rename_bones.load_mapping(p)
            self.assertEqual(mapping, {"bone_0": "pelvis"})
            self.assertEqual(duplicates, [])

    def test_load_mapping_bad_path_raises(self):
        with self.assertRaises(rename_bones.GlbError):
            rename_bones.load_mapping(Path("/nonexistent/x.json"))


class MainTests(unittest.TestCase):
    def _stage(self, tmp):
        results = Path(tmp) / "results"
        mappings = Path(tmp) / "mappings"
        results.mkdir()
        mappings.mkdir()
        names = ["bone_0", "bone_1", "other"]
        for model in ("A.glb", "B.glb"):
            (results / model).write_bytes(make_glb(sample_gltf(names)))
        for conv in ("mesh2motion", "m"):
            (mappings / f"{conv}.json").write_text(
                json.dumps({"bone_0": "pelvis", "bone_1": "spine_01"})
            )
        return results, mappings

    def _prev_dirs(self):
        return (
            rename_bones.RESULTS_DIR,
            rename_bones.MAPPINGS_DIR,
        )

    def _restore_dirs(self, saved):
        rename_bones.RESULTS_DIR, rename_bones.MAPPINGS_DIR = saved

    def test_no_args_exits_2_with_usage(self):
        saved = self._prev_dirs()
        try:
            rc = rename_bones.main([])
        finally:
            self._restore_dirs(saved)
        self.assertEqual(rc, 2)

    def test_unknown_convention_exits_2_lists_available(self):
        saved = self._prev_dirs()
        try:
            with tempfile.TemporaryDirectory() as td:
                _, mappings = self._stage(td)
                rename_bones.MAPPINGS_DIR = mappings
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    rc = rename_bones.main(["nope"])
                self.assertEqual(rc, 2)
                self.assertIn("available conventions:", buf.getvalue())
        finally:
            self._restore_dirs(saved)

    def test_missing_results_dir_exits_1(self):
        saved = self._prev_dirs()
        try:
            with tempfile.TemporaryDirectory() as td:
                mappings = Path(td) / "mappings"
                mappings.mkdir()
                (mappings / "m.json").write_text(
                    json.dumps({"bone_0": "spine_00"})
                )
                rename_bones.RESULTS_DIR = Path(td) / "nope"
                rename_bones.MAPPINGS_DIR = mappings
                rc = rename_bones.main(["m"])
                self.assertEqual(rc, 1)
        finally:
            self._restore_dirs(saved)

    def test_no_glb_files_exits_0_with_note(self):
        saved = self._prev_dirs()
        try:
            with tempfile.TemporaryDirectory() as td:
                results, mappings = self._stage(td)
                for g in results.glob("*.glb"):
                    g.unlink()
                rename_bones.RESULTS_DIR = results
                rename_bones.MAPPINGS_DIR = mappings
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    rc = rename_bones.main(["m"])
                self.assertEqual(rc, 0)
                self.assertIn("nothing to do", buf.getvalue())
        finally:
            self._restore_dirs(saved)

    def test_two_files_renamed_output_written(self):
        saved = self._prev_dirs()
        try:
            with tempfile.TemporaryDirectory() as td:
                results, mappings = self._stage(td)
                rename_bones.RESULTS_DIR = results
                rename_bones.MAPPINGS_DIR = mappings
                rc = rename_bones.main(["mesh2motion"])
                self.assertEqual(rc, 0)
                for model in ("A.glb", "B.glb"):
                    out = results / f"{Path(model).stem}__mesh2motion.glb"
                    self.assertTrue(out.exists())
                    data = out.read_bytes()
                    self.assertEqual(data[:4], b"glTF")
                    self.assertEqual(struct.unpack_from("<I", data, 8)[0], len(data))
        finally:
            self._restore_dirs(saved)

    def test_originals_unchanged(self):
        saved = self._prev_dirs()
        try:
            with tempfile.TemporaryDirectory() as td:
                results, mappings = self._stage(td)
                before = {p.name: p.read_bytes() for p in results.iterdir()}
                rename_bones.RESULTS_DIR = results
                rename_bones.MAPPINGS_DIR = mappings
                rc = rename_bones.main(["m"])
                after = {p.name: p.read_bytes() for p in results.iterdir()}
                self.assertEqual(rc, 0)
                for name, data in before.items():
                    self.assertEqual(after[name], data)
        finally:
            self._restore_dirs(saved)

    def test_rerun_is_idempotent(self):
        saved = self._prev_dirs()
        try:
            with tempfile.TemporaryDirectory() as td:
                results, mappings = self._stage(td)
                rename_bones.RESULTS_DIR = results
                rename_bones.MAPPINGS_DIR = mappings
                rename_bones.main(["m"])
                first = {
                    p.name: p.read_bytes() for p in results.iterdir()
                }
                rename_bones.main(["m"])
                self.assertEqual(
                    sorted(first.keys()),
                    sorted(p.name for p in results.iterdir()),
                )
        finally:
            self._restore_dirs(saved)

    def test_file_with_no_matching_bones_fails_run(self):
        saved = self._prev_dirs()
        try:
            with tempfile.TemporaryDirectory() as td:
                results, mappings = self._stage(td)
                # overwrite B.glb with a model whose bones are not in mapping
                (results / "B.glb").write_bytes(
                    make_glb(sample_gltf(["foo_0", "foo_1"]))
                )
                rename_bones.RESULTS_DIR = results
                rename_bones.MAPPINGS_DIR = mappings
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    rc = rename_bones.main(["m"])
                self.assertEqual(rc, 1)
                self.assertTrue((results / "A__m.glb").exists())
        finally:
            self._restore_dirs(saved)


if __name__ == "__main__":
    unittest.main()
