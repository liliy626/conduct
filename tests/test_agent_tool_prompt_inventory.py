from __future__ import annotations

import ast
from pathlib import Path

import gateway_core.prompts.agents.contract_planner  # noqa: F401
import gateway_core.prompts.agents.final_answer  # noqa: F401
import gateway_core.prompts.agents.school_sql_agent  # noqa: F401
import gateway_core.prompts.answer_context_prompt  # noqa: F401
import gateway_core.prompts.rules.answer_context  # noqa: F401
import gateway_core.prompts.rules.answer_evidence  # noqa: F401
import gateway_core.prompts.rules.answer_style  # noqa: F401
from gateway_core.agents.contracts.workflow_contracts import SCHOOL_DATA_ANSWER_WORKFLOW
from gateway_core.agents.universal_hub.registry import SKILL_REGISTRY
from gateway_core.prompts.prompt_layer import PROMPT_REGISTRY
from gateway_core.tools.catalog import build_non_sql_tool_registry


REPO_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = REPO_ROOT / "docs" / "agent_tool_prompt_inventory.md"
AGENT_CARDS_PATH = REPO_ROOT / "docs" / "agent_responsibility_cards.md"
TOOL_CARDS_PATH = REPO_ROOT / "docs" / "tool_responsibility_cards.md"
PROMPT_CARDS_PATH = REPO_ROOT / "docs" / "prompt_responsibility_cards.md"
MAS_ASSETS_PATH = REPO_ROOT / "docs" / "multi_agent_iteration_assets.md"
SQL_TOOLS_PATH = REPO_ROOT / "gateway_core" / "agents" / "school_sql" / "sql_tools.py"


def _inventory_text() -> str:
    return INVENTORY_PATH.read_text(encoding="utf-8")


def _agent_cards_text() -> str:
    return AGENT_CARDS_PATH.read_text(encoding="utf-8")


def _tool_cards_text() -> str:
    return TOOL_CARDS_PATH.read_text(encoding="utf-8")


def _prompt_cards_text() -> str:
    return PROMPT_CARDS_PATH.read_text(encoding="utf-8")


def _mas_assets_text() -> str:
    return MAS_ASSETS_PATH.read_text(encoding="utf-8")


def test_inventory_lists_registered_agent_skills() -> None:
    text = _inventory_text()

    for skill_name, spec in SKILL_REGISTRY.items():
        assert f"`{skill_name}`" in text
        assert spec.skill_cls.__module__ in text
        assert spec.skill_cls.__name__ in text
        for output in spec.outputs:
            assert f"`{output}`" in text


def test_agent_cards_list_registered_agent_contracts() -> None:
    text = _agent_cards_text()

    for skill_name, spec in SKILL_REGISTRY.items():
        assert f"## `{skill_name}`" in text
        assert spec.skill_cls.__module__ in text
        assert spec.skill_cls.__name__ in text
        assert f"`{spec.default_model_role}`" in text
        assert "支持流式" in text
        for output in spec.outputs:
            assert f"`{output}`" in text
        for tool_name in spec.tools:
            assert f"`{tool_name}`" in text


def test_inventory_lists_gateway_tools_and_aliases() -> None:
    text = _inventory_text()
    registry = build_non_sql_tool_registry()

    for tool in registry.list():
        assert f"`{tool.name}`" in text
        for alias in tool.aliases:
            assert f"`{alias}`" in text


def test_tool_cards_list_gateway_tools_and_aliases() -> None:
    text = _tool_cards_text()
    registry = build_non_sql_tool_registry()

    for tool in registry.list():
        assert f"### `{tool.name}`" in text
        assert tool.agent_tool.__class__.__module__ in text
        assert tool.agent_tool.__class__.__name__ in text
        assert f"`{tool.risk_level}`" in text
        for tag in tool.tags:
            assert f"`{tag}`" in text
        for scope in tool.scopes:
            assert f"`{scope}`" in text
        for alias in tool.aliases:
            assert f"`{alias}`" in text


def test_inventory_lists_school_sql_react_tools_from_source() -> None:
    text = _inventory_text()

    for tool_name in _structured_tool_names(SQL_TOOLS_PATH):
        assert f"`{tool_name}`" in text


def test_tool_cards_list_school_sql_react_tools_from_source() -> None:
    text = _tool_cards_text()

    for tool_name in _structured_tool_names(SQL_TOOLS_PATH):
        assert f"`{tool_name}`" in text


def test_tool_cards_list_internal_business_prompt_context_tool() -> None:
    text = _tool_cards_text()

    assert "`business_prompt_context`" in text
    assert "`gateway_core.tools.business_prompt_tool.BusinessPromptContextTool`" in text
    assert "内部 context helper" in text


def test_tool_cards_document_web_search_status_contract() -> None:
    text = _tool_cards_text()

    for status in (
        "invalid_input",
        "privacy_blocked",
        "disabled",
        "provider_unavailable",
        "provider_error",
    ):
        assert f"`{status}`" in text


def test_tool_cards_document_school_sql_allowlist_owner() -> None:
    text = _tool_cards_text()

    assert "只有 `ddl_search` 能扩大 SQL 白名单" in text
    assert "扩大 SQL 白名单" in text
    assert "record_schema 不匹配" in text
    assert "接受输入 rows 伪造证据" in text


def test_inventory_links_multi_agent_iteration_assets() -> None:
    text = _inventory_text()

    assert "docs/multi_agent_iteration_assets.md" in text
    assert "`RawDataEvidencePayload`" in text
    assert "`validate_data_evidence_payload()`" in text
    for field in (
        "task_id",
        "allowed",
        "intent",
        "dataset_label",
        "row_count",
        "sql_lineage",
        "evidence_summary",
        "raw_sql_handle",
        "original_count",
        "included_count",
        "truncated",
    ):
        assert f"`{field}`" in text


def test_multi_agent_iteration_assets_document_core_asset_packages() -> None:
    text = _mas_assets_text()

    for heading in (
        "## 1. 图拓扑与状态机转移矩阵",
        "## 2. 规划契约与多模态产物注册表",
        "## 3. 工具血缘与数据沙箱白名单",
        "## 4. 高维证据与柔性角色提示词资产库",
        "## 5. 流式分流策略与响应合成协议",
        "## 6. 回归单测与性能对账测试账本",
    ):
        assert heading in text
    for source in (
        "gateway_core/agents/contracts/workflow_contracts.py",
        "gateway_core/agents/contracts/inter_agent_state.py",
        "gateway_core/agents/school_sql/sql_tools.py",
        "gateway_core/agents/school_sql/final_handoff.py",
        "tests/test_tool_contract.py",
        "tests/test_output_contracts.py",
    ):
        assert f"`{source}`" in text


def test_inventory_lists_workflow_steps() -> None:
    text = _inventory_text()

    assert f"`{SCHOOL_DATA_ANSWER_WORKFLOW.workflow_id}`" in text
    for step in SCHOOL_DATA_ANSWER_WORKFLOW.steps:
        assert f"`{step.step_id}`" in text
        assert f"`{step.executor.executor_id}`" in text


def test_inventory_lists_registered_prompt_ids() -> None:
    text = _inventory_text()

    # The registry has no public iterator yet; the inventory test is the one
    # place that intentionally reads the registered ids to prevent doc drift.
    for prompt_id in sorted(PROMPT_REGISTRY._templates):  # noqa: SLF001
        assert f"`{prompt_id}`" in text


def test_prompt_cards_list_registered_prompt_templates() -> None:
    text = _prompt_cards_text()

    for prompt_id in sorted(PROMPT_REGISTRY._templates):  # noqa: SLF001
        template = PROMPT_REGISTRY.get(prompt_id)
        assert f"### `{prompt_id}`" in text
        assert f"`{template.version}`" in text
        assert template.description in text
        for tag in template.tags:
            assert f"`{tag}`" in text


def test_prompt_cards_list_known_inline_prompt_surfaces() -> None:
    text = _prompt_cards_text()

    for prompt_surface in (
        "answers.context.agent_label",
        "answers.context.domain_layer",
        "answers.context.payload_json",
        "build_data_format_guard",
        "prompt_domains",
        "TripleAxisPromptSynthesizer",
        "build_class_grade_portrait_note",
        "build_student_teacher_portrait_note",
        "_stream_contract_plan_chat",
        "PER_TURN_CONTRACT_PLAN_SCHEMA_TEXT",
    ):
        assert f"`{prompt_surface}`" in text


def _structured_tool_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "from_function":
            continue
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                names.add(keyword.value.value)
    return names
