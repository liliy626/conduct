from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from gateway_core.agents.universal_hub.graph_builder import compile_universal_hub_graph


def generate_mas_topology_map(
    *,
    output_path: str | Path = "mas_hub_topology.md",
    graph_factory: Callable[[], Any] = compile_universal_hub_graph,
) -> Path:
    """Export the compiled MAS hub graph as Mermaid Markdown."""
    path = Path(output_path)
    print("正在加载 conduct 工作树并编译多智能体核心大图...")

    hub_graph = graph_factory()
    mermaid_code = hub_graph.get_graph().draw_mermaid()

    markdown = (
        "# MAS 多智能体网关全局数据流转拓扑大图\n\n"
        "> 提示：可以直接在 VS Code Markdown 预览中查看，或复制下方代码至 mermaid.live。\n\n"
        "> 注意：当前 Universal Hub 使用 Command(goto=...) 动态跳转；LangGraph 静态 Mermaid "
        "可能不会展开这些运行期边，动态链路仍需结合 trace/SSE 事件核对。\n\n"
        f"```mermaid\n{mermaid_code}\n```\n"
    )
    path.write_text(markdown, encoding="utf-8")
    print(f"拓扑导出成功：{path.resolve()}")
    return path


if __name__ == "__main__":
    try:
        generate_mas_topology_map()
    except Exception as exc:
        print(f"拓扑导出失败：{type(exc).__name__}: {exc}")
        raise
