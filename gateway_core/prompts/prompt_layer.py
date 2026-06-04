from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


PromptRenderer = Callable[..., str]


@dataclass(frozen=True)
class PromptTemplate:
    prompt_id: str
    version: str
    renderer: PromptRenderer
    description: str = ""
    tags: tuple[str, ...] = ()

    def render(self, **context: Any) -> "PromptRender":
        return PromptRender(
            prompt_id=self.prompt_id,
            version=self.version,
            text=str(self.renderer(**context)),
            metadata={"description": self.description, "tags": list(self.tags)},
        )


@dataclass(frozen=True)
class PromptRender:
    prompt_id: str
    version: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    parts: tuple["PromptRender", ...] = ()


class PromptRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, dict[str, PromptTemplate]] = {}

    def register(self, template: PromptTemplate) -> None:
        prompt_id = str(template.prompt_id or "").strip()
        version = str(template.version or "").strip()
        if not prompt_id:
            raise ValueError("prompt_id is required")
        if not version:
            raise ValueError("prompt version is required")
        self._templates.setdefault(prompt_id, {})[version] = template

    def get(self, prompt_id: str, version: str | None = None) -> PromptTemplate:
        prompt_id = str(prompt_id or "").strip()
        versions = self._templates.get(prompt_id)
        if not versions:
            raise KeyError(f"unknown prompt id: {prompt_id}")
        resolved_version = str(version or "").strip() or sorted(versions)[-1]
        template = versions.get(resolved_version)
        if template is None:
            raise KeyError(f"unknown prompt version: {prompt_id}@{resolved_version}")
        return template

    def render(self, prompt_id: str, version: str | None = None, **context: Any) -> PromptRender:
        return self.get(prompt_id, version).render(**context)

    def versions(self, prompt_id: str) -> tuple[str, ...]:
        return tuple(sorted(self._templates.get(str(prompt_id or "").strip(), {})))


class PromptBuilder:
    def __init__(self, registry: PromptRegistry | None = None, *, separator: str = "\n\n") -> None:
        self.registry = registry or PROMPT_REGISTRY
        self.separator = separator
        self._parts: list[PromptRender] = []

    def add(self, prompt_id: str, version: str | None = None, **context: Any) -> "PromptBuilder":
        self._parts.append(self.registry.render(prompt_id, version, **context))
        return self

    def add_if(self, condition: bool, prompt_id: str, version: str | None = None, **context: Any) -> "PromptBuilder":
        if condition:
            self.add(prompt_id, version, **context)
        return self

    def literal(self, text: str, *, prompt_id: str = "literal", version: str = "inline") -> "PromptBuilder":
        self._parts.append(PromptRender(prompt_id=prompt_id, version=version, text=str(text or "")))
        return self

    def build(self, *, prompt_id: str = "composed", version: str | None = None) -> PromptRender:
        text = self.separator.join(part.text for part in self._parts if str(part.text or "").strip())
        resolved_version = version or "+".join(f"{part.prompt_id}@{part.version}" for part in self._parts)
        return PromptRender(prompt_id=prompt_id, version=resolved_version, text=text, parts=tuple(self._parts))


PROMPT_REGISTRY = PromptRegistry()
