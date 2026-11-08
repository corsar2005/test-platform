import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def export_question_bank(database: Path, output: Path) -> int:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT
                q.id, q.title, q.description, q.question_type, q.subject,
                q.difficulty, q.points, q.starter_code, q.reference_answer,
                o.id AS option_id, o.label AS option_label, o.is_correct,
                o.position AS option_position
            FROM questions AS q
            LEFT JOIN options AS o ON o.question_id = q.id
            WHERE q.is_active = 1
            ORDER BY q.subject, q.difficulty, q.id, o.position, o.id
            """
        )
        questions = []
        current_question = None
        for row in rows:
            question_id = row["id"]
            if current_question is None or current_question["id"] != question_id:
                current_question = {
                    "id": question_id,
                    "title": row["title"],
                    "description": row["description"],
                    "question_type": row["question_type"],
                    "subject": row["subject"],
                    "difficulty": row["difficulty"],
                    "points": row["points"],
                    "starter_code": row["starter_code"],
                    "reference_answer": row["reference_answer"],
                    "options": [],
                }
                questions.append(current_question)
            if row["option_id"] is not None:
                current_question["options"].append({
                    "id": row["option_id"],
                    "label": row["option_label"],
                    "is_correct": bool(row["is_correct"]),
                })
    finally:
        connection.close()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "window.OFFLINE_QUESTION_BANK="
        + json.dumps(questions, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    return len(questions)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export active questions for the offline Android app.")
    parser.add_argument("--database", type=Path, default=ROOT / "test_platform.db")
    parser.add_argument("--output", type=Path, default=ROOT / "static" / "offline-question-bank.js")
    args = parser.parse_args()
    count = export_question_bank(args.database, args.output)
    print(f"Exported {count} active questions.")


if __name__ == "__main__":
    main()
