"""Certivity requirements agent: the demo app.

    streamlit run app.py

Pick a case (or upload a specification and drawing), press Run, and get the requirements the product must
fulfil, each linked to the page of the regulation it comes from.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "tools"))
import pipeline  # noqa: E402
import subgoal  # noqa: E402
from fetch_regulations import fetch_one  # noqa: E402

STATIC = ROOT / "static"
INDEX_JSON = json.loads((ROOT / "regulations" / "index.json").read_text())
TITLES = {r["regulation"]: r.get("title", "") for r in INDEX_JSON}
# online copy of each base PDF, so page links work even where the PDFs were never downloaded
ONLINE = {r["regulation"]: v["files"][0]["pdf_url"] for r in INDEX_JSON for v in r["versions"] if v["role"] == "base"}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, .stApp, .stMarkdown, button, input, textarea, [data-baseweb] {font-family: 'Inter', system-ui, sans-serif;}
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"], #MainMenu, footer {display: none !important;}
.stApp {background-image:
    radial-gradient(rgba(15, 110, 90, .07) 1px, transparent 1px),
    radial-gradient(900px 520px at -5% 0%, rgba(43, 181, 154, .22) 0%, transparent 60%),
    radial-gradient(800px 520px at 105% 8%, rgba(90, 200, 240, .22) 0%, transparent 60%),
    radial-gradient(900px 600px at 50% 110%, rgba(150, 130, 230, .14) 0%, transparent 60%),
    linear-gradient(180deg, #ecf8f4 0%, #eef5fc 55%, #f3f1fb 100%);
    background-size: 22px 22px, auto, auto, auto, auto; background-attachment: fixed;}
.block-container {padding-top: 1.4rem; max-width: 1360px;}
.brand {display: flex; align-items: center; gap: 12px;}
.logo {width: 42px; height: 42px; border-radius: 12px; display: flex; align-items: center; justify-content: center;
       background: linear-gradient(135deg, #0f6e5a, #2bb59a 60%, #5ac8f0); color: #fff; font-weight: 800; font-size: 21px;
       box-shadow: 0 8px 18px -6px rgba(15, 110, 90, .55);}
.brand .t1 {font-weight: 800; font-size: 1.68rem; color: #0d1b17; letter-spacing: -.01em; line-height: 1.2;}
.hero {position: relative; overflow: hidden; border-radius: 24px; padding: 36px 40px; margin: 16px 0 26px 0; color: #fff;
       background: linear-gradient(118deg, #062f27 0%, #0f6e5a 42%, #1f9c83 72%, #39b3d4 100%);
       box-shadow: 0 24px 50px -24px rgba(6, 47, 39, .7);}
.hero:before {content: ""; position: absolute; width: 460px; height: 460px; right: -140px; top: -190px;
              background: radial-gradient(circle, rgba(255, 255, 255, .25), transparent 65%);}
.hero:after {content: ""; position: absolute; width: 300px; height: 300px; left: 38%; bottom: -220px;
             background: radial-gradient(circle, rgba(90, 200, 240, .35), transparent 65%);}
.hero h1 {color: #fff; font-size: 2.35rem; font-weight: 800; letter-spacing: -.025em; margin: 0 0 10px 0; padding: 0;}
.hero p {color: #d7f2ea; font-size: 1.06rem; margin: 0; max-width: 840px; line-height: 1.55;}
.chips {display: flex; gap: 10px; margin-top: 20px; flex-wrap: wrap; position: relative; z-index: 1;}
.chip {background: rgba(255, 255, 255, .13); border: 1px solid rgba(255, 255, 255, .26); backdrop-filter: blur(6px);
       border-radius: 999px; padding: 7px 15px; font-size: .86rem; color: #fff;}
.chip b {font-weight: 800;}
.section {display: flex; align-items: center; gap: 12px; margin: 30px 0 14px 0;}
.section .num {width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
               background: linear-gradient(135deg, #0f6e5a, #2bb59a); color: #fff; font-weight: 700; font-size: .9rem;
               box-shadow: 0 6px 14px -4px rgba(15, 110, 90, .5);}
.section .txt {font-size: 1.3rem; font-weight: 800; color: #0d1b17; letter-spacing: -.015em;}
.section .sub {color: #64748b; font-size: .92rem;}
.card {background: #fff; border: 1px solid #e5ebe9; border-radius: 18px; padding: 18px 20px; height: 100%;
       box-shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 10px 28px -14px rgba(16, 24, 40, .16);
       transition: transform .15s ease, box-shadow .15s ease;}
.card:hover {transform: translateY(-3px); box-shadow: 0 2px 4px rgba(16, 24, 40, .06), 0 18px 34px -14px rgba(16, 24, 40, .25);}
.card .name {font-weight: 800; color: #0f6e5a; font-size: 1.08rem;}
.card .title {color: #64748b; font-size: .82rem; margin: 2px 0 10px 0;}
.card .why {font-size: .88rem; color: #334155; line-height: 1.5;}
.card .count {float: right; background: linear-gradient(135deg, #0f6e5a, #2bb59a); color: #fff; border-radius: 999px;
              padding: 2px 11px; font-size: .78rem; font-weight: 700;}
.stats {display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; margin-bottom: 14px;}
.stat {background: #fff; border: 1px solid #e5ebe9; border-radius: 18px; padding: 16px 20px;
       box-shadow: 0 10px 28px -16px rgba(16, 24, 40, .2);}
.stat .v {font-size: 2.05rem; font-weight: 800; letter-spacing: -.03em; line-height: 1.15;
          background: linear-gradient(135deg, #0b4f41, #1f9c83); -webkit-background-clip: text; background-clip: text;
          -webkit-text-fill-color: transparent;}
.stat.ok .v {background: linear-gradient(135deg, #15803d, #22c55e); -webkit-background-clip: text; background-clip: text;}
.stat.bad .v {background: linear-gradient(135deg, #b42318, #f04438); -webkit-background-clip: text; background-clip: text;}
.stat .l {color: #64748b; font-size: .85rem; font-weight: 500;}
.label {font-size: .72rem; text-transform: uppercase; letter-spacing: .09em; color: #64748b; font-weight: 700; margin: 0 0 8px 0;}
.req {border-left: 4px solid #1f9c83; background: #fff; border-radius: 14px; padding: 16px 20px; font-size: 1.04rem;
      line-height: 1.55; box-shadow: 0 10px 28px -16px rgba(16, 24, 40, .22);}
.src {background: #fffdf2; border: 1px solid #f0e2a4; border-radius: 14px; padding: 16px 20px; font-size: .92rem;
      white-space: pre-wrap; line-height: 1.55;}
.meta {color: #475569; font-size: .9rem; margin-top: 10px;}
.badge {display: inline-block; border-radius: 999px; padding: 2px 11px; font-size: .74rem; font-weight: 800; letter-spacing: .04em;
        color: #fff; margin-right: 8px;}
.b-modified {background: linear-gradient(135deg, #b54708, #f79009);}
.b-removed {background: linear-gradient(135deg, #b42318, #f04438);}
.b-added {background: linear-gradient(135deg, #067647, #17b26a);}
.old {background: #fef3f2; border: 1px solid #fecdca; border-radius: 12px; padding: 12px 14px; font-size: .9rem; line-height: 1.5;}
.new {background: #ecfdf3; border: 1px solid #abefc6; border-radius: 12px; padding: 12px 14px; font-size: .9rem; line-height: 1.5;}
.why2 {color: #64748b; font-size: .85rem; margin-top: 8px;}
.stButton > button, .stDownloadButton > button, [data-testid="stPopover"] button {border-radius: 12px; font-weight: 600;
       padding: .55rem 1.15rem; transition: all .15s ease; border: 1px solid #dfe6e4; background: #fff;}
.stButton > button[kind="primary"], button[data-testid="stBaseButton-primary"] {border: 0; color: #fff;
       background: linear-gradient(135deg, #0f6e5a, #22a58a); box-shadow: 0 10px 20px -10px rgba(15, 110, 90, .75);}
.stButton > button:hover {transform: translateY(-1px);}
[role="tablist"] {gap: 6px; background: #fff; padding: 6px; border-radius: 14px; border: 1px solid #e5ebe9;
       width: fit-content; box-shadow: 0 6px 18px -12px rgba(16, 24, 40, .3);}
div:has(> [role="tablist"]) {border-bottom: 0 !important; box-shadow: none !important;}
[data-testid="stTab"] {border-radius: 10px; padding: 8px 18px;}
[data-testid="stTab"][aria-selected="true"] {background: linear-gradient(135deg, #0f6e5a, #22a58a);}
[data-testid="stTab"][aria-selected="true"] p {color: #fff !important; font-weight: 700;}
.react-aria-SelectionIndicator {display: none !important;}
.st-key-product_box, .st-key-subgoal_box, [class*="st-key-chg"] {background: #fff; border-radius: 18px !important;
       box-shadow: 0 10px 28px -16px rgba(16, 24, 40, .2);}
[data-testid="stExpander"] details {border-radius: 14px; background: #fff;}
[data-testid="stDataFrame"] {border-radius: 14px; overflow: hidden; box-shadow: 0 10px 28px -18px rgba(16, 24, 40, .25);}
</style>
"""


def ensure_static_pdfs() -> None:
    """Streamlit serves ./static at app/static/... for the page links, but refuses symlinks, so every PDF is
    hard-linked there (no extra disk space), or copied where hard links are not possible."""
    STATIC.mkdir(exist_ok=True)
    for name, src_root, pattern in (("pdf", ROOT / "regulations" / "pdf", "*/base/*.pdf"),
                                    ("subgoal", ROOT / "subgoal", "*/*.pdf")):
        dest_root = STATIC / name
        if dest_root.is_symlink():
            dest_root.unlink()
        for src in src_root.glob(pattern):
            dest = dest_root / src.relative_to(src_root)
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src, dest)
            except OSError:
                shutil.copy2(src, dest)


def pdf_link(regulation: str, page: int) -> str:
    folder = regulation.replace(" ", "_")
    if (STATIC / "pdf" / folder / "base" / f"{folder}_base.pdf").exists():
        return f"app/static/pdf/{folder}/base/{folder}_base.pdf#page={page}"
    return f"{ONLINE.get(regulation, '')}#page={page}"


def subgoal_files(folder: Path) -> list[dict]:
    path = folder / "files.json"
    return json.loads(path.read_text()) if path.exists() else []


def subgoal_pdf_link(folder: Path, name: str, page: int) -> str:
    if (STATIC / "subgoal" / folder.name / name).exists():
        return f"app/static/subgoal/{folder.name}/{name}#page={page}"
    online = {f["name"]: f["pdf_url"] for f in subgoal_files(folder)}
    return f"{online[name]}#page={page}" if name in online else ""


def ensure_subgoal_pdfs(folder: Path, log) -> None:
    """The comparison reads old.pdf and new.pdf, so fetch the two (small) files if they are not here yet."""
    for f in subgoal_files(folder):
        if not (folder / f["name"]).exists():
            log(f"Downloading {f['name']} ...")
            fetch_one(dict(f, regulation=folder.name), folder, name=f["name"])


@st.cache_data
def paragraph_texts(regulation: str) -> dict[str, str]:
    """Paragraph label -> text of the base version, to show each requirement next to its source."""
    path = ROOT / "data" / "parsed" / f"{regulation.replace(' ', '_')}__base.json"
    texts: dict[str, str] = {}
    if path.exists():
        for p in json.loads(path.read_text()):
            texts[p["paragraph"]] = (texts.get(p["paragraph"], "") + "\n" + p["text"]).strip()
    return texts


def source_text(regulation: str, paragraph: str) -> str:
    texts = paragraph_texts(regulation)
    label = paragraph.split(" Table")[0].strip()
    text = texts.get(label) or texts.get(label.rstrip(".")) or texts.get(label + ".") or ""
    text = re.sub(r"\s+", " ", text)  # undo the PDF's line breaks, but start each list item on its own line
    return re.sub(r" (\((?:[a-z]|[ivx]+)\)) ", r"\n\1 ", text).strip()


def run_in_background(fn, *args):
    """Run fn in a thread and stream its log lines into the page while it works."""
    lines: list[str] = []
    box: dict = {}

    def target():
        try:
            box["result"] = fn(*args, log=lines.append)
        except Exception as exc:  # shown to the user below
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    with st.status("Working ...", expanded=True) as status:
        shown = 0
        while thread.is_alive() or shown < len(lines):
            while shown < len(lines):
                st.write(lines[shown])
                shown += 1
            time.sleep(0.4)
        if "error" in box:
            status.update(label="Failed", state="error")
            raise box["error"]
        status.update(label="Done", state="complete", expanded=False)
    return box["result"]


def section(num: str, title: str, sub: str = "") -> None:
    st.markdown(f'<div class="section"><div class="num">{num}</div><div class="txt">{html.escape(title)}</div>'
                f'<div class="sub">{html.escape(sub)}</div></div>', unsafe_allow_html=True)


def stat_tiles(tiles: list[tuple[str, str, str]]) -> None:
    """tiles: (value, label, style) with style '', 'ok' or 'bad'."""
    cells = "".join(f'<div class="stat {s}"><div class="v">{html.escape(str(v))}</div><div class="l">{html.escape(l)}</div></div>'
                    for v, l, s in tiles)
    st.markdown(f'<div class="stats">{cells}</div>', unsafe_allow_html=True)


st.set_page_config(page_title="Certivity Requirements Analysis Tool", page_icon="🛡️", layout="wide",
                   initial_sidebar_state="collapsed")
ensure_static_pdfs()
st.markdown(CSS, unsafe_allow_html=True)

team = "team-03"
st.markdown('<div class="brand"><div class="logo">C</div><div class="t1">Certivity Requirements Analysis Tool</div></div>',
            unsafe_allow_html=True)

st.markdown("""<div class="hero">
<h1>Every requirement your part must meet, traced to its source</h1>
<p>Upload a product specification and its exploded view. The tool finds the UN Regulations that apply, writes each
obligation as a testable requirement, and links every one to the exact page it comes from.</p>
</div>""", unsafe_allow_html=True)

main_tab, sub_tab = st.tabs(["📋  Requirements Analysis", "🔄  Change Analysis"])

with main_tab:
    section("1", "Documentation Upload", "an existing folder, or a new specification and drawing")
    with st.container(border=True, key="product_box"):
        cases = sorted(p.name for p in (ROOT / "cases").iterdir() if p.is_dir())
        source = st.radio("Input", ["Existing Folder", "Upload New"], horizontal=True, label_visibility="collapsed")
        case_dir: Path | None = None
        if source == "Existing Folder":
            name = st.selectbox("Folder", cases)
            case_dir = ROOT / "cases" / name
            cols = st.columns([3, 2])
            for md in sorted(case_dir.glob("*.md")):
                with cols[0].container(height=380, border=False):  # headings shrunk so the spec reads as a card
                    st.markdown(re.sub(r"^(#+)", lambda m: "#" * min(6, len(m.group(1)) + 3), md.read_text(), flags=re.M))
            for img in sorted(case_dir.glob("*.png")):
                cols[1].image(str(img), caption="Exploded view")
        else:
            spec = st.file_uploader("Specification (.md or .txt)", type=["md", "txt"])
            drawing = st.file_uploader("Exploded view (image)", type=["png", "jpg", "jpeg", "webp"])
            case_name = st.text_input("Case id", "my-product")
            if spec or drawing:
                case_dir = Path(tempfile.mkdtemp()) / case_name
                case_dir.mkdir()
                if spec:
                    (case_dir / "spec.md").write_bytes(spec.getvalue())
                if drawing:
                    (case_dir / f"exploded_view{Path(drawing.name).suffix.lower()}").write_bytes(drawing.getvalue())

        saved = sorted((ROOT / "saved_runs").glob("*.details.json"))
        left, right = st.columns([1, 2], vertical_alignment="bottom")
        if left.button("🚀  Run Analysis", type="primary", disabled=case_dir is None, width="stretch"):
            result = run_in_background(pipeline.run_case, case_dir, team)
            st.session_state["result"] = result
            st.session_state["removed_ids"] = set()
            # keep every paid run: outputs/ holds the file to upload, saved_runs/ lets the demo replay it for free
            name = f"{case_dir.name}-{time.strftime('%Y%m%d-%H%M')}"
            (ROOT / "outputs").mkdir(exist_ok=True)
            (ROOT / "saved_runs").mkdir(exist_ok=True)
            submission = {k: v for k, v in result.items() if not k.startswith("_")}
            (ROOT / "outputs" / f"{name}.json").write_text(json.dumps(submission, ensure_ascii=False, indent=1))
            (ROOT / "saved_runs" / f"{name}.details.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
            saved = sorted((ROOT / "saved_runs").glob("*.details.json"))

        def load_saved():
            if st.session_state["saved_pick"] is not None:
                st.session_state["result"] = json.loads(st.session_state["saved_pick"].read_text())
                st.session_state["removed_ids"] = set()

        if saved:
            # loads only when the choice changes, so it never replaces a run that just finished
            right.selectbox("Display A Saved Run (Free)", [None] + saved, key="saved_pick", on_change=load_saved,
                            label_visibility="collapsed",
                            format_func=lambda p: "📂  Display A Saved Run (Free)" if p is None
                            else p.name.replace(".details.json", ""))

    result = st.session_state.get("result")
    if result:
        submission = {k: v for k, v in result.items() if not k.startswith("_")}
        # requirements the user removed while reviewing: hidden on screen only, back after a page refresh
        removed = st.session_state.setdefault("removed_ids", set())
        reqs = [r for r in submission["requirements"] if r["id"] not in removed]
        per_reg = Counter(r["source"]["regulation"] for r in reqs)

        section("2", "The Regulations", "and why each one was chosen")
        regs_chosen = result.get("_selection", {}).get("regulations", []) or [{"regulation": r, "reason": ""} for r in per_reg]
        cols = st.columns(min(4, max(1, len(regs_chosen))))
        for i, r in enumerate(regs_chosen):
            reg = r["regulation"]
            cols[i % len(cols)].markdown(
                f'<div class="card"><span class="count">{per_reg.get(reg, 0)} reqs</span>'
                f'<div class="name">{html.escape(reg)}</div><div class="title">{html.escape(TITLES.get(reg, ""))}</div>'
                f'<div class="why">{html.escape(r.get("reason", ""))}</div></div>', unsafe_allow_html=True)

        section("3", "The requirements", "filter, search, and open the source page")
        stat_tiles([(len(reqs), "Requirements", ""), (len(per_reg), "Regulations", ""),
                    (len({(r["source"]["regulation"], r["source"]["page"]) for r in reqs}), "Pages cited", "")])
        rows = [{"id": r["id"], "regulation": r["source"]["regulation"], "paragraph": r["source"].get("paragraph", ""),
                 "requirement": r["text"], "test case": r.get("test_case", ""),
                 "source": pdf_link(r["source"]["regulation"], r["source"]["page"])} for r in reqs]
        regs = sorted({r["regulation"] for r in rows})
        f1, f2 = st.columns([2, 3])
        pick = f1.multiselect("Filter by regulation", regs, default=regs)
        query = f2.text_input("Search the requirements", placeholder="e.g. vibration, colour, approval mark")
        shown = [r for r in rows if r["regulation"] in pick and query.lower() in r["requirement"].lower()]
        table = pd.DataFrame(shown, columns=["id", "regulation", "paragraph", "requirement", "test case", "source"])
        table.insert(0, "remove", False)
        # a fresh key after every removal, so the table comes back with no box ticked
        edited = st.data_editor(table, key=f"reqs_{len(removed)}", width="stretch", hide_index=True, height=440,
                                disabled=[c for c in table.columns if c != "remove"], column_config={
            # narrow enough (about 1,000 px in all) to fit a laptop screen without sideways scrolling
            "remove": st.column_config.CheckboxColumn("🗑️", width=40,
                                                      help="Remove from this view (a page refresh brings it back)"),
            "id": st.column_config.TextColumn(width=70),
            "regulation": st.column_config.TextColumn(width=80),
            "paragraph": st.column_config.TextColumn(width=75),
            "requirement": st.column_config.TextColumn(width=400),
            "test case": st.column_config.TextColumn(width=255),
            "source": st.column_config.LinkColumn("page", display_text=r"page=(\d+)", width=60),
        })
        ticked = set(edited.loc[edited["remove"], "id"])
        if ticked:
            removed |= ticked
            st.rerun()
        d1, d2, _ = st.columns([1, 1, 2])
        d1.download_button("⬇️  Download submission JSON", json.dumps(submission, ensure_ascii=False, indent=1),
                           file_name=f"{submission['case_id']}.json", mime="application/json")
        if removed and d2.button(f"↩️  Restore {len(removed)} removed"):
            removed.clear()
            st.rerun()

        section("4", "Traceability Matrix", "each requirement next to the regulation's own words")
        by_id = {r["id"]: r for r in reqs}
        options = [r["id"] for r in shown] or list(by_id)
        rid = st.selectbox("Requirement", options, label_visibility="collapsed",
                           format_func=lambda i: f"{i} · {by_id[i]['source']['regulation']} §{by_id[i]['source'].get('paragraph', '')} · {by_id[i]['text'][:90]}")
        if rid:
            r = by_id[rid]
            s = r["source"]
            left, right = st.columns(2)
            with left:
                st.markdown('<div class="label">The Requirement</div>'
                            f'<div class="req">{html.escape(r["text"])}</div>', unsafe_allow_html=True)
                st.markdown(f"**{s['regulation']}** · version {s['version']} · paragraph **{s.get('paragraph', '?')}** · "
                            f"[open page {s['page']} ↗]({pdf_link(s['regulation'], s['page'])})")
            with right:
                text = source_text(s["regulation"], str(s.get("paragraph", "")))
                st.markdown('<div class="label">The Regulation</div>'
                            f'<div class="src">{html.escape(text or "(paragraph text not available)")}</div>',
                            unsafe_allow_html=True)

with sub_tab:
    section("↻", "Requirements Comparison", "which old requirements must change, and why")
    folders = sorted(p for p in (ROOT / "subgoal").iterdir() if (p / "requirements_old.json").exists())
    with st.container(border=True, key="subgoal_box"):
        folder = st.selectbox("Existing Folder", folders, format_func=lambda p: p.name)
        saved_changes = ROOT / "saved_runs" / f"subgoal-{folder.name}.json" if folder else None
        b1, b2 = st.columns([1, 1])
        if saved_changes and saved_changes.exists() and b2.button("📂  Display A Saved Comparison", width="stretch"):
            st.session_state["changes"] = json.loads(saved_changes.read_text())
        if b1.button("🚀  Run New Comparison", type="primary", width="stretch"):
            old = json.loads((folder / "requirements_old.json").read_text())

            def run(log):
                ensure_subgoal_pdfs(folder, log)
                log(f"Comparing old.pdf and new.pdf for {len(old)} requirements ...")
                return subgoal.compare(folder / "old.pdf", folder / "new.pdf", old, "the new version")

            changes = run_in_background(run)
            out = {"team_id": team, "case_id": f"subgoal-{folder.name}", "changes": changes}
            st.session_state["changes"] = out
            # keep every paid comparison in outputs/, ready to upload
            (ROOT / "outputs").mkdir(exist_ok=True)
            (ROOT / "outputs" / f"subgoal-{folder.name}-{time.strftime('%Y%m%d-%H%M')}.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=1))
    out = st.session_state.get("changes")
    if out and folder:
        old_reqs = {r["id"]: r for r in json.loads((folder / "requirements_old.json").read_text())}
        kinds = Counter(c["change"] for c in out["changes"])
        stat_tiles([(len(old_reqs), "Old requirements", ""), (kinds.get("modified", 0), "Modified", ""),
                    (kinds.get("removed", 0), "Removed", ""), (kinds.get("added", 0), "Added", "")])
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        show = c1.multiselect("Show", ["modified", "removed", "added"], default=["modified", "removed", "added"])
        c2.download_button("⬇️  Download changes JSON", json.dumps(out, ensure_ascii=False, indent=1),
                           file_name=f"{out['case_id']}.json", mime="application/json", width="stretch")
        for i, ch in enumerate(out["changes"]):
            if ch["change"] not in show:
                continue
            src = ch.get("source", {})
            link = subgoal_pdf_link(folder, "new.pdf", src["page"]) if src.get("page") else ""
            with st.container(border=True, key=f"chg{i}"):
                where = f" · new version §{src.get('paragraph', '')} · [open page {src['page']} ↗]({link})" if link else ""
                st.markdown(f'<span class="badge b-{ch["change"]}">{ch["change"].upper()}</span>'
                            f'<b>{html.escape(ch.get("requirement_id") or "New requirement")}</b>', unsafe_allow_html=True)
                if where:
                    st.markdown(where.lstrip(" ·"))
                old_text = old_reqs.get(ch.get("requirement_id") or "", {}).get("text", "")
                if ch["change"] == "added":
                    st.markdown(f'<div class="new">{html.escape(ch.get("new_text", ""))}</div>', unsafe_allow_html=True)
                elif ch["change"] == "removed":
                    st.markdown(f'<div class="old">{html.escape(old_text)}</div>', unsafe_allow_html=True)
                else:
                    o, n = st.columns(2)
                    o.markdown(f'<div class="label">Before</div><div class="old">{html.escape(old_text)}</div>',
                               unsafe_allow_html=True)
                    n.markdown(f'<div class="label">After</div><div class="new">{html.escape(ch.get("new_text", ""))}</div>',
                               unsafe_allow_html=True)
                st.markdown(f'<div class="why2">Why: {html.escape(ch.get("reason", ""))}</div>', unsafe_allow_html=True)
