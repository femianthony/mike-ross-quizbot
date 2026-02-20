import io
import json
import os
from datetime import date
from pathlib import Path

import streamlit as st
from openai import OpenAI

from quizbot import build_quiz, grade_answer, summarize

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

st.set_page_config(page_title="Mike Ross QuizBot", page_icon="🧠", layout="wide")

# ---------- style ----------
st.markdown(
    """
    <style>
      .stApp {background: radial-gradient(1200px 600px at 10% -10%, #1a2440 0%, #0b1222 45%, #070d1a 100%); color: #eaf0ff;}
      [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] {visibility: hidden; height: 0;}
      .pill {display:inline-block;padding:4px 10px;border-radius:999px;background:#1a2a4a;border:1px solid rgba(140,170,255,.25);color:#a7c0ff;font-size:12px;}
      .muted {color:#9fb0d8;}
      .card {border:1px solid rgba(160,190,255,.2);border-radius:14px;padding:14px;background:rgba(11,18,34,.55);}
      @media (max-width: 900px) {
        .block-container {padding-top: 1.2rem; padding-left: 0.7rem; padding-right: 0.7rem;}
      }
    </style>
    """,
    unsafe_allow_html=True,
)

PROJECTS_PATH = Path(__file__).with_name("projects.json")

DEFAULTS = {
    "questions": [],
    "idx": 0,
    "results": [],
    "feedback": "",
    "source": "",
    "mode": "Practice",
    "test_answers": {},
    "projects": [],
    "active_project": None,
    "study_plan": "",
    "recent_question_stems": [],
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


def load_projects():
    if PROJECTS_PATH.exists():
        try:
            data = json.loads(PROJECTS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_projects():
    PROJECTS_PATH.write_text(json.dumps(st.session_state.projects, indent=2), encoding="utf-8")


def export_results_json() -> str:
    payload = {
        "mode": st.session_state.mode,
        "questions": [q.__dict__ for q in st.session_state.questions],
        "results_latest": latest_results(st.session_state.results),
        "attempt_history": attempts_by_question(st.session_state.results),
    }
    return json.dumps(payload, indent=2)


def export_results_markdown() -> str:
    rows = st.session_state.results
    if not rows:
        return "# Quiz Results\n\nNo results yet."
    pts = sum(r["points_awarded"] for r in rows)
    max_pts = sum(r["max_points"] for r in rows)
    pct = (pts / max_pts * 100) if max_pts else 0
    lines = [f"# Quiz Results", "", f"**Score:** {pts:.2f}/{max_pts:.2f} ({pct:.1f}%)", ""]
    for r in rows:
        lines.extend(
            [
                f"## Q{r['question_id']} — {r['credit_label'].upper()} ({r['points_awarded']}/{r['max_points']})",
                f"- Reasoning: {r.get('reasoning','')}",
                f"- Tip: {r.get('improvement_tip','')}",
                "",
            ]
        )
    return "\n".join(lines)




def attempts_by_question(rows):
    by_q = {}
    for r in rows:
        qid = r.get("question_id")
        if qid is None:
            continue
        by_q.setdefault(qid, []).append(r)
    return by_q


def latest_results(rows):
    by_q = attempts_by_question(rows)
    return [by_q[qid][-1] for qid in sorted(by_q.keys())]


def get_client():
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        st.error("Missing OPENAI_API_KEY. Set it in terminal first.")
        st.stop()
    return OpenAI(api_key=key)


@st.cache_data(ttl=600)
def fetch_models_cached(api_key: str):
    try:
        client = OpenAI(api_key=api_key)
        items = client.models.list().data
        names = sorted({m.id for m in items if m.id.startswith(("gpt-", "o"))})
        return names
    except Exception:
        return []


def get_model_options():
    priority = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1", "gpt-4o", "o3-mini", "o1-mini"]
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        return priority[:6]
    found = fetch_models_cached(key)
    if not found:
        return priority[:6]
    selected = [m for m in priority if m in found]
    if not selected:
        selected = [m for m in found if m.startswith(("gpt-", "o"))][:6]
    return selected[:6]


def extract_uploaded_text(uploaded_file):
    if not uploaded_file:
        return ""
    name = uploaded_file.name.lower()
    data = uploaded_file.read()

    if name.endswith((".txt", ".md", ".csv")):
        try:
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return ""

    if name.endswith(".pdf") and PdfReader:
        try:
            reader = PdfReader(io.BytesIO(data))
            parts = []
            for p in reader.pages[:80]:
                parts.append(p.extract_text() or "")
            return "\n".join(parts)
        except Exception:
            return ""

    return ""


def generate_study_plan(model: str, source: str, exam_date: str, objective: str):
    if not source.strip():
        return "Add notes/source first."
    client = get_client()
    prompt = f"""
You are a top professor and study coach.
Create a practical study plan.
Exam date: {exam_date or 'unknown'}
Objective: {objective or 'score as high as possible'}

Use this structure:
1) What to focus on first
2) 7-day (or nearest) plan
3) Active recall checklist
4) High-yield mistakes to avoid
5) Night-before strategy

Source notes:
{source[:12000]}
"""
    res = client.chat.completions.create(
        model=model,
        temperature=0.3,
        messages=[
            {"role": "system", "content": "You are concise and practical."},
            {"role": "user", "content": prompt},
        ],
    )
    return res.choices[0].message.content.strip()


def reset_quiz_state():
    st.session_state.questions = []
    st.session_state.idx = 0
    st.session_state.results = []
    st.session_state.feedback = ""
    st.session_state.test_answers = {}


if not st.session_state.projects:
    st.session_state.projects = load_projects()

# ---------- sidebar ----------
with st.sidebar:
    st.markdown("## Control")
    model_options = get_model_options()
    default_index = model_options.index("gpt-4o-mini") if "gpt-4o-mini" in model_options else 0
    model = st.selectbox("Model", options=model_options, index=default_index)
    num_questions = st.slider("Questions", 3, 15, 6)
    mode = st.radio("Mode", ["Practice", "Test"], index=0 if st.session_state.mode == "Practice" else 1)
    st.session_state.mode = mode

    total = len(st.session_state.questions)
    done = len(latest_results(st.session_state.results))
    st.markdown("---")
    st.markdown("## Status")
    st.markdown(f"<span class='pill'>Quiz: {done}/{total}</span>", unsafe_allow_html=True)
    if total:
        st.progress(min(done / total, 1.0))
    st.caption(f"Mode: {mode}")

st.title("Mike Ross QuizBot")
st.markdown("<span class='muted'>Practice like finals week. Grade like a strict TA. Improve like a coach.</span>", unsafe_allow_html=True)

if not st.session_state.source.strip() and not st.session_state.questions:
    st.info("👋 Quick start: upload notes or paste study info, then click **Generate Test**.")

main_tab, projects_tab, planner_tab = st.tabs(["Quiz", "Projects", "Study Planner"])

# ---------- QUIZ TAB ----------
with main_tab:
    colA, colB = st.columns([2.3, 1], gap="large")

    with colA:
        st.markdown("### Build Test")
        uploaded_files = st.file_uploader(
            "Upload notes (txt/md/csv/pdf)",
            type=["txt", "md", "csv", "pdf"],
            accept_multiple_files=True,
        )
        if uploaded_files:
            combined = []
            loaded_names = []
            for uf in uploaded_files:
                extracted = extract_uploaded_text(uf)
                if extracted.strip():
                    combined.append(f"\n\n===== FILE: {uf.name} =====\n" + extracted)
                    loaded_names.append(uf.name)
            if combined:
                st.session_state.source = "\n".join(combined)
                st.success(
                    f"Loaded {len(loaded_names)} file(s): "
                    + ", ".join(loaded_names[:5])
                    + (" ..." if len(loaded_names) > 5 else "")
                )
            else:
                st.warning("Could not extract text from uploaded files.")

        st.session_state.source = st.text_area(
            "Study Information",
            value=st.session_state.source,
            height=220,
            placeholder="Paste lecture notes, chapter summaries, formulas, or key concepts...",
        )

        g1, g2 = st.columns([1, 1])
        if g1.button("Generate Test", use_container_width=True):
            source = st.session_state.source.strip()
            if not source:
                st.warning("Add study info first.")
            else:
                with st.spinner("Generating questions..."):
                    client = get_client()
                    try:
                        st.session_state.questions = build_quiz(
                            client,
                            model,
                            source,
                            num_questions,
                            prior_questions=st.session_state.recent_question_stems,
                        )
                        # Keep memory of recent tests so next test differs.
                        stems = [q.question for q in st.session_state.questions]
                        st.session_state.recent_question_stems = (stems + st.session_state.recent_question_stems)[:60]
                        st.session_state.idx = 0
                        st.session_state.results = []
                        st.session_state.test_answers = {}
                        st.session_state.feedback = "Quiz ready."
                    except Exception as e:
                        st.error(f"Could not generate quiz: {e}")
                st.rerun()

        if g2.button("Reset Quiz", use_container_width=True):
            reset_quiz_state()
            st.rerun()

        st.caption("New quizzes are diversified against your recent generated tests to reduce repeats.")

        if st.session_state.questions:
            if st.session_state.mode == "Practice":
                q = st.session_state.questions[st.session_state.idx]
                st.markdown("### Practice Mode")
                st.markdown(f"**Question {st.session_state.idx + 1} / {len(st.session_state.questions)}**")
                st.markdown(f"> {q.question}")
                with st.expander("Rubric / grading criteria"):
                    st.write(q.rubric)

                answer = st.text_area("Your answer", key=f"practice_answer_{q.id}", height=170)
                a0, a1, a2, a3 = st.columns([1, 1, 1, 1.4])

                if a0.button("Back", use_container_width=True):
                    if st.session_state.idx > 0:
                        st.session_state.idx -= 1
                    st.rerun()

                if a1.button("Submit Answer", use_container_width=True):
                    if not answer.strip():
                        st.warning("Write an answer first.")
                    else:
                        with st.spinner("Grading..."):
                            client = get_client()
                            graded = grade_answer(client, model, q, answer)
                            st.session_state.results.append(graded)
                            missing = graded.get("missing") or []
                            strengths = graded.get("what_you_got_right") or []
                            hints = graded.get("missing_concept_hints") or []
                            st.session_state.feedback = (
                                f"**{graded['credit_label'].upper()}** · {graded['points_awarded']}/{graded['max_points']}\n\n"
                                f"**Why (in depth):** {graded.get('reasoning','')}\n\n"
                                f"**What you got right:** {'; '.join(strengths) if strengths else 'Not enough shown yet'}\n\n"
                                f"**Missing areas:** {'; '.join(missing) if missing else 'None'}\n\n"
                                f"**Concept hints (vague):** {'; '.join(hints) if hints else 'N/A'}\n\n"
                                f"**Tip:** {graded.get('improvement_tip','')}"
                            )
                        st.rerun()

                if a2.button("Next Question", use_container_width=True):
                    if st.session_state.idx < len(st.session_state.questions) - 1:
                        st.session_state.idx += 1
                    st.rerun()

                if a3.button("Finish + Summary", use_container_width=True):
                    if not st.session_state.results:
                        st.warning("Submit at least one answer first.")
                    else:
                        with st.spinner("Building performance summary..."):
                            client = get_client()
                            latest = latest_results(st.session_state.results)
                            summary = summarize(client, model, latest)
                            pts = sum(r["points_awarded"] for r in latest)
                            max_pts = sum(r["max_points"] for r in latest)
                            pct = (pts / max_pts) * 100 if max_pts else 0
                            st.session_state.feedback = f"### Final Score: {pts:.2f}/{max_pts:.2f} ({pct:.1f}%)\n\n{summary}"
                        st.rerun()

            else:
                st.markdown("### Test Mode")
                st.caption("Answer all questions first. Grading happens all at once at the end with comments.")

                for q in st.session_state.questions:
                    st.markdown(f"**Q{q.id}. {q.question}**")
                    st.session_state.test_answers[q.id] = st.text_area(
                        "",
                        value=st.session_state.test_answers.get(q.id, ""),
                        key=f"test_answer_{q.id}",
                        height=120,
                        placeholder="Type your answer...",
                        label_visibility="collapsed",
                    )

                if st.button("Grade Entire Test", use_container_width=True):
                    missing_ids = [q.id for q in st.session_state.questions if not st.session_state.test_answers.get(q.id, "").strip()]
                    if missing_ids:
                        st.warning(f"Please answer all questions first. Missing: {missing_ids}")
                    else:
                        with st.spinner("Grading full test..."):
                            client = get_client()
                            results = [grade_answer(client, model, q, st.session_state.test_answers[q.id]) for q in st.session_state.questions]
                            st.session_state.results = results
                            pts = sum(r["points_awarded"] for r in results)
                            max_pts = sum(r["max_points"] for r in results)
                            pct = (pts / max_pts) * 100 if max_pts else 0
                            summary = summarize(client, model, results)
                            comments = []
                            for r in results:
                                strengths = r.get('what_you_got_right') or []
                                hints = r.get('missing_concept_hints') or []
                                comments.append(
                                    f"Q{r['question_id']}: {r['credit_label'].upper()} ({r['points_awarded']}/{r['max_points']})\n"
                                    f"- Why (in depth): {r.get('reasoning','')}\n"
                                    f"- What you got right: {'; '.join(strengths) if strengths else '—'}\n"
                                    f"- Missing areas: {'; '.join(r.get('missing') or []) if (r.get('missing') or []) else '—'}\n"
                                    f"- Concept hints (vague): {'; '.join(hints) if hints else '—'}\n"
                                    f"- Tip: {r.get('improvement_tip','')}"
                                )
                            st.session_state.feedback = (
                                f"### Final Score: {pts:.2f}/{max_pts:.2f} ({pct:.1f}%)\n\n"
                                f"{summary}\n\n### Per-Question Comments\n" + "\n\n".join(comments)
                            )
                        st.rerun()

    with colB:
        st.markdown("### Feedback")
        st.markdown(st.session_state.feedback or "No grading yet.")

        st.markdown("### Export / Share")
        if st.session_state.results:
            st.download_button("Download JSON Report", data=export_results_json(), file_name="quiz_report.json", mime="application/json", use_container_width=True)
            st.download_button("Download Markdown Report", data=export_results_markdown(), file_name="quiz_report.md", mime="text/markdown", use_container_width=True)
        else:
            st.caption("Grade at least one answer to unlock exports.")

        st.markdown("### Activity")
        if st.session_state.results:
            attempts = attempts_by_question(st.session_state.results)
            for qid in sorted(attempts.keys(), reverse=True):
                history = attempts[qid]
                latest = history[-1]
                title = f"Q{qid} · {latest['credit_label'].upper()} · {latest['points_awarded']}/{latest['max_points']}"
                with st.expander(title):
                    st.caption(f"Tries: {len(history)}")
                    for i, h in enumerate(history, start=1):
                        st.write(f"Try {i}: {h['credit_label'].upper()} · {h['points_awarded']}/{h['max_points']}")
                        if i < len(history):
                            st.caption(f"Old score kept for history")
        else:
            st.caption("No activity yet")

# ---------- PROJECTS TAB ----------
with projects_tab:
    st.markdown("### Projects")
    c1, c2, c3 = st.columns(3)
    name = c1.text_input("Project name", value="")
    course = c2.text_input("Course", value="")
    exam = c3.date_input("Exam date", value=date.today())
    objective = st.text_input("Goal", placeholder="Ace midterm, target 90%+, etc")

    if st.button("Save Project"):
        if not name.strip():
            st.warning("Project needs a name.")
        else:
            st.session_state.projects.append(
                {
                    "name": name.strip(),
                    "course": course.strip(),
                    "exam": str(exam),
                    "objective": objective.strip(),
                    "notes": st.session_state.source,
                }
            )
            save_projects()
            st.success("Project saved.")

    if st.session_state.projects:
        labels = [f"{p['name']} · {p['course']} · {p['exam']}" for p in st.session_state.projects]
        idx = st.selectbox("Saved projects", range(len(labels)), format_func=lambda i: labels[i])
        p = st.session_state.projects[idx]
        st.json(p)
        x1, x2 = st.columns(2)
        if x1.button("Load Into Quiz"):
            st.session_state.source = p.get("notes", "")
            st.session_state.active_project = p
            st.success("Loaded into quiz source.")
        if x2.button("Delete Project"):
            st.session_state.projects.pop(idx)
            save_projects()
            st.success("Project deleted.")
            st.rerun()
    else:
        st.info("No saved projects yet. Create your first study project to speed up repeat sessions.")

# ---------- STUDY PLANNER TAB ----------
with planner_tab:
    st.markdown("### AI Study Planner")
    default_exam = ""
    default_goal = ""
    if st.session_state.active_project:
        default_exam = st.session_state.active_project.get("exam", "")
        default_goal = st.session_state.active_project.get("objective", "")

    exam_date = st.text_input("Exam date", value=default_exam, placeholder="YYYY-MM-DD")
    objective = st.text_input("Objective", value=default_goal, placeholder="Score target, weak topics, etc")

    if st.button("Generate Study Plan"):
        with st.spinner("Building plan..."):
            st.session_state.study_plan = generate_study_plan(model, st.session_state.source, exam_date, objective)

    st.markdown(st.session_state.study_plan or "No plan yet.")
