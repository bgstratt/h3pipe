"""
qwen_image_21: Qwen-Image 2.1 as an image target (target.json beside this
file) — text to image and edit from reference images in one graph.

`TextEncodeQwenImage21` returns positive conditioning, negative conditioning
and a LATENT spliced from whatever reference images it was given.
`ComfySwitchNode` chooses between that latent and an `EmptyLatentImage`, so
which job this target does is decided per generate, by whether the ref has
anything to edit from:

    references  -> the encoder's latent: an edit of those pictures
    none        -> the empty latent: text to image at the job's size

That is what makes one target serve both a character's first sheet and a
wardrobe variant edited out of it (docs/PLAN.md, Phase 10b), with no second
entry in the refs target picker.

Series refs and keyframes are worded as every image target words them
(targets/image/common.py). References are plain IMAGE inputs on one node, not
a conditioning chain, so this target fills them itself rather than through
`reference_chains`.

Stdlib only.
"""
from __future__ import annotations

import copy

from targets.image.common import keyframe_prompt, ref_prompt  # noqa: F401

ENCODER = "TextEncodeQwenImage21"
SWITCH = "ComfySwitchNode"
LOAD_NODE = "LoadImage"
REF_INPUT = "images.image_{}"      # the node's autogrow group, names image_1..image_16
REF_BASE = 1                       # ...and it counts from one, unlike H3's ref_image_0


def _node_of(g: dict, class_type: str) -> str | None:
    return next((k for k, v in g.items() if v.get("class_type") == class_type), None)


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    """Wire `inputs["references"]` into the encoder, and set the switch.

    The workflow carries one LoadImage feeding `images.image_1`; each further
    reference gets a copy of it and the next slot, up to the target's
    `max_refs`. With none, the LoadImage and the slots go and the switch sends
    the sampler to the empty latent — an ordinary text-to-image generate, which
    is what a character's own view wants (there is nothing to edit from).
    """
    enc, sw, load = _node_of(g, ENCODER), _node_of(g, SWITCH), _node_of(g, LOAD_NODE)
    if enc is None or sw is None or load is None:
        raise ValueError(f"a Qwen-Image 2.1 workflow needs a {ENCODER}, a {SWITCH} "
                         f"and a {LOAD_NODE}")
    names = list(inputs.get("references") or [])[:target.capabilities().get("max_refs", 0)]
    for k in [k for k in g[enc]["inputs"] if k.startswith("images.")]:
        g[enc]["inputs"].pop(k)
    if not names:
        g.pop(load, None)
        g[sw]["inputs"]["switch"] = True               # the empty latent
        return
    g[sw]["inputs"]["switch"] = False                  # the references' latent
    g[load]["inputs"]["image"] = names[0]
    g[enc]["inputs"][REF_INPUT.format(REF_BASE)] = [load, 0]
    nid = max((int(k) for k in g if str(k).isdigit()), default=0)
    for i, name in enumerate(names[1:], start=REF_BASE + 1):
        nid += 1
        extra = str(nid)
        g[extra] = copy.deepcopy(g[load])
        g[extra]["inputs"]["image"] = name
        if "_meta" in g[extra]:
            g[extra]["_meta"] = dict(g[extra]["_meta"], title=f"Reference {i}")
        g[enc]["inputs"][REF_INPUT.format(i)] = [extra, 0]
