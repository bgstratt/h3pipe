"""
qwen_rapid_aio: Phr00t's Qwen-Image-Edit Rapid AIO (target.json beside this
file) -- a merge of Qwen-Image-Edit-2511 with its accelerator LoRAs, model,
text encoder and VAE in one checkpoint, editing from up to three reference
images in four steps. Its strength is putting characters from their own
pictures into a scene while keeping their faces.

`TextEncodeQwenImageEditPlus` reads the references as plain IMAGE inputs
(`image1`..`image3`, counting from one) and splices them into the
conditioning itself, so this target wires them rather than going through
`reference_chains`. Both encoders get the same pictures: the negative is the
template's empty prompt over the same references, which is what keeps cfg 1
neutral. With none, the LoadImage goes and the graph is text to image.

Phr00t's replacement nodes_qwen.py (four inputs, better cropping) is NOT
used: it overwrites ComfyUI's own file, which also defines the
TextEncodeQwenImage21 node qwen_image_21 needs.

Stdlib only.
"""
from __future__ import annotations

import copy

from targets.image.common import keyframe_prompt, ref_prompt  # noqa: F401

ENCODER = "TextEncodeQwenImageEditPlus"
LOAD_NODE = "LoadImage"
REF_INPUT = "image{}"
MAX_INPUTS = 3                     # the stock node's image1..image3


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    """Wire `inputs["references"]` (ComfyUI input names, in order) into both
    encoders' image1..image3; with none, drop the images and the LoadImage."""
    encs = [k for k, v in g.items() if v.get("class_type") == ENCODER]
    load = next((k for k, v in g.items() if v.get("class_type") == LOAD_NODE), None)
    if not encs or load is None:
        raise ValueError(f"a Rapid AIO workflow needs a {ENCODER} and a {LOAD_NODE}")
    limit = min(MAX_INPUTS, int(target.capabilities().get("max_refs") or MAX_INPUTS))
    names = list(inputs.get("references") or [])[:limit]
    for e in encs:
        for i in range(1, MAX_INPUTS + 1):
            g[e]["inputs"].pop(REF_INPUT.format(i), None)
    if not names:
        g.pop(load, None)
        return
    g[load]["inputs"]["image"] = names[0]
    loads = [load]
    nid = max((int(k) for k in g if str(k).isdigit()), default=0)
    for i, name in enumerate(names[1:], start=2):
        nid += 1
        extra = str(nid)
        g[extra] = copy.deepcopy(g[load])
        g[extra]["inputs"]["image"] = name
        if "_meta" in g[extra]:
            g[extra]["_meta"] = dict(g[extra]["_meta"], title=f"Reference {i}")
        loads.append(extra)
    for e in encs:
        for i, node in enumerate(loads, start=1):
            g[e]["inputs"][REF_INPUT.format(i)] = [node, 0]
