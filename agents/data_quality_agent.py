from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from utils.react import build_specialist_graph
from utils.settings import Settings

TOOLS = ["data_quality_report", "count_contacts", "search_contacts"]


def build_graph(llm: BaseChatModel, tools: dict[str, BaseTool], settings: Settings) -> CompiledStateGraph:
    return build_specialist_graph("data_quality", TOOLS, llm, tools, settings)
