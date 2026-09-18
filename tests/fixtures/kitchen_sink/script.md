= ks01  Kitchen Sink

// Golden-test fixture. Every shot exists to reach a branch of h3build;
// the comment above each says which.

# sq01  kitchen
Scene-setting prose under a sequence header is skipped.

// explicit dur that snaps up with >15% padding (warning); text; music; ws
## sh010
who: ada
size: ws
dur: 2.5
Ada slams the walk-in door shut and turns to face the kitchen.
camera: pushes in on Ada with small amplitude at slow speed
sound: the compressor hum of the walk-in, a door latch clanking
music: a plucky ukulele sting
text: OPEN 24 HOURS

// cast:/props: aliases; dur: auto; same-speaker gap; delivery; extras; ms
## sh020
cast: ada, bo
props: kettle
size: ms
dur: auto
extras: two customers at the counter seen through the window, backs to camera
Bo sets the kettle down on the burner and flicks the gas on.
ADA (warmly): You know that one whistles.
BO: I know.
BO (whispering): That's the point.

// V.O. by a visible character: lips-closed clause with the bible pronoun; cu face view
## sh030
who: ada
size: cu
dur: 1.62
Ada's eyes narrow at the kettle.
ADA (V.O.): Not today.

// two subjects on a close (body view, weak plate); vehicle + prop; O.S. by the
// visible speaker; V.O. by an off-screen narrator with extra delivery; explicit
// generate policy; shot-level model, lora off, steps
## sh040
who: bo
with: van, kettle
size: close
dur: 3.04
policy: generate
model: shot_model.safetensors
lora: none
steps: 10
Bo leans out of the back door as the van reverses toward it, kettle in hand.
BO (O.S.): Left! Left!
NARRATOR (V.O., into phone): Nobody ever goes left.

// crammed dialogue; a speaker with no voice sample; kind-less subject
## sh050
who: cy, rex
dur: 1.62
pace: fast
Cy bursts in from the back with Rex at her heels.
CY: Absolutely everybody in this entire neighbourhood is waiting outside for breakfast!
REX: Woof.

// tight dialogue at a slow pace; voiceover / offscreen token spellings
## sh060
who: bo
dur: 3.04
pace: slow
Bo stares at the ticket rail as it fills with orders.
BO (voiceover): Here we go again, then, everybody.
CY (offscreen): Bo!

# sq02  street
continuous: yes
model: seq_model.safetensors
steps: 12

// three subjects; dub with fully_copy; audio window on the track
## sh110
who: ada, bo, cy
size: wide
audio: 1.00-3.50
policy: dub
retention: fully_copy
sound: traffic hiss on wet asphalt
Ada, Bo and Cy spill out of the diner door into the rain.
ADA: Van.
BO: Where?
CY: There!

// dub_keep_foley + fully_copy + sound (warning); continuation clause
## sh120
who: ada
audio: 3.50-5.00
policy: dub_keep_foley
retention: fully_copy
sound: running footsteps splashing through puddles
Ada sprints along the kerb after the van.
ADA: Stop!

// shot plate override; dub_keep_foley default retention (partially_copy); O.S. on screen
## sh130
plate: kitchen_window
who: bo
audio: 5.00-7.20
policy: dub_keep_foley
sound: rain drumming on the awning
Bo ducks back inside and leans through the pass-through window.
BO (O.S.): She's never catching that.

// no subjects: establishing plate with narration; dub + reference retention
## sh140
audio: 7.20-9.00
policy: dub
retention: reference
The street empties; the neon sign flickers once and goes dark.
NARRATOR (V.O.): And that was the last anyone saw of the kettle.

// duration: alias; short continuous shot (chaining-cost warning)
## sh150
who: ada
duration: 1.00
Ada stops dead in the middle of the road, soaked, and starts to laugh.

# sq03  kitchen_window
lora: seq_lora.safetensors

// sequence lora; three clone voices; repeat speaker in one shot
## sh210
who: ada, bo
dur: auto
Ada and Bo lean on the counter, dripping, as the kettle finally whistles.
ADA: Well.
BO: Well what?
ADA: Put the tea on.
NARRATOR (V.O.): They did.
