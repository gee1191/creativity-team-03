"""Split every regulation PDF into numbered paragraphs, each with the page it starts on.

    python agent/parse_pdfs.py            # writes data/parsed/<regulation>__<role>.json

Each record: {"paragraph": "Annex 6 Part 4 1", "page": 45, "text": "..."}.
Page numbers are the 1-based page of the PDF file, which is what the scoring uses.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "regulations" / "pdf"
OUT_DIR = ROOT / "data" / "parsed"

HEADER = re.compile(r"^UN (R|GTR) ?\d+.*\| page \d+ of \d+$")
NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,3})*)\.?(?:\s+(.*))?$")
ANNEX = re.compile(r"^Annex\s+(\d{1,2}[A-Z]?)(?:\s*[-–—:]?\s*(.*))?$")
RESTART = re.compile(r"^(?:[IVX]+\.|[A-Z]\.)?\s*Text of the (?:global technical )?regulation\s*$", re.I)
# a line ending like this continues into the next line, so a number there is a cross-reference, not a paragraph
# ("paragraphs 5.1.1. and" + "5.1.2. ..." is a reference; "...; and" + "5.21.2. ..." is the next item of a list)
CONTINUES = re.compile(r"(?i)(\b(paragraphs?|para\.|of|in|to|see|under|with|annex|regulation no\.)|\d\.? (and|or))$")
VERB_START = re.compile(r"^(shall|should|may|must|is|are|and|or|of|to|apply|applies|above|below|need|has|have|will)\b")
PART = re.compile(r"^Part\s+(\d{1,2}|[IVX]+)\b\s*[-–—:]?\s*(.*)$")


def _plausible_next(prev: str | None, cand: str, text: str | None) -> bool:
    """Reject stray numbers (table cells, values, cross-references) that are not a paragraph start."""
    c = [int(x) for x in cand.split(".")]
    if len(c) == 1 and not re.match(r"^[A-Z(\"“][A-Za-z]{2,}", text or ""):
        return False  # a top-level heading has words, not "7 V/m" or "10 pulses"
    if prev is None:
        return c[0] <= 2 or len(c) <= 2
    p = [int(x) for x in prev.split(".")]
    if c[0] == p[0]:
        return c > p  # numbering only moves forward: "5.4.1" after "5.5" is a cross-reference at a line start
    if c[0] == p[0] + 1:
        return len(c) == 1 or c[1:] in ([1], [0])  # a new section starts at N or N.1
    if c[0] == p[0] + 2:
        return len(c) == 1  # a skipped (deleted) section, but only on a heading line
    return False  # paragraphs never go back to an earlier section


def _annex_no(label: str) -> int:
    return int(re.match(r"\d+", label).group())


def parse_pdf(path: Path) -> list[dict]:
    doc = pymupdf.open(path)
    records: list[dict] = []
    annex, part = "", ""
    annex_no = 0
    last_num: str | None = None
    current: dict | None = None

    def start(label: str, page: int, text: str):
        nonlocal current
        current = {"paragraph": label, "page": page, "text": text.strip()}
        records.append(current)

    prev_line = ""
    for pno, page in enumerate(doc, start=1):
        for raw in page.get_text().splitlines():
            line = raw.strip()
            if not line or HEADER.match(line):
                continue
            after_ref, prev_line = bool(CONTINUES.search(prev_line)), line
            if RESTART.match(line):
                # GTRs: a long statement of rationale, then the regulation text numbered from 1 again
                annex, part, last_num, annex_no = "", "", None, 0
                records.clear()  # the rationale part sets no requirements and reuses the same numbers
                start("front matter", pno, line)
                continue
            m = ANNEX.match(line)
            if (m and len(line) < 140 and (not m.group(2) or not re.search(r"\.{4,}|\s\d{2,3}$", line))
                    and not re.match(r"[a-z]", m.group(2) or "")  # "Annex 1 to this Regulation": a cross-reference
                    and not line[len("Annex " + m.group(1)):].startswith((".", ",", ")"))
                    and _annex_no(m.group(1)) in (annex_no + 1, annex_no)
                    and f"Annex {m.group(1)}" != annex):
                annex, part, last_num = f"Annex {m.group(1)}", "", None
                annex_no = _annex_no(m.group(1))
                start(annex, pno, line)
                continue
            m = PART.match(line)
            if m and annex and len(line) < 140:
                part, last_num = f"Part {m.group(1)}", None
                start(f"{annex} {part}", pno, line)
                continue
            m = NUMBERED.match(line)
            if m and after_ref:
                m = None  # "... the requirements of paragraph" + "5.1.1. shall be applied": a cross-reference
            if m and m.group(2) and VERB_START.match(m.group(2)):
                m = None
            if m and (m.group(2) is None or not re.match(r"^(mm|cm|per|%|cd|°|lx|m\b)", m.group(2))):
                num = m.group(1)
                if (num == "1" and not annex and last_num and "." not in last_num and int(last_num) >= 3
                        and all("." not in r["paragraph"] for r in records if r["paragraph"][:1].isdigit())):
                    # an introduction numbered 1, 2, 3 ... then the regulation text numbered from 1 again
                    records.clear()  # like a GTR rationale, the introduction sets no requirements
                    last_num = None
                if _plausible_next(last_num, num, m.group(2)):
                    last_num = num
                    prefix = " ".join(x for x in (annex, part) if x)
                    start(f"{prefix} {num}".strip(), pno, m.group(2) or "")
                    continue
            if current is None:
                start("front matter", pno, line)
            else:
                current["text"] += ("\n" if current["text"] else "") + line
    return records


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(PDF_DIR.glob("*/*/*.pdf")) + sorted((ROOT / "subgoal").glob("*/*.pdf"))
    for pdf in pdfs:
        if pdf.parent.parent.name == "subgoal" or pdf.parent.parent.parent.name == "subgoal":
            name = f"subgoal_{pdf.parent.name}__{pdf.stem}"
        else:
            name = f"{pdf.parent.parent.name}__{pdf.parent.name}"
        recs = parse_pdf(pdf)
        (OUT_DIR / f"{name}.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1))
        print(f"{name}: {len(recs)} paragraphs", file=sys.stderr)


if __name__ == "__main__":
    main()
