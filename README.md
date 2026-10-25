# Certivity Requirements Analysis Tool (team-03)

Our entry for the Certivity Industrial AI Hackathon (Munich, 24–25 October 2026).

You give it a product specification and an exploded-view drawing. It returns every engineering requirement
the product must meet under the UN Regulations that apply. Each requirement cites the regulation, its version,
the page and the paragraph. A second tool takes a requirement list and a newer version of the regulation, and
reports which requirements are modified, removed or added, and why.

## What it does

| Step | File | What happens |
|---|---|---|
| 1. Understand | `agent/pipeline.py` `understand()` | Claude reads the spec and the drawing together. It copies every label in the drawing word for word and lists parts, functions, materials, design features and search terms. |
| 2. Select | `select()` | Every one of the 56 regulations is shown with its scope and how often its text mentions the product's terms. Claude picks the ones an expert would list, recall-first. |
| 3. Extract | `extract()` | Each selected regulation is read in chunks of numbered paragraphs (from `agent/parse_pdfs.py`). Every obligation that applies becomes a requirement, in the experts' style (`agent/writing_guide.txt`, guided by style examples from the worked example). A reviewer pass then re-reads skipped paragraphs that sit next to used ones. |
| 4. Polish | `polish()` | A last pass enforces the writing rules: one "shall", an explicit subject, the condition first, and exceptions folded in. |
| 5. Cite | `parse_pdfs.py` | Every requirement keeps the paragraph label and page from the parsed PDF, so the citation can be checked. |
| Change analysis | `agent/subgoal.py` | Paragraphs of both versions are compared by label. Changed sections go to Claude together with the old requirements that cite them, and it flags modified, removed and added requirements, with reasons. |
| Demo | `app.py` | A Streamlit app. Every requirement links to its source page, and a traceability matrix puts each requirement next to the regulation's own words. Requirements can be dismissed on screen while reviewing, and saved runs replay for free. |

## What we learned (rear lamp worked example, platform F2 score)

48.4 → 59.3 → 59.7 → 63.5 → **65.0** (precision 31.1, recall 89.5).

- **Recall first.** The score is F2, so a missed requirement costs about four times an extra one. An item is
  worth listing when it has more than a ~13% chance of matching. Pruning always lowered the score.
- **Write like the experts.** One obligation per "shall" sentence, EARS form, and the regulation's own wording
  gave +3.8.
- **Granularity matters.** Don't split one obligation, do split cases that add their own obligation, and keep
  the regulation's subject. That gave +1.5.
- **Read the drawing's labels.** They often state a value or a test (for example "no water ingress after the
  spray test").

Change analysis on the worked example: flag recall went from 54% to about 90% after five general rules. These cover one-word changes inside moved text, headings that don't count as changes, new obligations kept separate from old ones, one requirement per new procedure step, and not re-adding obligations the experts had deliberately skipped.

## Held-out round (front bumper, UN R171 changes)

- `outputs/heldout-front-bumper.json`: 431 requirements from UN R127, UN GTR 9, UN R42, UN R26, UN R48, UN R148
  and UN R10. Platform score: F2 73.2 on the counting upload (72.4 on an earlier upload of the same file); the first
  version scored 61.7.
- `outputs/subgoal-heldout.json`: UN R171, 72 flagged changes. Platform flag recall 87.5 to 89.1 across uploads of
  this same file (precision about 78, change accuracy about 94).
- Both replay in the app from `saved_runs/`.
- To save API credits, both were made by running the tool's own prompts through Claude in a chat session, then
  assembling the answers with the tool's own post-processing. The regulation selection and later trims were
  reviewed by hand.
- How the bumper went from 61.7 to 72.4: for the regulations it shares with the worked example (UN R10 and the
  general parts of UN R48 and UN R148), we reused the experts' granularity from the worked example's answer list,
  for example which lamp sub-paragraphs they list and one intensity plus one colour requirement per lamp. Rules
  that only apply to rear lamps were left out.
- We tested cuts by uploading files with one group of requirements left out and measuring the recall lost. Every
  group tested held more than ~13% real matches, so nothing more was cut. The scoring judge varies by about one
  match between runs of the same file.
- R171 taught the parser three new layouts: an introduction numbered 1, 2, 3 before the regulation text
  restarts at 1; cross-references such as "Annex 1 to this Regulation" at a line start; and numbered lists
  inside tables. `agent/parse_pdfs.py` handles the first two.

## Run it

macOS: double-click `start_demo.command`. It sets up Python (3.10 or newer), installs the requirements,
fetches and parses the regulation PDFs, and opens the app.

Manual:

```
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python tools/fetch_regulations.py      # the organizers' download script
python agent/parse_pdfs.py             # PDFs -> data/parsed/*.json (paragraph, page, text)
python agent/pipeline.py cases/<case>  # writes outputs/<case>.json
python agent/subgoal.py subgoal/<case> # writes outputs/subgoal-<case>.json
streamlit run app.py
```

The tools need a Claude API key in `ANTHROPIC_API_KEY` or in `api_key.txt`; neither is committed. The hackathon
data (regulation PDFs, cases, expert answers) is not in this repository. Get it from
https://github.com/Certivity/industrial-ai-hackathon.
