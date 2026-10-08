from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from utils.react import build_specialist_graph
from utils.settings import Settings

TOOLS = ["get_message_stats", "count_contacts", "search_contacts", "data_quality_report"]


def build_graph(llm: BaseChatModel, tools: dict[str, BaseTool], settings: Settings) -> CompiledStateGraph:
    return build_specialist_graph("analytics", TOOLS, llm, tools, settings)
