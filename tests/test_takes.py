"""Unit tests for h3takes: take numbering and reservation, overrides, the cut."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3takes as T  # noqa: E402


def touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()


class TakesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_paths(self):
        tp = T.take_paths(self.root, "proxy", "ep01/sh 010", 3)
        self.assertEqual(os.path.basename(tp.dir), "ep01_sh_010")
        self.assertIn("renders_proxy", tp.dir)
        self.assertTrue(tp.mp4.endswith("ep01_sh_010_t03.mp4"))
        self.assertTrue(tp.strip.endswith("ep01_sh_010_t03_strip.jpg"))
        self.assertTrue(tp.shotlist.endswith("ep01_sh_010_t03.shotlist.json"))

    def test_legacy_takes_without_sidecar(self):
        touch(T.take_paths(self.root, "final", "sh010", 1).mp4)
        touch(T.take_paths(self.root, "final", "sh010", 2).mp4)
        touch(T.take_paths(self.root, "final", "sh010", 2).h3_wav)
        takes = T.list_takes(self.root, "final", "sh010")
        self.assertEqual([t.take for t in takes], [1, 2])
        self.assertTrue(all(t.status == "ok" and t.usable for t in takes))
        self.assertEqual(T.latest_usable(takes).take, 2)

    def test_reserve_counts_sidecars_and_mp4s(self):
        touch(T.take_paths(self.root, "final", "sh010", 1).mp4)
        a = T.reserve_take(self.root, "final", "sh010", {"status": "queued"})
        b = T.reserve_take(self.root, "final", "sh010", {"status": "queued"})
        self.assertEqual((a.take, b.take), (2, 3))
        takes = T.list_takes(self.root, "final", "sh010")
        self.assertEqual([t.status for t in takes], ["ok", "queued", "queued"])
        self.assertEqual(T.latest_usable(takes).take, 1)
        self.assertEqual(b.sidecar["pass"], "final")
        self.assertEqual(b.sidecar["version"], T.SIDECAR_VERSION)

    def test_reserve_is_race_safe(self):
        got, lock = [], threading.Lock()

        def worker():
            t = T.reserve_take(self.root, "final", "sh010", {"status": "queued"})
            with lock:
                got.append(t.take)
        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(got), list(range(1, 13)))

    def test_reserve_explicit_take_overwrites(self):
        T.reserve_take(self.root, "final", "sh010", {"status": "ok", "seed": 1})
        t = T.reserve_take(self.root, "final", "sh010", {"status": "queued", "seed": 2}, take=1)
        self.assertEqual(t.take, 1)
        self.assertEqual(T.read_json(t.paths.sidecar)["seed"], 2)

    def test_ok_sidecar_without_mp4_is_not_usable(self):
        t = T.reserve_take(self.root, "final", "sh010", {"status": "queued"})
        T.update_sidecar(t.paths.sidecar, status="ok")
        self.assertFalse(T.get_take(self.root, "final", "sh010", 1).usable)
        touch(t.paths.mp4)
        self.assertTrue(T.get_take(self.root, "final", "sh010", 1).usable)

    def test_sweep_queued(self):
        a = T.reserve_take(self.root, "final", "sh010", {"status": "queued", "comfy_prompt_id": "a"})
        T.reserve_take(self.root, "final", "sh010", {"status": "queued", "comfy_prompt_id": "b"})
        changed = T.sweep_queued(T.list_takes(self.root, "final", "sh010"), alive={"b"})
        self.assertEqual([t.take for t in changed], [a.take])
        self.assertEqual([t.status for t in T.list_takes(self.root, "final", "sh010")],
                         ["failed", "queued"])

    def test_sweep_leaves_fresh_and_later_takes_alone(self):
        old = "2026-01-01T10:00:00+00:00"
        snap = "2026-01-01T10:05:00+00:00"
        a = T.reserve_take(self.root, "final", "sh010", {"status": "queued", "queued": old})
        b = T.reserve_take(self.root, "final", "sh010",   # reserved, not yet queued
                           {"status": "queued", "queued": "2026-01-01T10:04:30+00:00"})
        c = T.reserve_take(self.root, "final", "sh010",   # queued after the snapshot
                           {"status": "queued", "comfy_prompt_id": "x",
                            "queued": "2026-01-01T10:06:00+00:00"})
        d = T.reserve_take(self.root, "final", "sh010",
                           {"status": "queued", "comfy_prompt_id": "y", "queued": old})
        changed = T.sweep_queued(T.list_takes(self.root, "final", "sh010"), set(), as_of=snap)
        self.assertEqual(sorted(t.take for t in changed), [a.take, d.take])
        self.assertNotIn(b.take, [t.take for t in changed])
        self.assertNotIn(c.take, [t.take for t in changed])

    def test_claimed_but_empty_sidecar_reads_as_queued(self):
        touch(T.take_paths(self.root, "final", "sh010", 1).sidecar)
        (t,) = T.list_takes(self.root, "final", "sh010")
        self.assertEqual(t.status, "queued")
        self.assertEqual(T.reserve_take(self.root, "final", "sh010", {}).take, 2)

    def test_folder_override(self):
        t = T.reserve_take(self.root, "final", "sh010", {}, folder="renders_alt")
        self.assertIn("renders_alt", t.paths.dir)
        self.assertEqual(T.take_numbers(self.root, "final", "sh010"), [])
        self.assertEqual(T.take_numbers(self.root, "final", "sh010", "renders_alt"), [1])

    def test_write_json_atomic_and_lf(self):
        p = os.path.join(self.root, "x.json")
        T.write_json(p, {"a": 1})
        with open(p, "rb") as fh:
            self.assertNotIn(b"\r\n", fh.read())
        self.assertEqual(os.listdir(self.root), ["x.json"])

    def test_content_hash_is_key_order_independent(self):
        self.assertEqual(T.content_hash({"a": 1, "b": [1, 2]}),
                         T.content_hash({"b": [1, 2], "a": 1}))


class OverridesTest(unittest.TestCase):
    def test_set_get_clear(self):
        d = {}
        T.set_override(d, "sh020", seed=42, prompt="text", base_hash="h")
        T.set_override(d, "sh020", "final", steps=10, loras=[{"name": "x", "strength": 0.7}])
        T.set_override(d, "sh020", "proxy", model="m")
        self.assertEqual(T.shot_override(d, "sh020", "final"),
                         {"prompt": "text", "seed": 42, "base_hash": "h", "steps": 10,
                          "loras": [{"name": "x", "strength": 0.7}]})
        self.assertEqual(T.shot_override(d, "sh020", "proxy")["model"], "m")
        self.assertNotIn("steps", T.shot_override(d, "sh020", "proxy"))
        self.assertEqual(T.shot_override(d, "sh999", "final"), {})
        T.set_override(d, "sh020", "final", steps=None, loras=None)
        T.set_override(d, "sh020", "proxy", model=None)
        T.set_override(d, "sh020", seed=None, prompt=None)
        self.assertEqual(d["shots"], {})

    def test_empty_lora_list_is_kept(self):
        d = {}
        T.set_override(d, "sh020", "final", loras=[])
        self.assertEqual(T.shot_override(d, "sh020", "final"), {"loras": []})

    def test_pass_field_needs_pass(self):
        with self.assertRaises(ValueError):
            T.set_override({}, "sh020", steps=4)
        with self.assertRaises(ValueError):
            T.set_override({}, "sh020", bogus=1)

    def test_roundtrip_file(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(T.load_overrides(root), {"shots": {}})
            d = T.set_override(T.load_overrides(root), "sh010", seed=7)
            T.save_overrides(root, d)
            self.assertEqual(T.shot_override(T.load_overrides(root), "sh010", "final"),
                             {"seed": 7})


class CutTest(unittest.TestCase):
    ORDER = ["sh010", "sh020", "sh030", "sh040"]

    def shots(self, entries):
        return [e.shot for e in entries]

    def test_no_cut_is_script_order(self):
        es = T.resolve_cut({}, "final", self.ORDER)
        self.assertEqual(self.shots(es), self.ORDER)
        self.assertTrue(all(e.take is None and not e.in_cut_file for e in es))

    def test_reorder_kept_and_new_shots_inserted(self):
        cut = {"final": [{"shot": "sh030", "take": 2}, {"shot": "sh010"}]}
        es = T.resolve_cut(cut, "final", self.ORDER)
        # sh020 follows its script predecessor sh010; sh040 follows sh030
        self.assertEqual(self.shots(es), ["sh030", "sh040", "sh010", "sh020"])
        self.assertEqual(es[0].take, 2)

    def test_first_script_shot_missing_goes_to_front(self):
        es = T.resolve_cut({"final": [{"shot": "sh020"}]}, "final", self.ORDER)
        self.assertEqual(self.shots(es), self.ORDER)

    def test_orphan_placeholder_and_duplicates(self):
        cut = {"final": [{"shot": "sh010"}, {"shot": "sh015", "take": 1},
                         {"shot": "sh020", "pass": "proxy", "take": 4, "note": "stand-in"},
                         {"shot": "sh010", "take": 9}]}
        es = {e.shot: e for e in T.resolve_cut(cut, "final", self.ORDER)}
        self.assertTrue(es["sh015"].orphan)
        self.assertTrue(es["sh020"].placeholder)
        self.assertEqual(es["sh020"].pass_, "proxy")
        self.assertIsNone(es["sh010"].take)            # first listing wins

    def test_pick_materialises_and_roundtrips(self):
        cut = T.pick({}, "final", self.ORDER, "sh020", 3)
        self.assertEqual(cut["final"], [{"shot": "sh010"}, {"shot": "sh020", "take": 3},
                                        {"shot": "sh030"}, {"shot": "sh040"}])
        cut = T.pick(cut, "final", self.ORDER, "sh030", 1, from_pass="proxy")
        self.assertEqual(cut["final"][2], {"shot": "sh030", "pass": "proxy", "take": 1})
        cut = T.pick(cut, "final", self.ORDER, "sh020", None)
        self.assertEqual(cut["final"][1], {"shot": "sh020"})
        with self.assertRaises(KeyError):
            T.pick(cut, "final", self.ORDER, "sh999", 1)

    def test_extra_fields_survive(self):
        cut = {"final": [{"shot": "sh010", "color": "red"}]}
        cut = T.pick(cut, "final", self.ORDER, "sh010", 2)
        self.assertEqual(cut["final"][0], {"shot": "sh010", "take": 2, "color": "red"})


if __name__ == "__main__":
    unittest.main()
