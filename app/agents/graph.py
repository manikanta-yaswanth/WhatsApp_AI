"""LangGraph ReAct agent: route -> specialist agent <-> tools -> judge -> (retry) -> END."""

from datetime import date
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from app.agents.judge_agent import collect_observations, judge_answer
from app.agents.state import AgentState
from app.agents.tools import AGENT_TOOLSETS
from app.config.settings import Settings
from app.llm import prompts
from app.llm.structured import StructuredOutputError, ainvoke_structured
from app.schemas.agent import IntentDecision

_KEYWORDS = {
    "data_quality": ["duplicate", "quality", "missing", "invalid", "null"],
    "classification": ["meeting", "job", "follow up", "follow-up", "urgent", "spam", "classif", "categor", "important"],
    "analytics": ["how many", "count", "number of", "stat", "trend", "most active", "scrape run"],
    "message": ["message", "said", "say", "latest from", "texted", "wrote"],
    "contact": ["contact", "phone", "number", "named"],
}


def keyword_intent(query: str) -> str:
    q = query.lower()
    for intent, words in _KEYWORDS.items():
        if any(w in q for w in words):
            return intent
    return "search"


def _usage(msg: AIMessage) -> tuple[int, int]:
    u = msg.usage_metadata
    return (u["input_tokens"], u["output_tokens"]) if u else (0, 0)


def build_agent_graph(
    llm: BaseChatModel,
    tools: dict[str, BaseTool],
    settings: Settings,
    judge_llm: BaseChatModel | None = None,
) -> CompiledStateGraph:
    judge_llm = judge_llm or llm
    tool_node = ToolNode(list(tools.values()), handle_tool_errors=True)

    def toolset(intent: str) -> list[BaseTool]:
        names = AGENT_TOOLSETS.get(intent) or list(tools)
        return [tools[n] for n in names if n in tools]

    async def route(state: AgentState) -> dict[str, Any]:
        query = state["user_query"]
        try:
            decision = await ainvoke_structured(
                llm, IntentDecision, [SystemMessage(prompts.ROUTER), HumanMessage(query)]
            )
            intent = decision.intent
        except StructuredOutputError:
            intent = keyword_intent(query)  # type: ignore[assignment]
        system = prompts.BASE_RULES.format(today=date.today().isoformat()) + "\n" + prompts.SPECIALISTS[intent]
        return {
            "intent": intent,
            "messages": [SystemMessage(system), HumanMessage(query)],
            "iterations": 0,
            "retry_count": state.get("retry_count", 0),
            "tool_calls": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    async def agent(state: AgentState) -> dict[str, Any]:
        bound = llm.bind_tools(toolset(state["intent"]))
        msg = await bound.ainvoke(state["messages"])
        assert isinstance(msg, AIMessage)
        p, c = _usage(msg)
        update: dict[str, Any] = {
            "messages": [msg],
            "iterations": state.get("iterations", 0) + 1,
            "prompt_tokens": state.get("prompt_tokens", 0) + p,
            "completion_tokens": state.get("completion_tokens", 0) + c,
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
            if state.get("iterations", 0) >= settings.agent_max_iterations:
                return "finalize"
            return "tools"
        return "judge" if state.get("judge_enabled", True) else END

    async def finalize(state: AgentState) -> dict[str, Any]:
        """Iteration budget exhausted: answer from what has been observed, without more tools."""
        last = state["messages"][-1]
        dangling = [
            ToolMessage("Skipped: tool budget exhausted.", tool_call_id=tc["id"], name=tc["name"])
            for tc in (last.tool_calls if isinstance(last, AIMessage) else [])
        ]
        msg = await llm.ainvoke(
            state["messages"] + dangling + [HumanMessage("Give your best final answer now using only the data above.")]
        )
        return {"messages": dangling + [msg], "answer": msg.text}

    def after_finalize(state: AgentState) -> str:
        return "judge" if state.get("judge_enabled", True) else END

    async def judge(state: AgentState) -> dict[str, Any]:
        try:
            result = await judge_answer(
                judge_llm,
                state["user_query"],
                state.get("answer", ""),
                collect_observations(list(state["messages"])),
                state.get("expected"),
            )
            evaluation = {**result.model_dump(), "final_score": result.final_score, "schema_compliant": True}
        except StructuredOutputError as exc:
            evaluation = {"final_score": None, "schema_compliant": False, "reasoning": f"judge failed: {exc}"[:500]}
        return {"evaluation": evaluation}

    def after_judge(state: AgentState) -> str:
        score = (state.get("evaluation") or {}).get("final_score")
        if (
            score is not None
            and score < settings.judge_threshold
            and state.get("retry_count", 0) < settings.judge_max_retries
        ):
            return "retry"
        return END

    async def retry(state: AgentState) -> dict[str, Any]:
        ev = state.get("evaluation") or {}
        feedback = (
            f"An evaluator scored your answer {ev.get('final_score')}: {ev.get('reasoning')}. "
            "Re-check the data with tools if needed and give a corrected, fully grounded answer."
        )
        return {"messages": [HumanMessage(feedback)], "retry_count": state.get("retry_count", 0) + 1, "iterations": 0}

    g = StateGraph(AgentState)
    g.add_node("route", route)
    g.add_node("agent", agent)
    g.add_node("tools", tool_node)
    g.add_node("finalize", finalize)
    g.add_node("judge", judge)
    g.add_node("retry", retry)
    g.add_edge(START, "route")
    g.add_edge("route", "agent")
    g.add_conditional_edges("agent", after_agent, ["tools", "finalize", "judge", END])
    g.add_edge("tools", "agent")
    g.add_conditional_edges("finalize", after_finalize, ["judge", END])
    g.add_conditional_edges("judge", after_judge, ["retry", END])
    g.add_edge("retry", "agent")
    return g.compile()
