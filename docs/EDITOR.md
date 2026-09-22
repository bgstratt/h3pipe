# The editor

The h3pipe editor runs inside ComfyUI. This is the guide to driving it: the order things
happen in, what every button does, and what each click, double-click and right-click means
in each panel.

[INSTALL.md](../INSTALL.md) installs it. [AUTHORING.md](AUTHORING.md) is the format of the
two files you write. [API.md](API.md) is the HTTP contract underneath, for changing the
editor rather than using it.

## The order things happen in

The editor renders an episode. It does not invent one, so the first three steps happen
outside it:

1. **Install the node pack** and the model files ([INSTALL.md](../INSTALL.md)).
2. **Write the two files** — `Shows\ep01\series.json` and `Shows\ep01\ep01.md`
   ([AUTHORING.md](AUTHORING.md) opens with a complete pair to copy).
3. **Restart ComfyUI**, or reload the browser tab if it was already running. The two
   sidebar buttons only appear once the node pack has loaded: **h3 Shots** (a film frame)
   and **h3 Refs** (stacked pictures).
4. **Open h3 Shots → the folder button** and add the folder that *contains* your episode
   folders (the root), then pick the episode from the drop-down beside it.
5. **Build.** This runs `h3build` for both passes and fills the shot bin.
6. **Go to h3 Refs and make the references** — character sheets, props, plates, voices.
   Renders are blocked until the pictures a shot needs are on disk, so this comes before
   rendering, not after.
7. **Come back to h3 Shots and render**, proxy first.

**Proxy first, always.** The proxy pass is the same shots at the same seeds, rendered small
and cheap. It tells you whether the cut works, the timing lands and the characters hold,
for a fraction of the time. Switch the pass selector to `final` once the proxy says the
episode is right.

## h3 Shots

### The toolbar

Left to right:

| Icon | What it does |
|---|---|
| folder-open | **Project folders** — browse, add or remove roots, open an episode |
| refresh | **Rescan the project roots** (after adding an episode folder outside the editor) |
| folder | The episode drop-down |
| *(pass)* | **final / proxy** — which pass you are looking at and rendering |
| play | **Build** — runs `h3build` for both passes from the script and series config |
| file-edit | **Edit the episode's script** — opens the Script window |
| book | **Edit the series config** (`series.json`) |
| volume-up | **Recording** — attach a dialogue recording and time the script against it (`h3align`). Lit when the episode has one |
| sliders | **Inspect the selected shot** |
| play | **Play all** — the cut, played from its takes, no export needed |
| film | **Open the timeline** |
| images | **Open the Refs tab** |
| refresh | **Refresh** — re-read the episode from disk |

### Shots and takes

The bin is grouped by sequence (`sq01`, `sq02`, …) with every shot under it and every take
under the shot.

- **Click a sequence header** to expand or collapse it.
- **Click a shot** to select it. The Script window follows the selection, and so does
  Inspect.
- **Click a take** to select that candidate; **double-click** opens it in the Viewer.
- **Use** puts that take in the cut. A take has to be finished and have video before it can
  go in.
- **The ⋯ button, or right-click anywhere on a shot or take**, opens the context menu. It is
  the same menu in the bin, the timeline and the viewer strip.

### The context menu

Everything you do to a shot lives here:

| Item | What it does |
|---|---|
| **Render this shot** | Queue it on ComfyUI with its current settings |
| **Render with settings…** | The same, after changing model, LoRAs, steps, seed or target for this run |
| **New take like this one…** | Re-render from *this* take's exact settings, with a new seed by default. The one you will reach for most |
| **Cancel this render** | Stop a queued or running take |
| **Use this take** | Put it in the cut |
| **Play** / **Compare with…** | Open in the viewer; compare two takes side by side or with a wipe |
| **Play from here** | Play the cut starting at this shot |
| **Inspect shot** | The Inspector: target, overrides, refs used, refs missing |
| **Show in script** | Jump the Script window to that shot's lines |
| **Show missing refs (n)** | Which pictures this shot is waiting on |
| **Discard take…** | Move a candidate to `_trash/` — nothing is deleted, but the bin stays readable |
| **Show details** / **Copy path** | The take's sidecar, and its file path |
| **Generate first frame** / **Clear keyframe** | Make (or drop) this shot's opening still |
| **Use previous shot's last frame as first frame** | Continuity: chain this shot onto the one before it |
| **Use this frame as the next shot's first frame** | The same, pushed forward |
| **Lock in the cut** / **Unlock** | Freeze a clip so it can't be moved, trimmed or re-picked |
| **Audio from…** | This clip's picture with another clip's (or a file's) sound |
| **Move left / right in the cut** | Reorder (`Alt+←` / `Alt+→` in the timeline) |

### Editing a prompt

**Inspect shot** is where a shot's prompt, seed, model, LoRAs and steps are edited, per
pass. Save, then **New take like this one…** to render it.

> **Don't edit while that shot is rendering.** A finishing render refreshes the panel and
> your unsaved edit goes with it. Let the take finish (or cancel it) first.

Story edits — action, camera, dialogue — belong in the **Script** window instead, not in an
override. **Promote** moves overrides that the authored files can express back into them.

### The graph a model renders with

Each target renders a ComfyUI workflow, and **What's missing** (the model status window)
shows which copy of it is in force, per target:

| It says | What that means |
|---|---|
| **repo** | the graph h3pipe ships, in `targets/<kind>/<id>/workflow.json` |
| **ComfyUI** | a workflow of that name is saved in this ComfyUI, and it wins |
| **$VAR** | that environment variable points at a file, which beats both |
| **edited here** | the graph in force isn't the repo's — ignoring everything a render
  patches anyway (model, LoRAs, prompt, size, seed, the episode's folder) |

To change how a model renders, press **Copy to ComfyUI**: the repo's graph is written into
ComfyUI's workflows under the name the target looks up. Open it on the canvas, change what
you like, save — from then on your saved copy is what that target renders. Your own graph
works the same way: save it in ComfyUI under that name (What's missing shows the name, with
a copy button). **Revert to the repo's copy** deletes the saved one and puts the shipped
graph back; it never touches an environment variable.

What a render sets per shot stays out of your hands: the prompt, size, length, seed, model
and LoRAs are written into the graph at queue time, so editing those widgets on the canvas
changes nothing. Everything else — samplers, extra nodes, upscalers, how the picture is
built — is yours.

From the command line: `python h3.py targets` prints the same per-target line, and
`--install-workflow <target>` / `--revert-workflow <target>` are the two buttons.

## h3 Refs

Used heavily at the start of a series and rarely afterwards, if the references come out
right. Every character view, prop, plate, voice and shot keyframe the series config and
script call for is listed here.

- **Click a ref** to expand it; **double-click the ref's row** to open its live file — a
  character's stitched sheet, a prop, a plate — in the viewer.
- **Double-click a candidate** (one of the four view images) and it opens against the live
  one, for comparison.
- **Right-click is your browser's menu here**, not the editor's context menu. The Refs tab
  has buttons instead.
- **Expand the prompt** (it shows the series config's wording) to edit it, change the
  model, add LoRAs, or change the seed and steps. Settings above the views are shared by
  all four; the prompt is per view.

### Making references

- **Generate** makes candidates. The view selector and the count beside it decide which
  view and how many, each with its own seed — **you do not have to redo all four views.**
  If only the back view is wrong, regenerate the back view.
- **Import…** takes an image from the ComfyUI machine, and you can drag a file onto a slot
  or use Upload. Hand-made and externally generated references are the expected case; the
  generators are the fallback.
- **Generate missing** fills everything the episode needs in one go. It does the pictures —
  **not the voices.** Generate a voice from its own ref row.
- **Clear** unpicks a ref; **Discard** bins a candidate.
- A character's sheet is stitched automatically once all four views are picked.

### Choosing a model

The target picker sets which image model generates. They are not interchangeable:

| Target | Reads references? | Notes |
|---|---|---|
| `krea2` | No | Text to image. The default, and it holds a character across four views well because they share one seed |
| `z_image_turbo`, `flux2_klein` | No | Text to image |
| `flux_kontext` | One | The only edit target that takes a negative prompt |
| `flux2_klein_edit` | Up to 4 | The default for keyframes when installed |
| `minimax_h3_still` | Up to 9 | The video model rendering 5 frames and keeping the first |
| `qwen_image_21` | Up to 16 | Text to image *or* an edit, decided by whether there is anything to edit from. The only one with a usable cfg, scheduler, denoise and negative prompt |

`python h3.py targets --kind image` says which are installed.

### Wardrobe variants

A subject with `of:` in the series config is the same character in different clothes, with
its own sheet ([AUTHORING.md](AUTHORING.md), "A wardrobe change is a new subject"). How its
views are made depends on the model:

- **On an edit model** (`qwen_image_21`, `flux2_klein_edit`, `flux_kontext`,
  `minimax_h3_still`) each view is generated **from the character's own matching view** —
  the towel's back panel is an edit of their back panel.
- **On a text-to-image model** there is nothing to edit from, so it draws the variant from
  its `design` **on the character's seed**. Closer than a fresh draw, but not the same
  thing.

**Order matters.** A whole pass plans every job before anything is picked, so in the first
pass a variant has nothing to edit from and comes out cold. Run the pass, let the
character's views pick, *then* regenerate the variant. To check which happened, select the
candidate and read the **drawn** row: `edited from Dana (three-quarter)`, or `from the
prompt alone`.

## The timeline

The cut as an editable track, in the bottom panel.

- **Drag a clip** to reorder it; **drag its edges** to trim frame by frame.
- **Double-click** opens the clip; **right-click** is the same context menu as the bin, so
  everything above — new takes, keyframes, locking, audio — is available here too.
- **Ctrl + wheel** over the track zooms.
- **Turn on the waveform lane** to see the audio under the cut. Useful with a recording
  attached.
- **Export** assembles the mp4; **Play all** watches it straight from the takes without
  assembling anything.

Setting a shot's keyframe from its neighbour is how you make a cut continuous: use the
menu's **Use this frame as the next shot's first frame** on the take you like, and the
next shot starts where this one ended.

### Keyboard

These work while the timeline has focus, and never while you are typing in a field:

| Key | What |
|---|---|
| `Space` | Play / pause the cut |
| `J` `K` `L` | Shuttle back / pause / forward |
| `I` `O` | Trim the head or tail at the playhead |
| `Alt+←` `Alt+→` | Move the selected clip in the cut |
| `Ctrl+Z` / `Ctrl+Y`, `Ctrl+Shift+Z` | Undo / redo the cut |
| `Ctrl + wheel` | Zoom the track |
| `←` `→` | Step candidates, in the Viewer |
| `Escape` | Close a menu, or cancel a drag |
| `Ctrl+S` | Save, in the Script and Series config windows |

## Where it all lands

Everything the editor does is a file in the episode folder, and the command line does the
same things to the same files: `overrides.json` (prompts, seeds, models, targets),
`cut.json` (order, trims, locks, per-clip audio), take sidecars beside each render, and
`_history/` copies of the script and series config each time you save. Nothing is hidden in
a database, and nothing is lost — a discarded take moves to `_trash/`.
