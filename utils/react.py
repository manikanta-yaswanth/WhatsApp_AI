"""Shared bounded ReAct loop; each specialist owns its own tool node."""

from datetime import date
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from Models.schema import AgentState
from utils import prompts
from utils.settings import Settings


def build_specialist_graph(
    intent: str,
    tool_names: list[str],
    llm: BaseChatModel,
    tools: dict[str, BaseTool],
    settings: Settings,
) -> CompiledStateGraph:
    selected = [tools[name] for name in tool_names if name in tools]
    bound = llm.bind_tools(selected)

    async def agent(state: AgentState) -> dict[str, Any]:
        messages = state.get("messages") or [
            SystemMessage(
                prompts.BASE_RULES.format(today=date.today().isoformat()) + "\n" + prompts.SPECIALISTS[intent]
            ),
            HumanMessage(state["user_query"]),
        ]
        msg = await bound.ainvoke(messages)
        if not isinstance(msg, AIMessage):
            raise TypeError("Chat model must return AIMessage")
        usage = msg.usage_metadata
        update: dict[str, Any] = {
            "intent": intent,
            "messages": ([*messages, msg] if not state.get("messages") else [msg]),
            "iterations": state.get("iterations", 0) + 1,
            "prompt_tokens": state.get("prompt_tokens", 0) + (usage["input_tokens"] if usage else 0),
            "completion_tokens": state.get("completion_tokens", 0) + (usage["output_tokens"] if usage else 0),
        }
        if msg.tool_calls:
            update["tool_calls"] = state.get("tool_calls", []) + [
                {"name": tc["name"], "args": tc["args"]} for tc in msg.tool_calls
            ]
        else:
            update["answer"] = msg.text
        return update

    def after_agent(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "finalize" if state.get("iterations", 0) >= settings.agent_max_iterations else "tools"
        return END

    async def finalize(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        dangling = [
            ToolMessage("Skipped: tool budget exhausted.", tool_call_id=tc["id"], name=tc["name"])
            for tc in (last.tool_calls if isinstance(last, AIMessage) else [])
        ]
        msg = await llm.ainvoke(
            state["messages"] + dangling + [HumanMessage("Give your best final answer now using only the data above.")]
        )
        return {"messages": dangling + [msg], "answer": msg.text}

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(selected, handle_tool_errors=True))
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", after_agent, ["tools", "finalize", END])
    graph.add_edge("tools", "agent")
    graph.add_edge("finalize", END)
    return graph.compile()
