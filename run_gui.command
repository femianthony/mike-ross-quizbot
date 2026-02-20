#!/bin/zsh
cd /Users/femi/.openclaw/workspace/quizbot
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt
python gui_app.py
