"""Sub-goal: which requirements change between two versions of a regulation, and why.

    python agent/subgoal.py subgoal/dev --team team-03          # writes outputs/subgoal-dev.json

Paragraphs of old.pdf and new.pdf are compared by label. Every paragraph whose text changed or that is
new goes to the model together with the old requirements that cite it; the model says which of those
requirements are modified or removed, and which new requirements the new text adds.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm import ask  # noqa: E402
from parse_pdfs import parse_pdf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _ignored(label: str) -> bool:
    top = label.split(".")[0]
    return label in ("front matter", "0") or top in ("1", "2") or label.startswith("Annex 1")


class Change(BaseModel):
    requirement_id: str | None
    change: Literal["modified", "removed", "added"]
    paragraph: str = Field(description="label of the paragraph in the NEW version that the change comes from (for removed: the old label)")
    new_text: str | None
    reason: str


class Changes(BaseModel):
    changes: list[Change]


SYSTEM = """You compare one section of two versions of a UN Regulation and update the requirement list an engineering
team derived from the old version. Paragraphs may have been renumbered, split or moved within the section, so judge
by content, not by number.

For each old requirement listed, find the sentence it was derived from in the new version and compare the two word
by word:
- "modified" if any word of that sentence changed: a value, unit, reference, scope, condition, or even a single word
  added, removed or replaced (for example "each lamp" becoming "each rear lamp", or "the sample" becoming "the
  relevant sample"). Check moved and renumbered text just as carefully: a list moved to a new paragraph can carry a
  one-word change in one of its items. Give the full new requirement text: the old requirement with the changes
  applied and its old wording kept otherwise. The new_text holds that requirement's own obligation only: a new
  sentence or paragraph found next to it, even a new exemption or exception to it, is a separate "added"
  requirement, never folded into a modified one (the WRITING GUIDE's advice to fold exceptions in applies to text
  that was already there, not to text new in this version);
- not modified if the sentence's own words are unchanged, even when it moved, was renumbered, or now sits under a new
  heading or in a new sub-paragraph (a new heading such as "Daytime use" above unchanged text is not a change).
  The same holds for the shared lead-in of a list: when only the lead-in changed (for example it now names a
  narrower case), the items under it are not modified; flag an item only when its own words changed;
  the same value written another way ("10 seconds" / "10 s", "50%" / "50 per cent"), punctuation, typography and
  corrected typing errors are not changes either;
- "removed" only if the obligation no longer exists anywhere in the new section;
- when unsure whether the words changed, flag it: a missed change costs more than an extra one.

Then list as "added" (requirement_id null) every obligation in the new section that is new in this version and that
no old requirement covers:
- write them the way the old requirements are written, at the same granularity and in the same style: the old list
  shows the team's conventions. A new list of steps or of separate obligations ((a), (b), ...) gives one requirement
  per step or item, each repeating the shared lead-in. A list of alternative conditions or exemptions ("is not
  required when: (a) ...; or (b) ...") stays one requirement. New items inserted into a list that an old requirement
  already contains (a further condition in its list of conditions) change that requirement: report it as modified
  with the new items in its new_text, not as added. Follow the WRITING GUIDE below as well;
- one obligation per requirement: a sentence with two obligations gives two requirements;
- new test procedures, test targets and test conditions count as obligations too;
- do not re-add what the old version already contained: if the old text already had the obligation (even with a
  different reference, number or wording) and no old requirement covers it, the team chose to leave it out, so leave
  it out too; likewise skip new sentences added to a paragraph the old list does not cite at all;
- skip statements that only say who decides, with no value or criterion ("is to be determined by the technical
  service").

The reason says what changed in the regulation, naming the paragraph and the version, and quoting the changed words.
Keep each new_text under 1,500 characters.

WRITING GUIDE
""" + (Path(__file__).resolve().parent / "writing_guide.txt").read_text()


def _section(label: str, depth: int = 1) -> str:
    """Group paragraphs by their top level ('6.5.3' -> '6', or '6.5' at depth 2), annexes by annex and part.
    Whole top-level sections are compared so that text moved from 6.5 to 6.6 is still recognised."""
    if label.startswith("Annex"):
        return " ".join(label.split()[:4 if " Part " in label else 2])
    return ".".join(label.split(".")[:depth])


def compare(old_pdf: Path, new_pdf: Path, requirements_old: list[dict], new_version: str) -> list[dict]:
    old_paras = [p for p in parse_pdf(old_pdf) if not _ignored(p["paragraph"])]
    new_paras = [p for p in parse_pdf(new_pdf) if not _ignored(p["paragraph"])]
    old = {p["paragraph"]: p for p in old_paras}
    new = {p["paragraph"]: p for p in new_paras}
    regulation = requirements_old[0]["source"]["regulation"] if requirements_old else "the regulation"

    # sections too long to send whole are split one level deeper
    size: dict[str, int] = {}
    for p in new_paras:
        size[_section(p["paragraph"])] = size.get(_section(p["paragraph"]), 0) + len(p["text"])
    sec_of = lambda label: _section(label, 2) if size.get(_section(label), 0) > 80000 else _section(label)

    changed_sections = set()
    for label, p in new.items():
        if label not in old or _norm(old[label]["text"]) != _norm(p["text"]):
            changed_sections.add(sec_of(label))
    for label in old:
        if label not in new:
            changed_sections.add(sec_of(label))

    def text_of(paras: list[dict], sec: str) -> str:
        return "\n\n".join(f"[{p['paragraph']}] {p['text']}" for p in paras if sec_of(p["paragraph"]) == sec)

    def run(sec: str) -> list[dict]:
        reqs = [r for r in requirements_old if sec_of(str(r["source"].get("paragraph", "")).rstrip(".")) == sec]
        prompt = (f"{regulation}, section {sec}. New version: {new_version}.\n\n"
                  f"OLD VERSION OF THE SECTION:\n{text_of(old_paras, sec) or '(section did not exist)'}\n\n"
                  f"NEW VERSION OF THE SECTION:\n{text_of(new_paras, sec) or '(section deleted)'}\n\n"
                  "OLD REQUIREMENTS FROM THIS SECTION:\n"
                  + ("\n".join(f"{r['id']} [{r['source'].get('paragraph')}]: {r['text']}" for r in reqs) or "(none)"))
        valid = {r["id"] for r in reqs}
        out = []
        for c in ask(SYSTEM, prompt, Changes, effort="high").changes:
            if c.change == "added":
                c.requirement_id = None
            elif c.requirement_id not in valid:
                continue
            if c.change != "removed" and not c.new_text:
                continue
            label = c.paragraph.strip().strip("[]").rstrip(".")
            where = new.get(label) or old.get(label)
            item = {"requirement_id": c.requirement_id, "change": c.change, "reason": c.reason[:2000],
                    "source": {"page": where["page"], "paragraph": label} if where else {}}
            if not item["source"]:
                del item["source"]
            if c.change != "removed":
                item["new_text"] = c.new_text[:2000]
            out.append(item)
        return out

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [c for part in pool.map(run, sorted(changed_sections)) for c in part]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir", type=Path, help="e.g. subgoal/dev")
    ap.add_argument("--team", default="team-03")
    ap.add_argument("--case-id", help="defaults to subgoal-<folder name>")
    ap.add_argument("--new-version", default="the new version")
    args = ap.parse_args()
    reqs = json.loads((args.case_dir / "requirements_old.json").read_text())
    changes = compare(args.case_dir / "old.pdf", args.case_dir / "new.pdf", reqs, args.new_version)
    case_id = args.case_id or f"subgoal-{args.case_dir.name}"
    out = ROOT / "outputs" / f"{case_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"team_id": args.team, "case_id": case_id, "changes": changes}, ensure_ascii=False, indent=1))
    saved = ROOT / "saved_runs" / out.name  # the demo app can replay this for free
    saved.parent.mkdir(exist_ok=True)
    saved.write_text(out.read_text())
    print(f"wrote {out} with {len(changes)} changes", file=sys.stderr)


if __name__ == "__main__":
    main()
