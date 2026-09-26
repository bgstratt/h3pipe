"""ComfyUI-H3-Shotlist — shot-list driven MiniMax H3 Ref2VA production nodes,
plus the h3pipe editor's routes (docs/API.md) and its frontend (web/)."""

import logging

from .h3_shotlist import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .h3_upscale import NODE_CLASS_MAPPINGS as _UPSCALE, NODE_DISPLAY_NAME_MAPPINGS as _UPSCALE_NAMES

NODE_CLASS_MAPPINGS = {**NODE_CLASS_MAPPINGS, **_UPSCALE}
NODE_DISPLAY_NAME_MAPPINGS = {**NODE_DISPLAY_NAME_MAPPINGS, **_UPSCALE_NAMES}

WEB_DIRECTORY = "./web"

try:
    from . import h3pipe_routes  # noqa: F401  (registers /h3pipe/... on import)
except Exception as _exc:                                 # never stop the nodes loading
    logging.getLogger("h3pipe").warning("h3pipe: editor routes not registered: %s", _exc)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
