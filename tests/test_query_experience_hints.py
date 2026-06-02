from __future__ import annotations

import json

from gateway_core.schema_context.query_experience import experience_hints_for_question, merge_experience_hints


def test_experience_hints_match_schema_and_question(tmp_path, monkeypatch) -> None:
    hints_file = tmp_path / "sql_history_hints.json"
    hints_file.write_text(
        json.dumps(
            {
                "hints": [
                    {
                        "schema": "zx_mlh",
                        "question": "学校有哪些业务领域",
                        "question_patterns": ["业务领域"],
                        "raw_sql": "SELECT app_name FROM zx_mlh.app_detail",
                        "table_refs": ["app_detail"],
                        "column_refs": ["app_name"],
                        "answer_summary": "优先复用历史业务领域口径。",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SCHOOL_QUERY_EXPERIENCE_HINTS_FILE", str(hints_file))

    hints = experience_hints_for_question(question="学校业务领域有哪些？", schema_name="zx_mlh")

    assert len(hints) == 1
    assert hints[0]["manual_hint"] is True
    assert hints[0]["answer_summary"] == "优先复用历史业务领域口径。"


def test_merge_experience_hints_puts_manual_hints_first() -> None:
    merged = merge_experience_hints(
        hints=[{"question": "hint", "answer_summary": "first", "raw_sql": "SELECT 1"}],
        experiences=[{"question": "db", "answer_summary": "second", "raw_sql": "SELECT 2"}],
        limit=2,
    )

    assert [item["question"] for item in merged] == ["hint", "db"]
