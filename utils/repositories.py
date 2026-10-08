import uuid
from datetime import datetime, timedelta
from typing import Any

from psycopg2 import sql
from psycopg2.extras import Json

from Models.records import AgentRun, Contact, Conversation, Evaluation, EvaluationRun, Message, ScrapeRun
from Models.schema import ContactIn, MessageIn
from utils.database import SQLSession
from utils.phone import normalize_phone
from utils.time import utcnow


class ContactRepository:
    def __init__(self, session: SQLSession) -> None:
        self.session = session

    def upsert(self, contact: ContactIn) -> tuple[uuid.UUID, bool]:
        row = self.session.one(
            """INSERT INTO contacts (id, whatsapp_id, contact_name, phone_number, is_group)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (whatsapp_id) DO UPDATE SET
                 contact_name = coalesce(EXCLUDED.contact_name, contacts.contact_name),
                 phone_number = coalesce(EXCLUDED.phone_number, contacts.phone_number),
                 is_group = EXCLUDED.is_group, updated_at = now()
               RETURNING id, (xmax = 0) AS inserted""",
            (uuid.uuid4(), contact.whatsapp_id, contact.contact_name, contact.phone_number, contact.is_group),
        )
        return row["id"], row["inserted"]

    def upsert_conversation(self, cid: uuid.UUID, unread: int, last: datetime | None) -> None:
        self.session.execute(
            """INSERT INTO conversations (id, contact_id, unread_count, last_message_at, last_scraped_at)
               VALUES (%s, %s, %s, %s, now()) ON CONFLICT (contact_id) DO UPDATE SET
               unread_count = EXCLUDED.unread_count,
               last_message_at = greatest(conversations.last_message_at, EXCLUDED.last_message_at),
               last_scraped_at = now()""",
            (uuid.uuid4(), cid, unread, last),
        )

    def get(self, cid: uuid.UUID) -> Contact | None:
        rows = self.session.all("SELECT * FROM contacts WHERE id = %s", (cid,))
        return Contact.model_validate(rows[0]) if rows else None

    @staticmethod
    def _filters(
        name: str | None = None,
        phone: str | None = None,
        phone_prefix: str | None = None,
        has_phone: bool | None = None,
        is_group: bool | None = None,
        category: str | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("c.contact_name", name), ("c.phone_number", phone)):
            if value:
                clauses.append(f"{column} ILIKE %s")
                params.append(f"%{value}%")
        if phone_prefix:
            clauses.append("c.phone_number LIKE %s")
            params.append(f"+{phone_prefix.lstrip('+')}%")
        if has_phone is not None:
            clauses.append("c.phone_number IS NOT NULL" if has_phone else "c.phone_number IS NULL")
        if is_group is not None:
            clauses.append("c.is_group = %s")
            params.append(is_group)
        if category:
            clauses.append("v.category = %s")
            params.append(category)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def search(
        self,
        name: str | None = None,
        phone: str | None = None,
        phone_prefix: str | None = None,
        has_phone: bool | None = None,
        is_group: bool | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[tuple[Contact, int, datetime | None, str | None]]]:
        where, params = self._filters(name, phone, phone_prefix, has_phone, is_group, category)
        join = " FROM contacts c LEFT JOIN conversations v ON v.contact_id = c.id"
        total = self.session.scalar("SELECT count(*)" + join + where, params)
        rows = self.session.all(
            """SELECT c.*, v.category,
               (SELECT count(*) FROM messages m WHERE m.contact_id=c.id) AS message_count,
               (SELECT max(message_timestamp) FROM messages m WHERE m.contact_id=c.id) AS last_message_at"""
            + join
            + where
            + " ORDER BY coalesce(v.last_message_at, c.created_at) DESC, c.id LIMIT %s OFFSET %s",
            [*params, max(1, min(limit, 500)), max(0, offset)],
        )
        return total, [
            (Contact.model_validate(r), r["message_count"], r["last_message_at"], r["category"]) for r in rows
        ]

    def count(
        self,
        is_group: bool | None = None,
        has_phone: bool | None = None,
        phone_prefix: str | None = None,
    ) -> int:
        where, params = self._filters(is_group=is_group, has_phone=has_phone, phone_prefix=phone_prefix)
        return self.session.scalar("SELECT count(*) FROM contacts c" + where, params)

    def find_by_name_or_id(self, ref: str) -> Contact | None:
        try:
            return self.get(uuid.UUID(ref))
        except ValueError:
            pass
        rows = self.session.all(
            """SELECT * FROM contacts WHERE contact_name ILIKE %s OR phone_number ILIKE %s OR whatsapp_id=%s
               ORDER BY updated_at DESC LIMIT 1""",
            (f"%{ref}%", f"%{ref}%", ref),
        )
        return Contact.model_validate(rows[0]) if rows else None

    def get_conversation(self, cid: uuid.UUID) -> Conversation | None:
        rows = self.session.all("SELECT * FROM conversations WHERE contact_id=%s", (cid,))
        return Conversation.model_validate(rows[0]) if rows else None

    def set_classification(self, cid: uuid.UUID, category: str, action: str, confidence: float, reason: str) -> None:
        self.session.execute(
            """INSERT INTO conversations
               (id, contact_id, category, action, category_confidence, category_reason, classified_at)
               VALUES (%s, %s, %s, %s, %s, %s, now()) ON CONFLICT (contact_id) DO UPDATE SET
               category=EXCLUDED.category, action=EXCLUDED.action, category_confidence=EXCLUDED.category_confidence,
               category_reason=EXCLUDED.category_reason, classified_at=now()""",
            (uuid.uuid4(), cid, category, action, confidence, reason),
        )


class MessageRepository:
    def __init__(self, session: SQLSession) -> None:
        self.session = session

    def insert_new(self, cid: uuid.UUID, messages: list[MessageIn]) -> int:
        saved = 0
        for m in messages:
            saved += self.session.execute(
                """INSERT INTO messages (id, contact_id, whatsapp_message_id, sender_type, sender_name,
                   message_type, message_text, message_timestamp) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (whatsapp_message_id) DO NOTHING""",
                (
                    uuid.uuid4(),
                    cid,
                    m.whatsapp_message_id,
                    m.sender_type,
                    m.sender_name,
                    m.message_type,
                    m.message_text,
                    m.message_timestamp,
                ),
            )
        return saved

    def prune(self, cid: uuid.UUID, keep: int) -> int:
        if keep < 1:
            raise ValueError("keep must be positive")
        return self.session.execute(
            """DELETE FROM messages WHERE contact_id=%s AND id NOT IN (
               SELECT id FROM messages WHERE contact_id=%s
               ORDER BY message_timestamp DESC, scraped_at DESC, id DESC LIMIT %s)""",
            (cid, cid, keep),
        )

    def recent_for_contact(self, cid: uuid.UUID, limit: int = 3) -> list[Message]:
        return [
            Message.model_validate(r)
            for r in self.session.all(
                "SELECT * FROM messages WHERE contact_id=%s ORDER BY message_timestamp DESC, id DESC LIMIT %s",
                (cid, max(1, min(limit, 500))),
            )
        ]

    def recent_conversations(
        self,
        since: datetime | None = None,
        limit: int = 50,
        per_contact: int = 3,
    ) -> list[tuple[Contact, list[Message]]]:
        rows = self.session.all(
            """SELECT c.* FROM contacts c JOIN (
               SELECT contact_id, max(message_timestamp) last_at FROM messages GROUP BY contact_id
               HAVING (%s::timestamptz IS NULL OR max(message_timestamp) >= %s)
               ) m ON c.id=m.contact_id ORDER BY m.last_at DESC, c.id LIMIT %s""",
            (since, since, max(1, min(limit, 500))),
        )
        return [(Contact.model_validate(r), self.recent_for_contact(r["id"], per_contact)) for r in rows]

    def search_text(self, query: str, limit: int = 20, days: int | None = None) -> list[tuple[Message, Contact]]:
        terms = [t for t in query.split() if len(t) > 1][:8] or [query]
        where = " OR ".join("message_text ILIKE %s" for _ in terms)
        params: list[Any] = [f"%{t}%" for t in terms]
        if days is not None:
            where = f"({where}) AND message_timestamp >= %s"
            params.append(utcnow() - timedelta(days=max(0, days)))
        rows = self.session.all(
            "SELECT * FROM messages WHERE " + where + " ORDER BY message_timestamp DESC, id DESC LIMIT %s",
            [*params, max(1, min(limit, 500))],
        )
        out = []
        for row in rows:
            contact = ContactRepository(self.session).get(row["contact_id"])
            if contact:
                out.append((Message.model_validate(row), contact))
        return out

    def count(self, since: datetime | None = None) -> int:
        return self.session.scalar(
            "SELECT count(*) FROM messages WHERE (%s::timestamptz IS NULL OR message_timestamp >= %s)",
            (since, since),
        )


class QualityRepository:
    def __init__(self, session: SQLSession) -> None:
        self.session = session

    def report(self, max_messages_per_contact: int) -> dict[str, Any]:
        count = self.session.scalar
        contacts = count("SELECT count(*) FROM contacts")
        messages = count("SELECT count(*) FROM messages")
        phones = self.session.all("SELECT phone_number FROM contacts WHERE phone_number IS NOT NULL")
        issues = {
            "missing_names": count(
                "SELECT count(*) FROM contacts WHERE contact_name IS NULL OR btrim(contact_name)=''"
            ),
            "missing_phone_numbers": count("SELECT count(*) FROM contacts WHERE phone_number IS NULL AND NOT is_group"),
            "invalid_phone_numbers": sum(normalize_phone(r["phone_number"]) != r["phone_number"] for r in phones),
            "duplicate_contacts": int(
                count(
                    "SELECT coalesce(sum(n-1),0) FROM (SELECT count(*) n FROM contacts "
                    "WHERE phone_number IS NOT NULL GROUP BY phone_number HAVING count(*)>1) d"
                )
            ),
            "duplicate_messages": int(
                count(
                    "SELECT coalesce(sum(n-1),0) FROM (SELECT count(*) n FROM messages "
                    "GROUP BY contact_id,sender_type,message_timestamp,message_text HAVING count(*)>1) d"
                )
            ),
            "invalid_timestamps": count(
                "SELECT count(*) FROM messages WHERE message_timestamp>now()+interval '5 minutes'"
            ),
            "contacts_over_message_limit": count(
                "SELECT count(*) FROM (SELECT contact_id FROM messages GROUP BY contact_id HAVING count(*)>%s) d",
                (max_messages_per_contact,),
            ),
            "empty_text_messages": count(
                "SELECT count(*) FROM messages WHERE message_type='text' AND (message_text IS NULL OR message_text='')"
            ),
        }
        penalized = sum(v for k, v in issues.items() if k != "missing_phone_numbers")
        records = contacts + messages
        score = 1.0 if not records else max(0.0, 1 - penalized / records)
        return {
            "records_processed": records,
            "contacts": contacts,
            "messages": messages,
            **issues,
            "quality_score": round(score, 4),
        }


class RunRepository:
    def __init__(self, session: SQLSession) -> None:
        self.session = session

    def _insert(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        # table/columns come only from the fixed record models, never from an agent.
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, data)),
            sql.SQL(",").join(sql.Placeholder() for _ in data),
        )
        values = [Json(v) if isinstance(v, (list, dict)) else v for v in data.values()]
        return self.session.one(query, values)

    def save_agent(self, run: AgentRun) -> None:
        self._insert("agent_runs", run.model_dump())

    def get_agent(self, rid: uuid.UUID) -> AgentRun:
        return AgentRun.model_validate(self.session.one("SELECT * FROM agent_runs WHERE id=%s", (rid,)))

    def save_evaluation(self, evaluation: Evaluation) -> None:
        self._insert("evaluations", evaluation.model_dump())

    def evaluations(self, rid: uuid.UUID | None = None) -> list[Evaluation]:
        return [
            Evaluation.model_validate(r)
            for r in self.session.all(
                "SELECT * FROM evaluations WHERE (%s::uuid IS NULL OR evaluation_run_id=%s)", (rid, rid)
            )
        ]

    def create_evaluation_run(self, dataset: str) -> uuid.UUID:
        rid = uuid.uuid4()
        self._insert("evaluation_runs", {"id": rid, "status": "running", "dataset": dataset})
        return rid

    def get_evaluation_run(self, rid: uuid.UUID) -> EvaluationRun:
        return EvaluationRun.model_validate(self.session.one("SELECT * FROM evaluation_runs WHERE id=%s", (rid,)))

    def finish_evaluation(
        self,
        rid: uuid.UUID,
        metrics: dict[str, Any],
        total: int,
        error: str | None = None,
    ) -> EvaluationRun:
        self.session.execute(
            """UPDATE evaluation_runs SET status=%s, metrics=%s, total_questions=%s, error_message=%s,
               completed_at=now() WHERE id=%s""",
            ("failed" if error else "completed", Json(metrics), total, error, rid),
        )
        return self.get_evaluation_run(rid)

    def create_scrape(self) -> ScrapeRun:
        return ScrapeRun.model_validate(self._insert("scrape_runs", {"id": uuid.uuid4(), "status": "queued"}))

    def get_scrape(self, rid: uuid.UUID) -> ScrapeRun:
        return ScrapeRun.model_validate(self.session.one("SELECT * FROM scrape_runs WHERE id=%s", (rid,)))

    def mark_scrape_running(self, rid: uuid.UUID) -> None:
        self.session.execute("UPDATE scrape_runs SET status='running', started_at=now() WHERE id=%s", (rid,))

    def save_scrape(self, run: ScrapeRun) -> None:
        fields = run.model_dump(exclude={"id"})
        query = sql.SQL("UPDATE scrape_runs SET {} WHERE id=%s").format(
            sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in fields)
        )
        self.session.execute(query, [*fields.values(), run.id])

    def latest_scrapes(self, limit: int = 10) -> list[ScrapeRun]:
        return [
            ScrapeRun.model_validate(r)
            for r in self.session.all("SELECT * FROM scrape_runs ORDER BY created_at DESC LIMIT %s", (limit,))
        ]
