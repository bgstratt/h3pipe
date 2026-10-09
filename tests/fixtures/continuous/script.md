= cont01  Continuous

// Every shot after sh010 in sq01 continues the one before it (the header),
// except sh040, which breaks the chain.
# sq01  kitchen
continuous: first

// the sequence's first shot: it doesn't continue (H3 Ref2VA)
## sh010
who: ada
size: medium
dur: 3.04
Ada wipes down the counter, glancing at the ticket rail.

// continues sh010 by the header: built on H3 FL2VA
## sh020
who: ada
size: medium
dur: 3.04
Ada keeps wiping, then stops and looks up at the door.

// continues sh020 by the header
## sh030
who: ada, bo
size: wide
dur: 4.04
Bo pushes through the door, shaking rain off his jacket.
BO: Still open?

// breaks the chain: a real cut, back on H3 Ref2VA
## sh040
continuous: none
who: bo
size: close
dur: 2.04
Bo's eyes on the empty stools.

# sq02  street

// the old form, read as `continuous: first` (h3.py check says how to write it)
## sh050
first: continuity
who: ada
size: wide
dur: 3.04
Ada steps out into the rain and pulls her hood up.

// continues sh050 by its own line
## sh060
continuous: first
who: ada
size: medium
dur: 3.04
Ada walks on, past the neon sign, head down.

# sq03  kitchen
continuous: latent

// the sequence's first shot: it doesn't continue
## sh070
who: bo
size: medium
dur: 3.04
Bo sets his cup down on the counter and turns toward the window.

// continues sh070 by the header, holding its last 39 frames (the default)
## sh080
who: bo
size: medium
dur: 4.04
Bo crosses to the window and wipes the fog off the glass with his sleeve.

// an overlap that isn't 17j+5 rounds up: 30 holds 39
## sh090
overlap: 30
who: ada, bo
size: wide
dur: 3.04
Ada comes in behind him and stops at his shoulder.
ADA: Still raining?
