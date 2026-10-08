import phonenumbers


def normalize_phone(raw: str | None) -> str | None:
    """Return E.164 (+15551234567) or None if the input is not a valid number."""
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if not digits:
        return None
    if not digits.startswith("+"):
        digits = "+" + digits
    try:
        parsed = phonenumbers.parse(digits, None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def phone_from_whatsapp_id(whatsapp_id: str) -> str | None:
    """WhatsApp user ids look like 15551234567@c.us; group/LID ids carry no phone."""
    user, _, server = whatsapp_id.partition("@")
    if server not in {"c.us", "s.whatsapp.net"}:
        return None
    return normalize_phone(user)


def region_of(phone_e164: str | None) -> str | None:
    if not phone_e164:
        return None
    try:
        return phonenumbers.region_code_for_number(phonenumbers.parse(phone_e164, None))
    except phonenumbers.NumberParseException:
        return None
