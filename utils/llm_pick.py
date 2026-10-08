from langchain_core.language_models import BaseChatModel

from utils.settings import Settings


class LLMNotConfiguredError(RuntimeError):
    pass


def build_chat_model(settings: Settings, judge: bool = False) -> BaseChatModel:
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
        raise LLMNotConfiguredError("OPENAI_API_KEY is not set")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.judge_model if judge else settings.openai_model,
        api_key=settings.openai_api_key,
        temperature=0,
        timeout=60,
        max_retries=2,
    )
