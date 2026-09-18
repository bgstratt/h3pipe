= ep01  The Gold Path

// This is the whole authoring surface. Write shots like a screenplay.
// Everything else — the H3 prompt format, frame grids, seeds, reference
// wiring, audio policy — is generated. You never touch JSON.
//
//   #  sequence     #  <id> <location-from-bible>
//   ## shot         ## <id>
//   who: names       characters in the shot  -> <Picture 1..3>
//   with: names      props / vehicles        -> next free slot
//   key: value      size / audio / dur / camera / sound / music
//   NAME: line      dialogue — name must be a character in the bible
//   NAME (V.O.): ..  voiceover — speaks but is NOT on screen, costs no slot
//   NAME (O.S.): ..  off-screen — in the space, outside the frame
//   plain text      action
//   // comment      ignored


# sq01  street

## sh010
who: huey
size: wide
audio: 0.00-3.10
Huey stands near the edge of the yard, small in the frame, watching the empty street.
camera: holds a static wide shot
sound: late-afternoon suburban ambience with distant birds and a faint breeze through the treeline

## sh020
who: riley, huey
size: medium
audio: 3.10-7.40
Riley runs into frame from the left and stops hard beside Huey, breathing fast.
Huey does not turn his head.
camera: pushes in with small amplitude at slow speed on the two of them
RILEY (breathless and certain): There's gold at the end of the path.
HUEY (flat): There is not.
sound: running footsteps on grass, fabric movement, and continuing suburban ambience

## sh030
who: riley
size: close
audio: 7.40-10.00
A closer framing on Riley's face and shoulders, the treeline soft and out of focus behind him.
His eyes fix on the woods and he starts to back away toward the house.
camera: holds static as he exits frame right
RILEY (already turning): I've got to pack.
sound: sneakers scuffing dry grass over steady outdoor ambience


# sq02  bedroom

## sh040
who: riley
with: backpack
size: medium
dur: 3.04
Riley drags the backpack off the bunk and starts stuffing it with everything within reach.
camera: holds static
sound: zippers, fabric, objects hitting the bottom of a bag

## sh050
who: huey, riley
size: wide
audio: 12.20-16.50
Huey appears in the doorway and watches Riley pack without moving to help.
camera: holds a static wide shot from the hallway
HUEY (unimpressed): You don't even know where it is.
RILEY: I'll know when I see it.
sound: continuing packing sounds, a floorboard creak from the doorway

// A voice with no body. Riley's mother is in another room — she speaks, but
// she is never drawn and costs no reference slot. Only `who:` puts a
// character on screen.

## sh055
who: riley
size: medium
audio: 16.50-20.60
Riley freezes with a fistful of socks halfway to the bag.
camera: holds static
MOM (V.O., from downstairs): Riley! Shoes off in the house!
RILEY (caught): I'm not even outside!
sound: muffled television from another room


# sq03  path
continuous: yes

// continuous:yes means this whole sequence is ONE unbroken take.
// The shots chain together and carry motion across the joins.
// Use it sparingly — it costs 22 frames per shot after the first.

## sh060
who: huey, riley
with: backpack
dur: 12.0
Riley leads down the dirt path at a fast walk with the backpack half-zipped.
Huey trails two steps behind with his hands in his pockets. Branches slide past in the foreground.
camera: tracks backward ahead of them at their pace, holding both in frame
sound: footsteps on packed dirt, backpack buckles ticking, dense woodland ambience with layered insects

## sh070
who: huey, riley
audio: 20.60-32.60
The path narrows and the canopy thickens overhead, dropping the light.
Riley slows without stopping and glances back over his shoulder. Huey closes the gap.
camera: continues its backward track and arcs slightly to the right without breaking
RILEY (quieter now): It's further than I thought.
sound: continuing footsteps on dirt and thickening woodland ambience with fewer birds
