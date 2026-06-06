from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gateway_core.agents.contracts.inter_agent_state import validate_data_evidence_payload
from gateway_core.agents.contracts.models import PerTurnContractPlan
from gateway_core.agents.contracts.output_contracts import OUTPUT_CONTRACT_VERSION


@dataclass
class ToolContract:
    """Per-turn contract for required tool outputs and completion state."""

    question: str
    contract_version: str = OUTPUT_CONTRACT_VERSION
    required_outputs: set[str] = field(default_factory=set)
    allowed_tools: set[str] = field(default_factory=set)
    answer_mode: str = "data"
    answer_focus: str = ""
    reason: str = ""
    completed_outputs: set[str] = field(default_factory=set)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def needs(self, output_name: str) -> bool:
        return output_name in self.required_outputs

    def is_completed(self, output_name: str) -> bool:
        return output_name in self.completed_outputs

    def missing_outputs(self) -> list[str]:
        return sorted(self.required_outputs - self.completed_outputs)

    def record_tool_result(self, tool_name: str, payload: dict[str, Any]) -> None:
        artifacts = payload.get("artifacts")
        if isinstance(artifacts, list):
            self.artifacts.extend([item for item in artifacts if isinstance(item, dict)])
        if tool_name == "official_policy_search" and _tool_result_attempt_completed(payload):
            self.completed_outputs.add("policy_evidence")
        if tool_name == "web_search" and _tool_result_attempt_completed(payload):
            self.completed_outputs.add("web_evidence")
        if tool_name == "generate_image_tool" and _has_artifact(payload, "image"):
            self.completed_outputs.add("image_artifact")
        if tool_name == "chart" and _has_artifact(payload, "chart"):
            self.completed_outputs.add("chart_artifact")
        if tool_name == "plot" and _has_artifact(payload, "plot"):
            self.completed_outputs.add("plot_artifact")
        if tool_name == "slide" and any(
            _has_artifact(payload, artifact_type) for artifact_type in ("pptx", "slide_preview", "deck_source")
        ):
            self.completed_outputs.add("slide_artifact")
        if tool_name in {"sql_db_query", "jsonb_recordset_query"}:
            validate_data_evidence_payload(payload)
            self.completed_outputs.add("data_evidence")

    def handoff_block_payload(self) -> dict[str, Any] | None:
        missing = self.missing_outputs()
        if not missing:
            return None
        return {
            "contract_version": self.contract_version,
            "contract_blocked": True,
            "missing_outputs": missing,
            "message": _missing_message(missing),
            "completed_outputs": sorted(self.completed_outputs),
        }

    def prompt_text(self) -> str:
        lines = ["【本轮工具合同】："]
        lines.append(f"- 回答模式：{self.answer_mode or 'data'}")
        if self.answer_focus:
            lines.append(f"- 回答焦点：{self.answer_focus}")
        if self.reason:
            lines.append(f"- 规划理由：{self.reason}")
        lines.append(f"- 允许的非 SQL 可选工具：{', '.join(sorted(self.allowed_tools)) if self.allowed_tools else '无'}")
        lines.append(f"- 必须完成产物：{', '.join(sorted(self.required_outputs)) if self.required_outputs else '无'}")
        lines.append("- final_answer_handoff 前必须满足全部必需产物。")
        lines.append("- 产物完成后不要重复调用对应重型工具。")
        return "\n".join(lines)

    def trace_payload(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "question": self.question,
            "required_outputs": sorted(self.required_outputs),
            "allowed_tools": sorted(self.allowed_tools),
            "answer_mode": self.answer_mode,
            "answer_focus": self.answer_focus,
            "reason": self.reason,
            "completed_outputs": sorted(self.completed_outputs),
            "artifact_count": len(self.artifacts),
        }


def build_tool_contract(question: str, *, plan: PerTurnContractPlan | None = None) -> ToolContract:
    if plan is None:
        plan = PerTurnContractPlan(required_outputs=[], allowed_tools=[], answer_mode="data", reason="")
    return ToolContract(
        question=str(question or ""),
        contract_version=str(getattr(plan, "contract_version", "") or OUTPUT_CONTRACT_VERSION),
        required_outputs=set(str(item or "").strip() for item in plan.required_outputs if str(item or "").strip()),
        allowed_tools=set(str(item or "").strip() for item in plan.allowed_tools if str(item or "").strip()),
        answer_mode=str(plan.answer_mode or "data"),
        answer_focus=str(getattr(plan, "answer_focus", "") or "").strip(),
        reason=str(plan.reason or "").strip(),
    )


def _has_artifact(payload: dict[str, Any], artifact_type: str) -> bool:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return False
    return any(isinstance(item, dict) and item.get("type") == artifact_type for item in artifacts)


def _tool_result_attempt_completed(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is True:
        return True
    if payload.get("error"):
        return False
    return any(isinstance(payload.get(key), list) for key in ("sources", "evidence", "artifacts"))


def _missing_message(missing: list[str]) -> str:
    if "image_artifact" in missing:
        return "final_answer_handoff 被工具合同拒绝：用户要求图片，但 image_artifact 尚未完成。请先调用 generate_image_tool。"
    if "plot_artifact" in missing:
        return "final_answer_handoff 被工具合同拒绝：用户要求 PNG 数据图，但 plot_artifact 尚未完成。请先用已查 rows 调用 plot。"
    if "chart_artifact" in missing:
        return "final_answer_handoff 被工具合同拒绝：用户要求图表，但 chart_artifact 尚未完成。请先调用 chart。"
    if "slide_artifact" in missing:
        return "final_answer_handoff 被工具合同拒绝：用户要求 PPT/汇报材料，但 slide_artifact 尚未完成。请先调用 slide。"
    return "final_answer_handoff 被工具合同拒绝：仍有必需产物未完成。"
