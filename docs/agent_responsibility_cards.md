# Agent Responsibility Cards

This file records the operational contract for every skill registered in
`SKILL_REGISTRY`. It complements `docs/agent_tool_prompt_inventory.md`: the
inventory is the map; these cards are the per-agent operating manual.

## Shared Agent Contract

All registered skills implement `BaseAgentSkill.astream(state, ctx)` and emit
normalized `SkillEvent` objects. Runtime-only objects such as request handles,
model clients, test doubles, emitters, and trace objects belong in `ctx`, not in
`UniversalAgentState`.

For artifact agents, `BaseMultimodalAgentSkill` requires a typed
`MultimodalOutputContract` and emits a proof-bearing `evidence_completed` event.

## `school_sql`

| Field | Value |
| --- | --- |
| Registry class | `gateway_core.agents.school_sql.school_sql_skill.SchoolSqlSkill` |
| Default role | `data_agent` |
| Owned outputs | `data_evidence` |
| Declared tools | `ddl_search`, `sql_db_query`, `sample_table_rows` |
| Stream support | yes |
| Main runtime entry | `gateway_core.agents.school_sql.agent_stream.stream_school_sql_agent_native` |
| Prompt ownership | `agents.school_sql.system` |
| Workflow node | `school_sql.react_execute` |
| Focused tests | `tests/test_image_generation_skill.py`, `tests/test_agent_stream_workflow_trace.py`, `tests/test_agent_stream_direct_snapshot.py`, `tests/test_workflow_contracts.py` |

Responsibility:

- Turn a school data question into audited database evidence.
- Use DDL/search/schema/sample tools to identify valid tables and fields.
- Produce `data_evidence`, `evidence_board`, `source_views`, SQL lineage, and
  compact handoff material for downstream answer composition.
- Strip image/PPT/chart wording when multimodal workers are also required, so
  the SQL stage only retrieves evidence.

Must not own:

- Final natural-language answer composition.
- Image, chart, plot, slide, or PPT artifact generation.
- Policy or web evidence retrieval unless explicitly routed through planned
  non-SQL tools.
- Unbounded raw row payloads in cross-agent state.

Failure policy:

- Invalid contracts and missing invariants should fail fast with trace context.
- Tool failures should be represented as tool errors or caveats, not fabricated
  evidence.

Current cleanup focus:

- Keep SQL execution, evidence normalization, final handoff, and final answer
  boundaries separate.
- Continue reducing long orchestration logic in `agent_stream.py` into named
  contract, trace, and prompt helpers.

## `chat`

| Field | Value |
| --- | --- |
| Registry class | `gateway_core.agents.chat.chat_skill.ChatSkill` |
| Default role | `general_chat` |
| Owned outputs | none |
| Declared tools | none |
| Stream support | yes |
| Main runtime entry | `ChatSkill.astream` |
| Prompt ownership | no production PromptRegistry id yet |
| Workflow node | not part of `SCHOOL_DATA_ANSWER_WORKFLOW`; selected by route/contract plan |
| Focused tests | `tests/test_contract_planner.py`, `tests/test_agent_native_flow.py`, `tests/test_pipeline_audit.py` |

Responsibility:

- Handle plain chat or contract-planned `chat` route turns.
- Stream through an injected `chat_stream_fn` when present.
- Fall back to echoing the latest question only as a minimal test/runtime-safe
  behavior.

Must not own:

- School facts, SQL evidence, policy evidence, web evidence, or artifacts.
- Any fabricated answer to a school-data question.

Failure policy:

- If a chat model stream function exists, propagate its events as content or
  process events.
- If no stream function exists, do not invent data; emit only the latest user
  question.

Current cleanup focus:

- Decide whether production chat needs a registered prompt id, or whether the
  current route-specific final answer path remains the only production prompt.

## `image_generator`

| Field | Value |
| --- | --- |
| Registry class | `gateway_core.agents.visual.image_generation_skill.ImageGenerationSkill` |
| Default role | `visual_agent` |
| Owned outputs | `image_artifact` |
| Declared tools | `image_generation` |
| Stream support | yes |
| Main runtime entry | `ImageGenerationSkill.astream` |
| Prompt ownership | `TripleAxisPromptSynthesizer` via `gateway_core.prompts.prompt_domains` |
| Workflow node | universal hub multimodal worker after `data_evidence` |
| Focused tests | `tests/test_image_generation_skill.py`, `tests/test_universal_hub_graph.py` |

Responsibility:

- Convert already-audited SQL lineage into an image artifact.
- Bind artifacts to a 64-character SQL hash.
- Use the latest answer context or compact lineage snapshot as image prompt
  evidence.
- Emit a typed `image_artifact` with `image_md5_proof`, CDN URL, markdown
  render payload, linked SQL hash, and prompt used.

Must not own:

- Database queries.
- Final answer text.
- Writing generated image URLs back into chat history.
- Image generation without audited SQL lineage.

Failure policy:

- Skip generation when SQL lineage is missing or the SQL hash is invalid.
- Record multimodal errors for timeout, missing URL, tool error, or URL
  validation failure.

Current cleanup focus:

- Promote `TripleAxisPromptSynthesizer` prompt matrices into the prompt inventory
  or PromptRegistry if they become production prompt governance surfaces.

## `ppt_generator`

| Field | Value |
| --- | --- |
| Registry class | `gateway_core.agents.ppt.ppt_generation_skill.PptGenerationSkill` |
| Default role | `presentation_agent` |
| Owned outputs | `ppt_artifact` |
| Declared tools | `ppt_generation` |
| Stream support | yes |
| Main runtime entry | `PptGenerationSkill.astream` |
| Prompt ownership | provider payload from SQL lineage; no production PromptRegistry id yet |
| Workflow node | universal hub multimodal worker after `data_evidence` |
| Focused tests | `tests/test_image_generation_skill.py`, `tests/test_universal_hub_graph.py` |

Responsibility:

- Generate a proof-bearing PPT artifact from the current campus analysis state.
- Send a compact provider payload containing model, purpose, school id, and the
  latest SQL lineage.
- Use a provider result when injected, or fall back to `SlideTool` for local PPT
  artifact creation.
- Emit a typed `ppt_artifact` with `ppt_sha256`, CDN URL, title, page count,
  pages preview, and render engine.

Must not own:

- Database queries.
- SQL lineage creation.
- Final answer prose.
- Unvalidated external artifact URLs.

Failure policy:

- Convert provider/local generation `ValueError` into process events.
- Reject untrusted artifact URLs before emitting `ppt_artifact`.

Current cleanup focus:

- If PPT outline prompting becomes more complex, move the provider payload
  prompt/outline policy into a registered prompt or output contract.

## Next Review Order

1. `school_sql`: largest responsibility surface; verify executor/tool/prompt/handoff boundaries first.
2. `image_generator` and `ppt_generator`: verify artifact proof and lineage lock.
3. `chat`: decide whether plain chat remains deliberately minimal or needs a
   production prompt card.
