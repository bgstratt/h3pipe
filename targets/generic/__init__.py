"""
targets.generic — target code no single model owns.

A target.json can point at one of these instead of shipping Python, with
`"code": "builtin:<module>"` (targets.Target.module):

    builtin:video_prose   a loader-less video target whose shots are one
                          paragraph of prose: prompt, size, length, seed,
                          model and LoRAs are widgets, keyframes are
                          declared in `binding.inputs`. The three Wan 2.2
                          targets are built on it (targets/video/wan/common.py
                          binds it to their wording), and it is what a custom
                          target made from a user's own ComfyUI workflow uses.

Stdlib only.
"""
