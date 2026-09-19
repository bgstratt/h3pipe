"""
Golden tests: rebuild every fixture with h3build and compare against the saved
outputs in tests/golden/.

    python -m pytest                      # or: python -m unittest discover tests
    python tests/test_golden.py --update  # rewrite the goldens (intended changes only)

A fixture is a folder holding series.json and script.md. Two roots are searched:

    tests/fixtures/<name>/          committed (synthetic, or safe to publish)
    tests/local/fixtures/<name>/    gitignored (real episodes); goldens in
                                    tests/local/golden/<name>/

plus the `example` fixture, which is examples/series_example.json and
examples/script_example.md. tests/fixtures/errors/*.md are scripts that must
fail; each is checked against the kitchen_sink series config and its golden is the
error message.

What is captured per fixture: the story IR (shots.json), both passes' shotlist
(and, for an episode that mixes targets, each other target's
shotlist.<target>[_proxy].json) and refs_todo (.json and .md), the stdout of --check for both passes (it carries every warning, which is
H3 logic that moves in later phases), and the stdout of --pace.

Builds run in a fresh temp folder, so refs_todo never sees refs on disk.
Comparison normalises CRLF to LF: json.dump writes CRLF on Windows, and git's
autocrlf would rewrite the files anyway. Everything else is compared byte for
byte.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "h3build.py")

ROOTS = [(os.path.join(HERE, "fixtures"), os.path.join(HERE, "golden")),
         (os.path.join(HERE, "local", "fixtures"), os.path.join(HERE, "local", "golden"))]
ERRORS = os.path.join(HERE, "fixtures", "errors")
ERROR_SERIES_CFG = os.path.join(HERE, "fixtures", "kitchen_sink", "series.json")

OUTPUTS = ["shotlist/shotlist.json", "shotlist/shotlist_proxy.json", "shotlist/shots.json",
           "refs_todo.json", "refs_todo.md", "refs_todo_proxy.json", "refs_todo_proxy.md"]


def fixtures() -> dict[str, tuple[str, str, str]]:
    """name -> (series.json, script.md, golden dir)"""
    found = {"example": (os.path.join(ROOT, "examples", "series_example.json"),
                         os.path.join(ROOT, "examples", "script_example.md"),
                         os.path.join(HERE, "golden", "example"))}
    for fx_root, golden_root in ROOTS:
        if not os.path.isdir(fx_root):
            continue
        for name in sorted(os.listdir(fx_root)):
            d = os.path.join(fx_root, name)
            series_cfg, script = os.path.join(d, "series.json"), os.path.join(d, "script.md")
            if os.path.isfile(series_cfg) and os.path.isfile(script):
                found[name] = (series_cfg, script, os.path.join(golden_root, name))
    return found


def error_cases() -> dict[str, str]:
    """name -> script path"""
    if not os.path.isdir(ERRORS):
        return {}
    return {os.path.splitext(f)[0]: os.path.join(ERRORS, f)
            for f in sorted(os.listdir(ERRORS)) if f.endswith(".md")}


def h3build(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, BUILD, *args], capture_output=True, env=env)


def norm(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def capture(series_cfg: str, script: str) -> dict[str, bytes]:
    """Every output of one fixture, keyed by golden file name."""
    out: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for flags in ([], ["--proxy"]):
            r = h3build(series_cfg, script, "-o", tmp, *flags)
            if r.returncode:
                raise RuntimeError(f"h3build {' '.join(flags)} failed:\n"
                                   + r.stderr.decode("utf-8", "replace"))
        for rel in OUTPUTS:
            with open(os.path.join(tmp, rel), "rb") as fh:
                out[os.path.basename(rel)] = norm(fh.read())
        # an episode that mixes targets also writes shotlist.<target>[_proxy].json
        for name in sorted(os.listdir(os.path.join(tmp, "shotlist"))):
            if name.startswith("shotlist.") and name != "shotlist.json":
                with open(os.path.join(tmp, "shotlist", name), "rb") as fh:
                    out[name] = norm(fh.read())
    # --check writes nothing and never prints the output folder
    for flags, name in (([], "check.txt"), (["--proxy"], "check_proxy.txt"),
                        (["--pace"], "pace.txt")):
        r = h3build(series_cfg, script, "--check", *flags)
        out[name] = norm(r.stdout) + (norm(r.stderr) if r.returncode else b"")
    return out


def capture_error(script: str) -> bytes:
    r = h3build(ERROR_SERIES_CFG, script, "--check")
    return f"exit {r.returncode}\n".encode() + norm(r.stderr)


def error_golden(name: str) -> str:
    return os.path.join(HERE, "golden", "errors", name + ".txt")


def update() -> None:
    for name, (series_cfg, script, gdir) in fixtures().items():
        os.makedirs(gdir, exist_ok=True)
        for fname, data in capture(series_cfg, script).items():
            with open(os.path.join(gdir, fname), "wb") as fh:
                fh.write(data)
        print(f"  {name:20} -> {os.path.relpath(gdir, ROOT)}")
    for name, script in error_cases().items():
        path = error_golden(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(capture_error(script))
    print(f"  {len(error_cases())} error cases -> tests/golden/errors")


class GoldenTest(unittest.TestCase):
    maxDiff = None

    def _compare(self, got: bytes, golden_path: str) -> None:
        if not os.path.isfile(golden_path):
            self.fail(f"no golden at {os.path.relpath(golden_path, ROOT)} — "
                      f"run `python tests/test_golden.py --update`")
        with open(golden_path, "rb") as fh:
            want = norm(fh.read())
        if got != want:
            self.assertEqual(got.decode("utf-8", "replace"), want.decode("utf-8", "replace"),
                             os.path.relpath(golden_path, ROOT))


def _fixture_test(series_cfg: str, script: str, gdir: str):
    def test(self: GoldenTest) -> None:
        got = capture(series_cfg, script)
        for fname, data in got.items():
            with self.subTest(file=fname):
                self._compare(data, os.path.join(gdir, fname))
    return test


def _error_test(script: str, name: str):
    def test(self: GoldenTest) -> None:
        got = capture_error(script)
        self.assertFalse(got.startswith(b"exit 0\n"), "script was expected to fail")
        self._compare(got, error_golden(name))
    return test


for _name, (_b, _s, _g) in fixtures().items():
    setattr(GoldenTest, f"test_build_{_name}", _fixture_test(_b, _s, _g))
for _name, _s in error_cases().items():
    setattr(GoldenTest, f"test_error_{_name}", _error_test(_s, _name))


if __name__ == "__main__":
    if "--update" in sys.argv:
        update()
    else:
        unittest.main()
