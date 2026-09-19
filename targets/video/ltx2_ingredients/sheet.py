"""
The reference sheet, composed by comfy_nodes/h3_refsheet.py (PIL) run as a
subprocess of the running Python: ComfyUI's under the editor's routes, which
has PIL; the CLI's otherwise, which may not (then the error says so). The
pipeline itself stays stdlib only, as h3refs.stitch_sheet does with mksheet.

Stdlib only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
HELPER = os.path.join(ROOT, "comfy_nodes", "h3_refsheet.py")


class SheetError(RuntimeError):
    """The reference sheet couldn't be composed."""


def compose(spec: dict, out: str, timeout: int = 120) -> dict:
    """Write the sheet `spec` describes to `out` (see h3_refsheet.py).
    Returns the helper's report ({"width", "height", "boxes"})."""
    r = subprocess.run([sys.executable, HELPER, out], input=json.dumps(spec).encode("utf-8"),
                       capture_output=True, timeout=timeout)
    err = r.stderr.decode("utf-8", "replace").strip()
    if r.returncode or not os.path.isfile(out):
        if "No module named 'PIL'" in err:
            raise SheetError(f"composing a reference sheet needs PIL, and {sys.executable} "
                             f"can't import it (pip install pillow, or run with ComfyUI's "
                             f"python)")
        raise SheetError("composing the reference sheet failed: " + (err or "no output")[-600:])
    try:
        return json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        return {}
