from __future__ import annotations

import re
from typing import Any, Mapping, Tuple


SENSITIVE_KEYWORDS = {
    "student",
    "students",
    "student_name",
    "student_names",
    "parent",
    "parents",
    "parent_name",
    "teacher",
    "teacher_name",
    "teacher_names",
    "person",
    "person_name",
    "name_list",
    "names",
    "raw_row",
    "raw_rows",
    "rows",
    "evidence_rows",
    "明细",
    "姓名",
    "学生",
    "家长",
    "教师",
    "老师",
}

NAME_LIST_PATTERN = re.compile(r"([\u4e00-\u9fff]{2,4}[、,， ]+){2,}[\u4e00-\u9fff]{2,4}")
TEACHER_NAME_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,4}老师")
STUDENT_NAME_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,4}(同学|学生)")


def contains_sensitive_context(value: Any, path: str = "") -> Tuple[bool, str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(token in lowered or token in key_text for token in SENSITIVE_KEYWORDS):
                return True, f"sensitive key '{path + key_text}'"
            blocked, reason = contains_sensitive_context(item, path=f"{path}{key_text}.")
            if blocked:
                return blocked, reason
        return False, ""

    if isinstance(value, list):
        if value and all(isinstance(item, Mapping) for item in value):
            return True, f"raw row list at '{path[:-1] or 'context'}'"
        if len(value) > 1 and all(isinstance(item, str) for item in value):
            return True, f"name/detail list at '{path[:-1] or 'context'}'"
        for index, item in enumerate(value):
            blocked, reason = contains_sensitive_context(item, path=f"{path}{index}.")
            if blocked:
                return blocked, reason
        return False, ""

    if isinstance(value, str) and NAME_LIST_PATTERN.search(value):
        return True, f"possible person-name list at '{path[:-1] or 'context'}'"

    return False, ""


def sanitize_visual_prompt(prompt: str) -> str:
    text = str(prompt or "").strip()
    text = TEACHER_NAME_PATTERN.sub("某教师", text)
    text = STUDENT_NAME_PATTERN.sub("某学生", text)
    text = NAME_LIST_PATTERN.sub("多人名单", text)
    return re.sub(r"\s+", " ", text)[:1200]
