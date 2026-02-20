import io
import os
from datetime import date

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
    </style>
    """,
    unsafe_allow_html=True,
)

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
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


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
    done = len(st.session_state.results)
    st.markdown("---")
    st.markdown("## Status")
    st.markdown(f"<span class='pill'>Quiz: {done}/{total}</span>", unsafe_allow_html=True)
    st.caption(f"Mode: {mode}")

st.title("Mike Ross QuizBot")
st.markdown("<span class='muted'>Now with Projects, file upload, and smarter study workflows.</span>", unsafe_allow_html=True)

main_tab, projects_tab, planner_tab = st.tabs(["Quiz", "Projects", "Study Planner"])

# ---------- QUIZ TAB ----------
with main_tab:
    colA, colB = st.columns([2.3, 1], gap="large")

    with colA:
        st.markdown("### Build Test")
        uploaded = st.file_uploader("Upload notes (txt/md/csv/pdf)", type=["txt", "md", "csv", "pdf"])
        if uploaded:
            extracted = extract_uploaded_text(uploaded)
            if extracted.strip():
                st.session_state.source = extracted
                st.success(f"Loaded: {uploaded.name}")
            else:
                st.warning("Could not extract text from file.")

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
                    st.session_state.questions = build_quiz(client, model, source, num_questions)
                    st.session_state.idx = 0
                    st.session_state.results = []
                    st.session_state.test_answers = {}
                    st.session_state.feedback = "Quiz ready."
                st.rerun()

        if g2.button("Reset Quiz", use_container_width=True):
            reset_quiz_state()
            st.rerun()

        if st.session_state.questions:
            if st.session_state.mode == "Practice":
                q = st.session_state.questions[st.session_state.idx]
                st.markdown("### Practice Mode")
                st.markdown(f"**Question {st.session_state.idx + 1} / {len(st.session_state.questions)}**")
                st.markdown(f"> {q.question}")
                with st.expander("Rubric / grading criteria"):
                    st.write(q.rubric)

                answer = st.text_area("Your answer", key=f"practice_answer_{q.id}", height=170)
                a1, a2, a3 = st.columns([1, 1, 1.4])

                if a1.button("Submit Answer", use_container_width=True):
                    if not answer.strip():
                        st.warning("Write an answer first.")
                    else:
                        with st.spinner("Grading..."):
                            client = get_client()
                            graded = grade_answer(client, model, q, answer)
                            st.session_state.results.append(graded)
                            missing = graded.get("missing") or []
                            st.session_state.feedback = (
                                f"**{graded['credit_label'].upper()}** · {graded['points_awarded']}/{graded['max_points']}\n\n"
                                f"**Why:** {graded.get('reasoning','')}\n\n"
                                f"**Missing:** {'; '.join(missing) if missing else 'None'}\n\n"
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
                            summary = summarize(client, model, st.session_state.results)
                            pts = sum(r["points_awarded"] for r in st.session_state.results)
                            max_pts = sum(r["max_points"] for r in st.session_state.results)
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
                                comments.append(
                                    f"Q{r['question_id']}: {r['credit_label'].upper()} ({r['points_awarded']}/{r['max_points']})\n"
                                    f"- Why: {r.get('reasoning','')}\n"
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

        st.markdown("### Activity")
        if st.session_state.results:
            for r in reversed(st.session_state.results[-10:]):
                st.write(f"Q{r['question_id']} · {r['credit_label'].upper()} · {r['points_awarded']}/{r['max_points']}")
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
            st.success("Project saved.")

    if st.session_state.projects:
        labels = [f"{p['name']} · {p['course']} · {p['exam']}" for p in st.session_state.projects]
        idx = st.selectbox("Saved projects", range(len(labels)), format_func=lambda i: labels[i])
        p = st.session_state.projects[idx]
        st.write(p)
        x1, x2 = st.columns(2)
        if x1.button("Load Into Quiz"):
            st.session_state.source = p.get("notes", "")
            st.session_state.active_project = p
            st.success("Loaded into quiz source.")
        if x2.button("Delete Project"):
            st.session_state.projects.pop(idx)
            st.success("Project deleted.")
            st.rerun()

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
