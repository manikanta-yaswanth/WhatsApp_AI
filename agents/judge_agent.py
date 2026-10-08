from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from Models.schema import EvaluationResult
from utils import prompts
from utils.structured import ainvoke_structured

_MAX_OBSERVATION_CHARS = 12000


def collect_observations(messages: Sequence[BaseMessage]) -> str:
    parts: list[str] = []
    for m in messages:
        if isinstance(m, AIMessage) and m.tool_calls:
            parts.extend(f"CALL {tc['name']}({tc['args']})" for tc in m.tool_calls)
        elif isinstance(m, ToolMessage):
            parts.append(f"RESULT {m.name}: {m.content}")
    text = "\n".join(parts) or "(no tools were called)"
    return text[-_MAX_OBSERVATION_CHARS:]


async def judge_answer(
    llm: BaseChatModel, question: str, answer: str, observations: str, expected: str | None = None
) -> EvaluationResult:
    body = f"QUESTION:\n{question}\n\nTOOL OBSERVATIONS:\n{observations}\n\nAGENT ANSWER:\n{answer}"
    if expected:
        body += f"\n\nEXPECTED ANSWER (reference):\n{expected}"
    return await ainvoke_structured(llm, EvaluationResult, [SystemMessage(prompts.JUDGE), HumanMessage(body)])
