"""
minimax_h3_still: MiniMax H3 used as an image target (target.json beside this
file). H3 with reference images behaves like an image edit and its first frame
is a clean still, so the same model that renders the shots also generates the
references — a variant's view edited out of the character's own sheet, without
a second model stack installed.

The graph is the Ref2VA workflow with everything shot-shaped removed: it
renders `length` frames (5 by default, the node's floor) and keeps frame 0.
Series refs and keyframes are worded exactly as every image target words them
(targets/image/common.py), so switching to this target changes the picture,
not the brief.

References are plain IMAGE inputs on one node here, not a conditioning chain,
so this target fills them itself rather than through `reference_chains`.

Stdlib only.
"""
from __future__ import annotations

import copy

from targets.image.common import keyframe_prompt, ref_prompt  # noqa: F401

H3_NODE = "MiniMaxH3ReferenceToVideo"
REF_INPUT = "ref_images.ref_image_{}"      # the node's autogrow group, as the
                                           # video path already sends it
LOAD_NODE = "LoadImage"


def _node_of(g: dict, class_type: str) -> str | None:
    return next((k for k, v in g.items() if v.get("class_type") == class_type), None)


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    """Wire `inputs["references"]` (the names ComfyUI's LoadImage reads, in
    order) into the H3 node's reference slots.

    The workflow carries one LoadImage feeding `ref_image_0`; each further
    reference gets a copy of it and the next slot, up to the target's
    `max_refs`. With no references the LoadImage and the slot both go, and H3
    works from the prompt alone — which is what a plain character view wants
    (nothing to edit from) and what any text-to-image target would do.
    """
    h3 = _node_of(g, H3_NODE)
    load = _node_of(g, LOAD_NODE)
    if h3 is None or load is None:
        raise ValueError(f"an H3 still workflow needs a {H3_NODE} and a {LOAD_NODE}")
    names = list(inputs.get("references") or [])[:target.capabilities().get("max_refs", 0)]
    slots = [k for k in g[h3]["inputs"] if k.startswith("ref_images.")]
    for k in slots:
        g[h3]["inputs"].pop(k)
    if not names:
        g.pop(load, None)
        return
    g[load]["inputs"]["image"] = names[0]
    g[h3]["inputs"][REF_INPUT.format(0)] = [load, 0]
    nid = max((int(k) for k in g if str(k).isdigit()), default=0)
    for i, name in enumerate(names[1:], start=1):
        nid += 1
        extra = str(nid)
        g[extra] = copy.deepcopy(g[load])
        g[extra]["inputs"]["image"] = name
        if "_meta" in g[extra]:
            g[extra]["_meta"] = dict(g[extra]["_meta"], title=f"Reference {i + 1}")
        g[h3]["inputs"][REF_INPUT.format(i)] = [extra, 0]
