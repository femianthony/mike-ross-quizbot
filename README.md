# AI QuizBot

A local CLI tool that:
1. Takes in study information (text/markdown)
2. Generates a quiz
3. Collects your answers
4. Grades with AI using **full / partial / none** credit

## Quick Start

```bash
cd /Users/femi/.openclaw/workspace/quizbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set your OpenAI key:

```bash
export OPENAI_API_KEY="your_key_here"
```

Run (CLI):

```bash
python quizbot.py \
  --source ./sample_notes.txt \
  --num-questions 6 \
  --save-report ./report.json
```

Run (GUI app - basic Tk):

```bash
python gui_app.py
```

Run (Mission-style GUI - recommended):

```bash
streamlit run app_streamlit.py
```

Or double-click:

```bash
run_mission_gui.command
```

## What it outputs

- Interactive quiz in terminal
- Per-question scoring:
  - `full` (1.0)
  - `partial` (0.5 by default, model can choose 0.25/0.75 too)
  - `none` (0.0)
- Final score + feedback on weak areas
- Optional JSON report

## Notes

- Uses `gpt-4o-mini` by default (change with `--model`)
- If your notes are long, keep to key sections for best quiz quality
