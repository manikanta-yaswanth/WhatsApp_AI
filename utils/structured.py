from typing import TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    pass


async def ainvoke_structured[T: BaseModel](llm: BaseChatModel, schema: type[T], messages: list[BaseMessage]) -> T:
    """Call the model with a Pydantic schema as a forced tool call and validate the result."""
    runnable = llm.with_structured_output(schema, method="function_calling")
    try:
        result = await runnable.ainvoke(messages)
    except (ValidationError, ValueError) as exc:
        raise StructuredOutputError(str(exc)) from exc
    if not isinstance(result, schema):
        raise StructuredOutputError(f"expected {schema.__name__}, got {type(result).__name__}")
    return result
