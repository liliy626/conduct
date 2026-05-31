from __future__ import annotations


def test_canonical_plan_cache_key_includes_tenant_id() -> None:
    from gateway_core.api.openai_compat.chat_pipeline import _canonical_plan_cache_key

    question = "哪些老师请假最多？"

    assert _canonical_plan_cache_key(question, {"school_id": "sch_a"}) == (
        "tenant:sch_a:slot:teacher_leave_ranking"
    )
    assert _canonical_plan_cache_key(question, {"school_id": "sch_b"}) == (
        "tenant:sch_b:slot:teacher_leave_ranking"
    )


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
