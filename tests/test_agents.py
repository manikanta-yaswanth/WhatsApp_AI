import json

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from agents.whatsapp_agent import build_agent_graph, keyword_intent
from tests.conftest import tool_call
from tests.factories import conversation
from utils.agent_runner import AgentService
from utils.feed_db import persist_conversations
from utils.tools import build_tools


def _system_text(messages) -> str:  # type: ignore[no-untyped-def]
    return " ".join(str(m.content) for m in messages if isinstance(m, SystemMessage))


def make_responder(intent: str, plan: list[tuple[str, dict]], final: str, judge_scores: list[float]):  # type: ignore[no-untyped-def]
    """Router -> scripted tool calls -> final answer; judge returns successive scores."""
    scores = iter(judge_scores)

    def respond(messages, tools):  # type: ignore[no-untyped-def]
        if tools == ["IntentDecision"]:
            return tool_call("IntentDecision", {"intent": intent, "reason": "test"})
        if tools == ["EvaluationResult"]:
            v = next(scores)
            return tool_call(
                "EvaluationResult",
                {
                    "correctness": v,
                    "relevance": v,
                    "groundedness": v,
                    "completeness": v,
                    "hallucination": 1 - v,
                    "reasoning": "ok",
                },
            )
        done = sum(1 for m in messages if isinstance(m, ToolMessage))
        if done < len(plan):
            return tool_call(*plan[done])
        return AIMessage(content=final, usage_metadata={"input_tokens": 20, "output_tokens": 8, "total_tokens": 28})

    return respond


async def _seed(database) -> None:  # type: ignore[no-untyped-def]
    with database.transaction() as s:
        persist_conversations(
            s,
            [
                conversation("14155550123@c.us", "John Smith", "+14155550123", ["Can we schedule a meeting?"]),
                conversation("919876543210@c.us", "Priya", "+919876543210", ["see you"]),
            ],
            keep=3,
        )


async def test_react_loop_calls_tools_and_judges(database, settings, scripted) -> None:  # type: ignore[no-untyped-def]
    await _seed(database)
    llm = scripted(
        make_responder("contact", [("count_contacts", {"by_country": True})], "You have 2 contacts.", [0.95])
    )
    service = AgentService(settings, database, llm)
    response, state = await service.run("How many contacts do I have?")
    assert response.intent == "contact"
    assert [tc.name for tc in response.tool_calls] == ["count_contacts"]
    observation = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert json.loads(observation.text)["by_country"] == {"US": 1, "IN": 1}
    assert response.answer == "You have 2 contacts." and response.judge_score == 0.95
    assert response.retry_count == 0


async def test_low_judge_score_triggers_one_retry(database, settings, scripted) -> None:  # type: ignore[no-untyped-def]
    await _seed(database)
    llm = scripted(make_responder("search", [("search_messages", {"query": "meeting"})], "John asked.", [0.2, 0.9]))
    response, _ = await AgentService(settings, database, llm).run("Who asked about a meeting?")
    assert response.retry_count == 1 and response.judge_score == 0.9


async def test_specialist_only_gets_its_toolset(database, settings, scripted) -> None:  # type: ignore[no-untyped-def]
    seen: list[list[str]] = []
    base = make_responder("data_quality", [], "All good.", [1.0])

    def respond(messages, tools):  # type: ignore[no-untyped-def]
        if tools not in (["IntentDecision"], ["EvaluationResult"]):
            seen.append(tools)
            assert "Data Quality Agent" in _system_text(messages)
        return base(messages, tools)

    llm = scripted(respond)
    graph = build_agent_graph(llm, build_tools(database, settings, llm), settings)
    await graph.ainvoke({"user_query": "any duplicates?", "judge_enabled": False})
    assert seen == [["data_quality_report", "count_contacts", "search_contacts"]]


async def test_iteration_budget_forces_final_answer(database, settings, scripted) -> None:  # type: ignore[no-untyped-def]
    looping: list[tuple[str, dict]] = [("count_contacts", {})] * 50
    llm = scripted(make_responder("contact", looping, "unused", [1.0]))
    s = settings.model_copy(update={"agent_max_iterations": 2})
    response, _ = await AgentService(s, database, llm).run("count", judge=False)
    assert response.iterations == 2 and len(response.tool_calls) == 2


def test_keyword_router_fallback() -> None:
    assert keyword_intent("Any duplicate contacts?") == "data_quality"
    assert keyword_intent("who asked for a meeting") == "classification"
    assert keyword_intent("how many people") == "analytics"
    assert keyword_intent("pizza") == "search"


async def test_specialist_cannot_execute_an_unapproved_tool(database, settings, scripted) -> None:  # type: ignore[no-untyped-def]
    llm = scripted(make_responder("data_quality", [("get_message_stats", {})], "No access.", []))
    graph = build_agent_graph(llm, build_tools(database, settings, llm), settings)
    state = await graph.ainvoke({"user_query": "check quality", "judge_enabled": False})
    observations = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert "not a valid tool" in observations[0].text
    assert "messages_stored" not in observations[0].text


async def test_classify_tool_stores_category(database, settings, scripted) -> None:  # type: ignore[no-untyped-def]
    await _seed(database)

    def respond(messages, tools):  # type: ignore[no-untyped-def]
        return tool_call(
            "ConversationClassification",
            {"category": "meeting_request", "action": "ACTION_REQUIRED", "confidence": 0.9, "reason": "asks to meet"},
        )

    service = AgentService(settings, database, scripted(respond))
    result = await service.classify(None, limit=10, only_unclassified=False)
    assert {r.category for r in result} == {"meeting_request"}
    tools = build_tools(database, settings)
    found = json.loads(await tools["search_contacts"].ainvoke({"category": "meeting_request"}))
    assert found["total_matches"] == 2
