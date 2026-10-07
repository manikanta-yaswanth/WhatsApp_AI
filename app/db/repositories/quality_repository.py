from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.phone import normalize_phone


class QualityRepository:
    """Deterministic data-quality checks over stored contacts and messages."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def report(self, max_messages_per_contact: int) -> dict[str, Any]:
        s = self.session
        contacts = (await s.execute(text("SELECT count(*) FROM contacts"))).scalar_one()
        messages = (await s.execute(text("SELECT count(*) FROM messages"))).scalar_one()
        missing_names = (
            await s.execute(
                text("SELECT count(*) FROM contacts WHERE contact_name IS NULL OR btrim(contact_name) = ''")
            )
        ).scalar_one()
        missing_phones = (
            await s.execute(text("SELECT count(*) FROM contacts WHERE phone_number IS NULL AND NOT is_group"))
        ).scalar_one()
        phones = (await s.execute(text("SELECT phone_number FROM contacts WHERE phone_number IS NOT NULL"))).scalars()
        invalid_phones = sum(1 for p in phones if normalize_phone(p) != p)
        duplicate_contacts = (
            await s.execute(
                text(
                    "SELECT coalesce(sum(n - 1), 0) FROM (SELECT count(*) n FROM contacts "
                    "WHERE phone_number IS NOT NULL GROUP BY phone_number HAVING count(*) > 1) d"
                )
            )
        ).scalar_one()
        duplicate_messages = (
            await s.execute(
                text(
                    "SELECT coalesce(sum(n - 1), 0) FROM (SELECT count(*) n FROM messages "
                    "GROUP BY contact_id, sender_type, message_timestamp, message_text HAVING count(*) > 1) d"
                )
            )
        ).scalar_one()
        future_timestamps = (
            await s.execute(
                text("SELECT count(*) FROM messages WHERE message_timestamp > now() + interval '5 minutes'")
            )
        ).scalar_one()
        over_limit = (
            await s.execute(
                text(
                    "SELECT count(*) FROM (SELECT contact_id FROM messages GROUP BY contact_id HAVING count(*) > :k) x"
                ),
                {"k": max_messages_per_contact},
            )
        ).scalar_one()
        empty_messages = (
            await s.execute(
                text(
                    "SELECT count(*) FROM messages "
                    "WHERE message_type = 'text' AND (message_text IS NULL OR message_text = '')"
                )
            )
        ).scalar_one()

        issues = {
            "missing_names": int(missing_names),
            "missing_phone_numbers": int(missing_phones),
            "invalid_phone_numbers": int(invalid_phones),
            "duplicate_contacts": int(duplicate_contacts),
            "duplicate_messages": int(duplicate_messages),
            "invalid_timestamps": int(future_timestamps),
            "contacts_over_message_limit": int(over_limit),
            "empty_text_messages": int(empty_messages),
        }
        # Missing phones are expected for LID/privacy contacts, so they are reported but not penalized.
        penalized = sum(v for k, v in issues.items() if k != "missing_phone_numbers")
        records = int(contacts) + int(messages)
        score = 1.0 if records == 0 else max(0.0, 1 - penalized / records)
        return {
            "records_processed": records,
            "contacts": int(contacts),
            "messages": int(messages),
            **issues,
            "quality_score": round(score, 4),
        }
