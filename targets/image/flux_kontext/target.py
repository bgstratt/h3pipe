"""
flux_kontext: FLUX.1 Kontext dev, image edit with one reference image
(target.json beside this file). Series refs are worded as every image target
words them, keyframes by targets/image/common.py; the graph code fills the
workflow's reference chain, or cuts it out (text to image).

Stdlib only.
"""
from __future__ import annotations

from targets.image.common import keyframe_prompt, ref_prompt, reference_chains  # noqa: F401


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    """`inputs["references"]`: the name ComfyUI's LoadImage reads (one at
    most). None: the reference chain is cut out and FluxGuidance reads the
    prompt itself."""
    links = (target.spec["binding"].get("references") or {}).get("links") or {}
    reference_chains(g, list(inputs.get("references") or [])[:1], links)
