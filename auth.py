import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from config import TELEGRAM_TOKEN


SESSION_TTL_SECONDS = 12 * 60 * 60
TELEGRAM_INIT_DATA_MAX_AGE = 24 * 60 * 60


def _get_signing_key() -> bytes:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not configured")

    return hmac.new(
        b"ai-running-coach-session",
        TELEGRAM_TOKEN.encode(),
        hashlib.sha256,
    ).digest()


def validate_telegram_init_data(init_data: str) -> int:
    """
    Проверяет подпись Telegram Mini App initData.
    Возвращает Telegram user_id только после успешной проверки.
    """
    if not init_data or len(init_data) > 10_000:
        raise ValueError("Invalid Telegram initData")

    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = data.pop("hash", None)
        if not received_hash:
            raise ValueError("Missing Telegram signature")

        auth_date = int(data.get("auth_date", "0"))
        now = int(time.time())

        if auth_date > now + 30:
            raise ValueError("Telegram initData is from the future")

        if now - auth_date > TELEGRAM_INIT_DATA_MAX_AGE:
            raise ValueError("Telegram initData has expired")

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(data.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            TELEGRAM_TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            raise ValueError("Invalid Telegram signature")

        user_data = json.loads(data["user"])
        user_id = int(user_data["id"])

        if user_id <= 0:
            raise ValueError("Invalid Telegram user ID")

        return user_id

    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Telegram authentication failed") from exc


def create_session_token(user_id: int) -> str:
    """Создаёт подписанный токен пользовательской сессии."""
    payload = {
        "user_id": int(user_id),
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }

    payload_bytes = json.dumps(
        payload,
        separators=(",", ":"),
    ).encode()

    payload_part = base64.urlsafe_b64encode(
        payload_bytes
    ).decode().rstrip("=")

    signature = hmac.new(
        _get_signing_key(),
        payload_part.encode(),
        hashlib.sha256,
    ).hexdigest()

    return f"{payload_part}.{signature}"


def verify_session_token(token: str) -> int:
    """Проверяет токен сессии и возвращает user_id."""
    try:
        payload_part, received_signature = token.split(".", 1)

        expected_signature = hmac.new(
            _get_signing_key(),
            payload_part.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            expected_signature,
            received_signature,
        ):
            raise ValueError("Invalid session signature")

        padded = payload_part + "=" * (-len(payload_part) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(padded.encode())
        )

        if int(payload["exp"]) < int(time.time()):
            raise ValueError("Session expired")

        user_id = int(payload["user_id"])

        if user_id <= 0:
            raise ValueError("Invalid user ID")

        return user_id

    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid session token") from exc