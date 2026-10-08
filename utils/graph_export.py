"""Offline PNG export from the actual LangGraph nodes and edges (no API calls)."""

import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from PIL import Image, ImageDraw

from agents.whatsapp_agent import SPECIALISTS, build_agent_graph
from utils.database import DatabaseUtil
from utils.settings import Settings
from utils.tools import build_tools


class DiagramModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "diagram-only"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "DiagramModel":  # type: ignore[override]
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        raise RuntimeError("Diagram export must not invoke a model")


def draw_graph(graph: Any, path: Path) -> None:
    nodes = list(graph.nodes)
    edges = graph.edges
    levels = {"__start__": 0}
    queue = deque(["__start__"])
    while queue:
        source = queue.popleft()
        for edge in edges:
            if edge.source == source and edge.target not in levels and edge.target != "__end__":
                levels[edge.target] = levels[source] + 1
                queue.append(edge.target)
    levels["__end__"] = max(levels.values()) + 1
    layers: dict[int, list[str]] = defaultdict(list)
    for node in nodes:
        layers[levels.get(node, 1)].append(node)
    width = max(len(layer) for layer in layers.values()) * 240 + 80
    height = (max(layers) + 1) * 150 + 80
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    positions = {}
    for level, layer in layers.items():
        for index, node in enumerate(layer):
            positions[node] = ((index + 0.5) * width / len(layer), level * 150 + 65)
    for edge in edges:
        start, end = positions[edge.source], positions[edge.target]
        direction = 1 if end[1] > start[1] else -1
        start = (start[0], start[1] + direction * 28)
        end = (end[0], end[1] - direction * 28)
        draw.line([start, end], fill="#94a3b8", width=2)
        angle = math.atan2(end[1] - start[1], end[0] - start[0])
        arrow = [end] + [
            (end[0] - 12 * math.cos(angle + sign * 0.4), end[1] - 12 * math.sin(angle + sign * 0.4)) for sign in (-1, 1)
        ]
        draw.polygon(arrow, fill="#64748b")
    for node, (x, y) in positions.items():
        draw.rounded_rectangle(
            (x - 100, y - 28, x + 100, y + 28), radius=10, fill="#e0f2fe", outline="#0369a1", width=2
        )
        draw.text((x, y), node.strip("_"), anchor="mm", fill="#0f172a", font_size=18)
    canvas.save(path)


def export_graphs(output_dir: str, settings: Settings) -> list[str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    model = DiagramModel()
    tools = build_tools(DatabaseUtil.from_settings(settings), settings, model)
    graphs = {"whatsapp_agent": build_agent_graph(model, tools, settings)}
    graphs.update({f"{name}_agent": module.build_graph(model, tools, settings) for name, module in SPECIALISTS.items()})
    outputs = []
    for name, graph in graphs.items():
        path = directory / f"{name}_graph.png"
        draw_graph(graph.get_graph(), path)
        outputs.append(str(path))
    return outputs
