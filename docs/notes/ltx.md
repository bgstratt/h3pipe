# LTX 2.3 / 2.5 as a target: groundwork for Phase 8

Surveyed 2026-09-18 from the workflows saved in ComfyUI (`user/default/workflows`) and
the installed models. These are facts about the user's install and the LTX graphs,
gathered while Phase 7 (targets) was being built.

## What's installed
- **Diffusion models:** `ltx-2.3-22b-dev_transformer_only_int8_convrot`,
  `ltx-2.5-22b-distilled-transformer-comfy-int8-convrot`.
  - Checkpoints referenced by the 2.3 workflows: `ltx-2.3-22b-dev-fp8`,
    `ltx-2.3-22b-distilled-fp8`.
  - The 2.3 dev checkpoint runs with a distilled LoRA,
    `ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16` at 0.75.
- **Text encoders:**
  - 2.3: `gemma_3_12B_it_fp4_mixed`, via `LTXAVTextEncoderLoader`.
  - 2.5: `gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot`, via `CLIPLoader` with type
    `ltxv`. There is also a small `gemma4_e2b_it` used by `TextGenerateLTX2Prompt`, a
    prompt-enhancer node.
- **Custom nodes:** `ComfyUI-LTXVideo`.
- **Saved workflows:**
  - `video_ltx2_3_i2v.json` and `video_ltx2_5_i2v.json`: image-to-video.
  - `template_ltx2_3_ic_lora_ingredients.json`: the IC-LoRA "ingredients" multi-subject
    reference, which is LTX's answer to H3's reference pictures.
  - `LTX-2.3_-_V2V_Foley_…json`: audio for an existing video.
- Also installed: `minimax_h3_fl2va` (H3 first/last frame) and several Wan 2.2 models.
  Candidates for later targets.

## What differs from H3, and what it asks of the target interface
- **Subgraphs.** All three LTX workflows keep their graph inside a ComfyUI *subgraph*
  (`definitions.subgraphs`). `h3jobs.ui_to_api` does not flatten subgraphs. Either:
  - ship the LTX target's workflow as an **API export**; `resolve_workflow` already
    accepts one, and it's the simplest; or
  - teach the converter to inline subgraphs.
  
  Decide first: it blocks everything else.
- **Frame grid 8k+1** (9, 17, …, 97, …): a different template from H3's 17k+5. The
  templates default to 97 frames at 24–25 fps (`LTXVConditioning` frame_rate 24 or 25).
  Size defaults to 768×512, in multiples of 32.
- **Two stages.** A base pass, then `LTXVLatentUpsampler` and a refine pass, each with
  its own `ManualSigmas`/`RandomNoise`/`KSamplerSelect`. So the binding has **two seed
  and two sampler widgets**. The binding's `params` must allow more than one widget per
  role.
- **Audio + video.** LTX 2.x generates sound with the picture (`LTXVEmptyLatentAudio`,
  `LTXVConcatAVLatent`, `LTXVAudioVAEDecode`), like H3's generate policy. Clone and dub
  policies have no LTX equivalent. The recipe must reject them clearly, or fall back to
  generate with a warning.
- **Conditioning:**
  - **I2V / first frame:** `LTXVImgToVideoInplace` takes one image, which is where
    shot-scoped `shot:<id>:first` keyframes plug in.
  - **Last frame:** `LTXVAddGuide` with a frame index (e.g. -1), which gives FL2V.
  - **Subject references:** the IC-LoRA ingredients template, an
    `ltx-2.3-22b-ic-lora-ingredients` LoRA plus `LTXVAddGuide`. That's the closest
    equivalent to H3's subject sheets and a plate.
  
  The LTX recipe maps refs onto these; which inputs exist depends on the workflow the
  target ships.
- **Prompts:** prose, with no `<Picture N>` or `<Subject N>`, plus a negative prompt
  (the templates carry one). The prompt writer is new code. The shot IR has everything
  it needs: action, camera, dialogue with delivery, sound, the series config's
  `design`/`description`/`look`.
- **The saver:** the LTX graphs end in `CreateVideo`/`SaveVideo`. The target must feed
  `H3SaveShot`, a generic take saver, so sidecars, thumbnails and strips work. The
  binding decides which output (images + audio) goes to the saver.

## Suggested Phase 8 order
1. Pick one workflow: 2.5 distilled I2V is fast and suits proxies. Export it as API
   JSON and put it in `targets/video/ltx2/`.
2. Template (8k+1 frames, fps, size), then the binding (two seeds, the loader, and
   `H3SaveShot` in place of `SaveVideo`), then the prompt writer (prose).
3. Recipe: text-only first. Then the `first` keyframe via `LTXVImgToVideoInplace`, then
   `last` via `LTXVAddGuide`. Subject sheets via ingredients come last, if wanted.
4. Retarget one kitchen_sink shot, then a real episode's proxy. Fix every H3 assumption
   that surfaces.
5. The UI's target picker (Inspector and redo dialog), from `GET /h3pipe/targets`.
