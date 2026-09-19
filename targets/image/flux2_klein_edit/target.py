"""
flux2_klein_edit: FLUX.2 Klein 9B KV, image edit with reference images
(target.json beside this file). Series refs are worded as every image target
words them, keyframes by targets/image/common.py; the graph code gives the
workflow one reference chain per reference image (common.reference_chains).

Stdlib only.
"""
from __future__ import annotations

from targets.image.common import keyframe_prompt, ref_prompt, reference_chains  # noqa: F401


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    """`inputs["references"]`: the names ComfyUI's LoadImage reads, in order
    (at most capabilities.max_refs). None: the chain is cut out and the
    graph is text to image."""
    links = (target.spec["binding"].get("references") or {}).get("links") or {}
    reference_chains(g, list(inputs.get("references") or []), links)
