"""
The caches that keep the episode status fast: h3takes.file_sha1 (a ref's sha1,
kept while its size and mtime hold) and targets._folders (the target listing,
kept for a couple of seconds). Each must give the answer an uncached call would.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402


def sha1_of(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class FileSha1CacheTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "ref.png")
        T._SHA1_CACHE.clear()

    def tearDown(self):
        T._SHA1_CACHE.clear()
        self._tmp.cleanup()

    def write(self, data: bytes, age: float) -> None:
        with open(self.path, "wb") as fh:
            fh.write(data)
        t = time.time() - age
        os.utime(self.path, (t, t))

    def test_missing_file(self):
        self.assertIsNone(T.file_sha1(self.path))

    def test_unchanged_file_is_not_read_again(self):
        self.write(b"aaaa", age=60)
        self.assertEqual(T.file_sha1(self.path), sha1_of(b"aaaa"))
        st = os.stat(self.path)
        key = os.path.normcase(os.path.abspath(self.path))
        # poison the entry: a hit must come from the cache, not the file
        T._SHA1_CACHE[key] = (st.st_size, st.st_mtime_ns, "cached")
        self.assertEqual(T.file_sha1(self.path), "cached")

    def test_changed_mtime_is_hashed_again(self):
        self.write(b"aaaa", age=60)
        T.file_sha1(self.path)
        self.write(b"bbbb", age=30)               # same size, new mtime
        self.assertEqual(T.file_sha1(self.path), sha1_of(b"bbbb"))

    def test_changed_size_is_hashed_again(self):
        self.write(b"aaaa", age=60)
        T.file_sha1(self.path)
        self.write(b"aaaaaa", age=60)              # same mtime, new size
        self.assertEqual(T.file_sha1(self.path), sha1_of(b"aaaaaa"))

    def test_a_fresh_write_is_never_cached(self):
        # two writes inside the timer's resolution can share an mtime
        self.write(b"aaaa", age=0)
        T.file_sha1(self.path)
        self.assertEqual(T._SHA1_CACHE, {})

    def test_deleted_file(self):
        self.write(b"aaaa", age=60)
        T.file_sha1(self.path)
        os.remove(self.path)
        self.assertIsNone(T.file_sha1(self.path))


class FoldersCacheTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.show = self._tmp.name
        with open(os.path.join(self.show, "series.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        TG.clear_thread_roots()
        TG.forget_targets()

    def tearDown(self):
        TG.clear_thread_roots()
        TG.forget_targets()
        self._tmp.cleanup()

    def add_custom(self, tid: str) -> None:
        d = os.path.join(self.show, TG.CUSTOM_DIR, tid)
        os.makedirs(d)
        with open(os.path.join(d, "target.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": tid, "kind": "video"}, fh)

    def ids(self) -> set[str]:
        return {n for _, n, _ in TG._folders(self.show)}

    def test_listing_is_reused_and_forget_clears_it(self):
        self.assertNotIn("mine_x", self.ids())
        self.add_custom("mine_x")
        self.assertNotIn("mine_x", self.ids())      # cached
        TG.forget_targets()
        self.assertIn("mine_x", self.ids())

    def test_listing_expires(self):
        self.ids()
        self.add_custom("mine_y")
        key = (self.show, TG.thread_roots())
        stamp, folders = TG._FOLDERS[key]
        TG._FOLDERS[key] = (stamp - TG.FOLDERS_TTL - 1, folders)
        self.assertIn("mine_y", self.ids())

    def test_thread_roots_are_part_of_the_key(self):
        self.add_custom("mine_z")
        self.assertNotIn("mine_z", {n for _, n, _ in TG._folders(None)})
        TG.add_thread_root(self.show)               # a new key: scanned, not reused
        self.assertIn("mine_z", {n for _, n, _ in TG._folders(None)})
        TG.clear_thread_roots()
        self.assertNotIn("mine_z", {n for _, n, _ in TG._folders(None)})

    def test_callers_cannot_change_the_cached_listing(self):
        first = TG._folders(self.show)
        first.clear()
        self.assertTrue(TG._folders(self.show))


if __name__ == "__main__":
    unittest.main()
