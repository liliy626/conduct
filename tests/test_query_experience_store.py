from __future__ import annotations

from gateway_core.schema_context.query_experience_store import _experience_row_to_dict, build_embedding_text


def test_experience_row_to_dict_preserves_answer_summary() -> None:
    row = (
        "学校有哪些业务领域",
        'SELECT table_name FROM information_schema.tables WHERE table_schema = "zx_mlh"',
        '["yida_form_app_catalog"]',
        '["table_name"]',
        28,
        3,
        "v1",
        "历史提示：这是业务域归纳问题，优先复用已整理口径。",
        0.91,
    )

    item = _experience_row_to_dict(row)

    assert item["question"] == "学校有哪些业务领域"
    assert item["answer_summary"] == "历史提示：这是业务域归纳问题，优先复用已整理口径。"
    assert item["similarity"] == 0.91


def test_build_embedding_text_includes_hint_when_present() -> None:
    text = build_embedding_text(
        question="学校有哪些业务领域",
        table_refs=["yida_form_app_catalog"],
        column_refs=["table_name"],
        answer_summary="按业务域口径回答，不要逐表展开。",
    )

    assert "学校有哪些业务领域" in text
    assert "hint: 按业务域口径回答，不要逐表展开。" in text
