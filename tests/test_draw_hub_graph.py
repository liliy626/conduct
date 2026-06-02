from __future__ import annotations

from pathlib import Path

import draw_hub_graph


class _FakeDrawableGraph:
    def draw_mermaid(self) -> str:
        return "graph TD\n    __start__ --> supervisor_node\n"


class _FakeHubGraph:
    def get_graph(self) -> _FakeDrawableGraph:
        return _FakeDrawableGraph()


def test_generate_mas_topology_map_writes_mermaid_markdown(tmp_path: Path) -> None:
    output_path = tmp_path / "mas_hub_topology.md"

    result_path = draw_hub_graph.generate_mas_topology_map(
        output_path=output_path,
        graph_factory=lambda: _FakeHubGraph(),
    )

    markdown = output_path.read_text(encoding="utf-8")
    assert result_path == output_path
    assert "# MAS 多智能体网关全局数据流转拓扑大图" in markdown
    assert "```mermaid" in markdown
    assert "__start__ --> supervisor_node" in markdown
