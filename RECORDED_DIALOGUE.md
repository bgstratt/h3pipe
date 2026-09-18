# Recorded dialogue: lip-synced episodes

Use this when you have real recorded lines (actors, you, or TTS) instead of letting H3 clone or invent voices.

## Steps

```
cd <your projects folder>
pip install faster-whisper                                    # once

python h3.py align    Shows\ep05 audio\ep05_dialogue.wav --dry-run   # look first
python h3.py align    Shows\ep05 audio\ep05_dialogue.wav
python h3.py build    Shows\ep05
python h3.py render   Shows\ep05 --proxy
python h3.py assemble Shows\ep05 --proxy --audio master  # picture + your recording, to check sync
```

Put the recording inside the episode folder (for example `ep05\audio\`) so the bible can point at it with a relative path.

## Recording

- Record the lines in script order, dry: no music under them. If there is music, split it first with `python -m demucs --two-stems vocals file.wav` and use the vocals stem.
- Leave real pauses where the script has silent shots. h3align stretches or squeezes those shots to fit the pause, and warns you when the pause is far off.
- One file for the whole episode.

## What h3align changes

- **Script:** adds `audio: in-out` to every shot and turns old `dur:` lines into comments. A backup is saved as `.bak`.
- **Bible:** sets `audio.mode` to `source_track` and `audio.track` to your recording. A backup is saved as `.bak`.
- **Report:** writes `align_report.md` with every window and line, plus any problems it found.
- **Transcript cache:** saves the transcript as `<recording>.words.json`, so re-running is instant. Use `--retranscribe` after you replace the recording.
- **Cut points:** cuts land at the quietest point of each pause. Speaking shots are lengthened to H3's allowed frame counts when the pause has room.

## Audio policy and retention

A speaking shot defaults to `dub_keep_foley`: H3 lip-syncs to your line, and Save Shot keeps H3's sound effects with the voice removed, as `_foley.wav`.

| Retention | What H3 does with your line | Default for |
|---|---|---|
| `fully_copy` | Your clip becomes the shot's entire soundtrack. Lips follow it, and no sound effects are added. | `dub` |
| `partially_copy` | Copies your line exactly and adds ambience and action sounds around it | `dub_keep_foley` |
| `reference` | Only borrows the voice and timing, as before. Lip sync is loose. | |

To override, set it per shot (`retention: fully_copy`), for the episode (`"audio": {"retention": ...}`), or for the default policy (`"audio": {"default_policy": "dub"}`). You can also pass `--policy` or `--retention` to h3align. Shots in `clone` and `generate` mode are unchanged.

Frame lip-sync shots as close or medium shots. The reference image's framing matters more than the prompt.

## h3align options

`python h3align.py --help` lists them all. The ones you reach for:

| Flag | What it does |
|---|---|
| `--dry-run` | Report only. Changes no files. Always worth running first |
| `--retranscribe` | Ignore the cached `<recording>.words.json` after replacing the recording |
| `--words <file>` | Use word timings you already have instead of transcribing |
| `--model <name>` | Whisper model: `tiny.en`, `base.en`, `small.en`, `medium.en` (default), `large-v3` |
| `--device cuda\|cpu` | Where Whisper runs. `auto` by default |
| `--script <file>` | Pick the script when the folder holds more than one `.md` |
| `--no-snap` | Leave windows exactly as measured instead of lengthening them to H3's frame grid |
| `--policy`, `--retention` | Override the defaults in the table above for the whole episode |

## Conform

- **Trimming:** h3assemble trims each timed shot back to its exact window, so the cut lines up with the recording from the first window's start. `--no-trim` turns this off.
- **Sync check:** `--audio master` lays the recording under the cut. It's a check, not a mix.
- **Final mix:** do it in Resolve. Put the cut on one track and the recording on another, starting at the time shown in `align_report.md`. Add each shot's `_foley.wav` at its start time (listed in `_shots.txt`), then music.
