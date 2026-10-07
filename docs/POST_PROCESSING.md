# Post-processing: faces, enhancement, motion blur

Status (2026-10-06): **enhance and motion blur are built** (`h3post.py`, `h3.py post`;
not yet in master or the editor) and are being compared on ep01. **Faces are dropped**:
see **Bake-off, round 1**, below. This file is the design
record; once a phase lands, its contracts move to the docstrings, `docs/API.md` and
`docs/AUTHORING.md`, and this file shrinks to what's still open.

## Why

Side by side with Seedance 2.5, an H3 master falls short in three places:

1. **Faces in wider shots.** At 960×544 a face in a wide shot is 20–40 px tall. The 2x
   re-sample redraws it, but from very little, so it comes out soft, mushy or slightly
   off-model. Close-ups are fine.
2. **Overall texture.** Latent noise and "mush" in flat areas that the re-sample keeps.
3. **Motion.** Fast action can look stuttery next to footage with real shutter blur.

Each of these is a finishing pass on the upscaled picture. None of them changes the cut,
the timing or the sound.

## Where it sits

```
take  <stem>.mp4                   rendered (960×544 / 1344×768)
  │  h3upscale (exists)            re-sample 2x / pixel / SeedVR2
  ▼
upscale  <stem>.up.mp4             1080p / 1440p / 4K
  │  h3post (new)                  enhance → faces → motion blur, each optional
  ▼
post  <stem>.post.mp4              same size, same frames, same audio
  │  h3master (exists)             uses .post.mp4 when the recipe has a post block
  ▼
<episode>/master/
```

Post is **a version of the upscale**, the same way an upscale is a version of its take:
`<stem>.post.mp4` with `<stem>.post.json` beside it, recording the settings, the
upscale it was made from (its settings hash) and what it found. It is a separate stage,
not more nodes inside the upscale graph, because:

- the upscale is the expensive, settled step. Post is the experimental one: a different
  detailer or a different blur amount shouldn't mean re-sampling the shot again;
- one ComfyUI job holding H3 and a detailer model and VACE won't fit in VRAM;
- comparing variants means running post several times on one upscale.

A post is stale when its upscale changes, just as an upscale is stale when its take
changes. When master is asked to use posts (`--post`, or the dialog's Post-process
checkbox; see **Decided**), it treats them with the same rules it uses for upscales:
`ok`, `kept`, `--conform`, Keep, gap.

## The three steps, in order

**Enhance, then faces, then motion blur.** The pasted pipeline put faces first. That's
the wrong way round: a whole-frame restoration pass after the face detailer would redraw
the faces it just fixed. Faces go after anything that touches the whole frame, and blur
goes last because it imitates the camera.

### 1. Enhance (whole frame)

**Built** as h3post's first step: it runs on the upscale, at the upscale's size (a
restoration, not another upscale). The tiers name the methods:

| Tier | `enhance` | What it runs | Temporal? | Cost (5 s shot, est.) |
|---|---|---|---|---|
| Draft | `"draft"` / `{"method": "pixel"}` | an upscale model (RealESRGAN_x2) at 1x, colour and tone the source's: a sharpen | per frame, but it invents little, so little flickers | seconds |
| Production | `"production"` / `{"method": "seedvr2", "seedvr2_model": "7b", "chunk": "fit", "overlap": 6}` | SeedVR2 7B at 1x in fixed chunks (`"fit"`: 61 frames at 1080p, 33 at 1440p, 13 at 4K, by pixel count) crossfaded over 6 latent frames, then the frequency-split finish | yes, a video model | 4.3 min for a 5 s shot at 1080p (sh2100); 11.2 min for a 6.6 s shot at 1440p, with blur 0.3 (measured: about 100 s per second of footage) |
| | `{"method": "seedvr2", "seedvr2_model": "3b"}` | SeedVR2 3B | yes | 4.2–4.4 min at 1080p (measured) |
| Cinematic | `"cinematic"` / `{"method": "supir", "strength": 0.2}` | SUPIR (ComfyUI core's `SUPIRApply`) on SDXL at denoise 0.05–0.5, two frames at a time, the same seed on every batch, then the frequency split (only its detail is kept) | **no** | to measure: about 10 SDXL steps a frame |

**Production was chosen on ep01 (2026-10-06).** SeedVR2 cleaned the blocks off edges,
lettering, hoods and hairnets far better than the upscale, draft or an earlier re-sample
start (steps 6, 5, 4). 7B looked cleaner than 3B and was no slower. Faces drifted where
SeedVR2's chunks met. `"auto"` chunking sizes chunks to the VRAM free at that moment (33
to 61 frames on the same machine in one evening), so a redo split the shot at different
frames. Fixed 61-frame chunks with a 6-frame crossfade drifted least. `chunk` (a 4n+1
frame count, 0 for auto) and `overlap` (latent frames, default 2) are enhance fields. A
fixed 61 needs about 29 GB of free VRAM at 1080p, so `"fit"` scales it down by the
frame's pixel count (still fixed for a size). Give a number if a shot still runs out of
memory.

**CCSR is not offered.** It isn't installed, has no node in ComfyUI core, and the only
pack (kijai's ComfyUI-CCSR) is from 2024. If you install it, its `object_info` is enough
to wire it in as a fourth method. Until then the Production slot is SeedVR2 3B, which is
also video-native, which CCSR isn't.

SUPIR's defaults: `SUPIR-v0F_fp16.safetensors` (Kijai/SUPIR_pruned; v0F is trained for
light degradation, which is our case: v0Q is for heavy), on
`sd_xl_base_1.0_0.9vae.safetensors`, 10 steps, cfg 4, dpmpp_2m/karras, restore_cfg 4,
SUPIR's own prompts shortened. All of it is a field of the enhance object (`supir_model`,
`checkpoint`, `steps`, `cfg`, `prompt`, `batch`), as are the finish's `frequency_split`,
`keep_soft` and `grain`.

Doubts about SUPIR/CCSR on video (why Cinematic needs the comparison before it's a
default):

- Both are **image** models. They run per frame, each frame restored independently, so
  invented texture shimmers from frame to frame. That's exactly the defect our
  frequency-split finish was built to halve on SeedVR2, and SeedVR2 is at least temporal.
- SUPIR is SDXL-sized. At 1440p it is tens of seconds a frame, so a 121-frame shot takes
  an hour or more. That doesn't fit a 100-shot episode.
- CCSR isn't installed and has no video mode.

### 2. Faces (the main work)

#### Which shots

The script already says how wide a shot is, and the series config says who's in it.

- **Gate by framing.** The step runs on `size:` `ms`, `mws`, `fs`, `ws` and `ews`, and
  skips `ecu`, `cu` and `mcu` (`h3core/framing.py`). The list is configurable.
- **Gate by measured face size too.** The script's `size:` can be wrong, and a "wide" can
  still hold a close face. After detection, faces taller than `max_face` px (default
  ~220 at 1080p) are already resolved and are left alone. Faces under `min_face` (~20 px)
  are background people too small to help. This is the "leave it on and let it
  auto-skip close-ups" switch, done with numbers. Both limits are fractions of frame
  height, so they mean the same at 1080p, 1440p or 4K.

#### How: detect, then decide, then detail

Two ComfyUI jobs with a stdlib step between them. The decision in the middle is
plain Python, so it's testable without torch, and it gives you a natural place to look
before anything gets redrawn.

1. **Detect** (job 1, seconds). Find faces on every frame of the `.up.mp4` (or every 2nd
   frame, then interpolate) and return boxes, scores and a face embedding per
   detection. New node `H3DetectFaces`.
   - Detector: YOLO `face_yolov8m.pt` through Impact's `UltralyticsDetectorProvider`.
     Alternatively, the temporal detailer's `FaceDetectTrack` with insightface, which
     also gives landmarks.
   - Embedding: InsightFace ArcFace (`buffalo_l`'s recognition model). It's what tells
     Walker from Kell.
2. **Track and identify** (`h3post.py`, stdlib).
   - **Link** detections into tracks: IoU between frames, greedy matching, fill short
     gaps, smooth the box with a moving average so the crop window doesn't jitter.
   - **Assign** each track to a cast member by comparing its mean embedding with each
     cast member's face reference (below). One cast member: the most persistent track.
     Two or more: best one-to-one match. Below a confidence threshold, a track is
     `unknown`: either skipped, or detailed with no identity and the prompt only, as
     set in the config. The report flags it either way.
   - This is the "2-pass loop for two characters". Every track gets its own pass with its
     own reference and its own prompt, whether there are one, two or five.
3. **Detail** (job 2). For each track, cut a **stabilised crop video** around the face
   (fixed size, e.g. 512², following the smoothed box), redraw it with the chosen
   detailer, and paste it back with a feathered mask. Colour and tone come from the
   original through the frequency split, as `H3FinishUpscale` already does, so a
   detailed face can't drift in colour from the frame around it.

#### Face references

The detailer needs a clean face per character. In order of preference:

1. `subjects.<id>.face` in the series config: a face picture you supply. This fits
   "refs are supplied, not generated".
2. The face found in the subject's `sheet`. Run the same detector on the sheet and keep
   the largest, most frontal face. That's automatic, so no new authoring is required.
3. None: the detailer runs from the prompt alone, and the report says so.

The prompt for each track comes from the subject's `design` and the shot's compiled
prompt (lighting, mood). The style comes from `style`.

#### Detailer backends (pluggable, compared in the bake-off)

| Backend | Nodes | Installed? | Identity | Temporal | Notes |
|---|---|---|---|---|---|
| `image` | Impact `DetailerForEach` / FaceDetailer, any checkpoint | yes | IPAdapter FaceID (SD1.5/SDXL) | **no** | Cheapest to build. Fixed seed per track and denoise 0.25–0.40 hold flicker down but don't remove it. |
| `image_ad` | Impact `SEGSDetailerForAnimateDiff` + an AnimateDiff motion module | nodes yes; motion module to check | FaceID | yes (SD1.5 only) | Temporal, but limited to SD1.5 quality. |
| `vace` | Wan 2.2 VACE 14B on the crop video, face masked, face ref as VACE's reference image | yes (`WanVaceToVideo`, both 14B halves) | VACE reference | **yes** | The one most likely to hold a face steady through a shot. Wan's length limit means long shots go in overlapping chunks. Slowest. |
| `temporal_fd` | ComfyUI-Temporal-Face-Detailer (nikythebikky): `FaceDetectTrack` → `TrackedFaceDetail`, `FaceTrackPreview` | yes | weak on its own (see below); FaceID patched onto its MODEL | yes: fixed noise per track, flow blend, colour match | Any SD-style MODEL/CLIP/VAE (SDXL + FaceID is the obvious pairing). Its tracking already does the detect/link/stabilised-crop/paste work planned for P1–P2. |

**What the temporal face detailer does (read from its source, commit 6b9ef45).**
It covers most of the planned crop/detail/paste machinery, so h3post should drive it
rather than rebuild it:

- **Split nodes.** `FaceDetectTrack` returns `FACE_TRACKS`, a plain dict of tracks
  (`track_id`, and per frame the box, five landmarks, score and stabilised square crop).
  `TrackedFaceDetail` takes that dict back. That's exactly the gap between job 1 and job 2
  above. A small h3 node saves `FACE_TRACKS` to JSON and loads it back filtered to chosen
  track ids (JSON makes the frame keys strings, so loading converts them back to ints).
  The stdlib step decides which tracks belong to whom.
- **Detectors.** `insightface` (RetinaFace with landmarks), `yunet`, `haar`, or the YOLO
  files in `models/ultralytics/bbox`. YOLO runs on the GPU through torch but gives no
  landmarks, so crops anchor on the box, which jitters more.
- **`reference_image` is one image for every track, and it's weak.** It blends the
  reference face's latent into each crop's starting latent at
  `min(0.5, reference_strength × denoise)` (0.35 × 0.35 ≈ 0.12 at the defaults), then
  colour-matches the result towards the reference at `0.5 × reference_strength`. That
  colour pull is a risk: a sheet lit differently from the shot drags skin tone towards
  the sheet. Keep `reference_strength` low or at 0, and get identity from IPAdapter
  FaceID patched onto the MODEL (its tooltip suggests the same).
- **Two characters means two calls.** Since the reference and FaceID are per MODEL, each
  character is its own `TrackedFaceDetail` call: their FaceID-patched model, their tracks
  only, their prompt. The output of one call feeds the next. `track_prompts`
  (`id: prompt` lines) sets per-track prompts within a call.
- **It doesn't identify anyone.** Detection runs insightface with
  `allowed_modules=["detection"]`, so there are no embeddings. Matching tracks to
  characters stays h3post's job: ArcFace on a few crops per track, against each
  character's face reference.
- **Flicker controls:** `noise_mode: fixed_per_track` (the default), flow blend
  (`farneback` / `raft_small` / `raft_large`), colour match to the source, and
  `detail_every` (detail every Nth frame, flow-warp the rest).

The image model is a setting, not hard-coded (`checkpoint` / `unet`). Installed now: SD1.5,
SDXL base, Flux dev fp8 and schnell, Qwen-Image 2.1, Z-Image Turbo, Krea 2 turbo,
Flux 2 Klein. No SDXL Lightning. Expected trade-off, to be measured rather than assumed:

- SD1.5 + FaceID: fast, with the strongest identity tools, but the softest result.
- SDXL + FaceID: probably the default for `image`.
- Flux / Qwen: best looking, ~1.5–2 min more a shot (the notes' figure, unmeasured).
  FaceID doesn't work with them, and PuLID isn't installed.
- Z-Image Turbo: worth a try. It's few-step and already installed.

#### Lip sync (risk)

A speaking face redrawn at denoise > ~0.35 can lose the mouth shapes the upscale's held
audio drew. Planned mitigations:

- lower denoise on a track whose character has a line in the shot (the IR knows);
- or keep the mouth out of the mask: MediaPipe face mesh, which is installed, gives the
  lip landmarks.

The bake-off includes a dialogue wide to check this.

#### Checking the boxes (optional)

You shouldn't have to watch every clip's boxes:

- `h3.py post <ep> --only sh040 --boxes` runs **only job 1 and the decision**. It writes
  `<stem>.post.boxes.mp4`: a small preview with each track's box, its character name
  and match confidence, and skipped faces greyed out with the reason. It takes seconds
  and redraws nothing.
- The report (`.post.json`, and the master's `.md`) lists every shot whose assignment
  was low-confidence, had an `unknown` track, or found a different number of faces than
  the shot's cast. **Those are the ones to look at.** Everything else goes through.
- If a track gets the wrong character, a per-shot override fixes it:
  `"post": {"sh040": {"faces": {"swap": true}}}` or an explicit
  `{"tracks": {"1": "kell"}}` keyed by track number from the preview.
- Later, in the editor: a "Show face boxes" item on the take menu that plays the
  preview.

**Built**: `H3MotionBlur` (`comfy_nodes/h3_post.py`), h3post's last step.

- Optical flow from RAFT-large, through ComfyUI core's `OpticalFlowLoader`
  (`models/optical_flow/raft_large_C_T_SKHT_V2-ff5fadd5.pth`, torchvision's weights;
  ComfyUI never downloads them), measured at 960 wide and scaled up.
- A pixel's velocity is the mean of its flow to the next frame and from the previous
  one. Where the forward flow and the next frame's flow back disagree (occlusion, flow
  errors) it fades out, and it is capped at 8% of the frame width per frame.
- Each pixel is averaged over 9 samples along its velocity, centred on the frame, across
  `amount` of the frame interval. 0.5 is a 180° shutter.

`motion_blur` is 0–1 (`--blur`), **default 0**, and meant per shot (overrides.json's
`post`), not across an episode. Doubts to check:

- **Generated video already has motion blur.** H3 is trained on real footage, so most
  takes already show shutter blur. Adding more on top of that smears. The bake-off
  measures whether action shots really lack it.
- Where blur is missing, the stutter is often the 24 fps motion itself, which blur hides
  but doesn't fix.
- Flow is unreliable at occlusion edges (an arm crossing a face), which produces ghosting
  halos. Mitigations are a flow-confidence mask and a cap on vector length.

Audio is untouched. The step only reads and writes frames.

## Configuration

This mirrors `upscale.master`: one recipe in the series config, per-shot overrides in
`overrides.json`.

Built so far: `enhance` and `motion_blur`.

```json
"post": { "master": { "enhance": "production", "motion_blur": 0 } }
"post": { "master": { "enhance": { "method": "supir", "strength": 0.15 } } }
```

and in overrides.json, `"post": { "sh040": { "motion_blur": 0.3 }, "sh050": { "enhance": "none" } }`.
`h3.py post <ep>` uses the recipe when neither `--enhance` nor `--blur` is given.

The faces block, as planned (not built):

```json
"post": {
  "master": {
    "faces": {
      "sizes": ["ms", "mws", "fs", "ws", "ews"],
      "min_face": 0.02, "max_face": 0.15,
      "extras": "skip",
      "detector": "face_yolov8m.pt",
      "detailer": "vace",
      "checkpoint": "sd_xl_base_1.0.safetensors",
      "identity": { "method": "faceid", "strength": 0.45 },
      "denoise": 0.35, "speaking_denoise": 0.25,
      "unknown": "skip"
    },
    "motion_blur": 0
  }
}
```

Each step is skipped when its block is missing. `overrides.json` gets a top-level
`"post": {"sh040": {...}}` merged over the recipe, in the same way as `"upscale"`.

Each subject gets an optional `"face": "refs/walker/walker_face.png"`. Supplying faces for
the cast is the one new authoring step, and it's optional.

## Files and commands

| | |
|---|---|
| `h3post.py` | plan, track, identify, queue, report (stdlib); `h3.py post` |
| `comfy_nodes/h3_post.py` | `H3DetectFaces`, `H3FaceCrop` / `H3FacePaste` (stabilised crop out, feathered paste back), `H3MotionBlur` |
| `h3takes.py` | `post_mp4`, `post_sidecar`, `post_boxes` paths; staleness against the upscale |
| `h3master.py` | `--post` (the dialog's Post-process checkbox): a row uses `.post.mp4`, queueing upscale → post as needed; post status counts towards `ok` / `kept` / `gap`. Without it, unchanged |
| `h3upscale.py` | none, except the enhancer-tier shorthand resolving to `then` |
| editor | Master dialog: post on/off and tier. Take menu: Show face boxes, Redo post. Upscale dialog: tier picker |

```
python h3.py post <episode> [--only sh040,sh050] [--check] [--boxes] [--redo]
                  [--variant NAME --set faces.detailer=image ...] [--compare A,B]
```

`--variant` writes `<stem>.post.<NAME>.mp4` and doesn't touch the real post.
`--compare A,B` writes `<episode>/_compare/<shot>_A_vs_B.mp4`: the two side by side, and
under them a 2x crop of each tracked face. This is the bake-off tool, and it stays
useful afterwards.

## Phases

**P0: bake-off (no pipeline code beyond a throwaway harness).** Pick ~8 Porchlights shots
that have been upscaled already:

- two single-character wides;
- two two-character mediums;
- an extreme wide with small figures;
- a dialogue medium-wide (lip sync);
- an action shot (motion blur);
- a close-up as the control.

Before running anything, install Impact Subpack and `face_yolov8m.pt`, check for an
AnimateDiff motion module, and install the temporal face detailer pack if it's real. Then
hand-build one ComfyUI workflow per backend and run it on each shot. Record per shot:

- seconds and peak VRAM;
- flicker (watch the 2x face crops, and measure frame-to-frame difference inside the face
  box);
- identity (ArcFace similarity to the face reference, before and after);
- lip sync on the dialogue shot.

What P0 needs installed, checked against this machine's ComfyUI
(`C:\AI\ComfyUI`, Python 3.13, 2026-10-06):

| Need | For | Where | Status |
|---|---|---|---|
| ComfyUI-Impact-Subpack (custom node) | `UltralyticsDetectorProvider`, so YOLO works in Impact | `custom_nodes/` | installed |
| `face_yolov8m.pt` (Bingsu/adetailer) | face detection | `models/ultralytics/bbox/` | installed (plus `hand_yolov8s`, `person_yolov8m-seg`) |
| `insightface` (pip, into `python_embeded`) | FaceID, the temporal detailer's detector, and the ArcFace matching | python | installed (2.1) |
| `buffalo_l` (InsightFace model pack) | face analysis for FaceID and the detailer | `models/insightface/models/buffalo_l/` | installed (fetched on first use) |
| `ip-adapter-faceid-plusv2_sdxl.bin` + `_lora.safetensors` (h94/IP-Adapter-FaceID) | identity on SDXL | `models/ipadapter/`, `models/loras/` | installed |
| `ip-adapter-faceid-plusv2_sd15.bin` + `_lora.safetensors` | `image_ad` (SD1.5 AnimateDiff) identity | same | installed |
| `sdxl_lightning_4step_lora.safetensors` (ByteDance/SDXL-Lightning) | faster SDXL detailing | `models/loras/` | installed |
| ComfyUI-Temporal-Face-Detailer (nikythebikky, 6b9ef45) | `temporal_fd` | `custom_nodes/` | installed |
| `SUPIR-v0F_fp16.safetensors` (Kijai/SUPIR_pruned, 2.5 GB; v0Q optional) | the Cinematic tier | `models/model_patches/` | **to download**: https://huggingface.co/Kijai/SUPIR_pruned/resolve/main/SUPIR-v0F_fp16.safetensors |
| `raft_large_C_T_SKHT_V2-ff5fadd5.pth` (torchvision's RAFT-large) | motion blur's optical flow | `models/optical_flow/` | **to download**: https://download.pytorch.org/models/raft_large_C_T_SKHT_V2-ff5fadd5.pth |

Already present: Wan 2.2 VACE 14B (both halves), `umt5_xxl_fp8`, `wan_2.1_vae`, CLIP ViT-H,
`v3_sd15_mm.ckpt`, `sam_vit_b`, SAM2, SD1.5/SDXL/Flux/Qwen/Z-Image, `mediapipe`,
torchvision 0.29. Both `onnxruntime` (CPU) and `onnxruntime-gpu` are
installed, and the CPU one wins: only the CPU provider loads. So insightface detection
(the temporal detailer's default detector) and ArcFace run on the CPU. Before
reinstalling, measure: a 121-frame shot at 640² detection is probably 10–20 s on CPU. If
that's too slow, use the `yolo:` detector, which runs on the GPU through torch.

Output: a short results table appended here, the default detailer and model, a yes or no
on SUPIR, and a yes or no on motion blur.
*Exit:* one backend clearly better than the upscale alone on the wides, at a cost that
fits an episode. If none is, stop here. That's a valid outcome.

### Bake-off, round 1 (2026-10-06): image detailers on faces

Eight ep01 shots (1080p upscales): sh1350, sh2325, sh490, sh1090, sh510, sh1620, sh2100,
sh2260. Detection and matching ran through the temporal detailer's own code, outside
ComfyUI, with ArcFace against each sheet's face. The detail was `TrackedFaceDetail`, one
pass per character. Harness and per-shot outputs are in `ep01/_bakeoff/`:
`<shot>.<variant>.mp4`, `<shot>.boxes.mp4` and `<shot>.faces_compare.mp4`.

Means over the 13 cast faces. Identity is ArcFace similarity to the sheet. Flicker and
sharpness are ratios to the upscale, measured inside the face box.

| Variant | Identity (upscale 0.250) | Flicker | Sharpness | s / shot | Peak VRAM |
|---|---|---|---|---|---|
| sdxl (Lightning, prompt only) | 0.126 | 1.05 | 0.74 | 127 | 17 GB |
| sdxl_ref (+ the pack's reference 0.35) | 0.121 | 1.10 | 0.73 | 122 | 13 GB |
| sdxl_faceid (+ FaceID Plus V2) | 0.219 | 1.00 | 0.70 | 154 | 21 GB |
| sdxl_naive (per-frame noise, no smoothing) | 0.125 | **2.42** | 0.93 | 120 | 17 GB |
| zimage (Z-Image Turbo) | 0.258 | 0.86 | **0.65** | 516 | 30 GB |
| extras (sdxl + background faces) | 0.127 | 1.05 | 0.74 | 296 | 20 GB |

- **Nothing beat the upscale.** Every variant left faces softer (0.65–0.74).
- **SDXL halves the likeness.** It draws someone else. FaceID brings most of it back
  (−0.03), but only back to where the upscale already was.
- **Z-Image keeps the person** (+0.01) because it barely changes them. It's the softest,
  and the slowest by far.
- **The temporal machinery works:** without it (naive), flicker is 2.4x.
- **Extras on the crowd (sh2260):** softer too (0.65–0.77), with no visible gain.
- Detection and matching worked (all cast found on 7 of 8 shots). That part stands
  regardless (P1).

**Verdict (2026-10-06): faces are dropped.** Watching the outputs, every variant made
faces worse. Z-Image looked like an improvement on a still frame, but in motion the face
reads as an overlay the head moves through, like a watermark. The face step (P1–P2) is
not being built. The detection and matching code stays in the bake-off harness, and the
`H3SaveFaceTracks` / `H3LoadFaceTracks` nodes stay in case a body or face pass ever comes
back with a video model on the crop.

**P1: detect, track, identify, preview.** `H3DetectFaces`, the stdlib tracker and
assignment, `--boxes`, the report. Useful on its own: it tells you which shots have
off-model faces before anything is redrawn. Unit tests for linking and assignment on
recorded detections (no torch).

**P2: face detail.** Crop, detail and paste with the P0 winner (and `image` as the cheap
fallback), `.post.mp4` / `.post.json`, staleness, `--variant` / `--compare`.

**P3: master.** The `post.master` recipe, per-shot overrides, h3master's rows, the editor's
Master dialog, AUTHORING's section (then regenerate `prompts/`).

**P4: motion blur.** Built (2026-10-06): `H3MotionBlur`, `--blur`, `motion_blur`.
Whether to use it is the comparison's question.

**P5: enhancer tiers.** Built (2026-10-06): h3post's enhance step, the three tiers,
SeedVR2 3B/7B and SUPIR at the upscale's size. Not yet in master (`--post`) or the
editor (P3).

**Bodies.** The same detect → track → crop → redraw → paste, with a person detector
(`person_yolov8m-seg.pt` is installed) in place of the face one. Not planned until a
face redraw works: a body is a larger area that moves more, so a per-frame image
redraw flickers and drifts more than a face (hands and clothing especially), and the
face round shows the image detailers already change who the person is. If a video
model on the crop (VACE, or H3's own re-sample) wins for faces, the crop can be a
person's track as easily as a face's.

Golden outputs are unaffected throughout: nothing here touches the build, the compile or
the seeds. Post seeds come from the take's seed plus the track number, so a redo repeats.

## Decided (2026-10-06)

- **Delivery size.** Finals render at 1344×768, proxies at 544 high, and masters are
  1440p (a 2x re-sample of the 768). Post runs on the 1440p upscale. `min_face` and
  `max_face` are **fractions of frame height** (the defaults ≈ 0.02 and 0.15), so a 1080p
  or 4K recipe keeps the same meaning.
- **Extras.** A face matched to no cast member is left alone by default. A generic
  detail pass (prompt only, no identity, low denoise) can be switched on with
  `faces.extras: "generic"`, since it might clear background faces of noise and
  blotches. The bake-off tries it on the extreme wide.
- **Scheduling.** Post is **its own run** (`h3.py post`, and the editor's equivalent),
  meant for after the upscales are reviewed. The Master dialog gets a **Post-process**
  checkbox, like the upscale:
  - **unchecked (default):** master works as it does today. It takes each shot's
    upscale, upscales where needed, and ignores any post.
  - **checked:** master takes each shot's post. Where a post is missing or stale, it
    runs post first, and where the upscale is missing or stale, it runs the upscale
    before that (upscale → post → assemble, per shot, in cut order). Rows report post
    status the same way they report upscale status.

  On the command line that's `h3.py master <ep> --post`. The recipe's `post.master` says
  *how* to post-process. The checkbox and `--post` say *whether* this master uses it.
  **Revised (2026-10-07):** the checkbox is a three-way choice, Off / Where present / All by
  recipe (`--post present`). Where present uses each shot's fresh post where it has one and
  its upscale otherwise, queues nothing and needs no recipe: post is ~4 s a frame at 1440p,
  so posting only the shots that need it (from the take menu) is the usual way.
- **Timing.** The feature park is lifted (ep01 is out). The aim is to build this during
  ep02/ep03 so it's ready by the time their cuts lock: P0 now, with P1–P3 straight after.
