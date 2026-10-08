import hashlib


def stable_message_id(contact_whatsapp_id: str, sender: str, timestamp_iso: str, text: str) -> str:
    """Deterministic id for messages scraped from the DOM, where WhatsApp ids are unavailable."""
    raw = "\x1f".join([contact_whatsapp_id, sender, timestamp_iso, text])
    return "dom_" + hashlib.sha256(raw.encode()).hexdigest()[:40]
