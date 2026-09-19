# Breaking a screenplay scene into shots

A worked example for [AUTHORING.md](AUTHORING.md). The input is ordinary spec-format script
pages; the output is the shot list. The interesting part is not the syntax, it's where the
cuts land and why. (Packaged with the script-writing skill as `references/breakdown.md` by
`python tools/make_prompts.py`.)

## The source

```
EXT. SUBURBAN HOME - NIGHT

WE OPEN on a modern suburban home. The front window illuminated by the lights
inside. We see the silhouette of a small human figure as it runs back and
forth. We push in closer as we slowly see a BOY running around the house.

CUT TO:

INT. SUBURBAN HOME - KITCHEN - NIGHT

A GREEN BALL sits on a counter top. A young hand snatches it. It belongs to
FILBERT (9), wiry, lost in his own imaginary world. Dressed as a Knight. A toy
sword in his other hand.

                    FILBERT (V.O.)
          This is my castle. I am sworn to protect
          it. Anyone that stands in my way shall
          bear the wrath of the almighty--

Just then, the babysitter walks by. BECKY (23), trendy, distracted. She is
mid-phone call with Filbert's Mom, TRACY.

                    BECKY
                    (into phone)
          Oh yeah, he's being good. He's just
          fighting orcs or trolls.
```

## What the series config absorbs first

Before any shots, the character introductions leave the script entirely.
`FILBERT (9), wiry, lost in his own imaginary world. Dressed as a Knight.` becomes a series
config entry, and note what survives the translation and what doesn't:

```json
"filbert": {
  "kind": "character", "name": "Filbert", "pronoun": "his",
  "design": "a nine-year-old boy, wiry and small, in a grey plastic knight's helmet with the visor up, a foil breastplate over blue pyjamas, and mismatched socks, holding a wooden toy sword, drawn with thick confident outlines",
  "sheet": "refs/filbert/filbert_sheet_4panel.png",
  "voice": "high, earnest, fully committed to the bit",
  "voice_sample": "audio/voices/filbert_sample.wav"
}
```

"lost in his own imaginary world" is gone. It's characterisation, not appearance: the model
can't draw it, and it would displace detail that it can. The knight costume, however, gets
*expanded*, because the screenplay could say "Dressed as a Knight" and trust a costume
designer. The prompt can't. On a target that takes no reference pictures, this sentence is
the whole costume.

Tracy needs an entry too, but only a `voice`: she is heard and never seen, so she needs no
`sheet` and never occupies a reference slot.

Two locations, both at night:

```json
"house_ext_night": {
  "description": "the front of a modern two-storey suburban house at night, the large front window glowing warm from inside, the lawn and driveway dark",
  "plate": "refs/_bg/house_ext_night.png"
},
"kitchen_night": {
  "description": "a clean modern kitchen at night under warm overhead lighting, pale counters, a fruit bowl and a landline handset on the island",
  "plate": "refs/_bg/kitchen_night.png"
}
```

## The breakdown

The first paragraph reads as one continuous move, so it's one shot: the push in *is* the
shot, and cutting mid-push would throw away the thing the writer asked for.

The kitchen scene is four shots, and the reasoning for each split is what matters:

```
= ep02  Filbert the Brave

# sq01  house_ext_night

## sh010
size: wide
dur: 5.17
The front of the house at night, the large front window glowing from inside.
The small silhouette of a child in a helmet runs back and forth behind the glass.
camera: pushes in with small amplitude at slow speed toward the lit window
sound: night crickets, a distant car, muffled thumping from inside the house


# sq02  kitchen_night
first: generate

## sh020
with: green_ball
size: close
dur: 2.33
A green rubber ball sits alone on a pale kitchen counter. A small hand darts
into frame and snatches it away.
camera: holds a static shot
sound: a soft rubber scuff as the ball leaves the counter

## sh030
who: filbert
size: medium
dur: 4.46
Filbert stands in the middle of the kitchen in his knight's helmet, the green
ball in one hand and a wooden toy sword raised in the other.
camera: holds a static shot
FILBERT (V.O.): This is my castle. I am sworn to protect it.
sound: bare feet on tile, the plastic visor rattling

## sh040
who: becky
size: wide
dur: 4.46
Becky drifts through the kitchen behind the island, phone to her ear, not
looking at anything in particular.
camera: trucks right with small amplitude, following her across the room
BECKY (into phone): Oh yeah, he's being good. He's just fighting orcs.
sound: a fridge hum, slippers on tile

## sh050
who: filbert
size: close
dur: 2.33
Filbert's face under the raised visor, eyes fixed on something out of frame,
completely serious.
camera: holds a static shot
TRACY (V.O.): Oh that's perfectly normal.
sound: the fridge hum continuing, faint
```

## Why the cuts fall there

**`sh020` is its own shot** because the screenplay gives the ball its own sentence before
revealing whose hand it is. That reveal only works if the ball is alone in frame first. It's
also the cheapest shot in the scene: 2.33s, one prop, no characters.

**`sh030` cuts** rather than pulling out from `sh020`, because the subject genuinely changes:
ball to boy. A pull-out would work too, but then the ball snatch and the reveal share a shot
and the joke lands softer.

**`sh040` cuts** because a new person enters and speaks. Becky gets a wide, not a medium: the
width is the joke, since the point is how much room there is between her and what Filbert
thinks is happening.

**`sh050` cuts back to Filbert** even though Tracy is the one talking. Tracy is `(V.O.)`, a
phone voice from another house. She is never drawn and costs no reference slot. Cutting to
Filbert's face under the line is what makes it funny; cutting to Becky would just be
watching someone listen.

**Nothing became a camera move that should have been a cut, and vice versa.** The push-in in
`sh010` stays a move because the framing changes but the subject doesn't. The ball-to-boy
transition stays a cut because the subject does.

## Targets and keyframes

The script names no `target:`. Every shot here works on the default (H3 Ref2VA, with
Filbert's and Becky's sheets as reference pictures), and leaving it out means the episode
can be moved to another model from the editor without touching the script.

`first: generate` under `# sq02` matters only on a target that reads keyframes (H3 FL2VA,
LTX, Wan). There, a shot with a shot before it in its sequence would otherwise open on that
shot's last frame (continuity), and every cut in this kitchen changes the subject or the
size: `sh030` must not open on a close-up of a counter. Continuity is for a shot that picks
up the previous one's picture; this scene has none.

`sh010` is a good candidate for `dur: model` on LTX if you don't care exactly how long the
push-in runs; the dialogue shots keep their `dur:`, because the lines need their room.

## What got dropped, deliberately

`CUT TO:` disappears: every `##` is already a cut.

`FADE IN:` / `FADE OUT.` disappear entirely. A fade is an **edit** decision. If it were in
the prompt, the video model would render the fade into the clip and you could never change
your mind in the timeline. Generate clean handles; dissolve at conform.

The parenthetical `(into phone)` survives, but inline: `BECKY (into phone):`. Spec format puts
it on its own line; this format doesn't.

`WE OPEN on` and `We push in closer` move from the action prose into the `camera:` line,
where they belong. Anything phrased as a camera instruction in the source should end up
there, not in the action.

## Shot IDs

`sh010, sh020, sh030`: tens, so `sh035` can be inserted later without renumbering anything.
This matters more than it looks: seeds are derived from episode, sequence and shot ID, so
renumbering a shot changes its seed and invalidates every render of it.
