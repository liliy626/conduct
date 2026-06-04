from __future__ import annotations

from typing import Iterable

from gateway_core.tools.core.schemas import GatewayTool, ToolContext, ToolInput, ToolResult


class GatewayToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, GatewayTool] = {}
        self._aliases: dict[str, str] = {}

    def register(self, tool: GatewayTool) -> None:
        name = _clean_name(tool.name)
        if not name:
            raise ValueError("tool name is required")
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = tool
        for alias in getattr(tool, "aliases", ()) or ():
            clean_alias = _clean_name(alias)
            if not clean_alias:
                continue
            if clean_alias in self._aliases and self._aliases[clean_alias] != name:
                raise ValueError(f"tool alias already registered: {clean_alias}")
            if clean_alias in self._tools and clean_alias != name:
                raise ValueError(f"tool alias conflicts with registered tool: {clean_alias}")
            self._aliases[clean_alias] = name

    def get(self, name_or_alias: str) -> GatewayTool | None:
        key = _clean_name(name_or_alias)
        return self._tools.get(key) or self._tools.get(self._aliases.get(key, ""))

    def resolve_name(self, name_or_alias: str) -> str:
        key = _clean_name(name_or_alias)
        if key in self._tools:
            return key
        return self._aliases.get(key, "")

    def list(self) -> list[GatewayTool]:
        return [self._tools[name] for name in sorted(self._tools)]

    def names(self, *, include_aliases: bool = False) -> set[str]:
        names = set(self._tools)
        if include_aliases:
            names.update(self._aliases)
        return names

    def filter(self, names_or_aliases: Iterable[str]) -> list[GatewayTool]:
        seen: set[str] = set()
        out: list[GatewayTool] = []
        for item in names_or_aliases:
            resolved = self.resolve_name(item)
            if not resolved or resolved in seen:
                continue
            tool = self._tools.get(resolved)
            if tool is None:
                continue
            seen.add(resolved)
            out.append(tool)
        return out

    def run(
        self,
        name_or_alias: str,
        tool_input: ToolInput | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        tool = self.get(name_or_alias)
        if tool is None:
            return ToolResult(ok=False, error=f"tool not registered: {_clean_name(name_or_alias)}")
        return tool.run(tool_input or ToolInput(), context or ToolContext())


def _clean_name(value: str) -> str:
    return str(value or "").strip()
