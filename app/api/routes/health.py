from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings, get_settings
from app.db.database import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict:
    try:
        await session.execute(text("SELECT 1"))
        db = "ok"
    except Exception as exc:  # noqa: BLE001
        db = f"error: {type(exc).__name__}"
    return {
        "status": "ok" if db == "ok" else "degraded",
        "database": db,
        "llm_configured": bool(settings.openai_api_key and settings.openai_api_key.get_secret_value()),
        "whatsapp_profile_present": settings.profile_path.exists(),
    }
