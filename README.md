# Mike Ross QuizBot

An AI-powered study assistant that generates short-answer quizzes from your notes, grades with rubric-based feedback, and helps you plan prep for exams.

## Why it feels better now

- **Visible polish:** cleaner dark UI, progress indicator, onboarding hint, mobile-friendly spacing
- **Reliability upgrades:** safer parsing/normalization for generated questions and graded responses
- **Grading quality controls:** checklist-aware grading with constrained labels/points and confidence clamp
- **Project management:** save/load/delete projects with persistent local storage (`projects.json`)
- **Export/share:** download quiz results as JSON or Markdown reports
- **Empty states:** helpful prompts when no notes/projects/results exist

## Quick Start

```bash
cd /Users/femi/.openclaw/workspace/quizbot-deploy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY="your_key_here"
streamlit run app_streamlit.py
```

## Core Workflows

1. **Build Quiz**
   - Upload notes (`txt/md/csv/pdf`) or paste text
   - Generate Practice or Test mode quizzes
2. **Get Graded**
   - Practice: submit one by one
   - Test: grade all at once
3. **Improve & Share**
   - Read targeted feedback + summary
   - Export results to JSON/Markdown
4. **Plan Exam Prep**
   - Generate a study plan from your active project and notes

## Files

- `app_streamlit.py` — primary web app
- `quizbot.py` — quiz generation + grading logic (CLI-compatible)
- `projects.json` — persisted project list (created automatically)
- `sample_notes.txt` — starter notes

## Notes

- Default model preference: `gpt-4o-mini`
- If PDFs fail to parse, install/update `pypdf`
- Keep source material focused for best question quality
