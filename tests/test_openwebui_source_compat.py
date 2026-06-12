from __future__ import annotations

from gateway_core.api.openai_compat.source_compat import merge_openwebui_sources, openwebui_source_url


def test_sql_lineage_without_url_is_not_sent_as_openwebui_source() -> None:
    target: list[dict] = []
    incoming = [
        {
            "document": ["执行证据：学校整体概况怎么样？"],
            "metadata": [{"source": "学校整体概况怎么样？", "type": "sql_lineage"}],
            "source": {"name": "查询证据：学校整体概况怎么样？"},
        }
    ]

    added = merge_openwebui_sources(target, incoming)

    assert added == []
    assert target == []


def test_openwebui_source_with_nested_url_is_sent_once() -> None:
    target: list[dict] = []
    incoming = [
        {"document": ["政策原文"], "source": {"name": "政策", "url": "https://example.edu/policy"}},
        {"document": ["重复政策"], "source": {"name": "政策", "url": "https://example.edu/policy"}},
    ]

    added = merge_openwebui_sources(target, incoming)

    assert added == [incoming[0]]
    assert target == [incoming[0]]
    assert openwebui_source_url(incoming[0]) == "https://example.edu/policy"
