from __future__ import annotations

from typing import Any


def record_tool_result(owner: Any, tool_name: str, payload: dict[str, Any]) -> None:
    # 工具契约校验应显式失败；缺失 contract 代表调用链契约已经断裂。
    contract = getattr(owner, "tool_contract")
    contract.record_tool_result(tool_name, payload)
