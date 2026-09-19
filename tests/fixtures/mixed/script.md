= mx01  Mixed Targets

// Golden-test fixture for Phase 8: one episode, two video targets. H3 shots go
// to shotlist.json; ltx2 shots to shotlist.ltx2.json. Each shot's comment says
// how it gets its target.

# sq01  kitchen

// the series target (H3), before and after a retargeted shot in the same
// sequence: sh030's place in the sequence must not shift sh020's
## sh010
who: ada
size: ws
dur: 2.5
Ada slams the walk-in door shut and turns to face the kitchen.
camera: pushes in on Ada with small amplitude at slow speed
sound: the compressor hum of the walk-in, a door latch clanking
music: a plucky ukulele sting

// a shot-level target: line; dialogue with delivery and a V.O. by a visible
// speaker; props; extras; the series' clone mode falls back to generate
## sh020
target: ltx2
cast: ada, bo
props: kettle
size: ms
dur: auto
extras: two customers at the counter seen through the window, backs to camera
Bo sets the kettle down on the burner and flicks the gas on.
ADA (warmly): You know that one whistles.
BO: I know.
ADA (V.O.): He always knows.

// back on H3
## sh030
who: ada
size: cu
dur: 1.62
Ada's eyes narrow at the kettle.
ADA (V.O.): Not today.

# sq02  street
target: ltx2

// a sequence-level target: line; no camera line (the camera remains
// static); a recording window with dub_keep_foley (falls back to generate)
## sh040
who: bo
with: van
size: wide
audio: 1.00-3.50
policy: dub_keep_foley
Bo waves the van back toward the kitchen door.
BO: Left! Left!
sound: a reversing beeper, traffic in the distance

// a profile that picks ltx2, with its own steps; O.S. line by a narrator
## sh050
profile: ltx_quick
who: cy
size: close
dur: 3.04
plate: kitchen_window
Cy presses her face to the window glass.
NARRATOR (O.S.): And then it rained.
camera: slowly tilts up to the grey sky

# sq03  kitchen

// a profile that names H3 explicitly
## sh060
profile: dialogue_close
who: bo
size: close
dur: 2.0
Bo pours the tea.
