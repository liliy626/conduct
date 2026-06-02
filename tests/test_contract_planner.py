from __future__ import annotations

from gateway_core.agents.contracts.planner import ContractPlanner


class _Message:
    def __init__(self, content: str):
        self.content = content


class _StructuredFailure:
    def invoke(self, _messages):
        raise RuntimeError("BadRequestError: structured output schema is not supported")


class _StructuredAndPlainFailureModel:
    def with_structured_output(self, _schema):
        return _StructuredFailure()

    def invoke(self, _messages):
        raise RuntimeError("plain planner call failed")


class _StructuredFailsPlainSucceedsModel:
    def with_structured_output(self, _schema):
        return _StructuredFailure()

    def invoke(self, _messages):
        return _Message(
            '{"required_outputs":["plot_artifact","unknown_output"],'
            '"allowed_tools":["plot","not_available"],'
            '"answer_mode":"plot",'
            '"reason":""}'
        )


def test_contract_planner_uses_plain_json_fallback_after_structured_bad_request():
    plan = ContractPlanner(_StructuredFailsPlainSucceedsModel()).plan_turn(
        question="把本周请假数据画成图",
        available_tools=["plot", "time"],
    )

    assert plan.required_outputs == ["plot_artifact"]
    assert plan.allowed_tools == ["plot"]
    assert plan.answer_mode == "plot"
    assert "plain_json_fallback_after_structured_error" in plan.reason
    assert "BadRequestError" in plan.reason


def test_contract_planner_records_structured_and_plain_failure_details():
    plan = ContractPlanner(_StructuredAndPlainFailureModel()).plan_turn(
        question="今天教师请假人数是多少？",
        available_tools=["time"],
    )

    assert plan.required_outputs == []
    assert plan.allowed_tools == []
    assert plan.answer_mode == "data"
    assert "contract planner unavailable: RuntimeError: plain planner call failed" in plan.reason
    assert "structured_error=RuntimeError: BadRequestError" in plan.reason
