import io
import json
import os
import sqlite3
import hashlib
from datetime import date
from pathlib import Path

import requests
import streamlit as st
from openai import OpenAI

from quizbot import build_quiz, grade_answer, summarize

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

st.set_page_config(page_title="QuizBot AI", page_icon="🧠", layout="wide")

# ---------- style ----------
st.markdown(
    """
    <style>
      
      .stApp {
        background: radial-gradient(1200px 600px at 10% -10%, #1a2440 0%, #0b1222 45%, #070d1a 100%);
        color: #eaf0ff;
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Inter, Roboto, Helvetica, Arial, sans-serif;
      }
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
DB_PATH = Path(__file__).with_name("quizbot_data.db")

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
    "canvas_courses": [],
    "canvas_assignments": [],
    "canvas_selected_course": None,
    "auth_user": None,
    "auth_ok": False,
    "quiz_history": [],
    "file_library": [],
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v




def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quiz_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            project_name TEXT,
            mode TEXT,
            score REAL,
            max_score REAL,
            percent REAL,
            payload TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            filename TEXT,
            char_count INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


init_db()


def get_google_user():
    """Best-effort wrapper for Streamlit OIDC auth when configured."""
    try:
        u = getattr(st, "user", None)
        if u is not None and getattr(u, "is_logged_in", False):
            return {
                "id": getattr(u, "sub", None) or getattr(u, "email", None) or getattr(u, "name", None),
                "email": getattr(u, "email", None),
                "name": getattr(u, "name", None),
            }
    except Exception:
        pass
    return None


def google_login_available() -> bool:
    return callable(getattr(st, "login", None)) and callable(getattr(st, "logout", None))


def authlib_available() -> bool:
    try:
        import authlib  # type: ignore
        return True
    except Exception:
        return False


def google_login_button():
    if not google_login_available():
        st.caption("Google sign-in is not configured on this deployment yet.")
        return
    if not authlib_available():
        st.info("Google sign-in is temporarily unavailable while auth dependencies are updating.")
        return
    if st.button("Sign in with Google", use_container_width=True):
        try:
            st.login()
        except Exception:
            st.error("Google login is currently unavailable. Please use username/password for now.")


def google_logout_button():
    if google_login_available() and st.button("Sign out Google", use_container_width=True):
        try:
            st.logout()
        except Exception as e:
            st.error(f"Google logout failed: {e}")


def _hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def create_user(username: str, password: str):
    if not username or not password:
        return False, "Username and password required"
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users(username, password_hash) VALUES(?,?)", (username.strip().lower(), _hash_pw(password)))
        conn.commit()
        return True, None
    except sqlite3.IntegrityError:
        return False, "Username already exists"
    finally:
        conn.close()


def verify_user(username: str, password: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT password_hash FROM users WHERE username=?", (username.strip().lower(),))
    row = cur.fetchone()
    conn.close()
    return bool(row and row[0] == _hash_pw(password))


def load_user_projects(username: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT payload FROM projects WHERE username=? ORDER BY id DESC", (username,))
    rows = cur.fetchall()
    conn.close()
    out=[]
    for r in rows:
        try:
            out.append(json.loads(r[0]))
        except Exception:
            pass
    return out


def replace_user_projects(username: str, projects: list):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM projects WHERE username=?", (username,))
    for p in projects:
        cur.execute("INSERT INTO projects(username,payload) VALUES(?,?)", (username, json.dumps(p)))
    conn.commit()
    conn.close()


def save_quiz_history(username: str, mode: str, results: list, project_name: str = ""):
    if not results:
        return
    latest = latest_results(results)
    pts = sum(r.get("points_awarded", 0) for r in latest)
    max_pts = sum(r.get("max_points", 0) for r in latest)
    pct = (pts / max_pts * 100) if max_pts else 0
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO quiz_history(username,project_name,mode,score,max_score,percent,payload) VALUES(?,?,?,?,?,?,?)",
        (username, project_name, mode, pts, max_pts, pct, json.dumps({"results": latest})),
    )
    conn.commit()
    conn.close()


def load_quiz_history(username: str, limit: int = 30):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT project_name,mode,score,max_score,percent,created_at FROM quiz_history WHERE username=? ORDER BY id DESC LIMIT ?", (username, limit))
    rows = cur.fetchall()
    conn.close()
    return rows


def save_file_record(username: str, filename: str, char_count: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO file_library(username,filename,char_count) VALUES(?,?,?)", (username, filename, char_count))
    conn.commit()
    conn.close()


def load_file_library(username: str, limit: int = 50):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT filename,char_count,created_at FROM file_library WHERE username=? ORDER BY id DESC LIMIT ?", (username, limit))
    rows = cur.fetchall()
    conn.close()
    return rows

def load_projects():
    if st.session_state.get("auth_ok") and st.session_state.get("auth_user"):
        return load_user_projects(st.session_state["auth_user"])
    if PROJECTS_PATH.exists():
        try:
            data = json.loads(PROJECTS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_projects():
    if st.session_state.get("auth_ok") and st.session_state.get("auth_user"):
        replace_user_projects(st.session_state["auth_user"], st.session_state.projects)
    else:
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




def canvas_get(base_url: str, token: str, path: str, params=None):
    if not base_url or not token:
        return None, "Missing Canvas URL or token"
    base = base_url.rstrip("/")
    url = f"{base}/api/v1/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=20)
        if r.status_code >= 400:
            return None, f"Canvas API error {r.status_code}: {r.text[:180]}"
        return r.json(), None
    except Exception as e:
        return None, f"Canvas request failed: {e}"


def fetch_canvas_courses(base_url: str, token: str):
    data, err = canvas_get(base_url, token, "courses", params={"enrollment_state": "active", "per_page": 100})
    if err:
        return [], err
    courses = []
    for c in data or []:
        courses.append({"id": c.get("id"), "name": c.get("name") or c.get("course_code") or f"Course {c.get('id')}"})
    return courses, None


def fetch_canvas_assignments(base_url: str, token: str, course_id):
    data, err = canvas_get(base_url, token, f"courses/{course_id}/assignments", params={"per_page": 100})
    if err:
        return [], err
    out = []
    for a in data or []:
        out.append({
            "id": a.get("id"),
            "name": a.get("name") or f"Assignment {a.get('id')}",
            "due_at": a.get("due_at"),
            "description": (a.get("description") or "").replace("<p>", "").replace("</p>", "\\n"),
        })
    return out, None

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
    st.markdown("## Account")

    guser = get_google_user()
    if guser:
        username = (guser.get("email") or guser.get("id") or "google-user").lower()
        st.session_state.auth_ok = True
        st.session_state.auth_user = username
        if not st.session_state.projects:
            st.session_state.projects = load_user_projects(username)
            st.session_state.quiz_history = load_quiz_history(username)
            st.session_state.file_library = load_file_library(username)
        st.success(f"Google: {guser.get('email') or guser.get('name') or username}")
        google_logout_button()

    elif st.session_state.get("auth_ok"):
        st.success(f"Signed in as @{st.session_state.get('auth_user')}")
        if st.button("Sign out", use_container_width=True):
            st.session_state.auth_ok = False
            st.session_state.auth_user = None
            st.session_state.projects = []
            st.session_state.quiz_history = []
            st.session_state.file_library = []
            st.rerun()
    else:
        google_login_button()
        if not google_login_available():
            st.caption("Google sign-in requires OIDC config in Streamlit secrets.")
        login_tab, signup_tab = st.tabs(["Login", "Sign up"])
        with login_tab:
            lu = st.text_input("Username", key="login_user")
            lp = st.text_input("Password", type="password", key="login_pass")
            if st.button("Login", use_container_width=True):
                if verify_user(lu, lp):
                    st.session_state.auth_ok = True
                    st.session_state.auth_user = lu.strip().lower()
                    st.session_state.projects = load_user_projects(st.session_state.auth_user)
                    st.session_state.quiz_history = load_quiz_history(st.session_state.auth_user)
                    st.session_state.file_library = load_file_library(st.session_state.auth_user)
                    st.success("Logged in")
                    st.rerun()
                else:
                    st.error("Invalid username/password")
        with signup_tab:
            su = st.text_input("New username", key="signup_user")
            sp = st.text_input("New password", type="password", key="signup_pass")
            if st.button("Create account", use_container_width=True):
                ok, err = create_user(su, sp)
                if ok:
                    st.success("Account created. Login now.")
                else:
                    st.error(err or "Could not create account")

    st.markdown("---")
    st.markdown("## Control")
    model_options = get_model_options()
    default_index = model_options.index("gpt-4o-mini") if "gpt-4o-mini" in model_options else 0
    model = st.selectbox("Model", options=model_options, index=default_index)
    num_questions = st.slider("Questions", 3, 15, 6)
    mode = st.radio("Mode", ["Practice", "Test"], index=0 if st.session_state.mode == "Practice" else 1)
    st.session_state.mode = mode

    with st.expander("Canvas", expanded=False):
        st.session_state.canvas_base = st.text_input(
            "Canvas URL",
            value=st.session_state.get("canvas_base") or os.getenv("CANVAS_BASE_URL", ""),
            placeholder="https://glow.williams.edu",
        )
        st.session_state.canvas_token = st.text_input(
            "Canvas Token",
            value=st.session_state.get("canvas_token") or os.getenv("CANVAS_API_TOKEN", ""),
            type="password",
            placeholder="Paste API token",
        )
        if st.button("Load Canvas Courses", use_container_width=True):
            courses, err = fetch_canvas_courses(st.session_state.canvas_base, st.session_state.canvas_token)
            if err:
                st.error(err)
            else:
                st.session_state.canvas_courses = courses
                st.success(f"Loaded {len(courses)} course(s)")

    total = len(st.session_state.questions)
    done = len(latest_results(st.session_state.results))
    st.markdown("---")
    st.markdown("## Status")
    st.markdown(f"<span class='pill'>Quiz: {done}/{total}</span>", unsafe_allow_html=True)
    if total:
        st.progress(min(done / total, 1.0))
    st.caption(f"Mode: {mode}")

st.title("QuizBot AI")
st.markdown("<span class='muted'>Practice like finals week. Grade like a strict TA. Improve like a coach.</span>", unsafe_allow_html=True)

if not st.session_state.source.strip() and not st.session_state.questions:
    st.info("👋 Quick start: upload notes or paste study info, then click **Generate Test**.")

main_tab, projects_tab, planner_tab, canvas_tab = st.tabs(["Quiz", "Projects", "Study Planner", "Canvas"])

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
                if st.session_state.get("auth_ok") and st.session_state.get("auth_user"):
                    for uf in uploaded_files:
                        save_file_record(st.session_state.auth_user, uf.name, len(st.session_state.source))
                    st.session_state.file_library = load_file_library(st.session_state.auth_user)
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

    st.markdown("---")
    st.markdown("### Account Library")
    if st.session_state.get("auth_ok") and st.session_state.get("auth_user"):
        h = st.session_state.get("quiz_history") or []
        f = st.session_state.get("file_library") or []
        st.caption("Recent quiz attempts")
        if h:
            for row in h[:10]:
                project_name, mode_h, score, max_score, pct, created_at = row
                st.write(f"{created_at} · {project_name or 'General'} · {mode_h} · {score:.2f}/{max_score:.2f} ({pct:.1f}%)")
        else:
            st.caption("No saved attempts yet")

        st.caption("Recent uploaded files")
        if f:
            for row in f[:10]:
                filename, char_count, created_at = row
                st.write(f"{created_at} · {filename} · {char_count} chars")
        else:
            st.caption("No saved files yet")
    else:
        st.info("Create/login to an account to save and view files, projects, and past quiz data.")

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


# ---------- CANVAS TAB ----------
with canvas_tab:
    st.markdown("### Canvas Integration")
    base = st.session_state.get("canvas_base", "")
    token = st.session_state.get("canvas_token", "")
    if not base or not token:
        st.info("Add Canvas URL + token from the sidebar expander first.")
    else:
        courses = st.session_state.get("canvas_courses", [])
        if not courses:
            st.caption("No courses loaded yet. Click **Load Canvas Courses** in sidebar.")
        else:
            course_map = {f"{c['name']} (#{c['id']})": c['id'] for c in courses}
            course_label = st.selectbox("Course", list(course_map.keys()))
            course_id = course_map[course_label]

            c1, c2 = st.columns([1,1])
            if c1.button("Load Assignments", use_container_width=True):
                assignments, err = fetch_canvas_assignments(base, token, course_id)
                if err:
                    st.error(err)
                else:
                    st.session_state.canvas_assignments = assignments
                    st.success(f"Loaded {len(assignments)} assignment(s)")
            if c2.button("Use Course Name as Project", use_container_width=True):
                st.session_state.active_project = {"name": course_label, "course": course_label, "exam": "", "objective": "", "notes": st.session_state.source}
                st.success("Set active project from selected course")

            assignments = st.session_state.get("canvas_assignments", [])
            if assignments:
                labels = [f"{a['name']} · due {a['due_at'] or 'n/a'}" for a in assignments]
                idx = st.selectbox("Assignment", range(len(labels)), format_func=lambda i: labels[i])
                a = assignments[idx]
                st.markdown(f"**{a['name']}**")
                st.caption(f"Due: {a['due_at'] or 'n/a'}")
                st.text_area("Assignment details", value=a.get("description") or "(No description)", height=180)

                b1, b2 = st.columns([1,1])
                if b1.button("Load Assignment into Quiz Source", use_container_width=True):
                    st.session_state.source = (a.get("description") or a.get("name") or "")
                    st.success("Loaded assignment content into Quiz source.")
                if b2.button("Add Assignment to Projects", use_container_width=True):
                    st.session_state.projects.append({
                        "name": a.get("name") or "Canvas Assignment",
                        "course": course_label,
                        "exam": a.get("due_at") or "",
                        "objective": "Complete assignment with high score",
                        "notes": a.get("description") or "",
                    })
                    save_projects()
                    st.success("Added assignment to Projects.")
