"""Router -> specialist ReAct graph -> judge -> optional retry."""

from datetime import date
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents import analytics_agent, classification_agent, contact_agent, data_quality_agent, message_agent, search_agent
from agents.judge_agent import collect_observations, judge_answer
from Models.schema import AgentState, IntentDecision
from utils import prompts
from utils.settings import Settings
from utils.structured import StructuredOutputError, ainvoke_structured

_KEYWORDS = {
    "data_quality": ["duplicate", "quality", "missing", "invalid", "null"],
    "classification": ["meeting", "job", "follow up", "follow-up", "urgent", "spam", "classif", "categor", "important"],
    "analytics": ["how many", "count", "number of", "stat", "trend", "most active", "scrape run"],
    "message": ["message", "said", "say", "latest from", "texted", "wrote"],
    "contact": ["contact", "phone", "number", "named"],
}

SPECIALISTS = {
    "contact": contact_agent,
    "message": message_agent,
    "classification": classification_agent,
    "search": search_agent,
    "analytics": analytics_agent,
    "data_quality": data_quality_agent,
}


def keyword_intent(query: str) -> str:
    for intent, words in _KEYWORDS.items():
        if any(word in query.lower() for word in words):
            return intent
    return "search"


def build_agent_graph(
    llm: BaseChatModel,
    tools: dict[str, BaseTool],
    settings: Settings,
    judge_llm: BaseChatModel | None = None,
) -> CompiledStateGraph:
    judge_llm = judge_llm or llm

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
            "retry_count": 0,
            "tool_calls": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    def specialist(state: AgentState) -> str:
        return state["intent"]

    def after_specialist(state: AgentState) -> str:
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

    graph = StateGraph(AgentState)
    graph.add_node("route", route)
    graph.add_node("judge", judge)
    graph.add_node("retry", retry)
    for intent, module in SPECIALISTS.items():
        graph.add_node(intent, module.build_graph(llm, tools, settings))
        graph.add_conditional_edges(intent, after_specialist, ["judge", END])
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", specialist, list(SPECIALISTS))
    graph.add_conditional_edges("judge", after_judge, ["retry", END])
    graph.add_conditional_edges("retry", specialist, list(SPECIALISTS))
    return graph.compile()
