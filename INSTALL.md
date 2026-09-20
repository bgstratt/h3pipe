# Installing h3pipe

From a clean ComfyUI to a rendered proxy episode. Windows first (the development
machine); the Linux/macOS differences are called out where they exist.

What you end up with: a `custom_nodes` pack that adds five nodes and the editor to
ComfyUI, a `h3.py` command line, and one episode folder holding a series config, a
script, and everything the pipeline generated from them.

1. [Requirements](#1-requirements)
2. [The custom node pack](#2-the-custom-node-pack)
3. [Workflows](#3-workflows)
4. [Models](#4-models)
5. [First run](#5-first-run)
6. [Troubleshooting](#6-troubleshooting)

The format of what you write is [docs/AUTHORING.md](docs/AUTHORING.md); what the
pipeline does with it is the [README](README.md).

---

## 1. Requirements

Two different Pythons are in play, and it matters which one gets what:

| | Which Python | Needs |
|---|---|---|
| **The node pack and the editor's routes** | ComfyUI's own (`python_embeded` on the Windows portable build, or whatever venv you start ComfyUI with) | nothing beyond what ComfyUI already has — the nodes use torch, numpy and PIL, which ComfyUI provides |
| **The `h3.py` command line** | whichever Python you type | **nothing**: the pipeline scripts are Python 3.10+ standard library only. No virtualenv, no `pip install` |
| **`h3align` (timing a script to a recording)** | the first interpreter that has the packages (see below) | `faster-whisper` and `numpy` |
| **`demucs`, for the `dub_keep_foley` foley bed** | the `python` first on ComfyUI's PATH — the Save Shot node shells out to `python -m demucs` | `demucs` |

- **Python 3.10 or later** for `h3.py` and everything it runs. Nothing to install.
- **`ffmpeg` and `ffprobe` on PATH.** `h3.py assemble` needs them to cut the episode
  together; the waveform peaks, the audio import and `h3align` need them too. On Windows,
  `winget install Gyan.FFmpeg` (that is the hint the editor itself prints). On Linux/macOS,
  your package manager (`apt install ffmpeg`, `brew install ffmpeg`).
- **A local [ComfyUI](https://github.com/comfyanonymous/ComfyUI)**, frontend **1.3 or
  later** for the editor (an older frontend has no `extensionManager.registerSidebarTab`
  and the editor logs that and gives up). h3pipe expects it at
  `http://127.0.0.1:8188`; `--comfy URL` changes that per command.
- **The model files** of the targets you want to render on — see [Models](#4-models).

### Optional, and what each one unlocks

| Install | Unlocks |
|---|---|
| `pip install faster-whisper numpy` | `h3.py align` / the editor's Recording window: transcribe a dialogue recording and write `audio:` windows onto every shot |
| `pip install demucs` | the `dub_keep_foley` audio policy (H3's foley kept under your own vocal) |
| Node.js 20+ and `npm install` in `web/` | rebuilding the editor UI. Only if you change `web/`; the built bundle is committed |

**Which Python gets faster-whisper.** The editor runs inside ComfyUI's embedded Python,
which usually has neither numpy nor a Whisper, while your system Python does. h3pipe
therefore picks the interpreter for `h3align` itself, best first:

1. `$H3PIPE_ALIGN_PYTHON`, if set to an interpreter path
2. the process doing the asking (ComfyUI's embedded Python, in the editor; your own, on
   the command line)
3. `python`, then `python3`, as found on PATH

— the first of those that has both `numpy` and a Whisper. `h3align` runs as a subprocess
either way, so it does not have to be ComfyUI's Python. The editor's Recording window
names the interpreter it settled on and prints the exact `pip install` line for it; on
the command line, `GET /h3pipe/align/ready` reports the same. If you installed
`faster-whisper` somewhere none of those three find it, set `H3PIPE_ALIGN_PYTHON` to that
interpreter's full path.

### Getting the repo

```
git clone https://github.com/<you>/h3pipe.git C:\code\h3pipe
```

Anywhere you like — it does not have to live near ComfyUI. `h3.py` finds the other
scripts beside itself, so you can run it by its full path from any folder.

Two environment variables are worth setting for the command line (neither is required):

| Variable | What it does |
|---|---|
| `COMFYUI_PATH` | the ComfyUI root (the folder holding `models\` and `user\`). Lets the CLI read model files' headers to identify them, and find your saved workflows without asking the running ComfyUI |
| `H3PIPE_ROOTS` | `;`-separated project folders for the editor, used only until you set roots in the editor itself (they are then stored in `user/default/h3pipe/config.json`). `:`-separated on Linux/macOS |

---

## 2. The custom node pack

`comfy_nodes/` is the ComfyUI side: the five nodes (`H3 Shot List Loader`, `H3 Shot
Info`, `H3 Save Shot`, `H3 Save Ref Take`, `H3 Save Ref Audio`), the editor's HTTP routes
([docs/API.md](docs/API.md)) and the editor's frontend bundle
(`comfy_nodes/web/h3pipe-editor.js`).

### The link way (preferred)

The routes import the pipeline from **the folder above `comfy_nodes/`**, resolving symlinks
and junctions first. Link the folder into `custom_nodes` and everything just works — one
copy of the code, and editing the repo edits what ComfyUI loads.

Windows, in an ordinary (non-elevated) Command Prompt — `mklink /J` makes a directory
junction and needs no administrator rights:

```
mklink /J "C:\AI\ComfyUI\ComfyUI\custom_nodes\ComfyUI-H3-Shotlist" "C:\code\h3pipe\comfy_nodes"
```

PowerShell has no `mklink`; use `cmd /c mklink /J ...` or `New-Item -ItemType Junction`.

Linux/macOS:

```
ln -s /path/to/h3pipe/comfy_nodes /path/to/ComfyUI/custom_nodes/ComfyUI-H3-Shotlist
```

The link's name is up to you; `ComfyUI-H3-Shotlist` is the conventional one.

### The copy way

If you would rather copy `comfy_nodes\` into `custom_nodes\ComfyUI-H3-Shotlist\`, the
folder above it is now `custom_nodes\`, not the repo — so the routes cannot find
`h3edit`, `h3jobs`, `targets` and the rest. Point them at the repo with **`H3PIPE_HOME`**,
set in the environment ComfyUI starts in:

```
set H3PIPE_HOME=C:\code\h3pipe
```

(`export H3PIPE_HOME=/path/to/h3pipe` elsewhere.) The nodes themselves load either way;
without `H3PIPE_HOME` it is the editor that goes missing, and ComfyUI's log says
`h3pipe: can't import the pipeline from … set H3PIPE_HOME to the repo`.

With a copy you must also re-copy after every `git pull` — and restart ComfyUI whenever
`comfy_nodes/h3_shotlist.py` changes, since ComfyUI imports the node classes once at
startup.

### Restart ComfyUI and check it loaded

ComfyUI's console lists the pack among its custom nodes at startup. If the pipeline
import failed, the nodes still load and a warning line appears:

```
h3pipe: editor routes not registered: …
```

Three checks, weakest to strongest:

1. **The nodes.** Double-click the canvas and search for `H3 Shot List Loader`. If it is
   there, `custom_nodes` found the pack.
2. **The routes.** `curl http://127.0.0.1:8188/h3pipe/config` should answer JSON:
   ```json
   {"roots": [], "comfy": "http://127.0.0.1:8188", "version": 1}
   ```
   Anything else (404, or ComfyUI's HTML) means the pipeline import failed — check the
   log for the `h3pipe:` line and set `H3PIPE_HOME`.
3. **The editor.** Reload the browser tab. The left sidebar gains **h3 Shots** (a film
   icon) and **h3 Refs**, and the bottom panel gains an **h3 Timeline** tab. If the routes
   answer but the tabs do not appear, it is the frontend: the browser console will say
   `this ComfyUI frontend has no extensionManager.registerSidebarTab; the editor needs
   frontend >= 1.3`.

Open **h3 Shots** and set your **project roots** — the folders holding your episodes. That
is stored in `user/default/h3pipe/config.json`; every episode the editor touches has to
live inside one of them.

---

## 3. Workflows

Every target names one ComfyUI workflow, by the name it expects among ComfyUI's **saved**
workflows (`C:\AI\ComfyUI\ComfyUI\user\default\workflows\`):

| Target | Saved workflow it looks for | Override with |
|---|---|---|
| `minimax_h3_ref2va` | `H3_Ref2VA_Shotlist_v1.json` | `$H3_WORKFLOW` |
| `minimax_h3_fl2va` | `h3pipe_minimax_h3_fl2va.json` | `$H3_FL2VA_WORKFLOW` |
| `ltx2` | `h3pipe_ltx2_5.json` | `$H3_LTX2_WORKFLOW` |
| `ltx2_ingredients` | `h3pipe_ltx2_3_ingredients.json` | `$H3_LTX2_INGREDIENTS_WORKFLOW` |
| `wan22_i2v` | `h3pipe_wan22_14b_i2v.json` | `$H3_WAN22_I2V_WORKFLOW` |
| `wan22_ti2v` | `h3pipe_wan22_5b_ti2v.json` | `$H3_WAN22_TI2V_WORKFLOW` |
| `wan22_vace` | `h3pipe_wan22_14b_vace.json` | `$H3_WAN22_VACE_WORKFLOW` |
| `krea2` | `krea2_refs_t2i.json` | `$KREA_WORKFLOW` |
| `z_image_turbo` | `h3pipe_z_image_turbo.json` | `$H3_Z_IMAGE_WORKFLOW` |
| `flux2_klein` | `h3pipe_flux2_klein_t2i.json` | `$H3_FLUX2_KLEIN_WORKFLOW` |
| `flux2_klein_edit` | `h3pipe_flux2_klein_edit.json` | `$H3_FLUX2_KLEIN_EDIT_WORKFLOW` |
| `flux_kontext` | `h3pipe_flux_kontext.json` | `$H3_FLUX_KONTEXT_WORKFLOW` |
| `ltx2_voice` | `h3pipe_ltx2_voice.json` | `$H3_LTX2_VOICE_WORKFLOW` |

**You usually need to do nothing.** The lookup order is:

1. `--workflow <path>` on the command line
2. the target's environment variable, if it points at a file
3. the workflow of that name **as saved in the running ComfyUI** (fetched over its API, so
   it always matches your node versions)
4. `$COMFYUI_PATH\user\default\workflows\<name>`
5. **the copy in this repo**, `targets/<kind>/<id>/workflow.json`

Step 5 is why a fresh install renders without you saving anything. The repo copies are
checked in and kept working.

Saving a workflow in ComfyUI under the expected name is how you take over: from then on
that graph is what renders, and it is a ComfyUI-native way to change the sampler,
scheduler, or splice in a LoRA without touching Python. Keep the saved
`krea2_refs_t2i.json` free of style LoRAs (experiment under another name) — the reference
images have to follow the look your series config describes.

Two targets bend the order slightly:

- **`krea2`** has one more fallback: finding no file at all, it uses a built-in Krea 2
  turbo graph (`targets/image/krea2/graph.py`). `--no-workflow` forces that.
- **`ltx2_voice`** (and any audio target) puts the repo copy *ahead* of `$COMFYUI_PATH`,
  because no saved canvas is likely to hold an audio-only LTX graph and one of that name
  would be somebody's experiment. The running ComfyUI's saved copy still wins.

`python h3render.py --dry-run --check-nodes <episode>` asks the running ComfyUI whether it
knows every node and input in the graph it would send — worth running once after a
ComfyUI update.

---

## 4. Models

Put each file in the ComfyUI models folder named beside it, e.g.
`C:\AI\ComfyUI\ComfyUI\models\diffusion_models\`. ComfyUI also accepts `models\unet\` for
`diffusion_models` and `models\clip\` for `text_encoders`.

**Check before you download.** With ComfyUI running:

```
python h3.py targets
```

prints one line per target — `ready`, `degraded`, `not_ready` or `unknown` — and under it
every file it cannot find, with the folder it belongs in and the download link. `--kind
video`, `--kind image` or `--kind audio` narrows it; `--json` prints data. In the editor, the target
picker and the **What's missing** window show the same thing. A file you already have
under another name usually still counts: h3pipe identifies models by family (names first,
then the safetensors header), so a re-quantized or renamed copy of the same model is
accepted and the take's sidecar records what was actually used.

**Nodes count too.** Some targets need node classes as well as files — the LTX-2.5 and
Wan 2.2 graphs use nodes that ship with core ComfyUI, and MiniMax H3 FL2VA needs
`MiniMaxH3AddGuide`. `h3.py targets` lists a missing one as `node <Class> (update ComfyUI,
or install the node pack)`; the usual fix is updating ComfyUI.

The list below is generated from the targets themselves
(`python tools/make_models_md.py`), so it cannot drift from what the code asks ComfyUI to
load. No URL is guessed: a file whose download record has no trustworthy source is listed
as having none.

<!-- BEGIN MODELS (generated by tools/make_models_md.py — do not edit) -->

### The minimum set

The two default targets — `minimax_h3_ref2va` (the video model every shot renders on unless the script says otherwise) and `krea2` (the image model that draws the character sheets, props and location plates) — need 7 required files:

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `models/text_encoders/` | required | `minimax_h3_ref2va` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) |
| `minimax_h3_video_vae_fp16.safetensors` | `models/vae/` | required | `minimax_h3_ref2va` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_fp16.safetensors) |
| `minimax_h3_audio_vae_fp32.safetensors` | `models/vae/` | required | `minimax_h3_ref2va` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) |
| `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `models/diffusion_models/` | required | `minimax_h3_ref2va` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors) |
| `wan_2.1_vae.safetensors` | `models/vae/` | required | `krea2` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors) |
| `krea2_turbo_fp8_scaled.safetensors` | `models/diffusion_models/` | required | `krea2` | [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2/resolve/main/diffusion_models/krea2_turbo_fp8_scaled.safetensors) |
| `qwen3vl_4b_fp8_scaled.safetensors` | `models/text_encoders/` | required | `krea2` | [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2/resolve/main/text_encoders/qwen3vl_4b_fp8_scaled.safetensors) |

Plus, for those two targets:

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors` | `models/loras/` | accelerator | `minimax_h3_ref2va` | [Kijai/MiniMax-H3_comfy](https://huggingface.co/Kijai/MiniMax-H3_comfy/resolve/main/loras/minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors) |
| `minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` | `models/loras/` | accelerator | `minimax_h3_ref2va` | **none recorded** |

An **accelerator** is a turbo/distilled LoRA or checkpoint: without it the pass still renders, on its slower `base` preset (more steps, no LoRA), and the take's sidecar says so. A **required** file missing skips the shot. An **optional** file only switches off the feature named beside it.

### Everything, by model family

Every file any target names. `python h3.py targets` tells you which of these your ComfyUI is actually missing — you only need the families of the targets you use.

**LTX 2.5** (`ltx2.5`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors` | `models/diffusion_models/` | required | `ltx2`, `ltx2_voice` (accelerator) | [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5/resolve/main/diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors) |
| `ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors` | `models/diffusion_models/` | accelerator | `ltx2` (optional: the quality profile (LTX-2.5 dev transformer)), `ltx2_voice` | [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5/resolve/main/diffusion_models/ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors) |

**Gemma 4 12B with the LTX 2.5 projection** (`ltx2.5-text-encoder`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors` | `models/text_encoders/` | required | `ltx2`, `ltx2_voice` | [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5/resolve/main/text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors) |

**LTX 2.5 video VAE** (`ltx2.5-video-vae`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `ltx-2.5-video-vae-bf16.safetensors` | `models/vae/` | required | `ltx2` | [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5/resolve/main/vae/ltx-2.5-video-vae-bf16.safetensors) |

**LTX 2.5 audio VAE** (`ltx2.5-audio-vae`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `ltx-2.5-audio-vae-bf16.safetensors` | `models/vae/` | required | `ltx2`, `ltx2_voice` | [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5/resolve/main/vae/ltx-2.5-audio-vae-bf16.safetensors) |

**LTX 2.5 latent upscaler** (`ltx2.5-latent-upscaler`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors` | `models/latent_upscale_models/` | required | `ltx2` | [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5/resolve/main/latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors) |

**LTX 2.5 duration head** (`ltx2.5-duration-head`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `ltx-2.5-duration-head-bf16.safetensors` | `models/model_patches/` | optional — dur: model (duration head) | `ltx2` | [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5/resolve/main/model_patches/ltx-2.5-duration-head-bf16.safetensors) |

**LTX 2.5 ingredients IC-LoRA** (`ltx2.5-ic-lora-ingredients`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `ltx-2.5-22b-ic-lora-ingredients-0.9.safetensors` | `models/loras/` | optional — reference sheets (LTX-2.5 ingredients IC-LoRA) | `ltx2` | [Lightricks/LTX-2.5-22b-IC-LoRA-Ingredients](https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-Ingredients/resolve/main/ltx-2.5-22b-ic-lora-ingredients-0.9.safetensors) |

**LTX 2.3** (`ltx2.3`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `ltx-2.3-22b-dev-fp8.safetensors` | `models/checkpoints/` | accelerator | `ltx2_ingredients` | [Lightricks/LTX-2.3-fp8](https://huggingface.co/Lightricks/LTX-2.3-fp8/resolve/main/ltx-2.3-22b-dev-fp8.safetensors) |
| `ltx-2.3-22b-distilled-fp8.safetensors` | `models/checkpoints/` | accelerator | `ltx2_ingredients` | [Lightricks/LTX-2.3-fp8](https://huggingface.co/Lightricks/LTX-2.3-fp8/resolve/main/ltx-2.3-22b-distilled-fp8.safetensors) |

**LTX 2.3 ingredients IC-LoRA** (`ltx2.3-ic-lora-ingredients`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors` | `models/loras/` | required | `ltx2_ingredients` | [Comfy-Org/ltx-2.3](https://huggingface.co/Comfy-Org/ltx-2.3/resolve/main/split_files/loras/ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors) |

**Gemma 3 12B (LTX 2.3's text encoder)** (`gemma3-12b`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `gemma_3_12B_it_fp4_mixed.safetensors` | `models/text_encoders/` | required | `ltx2_ingredients` | [Comfy-Org/ltx-2](https://huggingface.co/Comfy-Org/ltx-2/resolve/main/split_files/text_encoders/gemma_3_12B_it_fp4_mixed.safetensors) |

**MiniMax H3 FL2VA** (`minimax-h3-fl2va`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `models/diffusion_models/` | required | `minimax_h3_fl2va` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors) |

**MiniMax H3 FL2V turbo LoRA** (`minimax-h3-fl2v-turbo-lora`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors` | `models/loras/` | accelerator | `minimax_h3_fl2va` | **none recorded** |
| `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` | `models/loras/` | accelerator | `minimax_h3_fl2va` | [lightx2v/Minimax-h3-Turbo](https://huggingface.co/lightx2v/Minimax-h3-Turbo/resolve/main/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors) |

**Qwen3-VL 32B (MiniMax H3's text encoder)** (`qwen3vl-32b`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `models/text_encoders/` | required | `minimax_h3_fl2va`, `minimax_h3_ref2va` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) |

**MiniMax H3 video VAE** (`minimax-h3-video-vae`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `minimax_h3_video_vae_fp16.safetensors` | `models/vae/` | required | `minimax_h3_fl2va`, `minimax_h3_ref2va` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_fp16.safetensors) |

**MiniMax H3 audio VAE** (`minimax-h3-audio-vae`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `minimax_h3_audio_vae_fp32.safetensors` | `models/vae/` | required | `minimax_h3_fl2va`, `minimax_h3_ref2va` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) |

**MiniMax H3 Ref2VA** (`minimax-h3-ref2va`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `models/diffusion_models/` | required | `minimax_h3_ref2va` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors) |

**MiniMax H3 Ref2V turbo LoRA** (`minimax-h3-ref2v-turbo-lora`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors` | `models/loras/` | accelerator | `minimax_h3_ref2va` | [Kijai/MiniMax-H3_comfy](https://huggingface.co/Kijai/MiniMax-H3_comfy/resolve/main/loras/minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors) |
| `minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` | `models/loras/` | accelerator | `minimax_h3_ref2va` | **none recorded** |

**Wan 2.2 I2V 14B high-noise** (`wan2.2-i2v-14b-high`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors` | `models/diffusion_models/` | required | `wan22_i2v` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors) |

**Wan 2.2 I2V 14B low-noise** (`wan2.2-i2v-14b-low`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors` | `models/diffusion_models/` | required | `wan22_i2v` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors) |

**Wan 2.2 I2V lightx2v 4-step LoRA** (`wan2.2-i2v-lightx2v-lora`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors` | `models/loras/` | accelerator | `wan22_i2v` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors) |
| `wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors` | `models/loras/` | accelerator | `wan22_i2v` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors) |

**UMT5-XXL (Wan's text encoder)** (`umt5-xxl`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `models/text_encoders/` | required | `wan22_i2v`, `wan22_ti2v`, `wan22_vace` | [Comfy-Org/Wan_2.1_ComfyUI_repackaged](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors) |

**Wan 2.1 VAE** (`wan2.1-vae`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `wan_2.1_vae.safetensors` | `models/vae/` | required | `wan22_i2v`, `wan22_vace`, `krea2` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors) |

**Wan 2.2 TI2V 5B** (`wan2.2-ti2v-5b`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `wan2.2_ti2v_5B_fp16.safetensors` | `models/diffusion_models/` | required | `wan22_ti2v` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors) |

**Wan 2.2 VAE** (`wan2.2-vae`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `wan2.2_vae.safetensors` | `models/vae/` | required | `wan22_ti2v` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors) |

**Wan 2.2 Fun VACE 14B high-noise** (`wan2.2-vace-14b-high`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors` | `models/diffusion_models/` | required | `wan22_vace` | **none recorded** |

**Wan 2.2 Fun VACE 14B low-noise** (`wan2.2-vace-14b-low`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors` | `models/diffusion_models/` | required | `wan22_vace` | **none recorded** |

**FLUX.2 Klein 9B** (`flux2-klein-9b`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `flux-2-klein-9b-kv.safetensors` | `models/diffusion_models/` | accelerator | `flux2_klein`, `flux2_klein_edit` | **none recorded** |
| `flux-2-klein-base-9b-fp8.safetensors` | `models/diffusion_models/` | accelerator | `flux2_klein`, `flux2_klein_edit` | [black-forest-labs/FLUX.2-klein-base-9b-fp8](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-9b-fp8/resolve/main/flux-2-klein-base-9b-fp8.safetensors) |

**Qwen3 8B (FLUX.2 Klein 9B's text encoder)** (`qwen3-8b`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `qwen_3_8b_fp8mixed.safetensors` | `models/text_encoders/` | required | `flux2_klein`, `flux2_klein_edit` | [Comfy-Org/flux2-klein-9B](https://huggingface.co/Comfy-Org/flux2-klein-9B/resolve/main/split_files/text_encoders/qwen_3_8b_fp8mixed.safetensors) |

**FLUX.2 VAE** (`flux2-vae`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `flux2-vae.safetensors` | `models/vae/` | required | `flux2_klein_edit` | [Comfy-Org/flux2-dev](https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors) |
| `full_encoder_small_decoder.safetensors` | `models/vae/` | required | `flux2_klein` | [black-forest-labs/FLUX.2-small-decoder](https://huggingface.co/black-forest-labs/FLUX.2-small-decoder/resolve/main/full_encoder_small_decoder.safetensors) |

**FLUX.1 Kontext dev** (`flux1-kontext-dev`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `flux1-dev-kontext_fp8_scaled.safetensors` | `models/diffusion_models/` | required | `flux_kontext` | [Comfy-Org/flux1-kontext-dev_ComfyUI](https://huggingface.co/Comfy-Org/flux1-kontext-dev_ComfyUI/resolve/main/split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors) |

**CLIP-L** (`clip-l`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `clip_l.safetensors` | `models/text_encoders/` | required | `flux_kontext` | [comfyanonymous/flux_text_encoders](https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors) |

**T5-XXL (FLUX.1's text encoder)** (`t5-xxl`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `t5xxl_fp16.safetensors` | `models/text_encoders/` | required | `flux_kontext` | [comfyanonymous/flux_text_encoders](https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors) |

**FLUX.1 VAE (ae)** (`flux1-vae`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `ae.safetensors` | `models/vae/` | required | `flux_kontext`, `z_image_turbo` | [Comfy-Org/Lumina_Image_2.0_Repackaged](https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/resolve/main/split_files/vae/ae.safetensors) |

**Krea 2** (`krea2`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `krea2_turbo_fp8_scaled.safetensors` | `models/diffusion_models/` | required | `krea2` | [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2/resolve/main/diffusion_models/krea2_turbo_fp8_scaled.safetensors) |

**Qwen3-VL 4B (Krea 2's text encoder)** (`qwen3vl-4b`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `qwen3vl_4b_fp8_scaled.safetensors` | `models/text_encoders/` | required | `krea2` | [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2/resolve/main/text_encoders/qwen3vl_4b_fp8_scaled.safetensors) |

**Z-Image Turbo** (`z-image-turbo`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `z_image_turbo_bf16.safetensors` | `models/diffusion_models/` | required | `z_image_turbo` | [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors) |

**Qwen3 4B (Z-Image's text encoder)** (`qwen3-4b`)

| File | ComfyUI folder | Tier | Targets | Download |
|---|---|---|---|---|
| `qwen_3_4b.safetensors` | `models/text_encoders/` | required | `z_image_turbo` | [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors) |

#### Files with no recorded download URL

h3pipe only records a URL it can trace to a ComfyUI template, a saved workflow or ComfyUI-Manager's model list, so these are listed without one rather than with a guess:

- `flux-2-klein-9b-kv.safetensors` → `models/diffusion_models/` — no URL: no ComfyUI template, saved workflow or ComfyUI-Manager model list on this machine records this file (the templates name BFL's fp8 file, below, which stands in for it by family). Search Hugging Face for its exact name (black-forest-labs FLUX.2 Klein 9B KV).
- `minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors` → `models/loras/` — no URL: no ComfyUI template, saved workflow or ComfyUI-Manager model list on this machine records this file. Search Hugging Face for its exact name (lightx2v's MiniMax H3 FL2V 4-step turbo LoRA v0.1, ComfyUI conversion).
- `minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` → `models/loras/` — no URL: no ComfyUI template, saved workflow or ComfyUI-Manager model list on this machine records this file. Search Hugging Face for its exact name (lightx2v's MiniMax H3 Ref2V 8-step turbo LoRA v1.0, ComfyUI conversion).
- `wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors` → `models/diffusion_models/` — no URL: no ComfyUI template, saved workflow or ComfyUI-Manager model list on this machine records this file. Search Hugging Face for its exact name (Wan 2.2 Fun VACE A14B high-noise expert, fp8 scaled ComfyUI repack).
- `wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors` → `models/diffusion_models/` — no URL: no ComfyUI template, saved workflow or ComfyUI-Manager model list on this machine records this file. Search Hugging Face for its exact name (Wan 2.2 Fun VACE A14B low-noise expert, fp8 scaled ComfyUI repack).

<!-- END MODELS -->

---

## 5. First run

An **episode** is one folder holding a series config and a script. Everything else in it
is generated.

```
Shows\
  ep01\
    series.json     the series config: the look, the cast, the locations, the voices
    ep01.md         the script
```

Two rules that save an hour of confusion:

- The script should be named after its folder — `ep01\ep01.md`. Any other single `.md` in
  the folder is accepted, but two of them and nothing can tell which is the script.
- `series.json` goes **in the episode folder**. The command line will also read one from
  the parent folder, but the editor only lists folders that contain it directly.

### Something to copy from

- `examples/series_example.json` and `examples/script_example.md` — a full three-sequence
  episode with props, a voice-only character and `audio:` windows. Note that it is written
  for a **recorded** dialogue mix (`audio.mode: source_track`), so its dialogue shots stay
  blocked until you supply `audio/ep01_dialogue_mix.wav` or change the mode.
- `tests/fixtures/kitchen_sink/` and `tests/fixtures/mixed/` — small episodes that
  exercise most of the format, including shots retargeted onto other models.
- [docs/AUTHORING.md](docs/AUTHORING.md) is the format itself: every field, the frame
  grids, the syllable budget, how to break a scene into shots.
- To draft with an AI assistant, `python tools/make_prompts.py` packages that guide as a
  skill in `build/skill/h3pipe-episode-script/` — copy the folder into `.claude/skills/`
  for Claude Code, or upload the zip beside it on claude.ai.

### A first episode by hand

`Shows\ep01\series.json`. `audio.mode: generate` means the model invents the voices from
each character's `voice` line, so nothing has to be recorded first:

```json
{
  "series": {
    "id": "first_light",
    "title": "First Light",
    "fps": 24,
    "width": 1344,
    "height": 768
  },
  "proxy": { "width": 448, "height": 256 },
  "style": {
    "look": "a 2D hand-drawn cartoon animation with flat 2D illustration, clean black line art, flat solid colors, and cel-shaded artwork"
  },
  "subjects": {
    "ada": {
      "kind": "character",
      "name": "Ada",
      "pronoun": "her",
      "design": "a nine-year-old girl with short curly black hair, a green raincoat, and yellow rain boots, drawn with thick confident outlines",
      "sheet": "refs/ada/ada_sheet_4panel.png",
      "voice": "bright, quick, a little breathless"
    }
  },
  "locations": {
    "porch": {
      "description": "a wooden front porch at dawn, wet boards, a hanging lamp still lit, mist over the lawn beyond",
      "plate": "refs/_bg/porch.png"
    }
  },
  "audio": { "mode": "generate" }
}
```

`Shows\ep01\ep01.md`:

```
= ep01  First Light

# sq01  porch

## sh010
who: ada
size: wide
dur: 3
Ada steps out onto the wet porch and stops, looking at the mist on the lawn.
camera: holds a static wide shot
sound: dawn birdsong, dripping water, a faint breeze

## sh020
who: ada
size: close
dur: auto
Ada grins and pulls her hood up.
camera: pushes in with small amplitude at slow speed
ADA: It rained all night and nobody saw it but me.
sound: rustling raincoat, dripping water, distant birds
```

### The command line

Run these from the repo, or by `h3.py`'s full path from anywhere.

```
python h3.py targets                      # what this ComfyUI can render (ComfyUI must be up)
python h3.py check    Shows\ep01          # validate + dialogue pacing; writes nothing
python h3.py build    Shows\ep01          # story IR, shotlists (final AND proxy), refs_todo
python h3.py refs     Shows\ep01 --list   # the reference images it would draw
python h3.py refs     Shows\ep01          # draw them (ComfyUI)
python h3.py render   Shows\ep01 --proxy  # queue the shots, one take each -> renders_proxy\
python h3.py assemble Shows\ep01 --proxy  # join them -> renders_proxy\ep01_proxy.mp4
```

Then the full-size pass, once the animatic reads right:

```
python h3.py render   Shows\ep01
python h3.py assemble Shows\ep01          # -> renders\ep01.mp4
```

`python h3.py all Shows\ep01 --proxy` runs build → refs → render → assemble in one go.

What to expect along the way:

- `check` prints the shot count, the runtime, and a pacing table — every dialogue shot
  with the syllables-per-second it forces on the performance. `0 crammed` is what you want.
- `build` writes `shotlist\`, `refs_todo.md` and `refs_todo.json` and prints the model,
  LoRA and step count each pass will use.
- `refs --list` shows what is missing before it spends any GPU time; `refs` then draws
  each one, keeping every attempt as a take under `refs\_takes\`.
- `render --list` is the dry run. Shots whose reference images are still missing show as
  `blocked`, and the report names the exact file each one wants —
  `--allow-missing-refs` renders them against flat grey stand-ins if you want to see the
  motion first.
- `assemble --proxy --check` reports the cut (order, take, trims) and writes nothing.

`--only sh020,sh030` narrows any of them to a few shots; `--redo` makes a new take instead
of reusing the one on disk. Every flag after the episode goes straight through to the
underlying script, and each one has `--help`.

### The same thing in the editor

Reload ComfyUI, open the **h3 Shots** sidebar tab, and:

1. Set the **project roots** to the folder holding `Shows\` (or `Shows\` itself). The
   episode list fills in.
2. Pick **ep01** and the **proxy** pass.
3. **Build** — the same `h3build` the CLI runs, with its report in the panel.
4. Open **h3 Refs** and generate the character sheet and the plate. Every candidate is a
   take; pick the one you want.
5. Back in **h3 Shots**, render — a shot, a selection or the episode. Takes appear as they
   finish; the **Viewer** window plays them and compares two side by side or under a wipe.
6. **h3 Timeline** (bottom panel) is the cut: reorder, trim, pick a take per shot, **Play
   all** to watch it without rendering anything, then **Export** to assemble the one mp4.

Everything the editor does is a plain file in the episode folder — `overrides.json`,
`cut.json`, the per-take sidecars — the same files `h3.py override`, `h3.py pick` and
`h3.py cut` write. You can move between the two freely.

---

## 6. Troubleshooting

**`!! can't tell which script to use in <folder>: ['draft.md', 'notes2.md']`**
The episode folder has more than one candidate `.md`. Name the script after the folder
(`ep01\ep01.md`) or delete the spare. The editor's failure mode is quieter: a folder it
cannot resolve a script for simply never appears in the episode list.

**`!! <folder>\series.json not found`**
The series config has to be in the episode folder (the CLI also accepts the parent
folder; the editor's episode list does not).

**The animatic renders at a size you did not ask for.**
With no `proxy` block in the series config, the proxy pass falls back to the *target's*
own proxy preset — 512×288 for MiniMax H3 — rather than a scaled-down copy of your final
size. That is fine until your final size is not 16:9, at which point the proxy and the
final have different aspect ratios and proxy placeholders in a final cut get scaled
oddly. Add a `proxy` block with the same aspect ratio as `series` (the examples use
448×256 against 1344×768 — exactly one third, and both axes still on H3's /32 grid).

**Shots skipped with a missing-model message.**
Run `python h3.py targets` (add the episode folder to include its series config's model
choices). It names every missing file, the models folder it belongs in, and its download
link. A missing **required** file skips the shot; a missing **accelerator** (a turbo
LoRA) is not an error — the pass renders on its slower `base` preset and the take's
sidecar records that.

**`ComfyUI at http://127.0.0.1:8188 didn't answer`, or every target is `unknown`.**
ComfyUI is not running, or not on that port. `targets`, `refs`, `keyframe --generate` /
`--missing` and `render` all need it up. `--comfy http://host:port` points any of them
elsewhere; `h3render` must still run on the machine that holds the episode folder, since
ComfyUI reads and writes those files by path.

**`/h3pipe/config` 404s, or the sidebar tabs never appear.**
In order: is the pack in `custom_nodes` (search the canvas for `H3 Shot List Loader`)? Did
ComfyUI log `h3pipe: editor routes not registered` — if so, you copied `comfy_nodes\`
instead of linking it and need `H3PIPE_HOME`. If the routes answer but the tabs do not
show, your ComfyUI frontend is older than 1.3; the browser console says so.

**You changed `web/` and nothing changed in the browser.**
The editor is served from the committed bundle `comfy_nodes/web/h3pipe-editor.js`, not
from `web/`. Rebuild it:

```
cd web
npm install        # once
npm run build      # -> ../comfy_nodes/web/h3pipe-editor.js
```

then hard-reload the tab (Ctrl-Shift-R). `npm run dev` serves `dev.html` against a mock
API for UI work without ComfyUI; it never opens a browser by itself.

**`h3align` says faster-whisper is missing, but you installed it.**
You installed it into a different interpreter from the one h3pipe picked — most often you
installed into your system Python while the editor probed ComfyUI's embedded Python
first, or the other way round. The Recording window names the interpreter it chose and
prints the exact `pip install` line for it. To force one, set `H3PIPE_ALIGN_PYTHON` to
that interpreter's full path in the environment ComfyUI (or your shell) starts in, and
restart.

**No foley bed on `dub_keep_foley` shots.**
The Save Shot node shells out to `python -m demucs`, so `demucs` has to be importable by
the `python` first on ComfyUI's PATH — not necessarily ComfyUI's own interpreter. The
take's status line says `demucs not installed - foley bed skipped`.

**A workflow ComfyUI refuses.**
h3pipe converts a canvas workflow to API format itself. If your graph uses something the
converter cannot express faithfully, save it with **Workflow → Export (API)** and pass
that file with `--workflow`.

**Never hand-edit generated files.** `shotlist\`, `refs_todo.*` and the per-take
`*.shotlist.json` are outputs. Change the script, the series config or `overrides.json`
and rebuild — seeds are derived from the episode, sequence and shot ids, so untouched
shots re-render identically.
