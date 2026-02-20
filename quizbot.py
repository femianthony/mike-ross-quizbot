#!/usr/bin/env python3
import argparse
import json
import os
from dataclasses import dataclass, asdict
from typing import List, Dict, Any

from openai import OpenAI


@dataclass
class QuizQuestion:
    id: int
    question: str
    answer_key: str
    rubric: str
    max_points: float = 1.0


def read_source(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_quiz(client: OpenAI, model: str, source_text: str, n: int) -> List[QuizQuestion]:
    system = (
        "You are a college professor writing clear, fair assessments. "
        "Questions and grading criteria must align tightly. Output strict JSON only."
    )
    user = f"""
Create exactly {n} short-answer questions from the study material below.
Each question must be specific and gradeable from the prompt itself (no hidden requirements).

Return JSON with this schema:
{{
  "questions": [
    {{
      "id": 1,
      "question": "...",
      "answer_key": "ideal concise answer",
      "rubric": "what earns full vs partial vs no credit",
      "grading_checklist": ["required element 1", "required element 2"],
      "max_points": 1.0
    }}
  ]
}}

Study material:
---
{source_text}
---
"""

    res = client.chat.completions.create(
        model=model,
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    payload = json.loads(res.choices[0].message.content)
    questions = []
    for q in payload.get("questions", []):
        questions.append(
            QuizQuestion(
                id=int(q["id"]),
                question=q["question"].strip(),
                answer_key=q["answer_key"].strip(),
                rubric=(
                    q["rubric"].strip()
                    + ("\nChecklist: " + ", ".join(q.get("grading_checklist", [])) if q.get("grading_checklist") else "")
                ),
                max_points=float(q.get("max_points", 1.0)),
            )
        )
    if len(questions) != n:
        raise ValueError(f"Expected {n} questions, got {len(questions)}")
    return questions


def grade_answer(client: OpenAI, model: str, q: QuizQuestion, user_answer: str) -> Dict[str, Any]:
    system = (
        "You are a fair professor. Grade ONLY what the question asks and rubric/checklist requires. "
        "Do not invent missing requirements. Output strict JSON only."
    )
    user = f"""
Grade the user's answer using the provided answer key and rubric.

Question: {q.question}
Answer key: {q.answer_key}
Rubric: {q.rubric}
User answer: {user_answer}
Max points: {q.max_points}

Return JSON:
{{
  "credit_label": "full|partial|none",
  "points_awarded": 0.0,
  "reasoning": "2-4 sentence explanation tied directly to rubric/checklist and what was demonstrated",
  "missing": ["high-level missing area 1", "high-level missing area 2"],
  "improvement_tip": "one actionable tip",
  "what_you_got_right": ["high-level strength 1", "high-level strength 2"],
  "missing_concept_hints": ["vague concept hint 1", "vague concept hint 2"],
  "matched_checklist_items": ["item"],
  "confidence": 0.0
}}

Rules:
- Grade only against what was explicitly asked + rubric/checklist.
- Never deduct for information that was not requested.
- If core requested ideas are present but imperfect wording, prefer partial/full (not none).
- Give richer feedback: reasoning should be 2-4 sentences and educational.
- missing_concept_hints must be VAGUE (category-level), not exact answer text.
- what_you_got_right should call out concrete strengths at a high level.
- full => points_awarded must equal max points.
- none => points_awarded must be 0.
- partial => points_awarded > 0 and < max points.
- Be consistent with rubric.
"""

    res = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    graded = json.loads(res.choices[0].message.content)

    label = graded.get("credit_label", "none")
    pts = float(graded.get("points_awarded", 0.0))
    max_pts = q.max_points

    if label == "full":
        pts = max_pts
    elif label == "none":
        pts = 0.0
    else:
        pts = max(0.0, min(max_pts - 1e-6, pts))

    graded["points_awarded"] = round(pts, 3)
    graded["max_points"] = max_pts
    graded["question_id"] = q.id
    graded["question"] = q.question
    graded["answer_key"] = q.answer_key
    return graded


def summarize(client: OpenAI, model: str, graded_rows: List[Dict[str, Any]]) -> str:
    system = "You are a concise academic coach."
    user = f"""
Given graded quiz results, give a short performance summary:
1) strongest area
2) weakest area
3) 3-bullet study plan

Results JSON:
{json.dumps(graded_rows, indent=2)}
"""
    res = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return res.choices[0].message.content.strip()


def run_quiz(args):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key)
    source = read_source(args.source)

    print("\nGenerating quiz...\n")
    quiz = build_quiz(client, args.model, source, args.num_questions)

    graded_rows = []
    total = 0.0
    total_max = sum(q.max_points for q in quiz)

    for q in quiz:
        print(f"\nQ{q.id}. {q.question}")
        ans = input("Your answer: ").strip()
        graded = grade_answer(client, args.model, q, ans)
        graded_rows.append(graded)
        total += graded["points_awarded"]

        print(
            f"-> {graded['credit_label'].upper()} | "
            f"{graded['points_awarded']}/{graded['max_points']}"
        )
        print(f"   Why: {graded.get('reasoning', 'n/a')}")
        missing = graded.get("missing") or []
        if missing:
            print("   Missing:", "; ".join(missing))
        tip = graded.get("improvement_tip")
        if tip:
            print(f"   Tip: {tip}")

    pct = (total / total_max) * 100 if total_max else 0
    print("\n" + "=" * 60)
    print(f"Final Score: {round(total,2)}/{round(total_max,2)} ({pct:.1f}%)")
    print("=" * 60)

    print("\nAI Performance Summary:\n")
    summary = summarize(client, args.model, graded_rows)
    print(summary)

    if args.save_report:
        report = {
            "source": args.source,
            "model": args.model,
            "score": {"points": total, "max_points": total_max, "percent": pct},
            "results": graded_rows,
            "summary": summary,
        }
        with open(args.save_report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nSaved report -> {args.save_report}")


def parse_args():
    p = argparse.ArgumentParser(description="AI QuizBot")
    p.add_argument("--source", required=True, help="Path to study text/notes file")
    p.add_argument("--num-questions", type=int, default=5)
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--save-report", default="")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_quiz(args)
