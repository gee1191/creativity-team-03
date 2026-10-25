"""From a product specification and/or exploded view to the requirements it must fulfil.

    python agent/pipeline.py cases/dev-rear-lamp --team team-03          # writes outputs/dev-rear-lamp.json

Stages: understand the product -> select the regulations -> extract requirements paragraph by
paragraph -> assemble and validate. Page and paragraph always come from the parsed PDF, never
from the model.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm import ask, image_block  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PARSED = ROOT / "data" / "parsed"
INDEX = json.loads((ROOT / "regulations" / "index.json").read_text())
CHUNK_CHARS = 30000
# Extra passes re-read each regulation in differently cut chunks and keep only what is new. Off by default: on the
# rear lamp, merging a second run added 51 requirements but only 3 matches, below the ~12% that pays off under F2.
PASSES = int(os.environ.get("CERTIVITY_PASSES", "1"))
# Style examples from the worked example's expert answer (agent/build_examples.py). On the rear lamp, showing the
# experts' answer for two regulations raised matches on the other two from 71 to 92 of 102 (offline estimate).
EXAMPLES_FILE = Path(__file__).resolve().parent / "expert_examples.json"
USE_EXAMPLES = os.environ.get("CERTIVITY_EXAMPLES", "1") == "1" and EXAMPLES_FILE.exists()
# How the experts phrase a requirement (EARS style). Rewriting our v6 rear-lamp list in this style, and nothing else,
# raised F2 from 59.7 to 63.5 on the platform, so extraction follows it and a final polish pass enforces it.
WRITING_GUIDE = (Path(__file__).resolve().parent / "writing_guide.txt").read_text()
POLISH = os.environ.get("CERTIVITY_POLISH", "1") == "1"

Log = Callable[[str], None]


# ---------- 1. understand the product ----------

class DrawingFeature(BaseModel):
    feature: str = Field(description="a shape or design detail seen in the drawing, e.g. 'rounded outer corners', 'sensor holes in the fascia', 'lens wraps around the side'")
    where: str = Field(description="where on the product it is")
    why_it_matters: str = Field(description="which kind of requirement it could trigger (edge radii, projections, visibility angles, mounting height, impact zones, ...)")


class DrawingLabel(BaseModel):
    part: str = Field(description="the part name written in the drawing, copied exactly")
    note: str = Field(description="the text written next to it, copied exactly (a value, a test, a condition); empty if none")


class ProductProfile(BaseModel):
    product_type: str
    vehicle_categories: list[str] = Field(description="UNECE categories, e.g. M1, N1")
    parts: list[str] = Field(description="every part or sub-assembly, including those only visible in the drawing")
    functions: list[str] = Field(description="what the product does: lighting functions, protection, sensing, ...")
    materials: list[str]
    electrical_or_electronic: bool = Field(description="contains electrical/electronic parts (LEDs, sensors, ECUs, heaters, wiring)")
    drawing_labels: list[DrawingLabel] = Field(description="every text label written in the drawing, word for word; notes next to a part often state a requirement or a test")
    drawing_features: list[DrawingFeature] = Field(description="details visible only in the drawing: shapes, curves, edges, openings, protrusions, how parts are arranged and mounted")
    parts_only_in_drawing: list[str] = Field(description="parts the drawing shows that the specification does not mention")
    summary: str = Field(description="two or three sentences an engineer would recognise as this product")
    search_terms: list[str] = Field(description="3 to 8 terms UN Regulations use for this product and its regulated parts or functions, e.g. 'bumper', 'stop lamp', 'retro-reflector', 'electronic sub-assembly'")


def understand(case_dir: Path) -> ProductProfile:
    content: list[dict] = []
    for img in sorted(case_dir.glob("*.png")) + sorted(case_dir.glob("*.jp*g")):
        content.append(image_block(img))
    spec = "\n\n".join(p.read_text() for p in sorted(case_dir.glob("*.md")) if p.name != "expected.md")
    content.append({"type": "text", "text": f"Technical specification:\n\n{spec or '(none, use the drawing)'}\n\n"
                    "Describe this product for a homologation engineer. Combine both sources: take facts (category, "
                    "materials, functions) from the specification, and study the drawing for everything the text does "
                    "not say: extra parts, shapes, curves, edges, openings, protrusions, how parts fit and are mounted. "
                    "Read every text label in the drawing word for word and copy each one into drawing_labels: the notes "
                    "next to a part name often state a value or a test the part must pass (for example 'no water ingress "
                    "after the spray test'). Use them for the parts, functions and search terms too."})
    return ask("You are an automotive homologation engineer. Read the specification and the exploded view together "
               "and describe the product precisely: parts, functions, materials, vehicle categories, and the design "
               "details visible in the drawing that can trigger regulatory requirements. Do not invent what neither "
               "source shows.", content, ProductProfile, effort="high")


# ---------- 2. select the regulations ----------

def load_paragraphs(regulation: str, role: str = "base") -> list[dict]:
    return json.loads((PARSED / f"{regulation.replace(' ', '_')}__{role}.json").read_text())


def base_version(regulation: str) -> str:
    entry = next(e for e in INDEX if e["regulation"] == regulation)
    return next(v["version_label"] for v in entry["versions"] if v["role"] == "base")


def catalogue(terms: list[str] = ()) -> str:
    """One line per regulation: title, scope, and how often its text mentions the product's terms."""
    lines = []
    for e in INDEX:
        paras = load_paragraphs(e["regulation"])
        scope = " ".join(p["text"] for p in paras if p["paragraph"].split(".")[0] == "1")
        scope = re.sub(r"\s+", " ", scope)[:700]
        text = " ".join(p["text"] for p in paras).lower()
        hits = [f"'{t}' x{n}" for t in terms if (n := text.count(t.lower()))]
        mentions = f" Mentions: {', '.join(hits)}." if hits else " Mentions: none of the product terms."
        lines.append(f"- {e['regulation']}: {e['title']}. Scope: {scope}{mentions}")
    return "\n".join(lines)


class SelectedRegulation(BaseModel):
    regulation: str
    reason: str


class Selection(BaseModel):
    regulations: list[SelectedRegulation]


SELECT_SYSTEM = """You are an automotive homologation engineer. Choose the UN Regulations whose requirements this
component or product must fulfil itself: its own type approval as a device, the installation rules that apply to it
on the vehicle, and horizontal regulations its technology triggers (for example electromagnetic compatibility when
it contains electronics). Prefer the current device regulation over an older one it replaced (for example UN R148,
R149 and R150 replaced R3, R6, R7, R19, R23, R38, R50, R77, R87, R91, R98, R112, R113, R119, R123). Leave out
vehicle-level regulations that do not set requirements on this product, but keep vehicle-level ones that test or
constrain this product directly (for example pedestrian protection tests on a bumper, external projections of a body
part). The list is graded recall-first: a missed regulation loses all of its requirements, a wrong one only adds
extras. Include every regulation you judge at least 40% likely to be on an expert's list, and no others.
Usually 2 to 7 regulations apply.
The drawing's labels can name values and tests (for example a luminous intensity, a spray test, vibration): make sure
the regulations that set them are in the list.
Each regulation lists how often its text mentions the product's terms. A regulation that mentions the product or its
parts only a handful of times in passing usually regulates something else (the whole vehicle, another device) and
does not belong in the list; one with a section dedicated to the product does."""


def select(profile: ProductProfile) -> Selection:
    valid = {e["regulation"] for e in INDEX}
    sel = ask(SELECT_SYSTEM, f"Product:\n{profile.model_dump_json(indent=1)}\n\nRegulations you may cite:\n{catalogue(profile.search_terms)}",
              Selection, effort="high")
    sel.regulations = [r for r in sel.regulations if r.regulation in valid]
    return sel


# ---------- 3. extract requirements ----------

class ExtractedRequirement(BaseModel):
    paragraph: str = Field(description="the paragraph label exactly as given in the input, e.g. '6.10.4.2' or 'Annex 8 2.1'")
    text: str = Field(description="one engineering obligation, close to the regulation's own wording, understandable on its own")
    test_case: str = Field(description="one sentence on how to verify it")


class Extraction(BaseModel):
    requirements: list[ExtractedRequirement]


EXTRACT_SYSTEM = """You are an automotive homologation engineer writing the requirement list for one product from one
UN Regulation. You get numbered paragraphs of the regulation. For every paragraph that sets an obligation the
product (or its installation on the vehicle, its markings, its approval documents) must fulfil, write the
requirements it contains. Experts grade the list against their own, so write it the way they do:
- Completeness: once a paragraph applies, EVERY obligation sentence in it becomes a requirement, not just the
  most important one. Work through the paragraph sentence by sentence, including its sub-items (a), (b), ...
- One requirement per "shall" (or "must", "shall not", "is not permitted") sentence. A sentence that sets limits for
  several separate devices or table rows becomes one requirement per device or row; several values or ranges of the
  same limit stay in one requirement. Never merge two sentences.
- Keep the regulation's wording for the obligation, its conditions, scope phrases and alternatives ("where a rear
  side-marker lamp is installed", "for M1 and N1 vehicles", "at the discretion of the manufacturer") and every device
  the sentence names, even ones this product lacks. Do not shorten, narrow or paraphrase, but write each requirement
  in the experts' form described in the WRITING GUIDE below (one "shall", explicit subject, condition first,
  exceptions folded in).
- Only resolve what a standalone sentence needs: replace "it", "this", "such devices" with the noun they refer to,
  and when a list item continues an introductory sentence ("The approval marking shall consist of: (a) ..."),
  write the full sentence.
- Include numeric limits, tolerances, colours, angles and referenced standards exactly as written.
- Only paragraphs about functions and parts this product has: skip paragraphs about other devices, other vehicle
  categories, definitions, scope, the approval authority's own duties, transitional provisions and communication forms.
- Markings (approval mark, trade name, symbols, arrows, "TOP", function codes), failure behaviour (e.g. what happens
  when a light source or LED fails) and general construction rules always apply to the device. So do the general
  design paragraphs that name no specific device: resistance to vibration and maladjustment, manufacturer logos,
  non-interchangeability of light sources or modules, grouped/combined/reciprocally incorporated lamps, corrosion.
- A sentence with two different obligations ("... and shall ...", two clauses joined by ";") becomes one
  requirement per obligation, each repeating the shared subject. Alternatives ("A and/or B", "A or B"), paired
  qualities of the same thing ("clear and permanent") and a number with its approval stay in ONE requirement
  (WRITING GUIDE rule 9).
- A lettered or numbered list ((a), (b), (i), (ii)) gives one requirement per item, and one per part the item names
  ("the light source module shall bear ..." and "the lamp shall bear ..." are two requirements).
- Conditional sentences ("where replaceable light source modules are used", "in case of", "if the lamp has", optional
  lamps) inside a section that applies are included even when the product description does not mention that
  feature: the expert list covers them, because the design may still change. A case that adds its own obligation for
  another device or situation ("however, where optional lamps are installed, they shall ...") is a requirement of its
  own, next to the main rule (WRITING GUIDE rule 10).
- When unsure whether a sentence applies, include it: a missed requirement costs four times more than an extra one.
- The product's drawing_labels name values and tests the designers already check (for example "no water ingress after
  the spray test", "stays aimed under vibration"): every paragraph of this regulation that sets such a requirement
  must be covered.
- Focus on what the product must BE or DO: construction, markings, photometry, colour, dimensions, installation
  position and visibility, resistance and durability, electrical behaviour, and the test methods and test levels the
  product must pass. Skip the paperwork (application documents, number of samples to submit, notification of
  modifications, conformity-of-production sampling) and laboratory set-up details that set no limit on the product.
- Cite the paragraph label exactly as it appears in square brackets.
Return an empty list if no paragraph in this extract applies."""


def _skip(p: dict) -> bool:
    label = p["paragraph"]
    if label == "front matter" or label == "Annex 1" or label.startswith("Annex 1 ") or len(p["text"]) < 3:
        return True
    return label.split(".")[0] in ("1", "2")  # scope and definitions


def _chunks(paras: list[dict], limit: int = CHUNK_CHARS) -> list[list[dict]]:
    out, cur, size = [], [], 0
    for p in paras:
        if cur and size + len(p["text"]) > limit:
            out.append(cur); cur, size = [], 0
        cur.append(p); size += len(p["text"]) + 20
    return out + ([cur] if cur else [])


EXAMPLES_INTRO = """

EXPERT EXAMPLES. Below is how Certivity's regulatory experts wrote their own requirement list for a different product
(a vehicle rear lamp) from other regulations. For complete sections it shows every paragraph, with what the experts
wrote from it, or that they wrote nothing. Your list is graded against the same experts, so study their taste before
you start: which paragraphs they use and which they skip (headings, "no special requirement", other vehicle
categories, lab set-up, procedure text), how many requirements they make from one paragraph, how they split lists and
conditions, and how closely they keep the regulation wording. Where these examples and the rules above disagree,
follow the examples.
"""


def _system_for(regulation: str) -> str:
    system = EXTRACT_SYSTEM + "\n\nWRITING GUIDE\n" + WRITING_GUIDE
    if not USE_EXAMPLES:
        return system
    examples = json.loads(EXAMPLES_FILE.read_text())
    # never show the experts' answer for the regulation being extracted, so the rear lamp stays a fair test
    shown = "\n".join(text for reg, text in examples.items() if reg != regulation)
    return system + EXAMPLES_INTRO + shown


def _known(text: str, have: list[dict], cutoff: float = 0.6) -> bool:
    low = text.lower()
    return any(difflib.SequenceMatcher(None, low, h["text"].lower()).ratio() >= cutoff for h in have)


def extract(profile: ProductProfile, regulation: str, reason: str, log: Log) -> list[dict]:
    paras = [p for p in load_paragraphs(regulation) if not _skip(p)]
    pages = {}
    for p in paras:
        pages.setdefault(p["paragraph"], p["page"])
    version = base_version(regulation)
    system = _system_for(regulation)

    def run(chunk: list[dict], review: bool = False) -> list[dict]:
        body = "\n\n".join(f"[{p['paragraph']}] (page {p['page']})\n{p['text']}" for p in chunk)
        note = ("\n\nThese paragraphs were skipped in a first pass although paragraphs next to them were used. "
                "Look again: write the obligations that do apply to this product, and skip the rest." if review else "")
        res = ask(system, f"Product:\n{profile.model_dump_json(indent=1)}\n\n"
                  f"Why {regulation} applies: {reason}\n\nParagraphs of {regulation}:\n\n{body}{note}", Extraction,
                  cache=USE_EXAMPLES)
        out = []
        for r in res.requirements:
            label = r.paragraph.strip().strip("[]").rstrip(".")
            if label not in pages:
                continue  # the model cited a paragraph that is not in this extract
            out.append({"text": r.text.strip()[:2000], "test_case": r.test_case.strip(),
                        "source": {"regulation": regulation, "version": version,
                                   "page": pages[label], "paragraph": label}})
        return out

    chunks = _chunks(paras)
    results = run(chunks[0]) if chunks else []  # first call alone, so the cached examples are reused by the rest
    with ThreadPoolExecutor(max_workers=8) as pool:
        results += [r for part in pool.map(run, chunks[1:]) for r in part]
    for n in range(1, PASSES):
        with ThreadPoolExecutor(max_workers=8) as pool:
            again = [r for part in pool.map(run, _chunks(paras, CHUNK_CHARS * 3 // (3 + 2 * n))) for r in part]
        new = [r for r in again if not _known(r["text"], results)]
        results += new
        log(f"{regulation}: pass {n + 1} added {len(new)} requirements the first pass missed")

    # Reviewer pass: paragraphs skipped next to ones that were used often hold obligations that were missed.
    used = {r["source"]["paragraph"] for r in results}
    parent = lambda label: label.rsplit(".", 1)[0] if "." in label else label
    used_parents = {parent(u) for u in used}
    siblings = [p for p in paras if p["paragraph"] not in used and parent(p["paragraph"]) in used_parents]
    if siblings:
        with ThreadPoolExecutor(max_workers=8) as pool:
            extra = [r for part in pool.map(lambda c: run(c, review=True), _chunks(siblings)) for r in part]
        results += extra
        log(f"{regulation}: reviewer pass re-read {len(siblings)} skipped paragraphs and added {len(extra)}")
    log(f"{regulation}: {len(results)} requirements from {len(paras)} paragraphs")
    return results


# ---------- 4. polish the wording ----------

class PolishedItem(BaseModel):
    id: str
    texts: list[str] = Field(description="the rewritten requirement(s); empty if folded into another item")


class Polished(BaseModel):
    items: list[PolishedItem]


POLISH_SYSTEM = """You rewrite draft requirements so they follow the WRITING GUIDE of the regulatory experts who grade
them. The drafts are grouped under the regulation paragraph they come from, with the paragraph's source text.
Rewrite EVERY draft: one sentence, one "shall", explicit subject, EARS condition first (While / When / Where /
If ... then / For ...), exceptions and relaxations folded in with "except that" / "unless", permissions turned into
obligations or folded in, the regulation's own wording, values and references kept. Do not change WHAT is required
and do not add obligations that no draft of that paragraph covers.
- A draft that is only an exception, relaxation or permission belonging to another draft of the same paragraph:
  fold it into that draft and return no text for it. But a case that adds its own obligation for another device or
  situation (rule 10) stays a requirement of its own; if a draft folded such a case into "except that", return it
  as an extra sentence of that draft.
- A draft that still holds two different obligations: return two sentences for it.
- Drafts that split ONE obligation into pieces (rule 9: paired qualities, a number and its approval, several values
  of the same limit, alternatives): return the joined sentence for the first and no text for the others.
- Two drafts of the same paragraph that state the same obligation: keep one, return no text for the other.
Return every id you were given.

WRITING GUIDE
""" + WRITING_GUIDE

POLISH_BATCH = 120


def polish(reqs: list[dict], log: Log) -> list[dict]:
    texts: dict[tuple[str, str], str] = {}
    for reg in {r["source"]["regulation"] for r in reqs}:
        for p in load_paragraphs(reg):
            texts.setdefault((reg, p["paragraph"]), " ".join(p["text"].split()))
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in reqs:
        groups.setdefault((r["source"]["regulation"], r["source"]["paragraph"]), []).append(r)
    batches, cur = [], []
    for key, items in groups.items():
        if cur and sum(len(g[1]) for g in cur) + len(items) > POLISH_BATCH:
            batches.append(cur); cur = []
        cur.append((key, items))
    if cur:
        batches.append(cur)

    def run(batch: list) -> dict[str, list[str]]:
        lines = []
        for (reg, label), items in batch:
            lines.append(f"=== {reg} [{label}] SOURCE: {texts.get((reg, label), '')[:2500]}")
            lines += [f"  {r['id']}: {r['text']}" for r in items]
        try:
            res = ask(POLISH_SYSTEM, "\n".join(lines), Polished, cache=True)
        except Exception as e:  # a failed batch keeps its drafts as they are
            log(f"polish: one batch failed ({e}); keeping its drafts")
            return {}
        return {i.id: [t.strip()[:2000] for t in i.texts if t.strip()] for i in res.items}

    done: dict[str, list[str]] = {}
    if batches:
        done.update(run(batches[0]))  # first call alone, so the cached guide is reused by the rest
        with ThreadPoolExecutor(max_workers=6) as pool:
            for part in pool.map(run, batches[1:]):
                done.update(part)
    out = []
    for r in reqs:
        for t in done.get(r["id"], [r["text"]]):
            out.append({**r, "text": t})
    log(f"Polished the wording: {len(reqs)} drafts became {len(out)} requirements")
    return out


# ---------- 5. assemble ----------

def run_case(case_dir: Path, team_id: str, log: Log = lambda m: print(m, file=sys.stderr)) -> dict:
    log("Reading the specification and drawing ...")
    profile = understand(case_dir)
    log(f"Product: {profile.product_type}; parts: {', '.join(profile.parts)}")
    log("Choosing the applicable regulations ...")
    selection = select(profile)
    for r in selection.regulations:
        log(f"  {r.regulation}: {r.reason}")
    reqs: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for part in pool.map(lambda r: extract(profile, r.regulation, r.reason, log), selection.regulations):
            reqs.extend(part)
    if POLISH:
        for i, r in enumerate(reqs, 1):
            r["id"] = f"D-{i:04d}"
        log("Polishing the wording in the experts' style ...")
        reqs = polish(reqs, log)
    seen, unique = set(), []
    for r in reqs:
        key = (r["source"]["regulation"], r["source"]["paragraph"], re.sub(r"\W+", " ", r["text"].lower()).strip())
        if key not in seen:
            seen.add(key); unique.append(r)
    for i, r in enumerate(unique, 1):
        r["id"] = f"R-{i:04d}"
    return {"team_id": team_id, "case_id": case_dir.name,
            "requirements": [{"id": r["id"], "text": r["text"], "source": r["source"], "test_case": r["test_case"]}
                             for r in unique],
            "_profile": profile.model_dump(), "_selection": selection.model_dump()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir", type=Path)
    ap.add_argument("--team", default="team-03")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    result = run_case(args.case_dir, args.team)
    out = args.out or ROOT / "outputs" / f"{args.case_dir.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    submission = {k: v for k, v in result.items() if not k.startswith("_")}
    out.write_text(json.dumps(submission, ensure_ascii=False, indent=1))
    saved = ROOT / "saved_runs" / f"{args.case_dir.name}.details.json"  # the demo app can replay this for free
    saved.parent.mkdir(exist_ok=True)
    saved.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"wrote {out} with {len(submission['requirements'])} requirements", file=sys.stderr)


if __name__ == "__main__":
    main()
