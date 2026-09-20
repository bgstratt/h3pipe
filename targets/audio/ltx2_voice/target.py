"""
ltx2_voice: LTX-2.5 audio-only, one speaking voice (target.json beside this
file). The brief is every audio target's (targets/audio/common.py); the graph
needs one piece of surgery the binding's widgets can't express, the
reference-audio (voice clone) chain.

Stdlib only.
"""
from __future__ import annotations

from targets.audio.common import NEGATIVE, NEUTRAL_LINE, voice_line, voice_prompt  # noqa: F401,E402

REF_LOAD = "LTXVReferenceAudio input"       # the LoadAudio node patch_graph adds
REF_PATCH = "Reference audio (ID-LoRA)"     # the LTXVReferenceAudio node it adds


def patch_graph(target, graph: dict, job, inputs: dict) -> None:
    """Splice LTXVReferenceAudio in when this job copies a voice sample:
    `inputs["reference_audio"]` is the name ComfyUI's LoadAudio reads (the
    sample, uploaded to its input folder by h3refs.stage_voice_reference).

    The node reads the model and the two conditionings that feed the guider
    named by `binding.reference_audio.guider`, and the guider then reads its
    three outputs. With no sample (or with `capabilities.reference_audio`
    false, which is how ltx2_voice ships: see target.json) nothing changes.
    """
    name = (inputs or {}).get("reference_audio")
    if not name:
        return
    spec = (target.spec.get("binding") or {}).get("reference_audio") or {}
    gspec = dict(spec.get("guider") or {})
    if not gspec.get("class_type"):
        raise ValueError(f"{target.id}: binding.reference_audio.guider must name the "
                         f"guider node the reference audio patches")
    import h3jobs as J

    gid = J.select_nodes(graph, gspec)[0]
    links = gspec.get("inputs") or {"model": "model", "positive": "positive",
                                    "negative": "negative"}
    g_in = graph[gid]["inputs"]
    preset = target.presets.get("final")
    scale = float((preset.extra if preset is not None else {}).get(
        "identity_guidance_scale", 3.0))
    graph["h3_ref_audio"] = {
        "class_type": "LoadAudio", "inputs": {"audio": name},
        "_meta": {"title": REF_LOAD}}
    graph["h3_ref_patch"] = {
        "class_type": "LTXVReferenceAudio",
        "inputs": {"model": g_in[links["model"]],
                   "positive": g_in[links["positive"]],
                   "negative": g_in[links["negative"]],
                   "reference_audio": ["h3_ref_audio", 0],
                   "audio_vae": _audio_vae(target, graph),
                   "identity_guidance_scale": scale,
                   "start_percent": 0.0, "end_percent": 1.0},
        "_meta": {"title": REF_PATCH}}
    g_in[links["model"]] = ["h3_ref_patch", 0]
    g_in[links["positive"]] = ["h3_ref_patch", 1]
    g_in[links["negative"]] = ["h3_ref_patch", 2]


def _audio_vae(target, graph: dict):
    """The link to the audio VAE the graph loads (the binding's `audio_vae`
    widget names its loader)."""
    import h3jobs as J

    specs = target.binding.specs("audio_vae")
    if not specs:
        raise ValueError(f"{target.id}: binding.params needs `audio_vae` to wire the "
                         f"reference audio")
    return [J.select_nodes(graph, specs[0])[0], 0]
