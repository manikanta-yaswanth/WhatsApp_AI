from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from utils.react import build_specialist_graph
from utils.settings import Settings

TOOLS = ["classify_recent_conversations", "search_contacts", "get_recent_conversations", "get_recent_messages"]


def build_graph(llm: BaseChatModel, tools: dict[str, BaseTool], settings: Settings) -> CompiledStateGraph:
    return build_specialist_graph("classification", TOOLS, llm, tools, settings)
