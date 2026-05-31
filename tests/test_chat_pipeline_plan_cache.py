from __future__ import annotations

import inspect

import pytest


def test_canonical_plan_cache_key_includes_tenant_id() -> None:
    from gateway_core.api.openai_compat.chat_pipeline import _canonical_plan_cache_key

    question = "哪些老师请假最多？"

    assert _canonical_plan_cache_key(question, {"school_id": "sch_a"}) == (
        "tenant:sch_a:slot:teacher_leave_ranking"
    )
    assert _canonical_plan_cache_key(question, {"school_id": "sch_b"}) == (
        "tenant:sch_b:slot:teacher_leave_ranking"
    )


def test_query_normalizer_extracts_tenant_id_from_context_and_payload() -> None:
    from gateway_core.agents.school_sql.query_normalizer import QueryNormalizer

    assert QueryNormalizer.extract_tenant_id({"school_id": " sch_a "}) == "sch_a"
    assert QueryNormalizer.extract_tenant_id({"tenant_id": "sch_b"}) == "sch_b"
    assert QueryNormalizer.extract_tenant_id({"session_context": {"school_id": "sch_payload"}}) == "sch_payload"
    assert QueryNormalizer.extract_tenant_id({"session_context": {"tenant_id": "sch_payload_tenant"}}) == (
        "sch_payload_tenant"
    )
    assert QueryNormalizer.extract_tenant_id({}) == "default"


def test_canonical_plan_cache_key_delegates_tenant_extraction() -> None:
    import gateway_core.api.openai_compat.chat_pipeline as chat_pipeline

    source = inspect.getsource(chat_pipeline._canonical_plan_cache_key)

    assert "QueryNormalizer.extract_tenant_id" in source
    assert ".get(\"session_context\"" not in source
    assert ".get('session_context'" not in source
    assert "isinstance" not in source


def test_canonical_plan_cache_never_uses_bare_slot_key(monkeypatch) -> None:
    import gateway_core.api.openai_compat.chat_pipeline as chat_pipeline

    monkeypatch.setattr(chat_pipeline.rt, "_truthy_env", lambda *_args, **_kwargs: True)
    monkeypatch.setitem(
        chat_pipeline.CANONICAL_PLAN_CACHE,
        "slot:teacher_leave_ranking",
        {"title": "unsafe bare slot plan"},
    )
    monkeypatch.delitem(
        chat_pipeline.CANONICAL_PLAN_CACHE,
        "tenant:sch_missing:slot:teacher_leave_ranking",
        raising=False,
    )

    assert (
        chat_pipeline._canonical_plan_for_question(
            "哪些老师请假最多？",
            route_name="universal_hub_ga",
            session_context={"school_id": "sch_missing"},
        )
        is None
    )


def test_canonical_plan_cache_hits_only_matching_tenant(monkeypatch) -> None:
    import gateway_core.api.openai_compat.chat_pipeline as chat_pipeline

    monkeypatch.setattr(chat_pipeline.rt, "_truthy_env", lambda *_args, **_kwargs: True)
    monkeypatch.setitem(
        chat_pipeline.CANONICAL_PLAN_CACHE,
        "tenant:sch_a:slot:teacher_leave_ranking",
        {"title": "tenant a plan"},
    )
    monkeypatch.setitem(
        chat_pipeline.CANONICAL_PLAN_CACHE,
        "tenant:sch_b:slot:teacher_leave_ranking",
        {"title": "tenant b plan"},
    )

    plan = chat_pipeline._canonical_plan_for_question(
        "请统计教师请假排行",
        route_name="universal_hub_ga",
        session_context={"school_id": "sch_b"},
    )

    assert plan == {"title": "tenant b plan"}


def test_canonical_plan_cache_still_matches_when_multimodal_requirements_exist(monkeypatch) -> None:
    import gateway_core.api.openai_compat.chat_pipeline as chat_pipeline

    monkeypatch.setattr(chat_pipeline.rt, "_truthy_env", lambda *_args, **_kwargs: True)
    monkeypatch.setitem(
        chat_pipeline.CANONICAL_PLAN_CACHE,
        "tenant:sch_zx_mlh:slot:teacher_leave_ranking",
        {"title": "teacher leave plan", "required_outputs": ["data_evidence"]},
    )

    assert chat_pipeline._canonical_plan_for_question(
        "帮我统计教师请假排行，并生成一张管理图和一份汇报PPT。",
        route_name="universal_hub_ga",
        session_context={"school_id": "sch_zx_mlh"},
    ) == {"title": "teacher leave plan", "required_outputs": ["data_evidence"]}


def test_canonical_plan_cache_reports_remaining_multimodal_outputs() -> None:
    import gateway_core.api.openai_compat.chat_pipeline as chat_pipeline

    assert chat_pipeline._canonical_plan_remaining_outputs(
        {"required_outputs": ["data_evidence"]},
        "帮我统计教师请假排行，并生成一张管理图和一份汇报PPT。",
    ) == ["image_artifact", "ppt_artifact"]

    assert (
        chat_pipeline._canonical_plan_remaining_outputs(
            {"required_outputs": ["data_evidence"]},
            "请统计教师请假排行",
        )
        == []
    )


def test_canonical_plan_lineage_rejects_cross_tenant_handoff() -> None:
    import gateway_core.api.openai_compat.chat_pipeline as chat_pipeline

    result = {
        "lineage_ledger": [
            {
                "sql_hash": "a" * 64,
                "meta_context": {"tenant_id": "sch_a"},
            }
        ]
    }

    with pytest.raises(ValueError, match="tenant mismatch"):
        chat_pipeline._canonical_plan_lineage_ledger_for_tenant(
            result,
            {"school_id": "sch_b"},
        )


def test_canonical_plan_lineage_accepts_matching_tenant_handoff() -> None:
    import gateway_core.api.openai_compat.chat_pipeline as chat_pipeline

    lineage = {
        "sql_hash": "a" * 64,
        "meta_context": {"tenant_id": "sch_a"},
    }
    result = {"lineage_ledger": [lineage]}

    assert chat_pipeline._canonical_plan_lineage_ledger_for_tenant(
        result,
        {"school_id": "sch_a"},
    ) == [lineage]
