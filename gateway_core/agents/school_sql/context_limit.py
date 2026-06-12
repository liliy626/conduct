from __future__ import annotations

from typing import Any


_CONTEXT_LIMIT_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "max context",
    "too many tokens",
    "token limit",
    "input is too long",
    "prompt is too long",
    "maximum number of tokens",
    "exceeds the model",
    "exceeded model",
    "超出上下文",
    "上下文长度",
    "上下文限制",
    "超过模型",
    "超过最大",
    "token超限",
    "tokens超限",
)


def is_context_limit_error(exc: BaseException) -> bool:
    text = _exception_text(exc).lower()
    return any(marker in text for marker in _CONTEXT_LIMIT_MARKERS)


def build_context_limit_clarification(question: str) -> str:
    clean_question = " ".join(str(question or "").split())
    prefix = f"刚才这个问题「{clean_question}」" if clean_question else "刚才这个问题"
    return (
        f"{prefix}在查询过程中涉及的数据和工具上下文太多，最后一次调用上游模型时超过了模型上下文限制。\n\n"
        "为了继续查下去，你可以先指定一个更具体的方向，例如：\n"
        "1. 德育/行规/纪律\n"
        "2. 教学/教研/作业/考试\n"
        "3. 学生请假/健康/心理\n"
        "4. 教师发展/考勤/成果/职称\n"
        "5. 后勤/资产/报修/安全\n"
        "6. 行政/人事/党建/公文\n\n"
        "也可以直接补充时间和对象，比如“5月学生请假和健康情况”或“本月教学教研重点”。"
    )


def _exception_text(exc: BaseException) -> str:
    parts = [type(exc).__name__, str(exc)]
    for attr in ("body", "response", "message", "code"):
        value = getattr(exc, attr, None)
        if value:
            parts.append(str(value))
    return " ".join(parts)
