"""H3ContinuityFrame: a continuity shot's first frame, cut when the job starts.

A `first: continuity` shot starts on the last frame of the take the cut uses
for the shot before it. Queued in one run behind that shot's new take, the
right frame doesn't exist until the take ahead of it has rendered; ComfyUI runs
its queue in order, so by the time this node runs it has. h3jobs.graph_for puts
this node in place of the LoadImage that would read the frame copied at queue
time (`image`, used as it is when the pipeline can't be reached), and it:

- makes the keyframe current (h3refs.continuity_at_start: cut again from the
  take the cut uses now when it is missing or stale, left alone when imported
  or generated),
- records in the take's sidecar what it started from (`continuity`), and
- outputs the frame, as LoadImage does (IMAGE, MASK).

It always runs again (IS_CHANGED): a cached frame is the wrong frame.
"""

from __future__ import annotations

import logging
import os

import torch

from .h3_shotlist import load_image

log = logging.getLogger("h3pipe")


class H3ContinuityFrame:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("STRING", {"default": ""}),
            "project_root": ("STRING", {"default": ""}),
            "shot": ("STRING", {"default": ""}),
            "pass_": ("STRING", {"default": "final"}),
        }, "optional": {
            "sidecar": ("STRING", {"default": ""}),
        }}

    RETURN_TYPES = ("IMAGE", "MASK")
    FUNCTION = "load"
    CATEGORY = "h3pipe"

    @classmethod
    def IS_CHANGED(cls, **_kw):
        return float("nan")

    def load(self, image, project_root, shot, pass_, sidecar=""):
        path = None
        try:
            from . import h3pipe_api as API
            if API.IMPORT_ERROR:
                raise RuntimeError(API.IMPORT_ERROR)
            res = API.R.continuity_at_start(os.path.normpath(project_root), shot, pass_, sidecar)
            path = res["path"]
            if res["note"]:
                log.info("h3pipe: %s: %s", shot, res["note"])
        except Exception as e:                           # the frame copied at queue time
            log.warning("h3pipe: %s: continuity frame not refreshed (%s); using the queued one",
                        shot, e)
        if not path:
            import folder_paths
            path = folder_paths.get_annotated_filepath(image)
        img = load_image(path)
        mask = torch.zeros((1, img.shape[1], img.shape[2]), dtype=torch.float32)
        return (img, mask)


NODE_CLASS_MAPPINGS = {"H3ContinuityFrame": H3ContinuityFrame}
NODE_DISPLAY_NAME_MAPPINGS = {"H3ContinuityFrame": "H3 Continuity Frame"}
