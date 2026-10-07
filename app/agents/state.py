from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    user_query: str
    intent: str
    messages: Annotated[list[AnyMessage], add_messages]
    tool_calls: list[dict[str, Any]]
    iterations: int
    answer: str
    evaluation: dict[str, Any] | None
    retry_count: int
    judge_enabled: bool
    expected: str | None
    prompt_tokens: int
    completion_tokens: int
