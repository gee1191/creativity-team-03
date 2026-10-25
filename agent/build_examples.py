"""Turn the experts' own answers into style examples for extraction (python agent/build_examples.py).

Sources: the worked example (cases/dev-rear-lamp/expected.json, four regulations) and the sub-goal example
(subgoal/dev/requirements_old.json, UN R175). For chosen complete sections, every paragraph is listed with what the
experts wrote from it, or that they wrote nothing; every other paragraph the experts used follows with its
requirements. The extraction prompt shows these so the model copies the experts' taste: which paragraphs they use,
how finely they split, how closely they keep the wording.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / "cases" / "dev-rear-lamp" / "expected.json"
SUBGOAL = ROOT / "subgoal" / "dev" / "requirements_old.json"
OUT = Path(__file__).resolve().parent / "expert_examples.json"

# Sections chosen to show both what experts take and what they leave out.
WINDOWS = {
    "UN R48": [("6.10", "section 6.10 (rear position lamps)"), ("5", "section 5 (general specifications)")],
    "UN R10": [("6", "section 6 (specifications for electronic sub-assemblies)"), ("Annex 9", "Annex 9 (immunity test method)")],
    "UN R148": [("3", "section 3 (application, markings)")],
    "UN R150": [("4", "section 4 (general specifications)"), ("Annex 6 Part 4", "Annex 6 Part 4 (corrosion test)")],
    # a regulation about the product itself (a vehicle system), shown in full apart from scope and definitions
    "UN R175": [(str(n), f"section {n}") for n in range(3, 10)] + [(f"Annex {n}", f"Annex {n}") for n in (1, 2, 3)],
}
PARSED_NAME = {"UN R175": "subgoal_dev__old"}
INTRO = {
    "UN R175": "UN R175 (acceleration control for pedal error) is about the regulated system itself, so the experts "
               "also list application, approval and conformity paragraphs there. Product: the ACPE system of a car.",
}


def _norm(label: str) -> str:
    return re.sub(r"[\s,]+", " ", str(label).lower()).strip(" .")


def _in(label: str, prefix: str) -> bool:
    return label == prefix or label.startswith(prefix + ("." if prefix[0].isdigit() else " "))


def _paragraph(p: dict, reqs: list[str]) -> list[str]:
    text = re.sub(r"\s+", " ", p["text"])
    if reqs:
        return [f"[{p['paragraph']}] {text[:1500]}\n  EXPERTS WROTE:"] + [f"   - {r}" for r in reqs]
    return [f"[{p['paragraph']}] {text[:110]}{'...' if len(text) > 110 else ''}\n  experts wrote nothing"]


def build() -> dict[str, str]:
    expected = json.loads(EXPECTED.read_text())["requirements"]
    if SUBGOAL.exists():
        expected += [dict(r, source=dict(r["source"], regulation="UN R175")) for r in json.loads(SUBGOAL.read_text())]
    out = {}
    for reg, windows in WINDOWS.items():
        wrote = defaultdict(list)
        for e in expected:
            if e["source"]["regulation"] == reg:
                wrote[_norm(e["source"].get("paragraph", ""))].append(e["text"])
        if not wrote:
            continue
        name = PARSED_NAME.get(reg, f"{reg.replace(' ', '_')}__base")
        paras = json.loads((ROOT / "data" / "parsed" / f"{name}.json").read_text())
        lines, shown = [], set()
        if reg in INTRO:
            lines.append(f"\n({INTRO[reg]})")
        for prefix, title in windows:
            lines.append(f"\n### {reg}: {title}, complete\n")
            for p in paras:
                if _in(p["paragraph"], prefix):
                    lines += _paragraph(p, wrote.get(_norm(p["paragraph"])))
                    shown.add(_norm(p["paragraph"]))
        rest = [p for p in paras if _norm(p["paragraph"]) in wrote and _norm(p["paragraph"]) not in shown]
        if rest:
            lines.append(f"\n### {reg}: every other paragraph the experts used (paragraphs between them were skipped)\n")
            for p in rest:
                lines += _paragraph(p, wrote[_norm(p["paragraph"])])
                shown.add(_norm(p["paragraph"]))
        # labels such as "5.1.4.4.1. Table 3" that are not parsed paragraphs of their own
        extra = [(k, v) for k, v in wrote.items() if k not in shown]
        for label, reqs in extra:
            lines.append(f"[{label}] (a table or item inside a paragraph)\n  EXPERTS WROTE:")
            lines += [f"   - {r}" for r in reqs]
        out[reg] = "\n".join(lines)
    return out


if __name__ == "__main__":
    examples = build()
    OUT.write_text(json.dumps(examples, ensure_ascii=False, indent=1))
    for reg, text in examples.items():
        print(f"{reg}: {len(text)} characters")
