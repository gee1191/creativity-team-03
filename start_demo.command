#!/bin/bash
# Double-click this file to start the Certivity demo. The first run sets everything up (a few minutes).
cd "$(dirname "$0")" || exit 1
echo "== Certivity demo =="

fail() { echo "$1"; read -n 1 -s -r -p "Press any key to close."; exit 1; }
# the add-ons need Python 3.10 or newer; macOS ships 3.9
new_enough() { "$1" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; }

if [ -d .venv ] && ! new_enough .venv/bin/python; then
  echo "Replacing the earlier setup, which used a Python that is too old ..."
  rm -rf .venv
fi

if [ ! -d .venv ]; then
  echo "First run: setting up (this takes a few minutes) ..."
  PY=""
  for p in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$p" >/dev/null 2>&1 && new_enough "$p"; then PY="$p"; break; fi
  done
  if [ -n "$PY" ]; then
    "$PY" -m venv .venv || fail "Setting up Python failed."
  else
    # no recent Python on this Mac: uv downloads a private copy that only this folder uses
    echo "Downloading a recent Python (used only by this folder) ..."
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null \
        || fail "The download failed. Check the internet connection and double-click this file again."
    fi
    uv venv --seed --python 3.12 .venv || fail "Setting up Python failed."
  fi
fi
source .venv/bin/activate
if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  echo "Installing the add-ons ..."
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt || fail "Installing add-ons failed."
  touch .venv/.installed
fi

# the tool works from data/parsed (the regulations read into paragraphs); the PDFs are only needed to build it,
# and the app links to the online PDFs when they are not here
if [ ! -d data/parsed ]; then
  echo "Downloading the regulation PDFs (about 250 MB, once) ..."
  python tools/fetch_regulations.py || echo "Some PDFs could not be downloaded; continuing."
  echo "Reading the PDFs into paragraphs ..."
  python agent/parse_pdfs.py 2>/dev/null
fi
if [ ! -f agent/expert_examples.json ]; then
  python agent/build_examples.py >/dev/null
fi

echo "Starting the app. It opens in your browser; keep this window open while you use it."
# headless, so Streamlit does not stop to ask for an e-mail address on first start; the browser is opened here
(sleep 5; open "http://localhost:8501") &
python -m streamlit run app.py --server.headless true --browser.gatherUsageStats false
