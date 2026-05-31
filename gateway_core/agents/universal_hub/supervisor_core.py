from __future__ import annotations

from gateway_core.agents.universal_hub.registry import SKILL_REGISTRY


VISUAL_OUTPUT_KEYWORDS = (
    "图",
    "画",
    "插图",
    "视觉",
    "大屏",
    "图表",
    "可视化",
    "infographic",
    "illustration",
    "chart",
)


def determine_required_outputs(user_query: str, current_outputs: list[str]) -> list[str]:
    """Return per-turn output requirements with deterministic visual intent.

    Visual requests need both audited data and a generated image. The order is
    important: data evidence must be produced before the image skill can bind
    itself to a SQL lineage hash.
    """

    outputs = list(dict.fromkeys(current_outputs))
    query = str(user_query or "").strip().lower()
    if any(keyword in query for keyword in VISUAL_OUTPUT_KEYWORDS):
        if "data_evidence" not in outputs:
            outputs.append("data_evidence")
        if "image_artifact" not in outputs:
            outputs.append("image_artifact")
    return outputs


def get_mandatory_candidate_skills(required: frozenset[str], completed: frozenset[str]) -> list[str]:
    missing_outputs = frozenset(required).difference(completed)
    if not missing_outputs:
        return ["FINISH"]
    candidates: list[str] = []
    for skill_name, spec in SKILL_REGISTRY.items():
        if spec.outputs.intersection(missing_outputs):
            candidates.append(skill_name)
    return candidates
