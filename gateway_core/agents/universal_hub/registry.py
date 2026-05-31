from __future__ import annotations

from gateway_core.agents.chat.chat_skill import ChatSkill
from gateway_core.agents.policy_only.policy_skill import PolicySkill
from gateway_core.agents.ppt.ppt_generation_skill import PptGenerationSkill
from gateway_core.agents.school_sql.school_sql_skill import SchoolSqlSkill
from gateway_core.agents.universal_hub.models import SkillSpec
from gateway_core.agents.visual.image_generation_skill import ImageGenerationSkill


SKILL_REGISTRY: dict[str, SkillSpec] = {
    "school_sql": SkillSpec(
        name="school_sql",
        skill_cls=SchoolSqlSkill,
        outputs=frozenset({"data_evidence"}),
        tools=frozenset({"ddl_search", "sql_db_query", "sample_table_rows"}),
        supports_stream=True,
        default_model_role="data_agent",
    ),
    "policy": SkillSpec(
        name="policy",
        skill_cls=PolicySkill,
        outputs=frozenset({"policy_evidence"}),
        tools=frozenset({"official_policy_search", "web_search"}),
        supports_stream=True,
        default_model_role="policy_assistant",
    ),
    "chat": SkillSpec(
        name="chat",
        skill_cls=ChatSkill,
        outputs=frozenset(),
        tools=frozenset(),
        supports_stream=True,
        default_model_role="general_chat",
    ),
    "image_generator": SkillSpec(
        name="image_generator",
        skill_cls=ImageGenerationSkill,
        outputs=frozenset({"image_artifact"}),
        tools=frozenset({"image_generation"}),
        supports_stream=True,
        default_model_role="visual_agent",
    ),
    "ppt_generator": SkillSpec(
        name="ppt_generator",
        skill_cls=PptGenerationSkill,
        outputs=frozenset({"ppt_artifact"}),
        tools=frozenset({"ppt_generation"}),
        supports_stream=True,
        default_model_role="presentation_agent",
    ),
}


def mandatory_candidate_skill_names(required: frozenset[str], completed: frozenset[str]) -> list[str]:
    missing_outputs = frozenset(required).difference(completed)
    if not missing_outputs:
        return ["FINISH"]
    return [
        skill_name
        for skill_name, spec in SKILL_REGISTRY.items()
        if spec.outputs.intersection(missing_outputs)
    ]
