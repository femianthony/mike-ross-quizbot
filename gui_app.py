#!/usr/bin/env python3
import json
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from openai import OpenAI
from quizbot import QuizQuestion, build_quiz, grade_answer, summarize


class QuizBotGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AI QuizBot")
        self.root.geometry("980x760")

        self.client = None
        self.questions: list[QuizQuestion] = []
        self.current_idx = 0
        self.results = []
        self.total = 0.0
        self.total_max = 0.0

        self.source_text = tk.Text(root, height=12, wrap="word")
        self.question_box = tk.Text(root, height=8, wrap="word", state="disabled")
        self.answer_box = tk.Text(root, height=8, wrap="word")
        self.feedback_box = tk.Text(root, height=12, wrap="word", state="disabled")

        self.model_var = tk.StringVar(value="gpt-4o-mini")
        self.num_var = tk.IntVar(value=5)
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Model:").pack(side="left")
        ttk.Entry(top, textvariable=self.model_var, width=20).pack(side="left", padx=6)
        ttk.Label(top, text="# Questions:").pack(side="left", padx=(10, 0))
        ttk.Spinbox(top, from_=1, to=20, textvariable=self.num_var, width=5).pack(side="left", padx=6)

        ttk.Button(top, text="Load File", command=self.load_file).pack(side="left", padx=6)
        ttk.Button(top, text="Generate Test", command=self.generate_test).pack(side="left", padx=6)

        body = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Study Information").pack(anchor="w")
        self.source_text.pack(in_=body, fill="x", pady=(2, 10))

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=6)

        ttk.Label(body, text="Current Question").pack(anchor="w")
        self.question_box.pack(in_=body, fill="x", pady=(2, 8))

        ttk.Label(body, text="Your Answer").pack(anchor="w")
        self.answer_box.pack(in_=body, fill="x", pady=(2, 8))

        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="Submit Answer", command=self.submit_answer).pack(side="left")
        ttk.Button(btns, text="Next Question", command=self.next_question).pack(side="left", padx=6)
        ttk.Button(btns, text="Finish + Summary", command=self.finish_quiz).pack(side="left", padx=6)

        ttk.Label(body, text="Grading Feedback").pack(anchor="w")
        self.feedback_box.pack(in_=body, fill="both", expand=True, pady=(2, 8))

        ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w").pack(fill="x", side="bottom")

    def _ensure_client(self):
        if self.client:
            return True
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            messagebox.showerror("Missing API Key", "Set OPENAI_API_KEY in your terminal before launching GUI.")
            return False
        self.client = OpenAI(api_key=key)
        return True

    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt *.md"), ("All files", "*.*")])
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.source_text.delete("1.0", "end")
        self.source_text.insert("1.0", content)
        self.status_var.set(f"Loaded: {path}")

    def generate_test(self):
        if not self._ensure_client():
            return
        source = self.source_text.get("1.0", "end").strip()
        if not source:
            messagebox.showwarning("No source", "Paste or load study material first.")
            return

        try:
            self.status_var.set("Generating test...")
            self.root.update_idletasks()
            self.questions = build_quiz(self.client, self.model_var.get(), source, self.num_var.get())
            self.current_idx = 0
            self.results = []
            self.total = 0.0
            self.total_max = sum(q.max_points for q in self.questions)
            self._render_question()
            self._set_feedback("Quiz generated. Answer Q1 and hit Submit.")
            self.status_var.set(f"Quiz ready: {len(self.questions)} questions")
        except Exception as e:
            messagebox.showerror("Generate failed", str(e))
            self.status_var.set("Generate failed")

    def _render_question(self):
        if not self.questions:
            return
        q = self.questions[self.current_idx]
        self.question_box.configure(state="normal")
        self.question_box.delete("1.0", "end")
        self.question_box.insert("1.0", f"Q{q.id}. {q.question}\n\nRubric hint: {q.rubric}")
        self.question_box.configure(state="disabled")
        self.answer_box.delete("1.0", "end")

    def submit_answer(self):
        if not self._ensure_client() or not self.questions:
            return
        q = self.questions[self.current_idx]
        ans = self.answer_box.get("1.0", "end").strip()
        if not ans:
            messagebox.showwarning("No answer", "Type an answer first.")
            return
        try:
            self.status_var.set("Grading...")
            self.root.update_idletasks()
            graded = grade_answer(self.client, self.model_var.get(), q, ans)
            self.results.append(graded)
            self.total += graded["points_awarded"]

            lines = [
                f"Q{graded['question_id']} -> {graded['credit_label'].upper()} | {graded['points_awarded']}/{graded['max_points']}",
                f"Why: {graded.get('reasoning', '')}",
            ]
            missing = graded.get("missing") or []
            if missing:
                lines.append("Missing: " + "; ".join(missing))
            tip = graded.get("improvement_tip")
            if tip:
                lines.append("Tip: " + tip)
            self._set_feedback("\n".join(lines))
            self.status_var.set("Graded")
        except Exception as e:
            messagebox.showerror("Grade failed", str(e))
            self.status_var.set("Grade failed")

    def next_question(self):
        if not self.questions:
            return
        if self.current_idx < len(self.questions) - 1:
            self.current_idx += 1
            self._render_question()
            self._set_feedback("Moved to next question.")
            self.status_var.set(f"Question {self.current_idx + 1}/{len(self.questions)}")

    def finish_quiz(self):
        if not self._ensure_client() or not self.results:
            messagebox.showinfo("No results", "Submit at least one answer first.")
            return
        pct = (self.total / self.total_max) * 100 if self.total_max else 0
        try:
            summary = summarize(self.client, self.model_var.get(), self.results)
        except Exception:
            summary = "Summary unavailable."
        self._set_feedback(
            f"Final Score: {round(self.total,2)}/{round(self.total_max,2)} ({pct:.1f}%)\n\n{summary}"
        )
        self.status_var.set("Quiz finished")

    def _set_feedback(self, text: str):
        self.feedback_box.configure(state="normal")
        self.feedback_box.delete("1.0", "end")
        self.feedback_box.insert("1.0", text)
        self.feedback_box.configure(state="disabled")


def main():
    root = tk.Tk()
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    QuizBotGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
