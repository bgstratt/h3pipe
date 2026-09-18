"""
h3core — the model-free core of the pipeline.

    story.py   script text -> story IR (and the legacy parser dict)
    ir.py      the IR dataclasses (Episode / Sequence / Shot / Line) and their JSON
    bible.py   loading series.json
    speech.py  dialogue pacing: syllables, speech time, forced rate

Nothing here knows about a model: no frame grid, no reference slots, no prompt
format. Those belong to a target (today the H3 compile code in h3build.py).
Stdlib only.
"""
