"""Passkey (WebAuthn) authentication and session handling.

Single user, no password anywhere in the system. Three ways in:

1. A registered passkey — the normal path.
2. A recovery code — ten single-use codes issued at enrollment, stored only as
   hashes, for when the passkey device is lost.
3. A one-time enrollment token printed to the container logs on first run, which
   is how the first passkey gets registered at all.

Every secret here is stored hashed. A read of the database yields nothing that
can be replayed against the site.
"""

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from app.config import get_settings
from app.models import (
    PasskeyCredential,
    RecoveryCode,
    Session as SessionRow,
    Setting,
    utcnow,
)

settings = get_settings()

SESSION_COOKIE = "yayo_session"
ENROLLMENT_SETTING_KEY = "enrollment_token_hash"
CHALLENGE_SETTING_KEY = "webauthn_challenge"
RECOVERY_CODE_COUNT = 10


def _hash(value: str) -> str:
    """SHA-256 for high-entropy tokens.

    These are 256-bit random values, not user-chosen passwords, so there is no
    dictionary to attack and a slow KDF would buy nothing. Recovery codes are
    generated with the same entropy for exactly this reason.
    """
    return hashlib.sha256(value.encode()).hexdigest()


# --------------------------------------------------------------------------
# Settings helpers
# --------------------------------------------------------------------------


def get_setting(session: Session, key: str) -> Optional[str]:
    row = session.get(Setting, key)
    return row.value if row else None


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value)
    else:
        row.value = value
        row.updated_at = utcnow()
    session.add(row)
    session.commit()


def clear_setting(session: Session, key: str) -> None:
    row = session.get(Setting, key)
    if row is not None:
        session.delete(row)
        session.commit()


# --------------------------------------------------------------------------
# Enrollment
# --------------------------------------------------------------------------


def has_any_passkey(session: Session) -> bool:
    return session.exec(select(PasskeyCredential)).first() is not None


def issue_enrollment_token(session: Session) -> str:
    """Mint a one-time token for registering a passkey.

    Only the hash is stored, so the token exists in exactly two places: the
    container log, and whatever you paste into the browser. It is consumed on
    first successful registration.
    """
    token = secrets.token_urlsafe(32)
    set_setting(session, ENROLLMENT_SETTING_KEY, _hash(token))
    return token


def check_enrollment_token(session: Session, token: str) -> bool:
    stored = get_setting(session, ENROLLMENT_SETTING_KEY)
    if not stored or not token:
        return False
    return secrets.compare_digest(stored, _hash(token))


def consume_enrollment_token(session: Session) -> None:
    clear_setting(session, ENROLLMENT_SETTING_KEY)


# --------------------------------------------------------------------------
# Recovery codes
# --------------------------------------------------------------------------


def generate_recovery_codes(session: Session) -> list[str]:
    """Replace the existing set with ten fresh codes.

    Returned in plaintext exactly once — the caller shows them and then they are
    unrecoverable. Formatted in two groups so they can be written down without
    transcription errors.
    """
    for old in session.exec(select(RecoveryCode)).all():
        session.delete(old)

    codes: list[str] = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(8)  # 64 bits
        pretty = f"{raw[:8]}-{raw[8:]}"
        codes.append(pretty)
        session.add(RecoveryCode(code_hash=_hash(pretty)))
    session.commit()
    return codes


def redeem_recovery_code(session: Session, code: str) -> bool:
    """Consume a recovery code. Single use, constant-time comparison."""
    candidate = _hash(code.strip().lower())
    for row in session.exec(select(RecoveryCode).where(RecoveryCode.used_at.is_(None))).all():
        if secrets.compare_digest(row.code_hash, candidate):
            row.used_at = utcnow()
            session.add(row)
            session.commit()
            return True
    return False


def unused_recovery_code_count(session: Session) -> int:
    return len(
        session.exec(select(RecoveryCode).where(RecoveryCode.used_at.is_(None))).all()
    )


# --------------------------------------------------------------------------
# Challenges
#
# WebAuthn needs the challenge it issued to still be known when the browser
# responds. With a single user and a single server process, stashing it in the
# settings table is simpler than a cache and survives a restart mid-login.
# --------------------------------------------------------------------------


def store_challenge(session: Session, challenge: bytes, purpose: str) -> None:
    set_setting(
        session,
        CHALLENGE_SETTING_KEY,
        json.dumps(
            {
                "challenge": challenge.hex(),
                "purpose": purpose,
                "expires": (utcnow() + timedelta(minutes=5)).isoformat(),
            }
        ),
    )


def take_challenge(session: Session, purpose: str) -> Optional[bytes]:
    """Read and consume the pending challenge. Replay is not possible."""
    raw = get_setting(session, CHALLENGE_SETTING_KEY)
    clear_setting(session, CHALLENGE_SETTING_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if data.get("purpose") != purpose:
            return None
        if datetime.fromisoformat(data["expires"]) < utcnow():
            return None
        return bytes.fromhex(data["challenge"])
    except (ValueError, KeyError):
        return None


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------


def create_session(session: Session, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    session.add(
        SessionRow(
            token_hash=_hash(token),
            expires_at=utcnow() + timedelta(days=settings.session_days),
            user_agent=user_agent[:300],
        )
    )
    session.commit()
    return token


def resolve_session(session: Session, token: Optional[str]) -> Optional[SessionRow]:
    """Validate a session cookie, sliding its expiry forward on use."""
    if not token:
        return None
    row = session.exec(
        select(SessionRow).where(SessionRow.token_hash == _hash(token))
    ).first()
    if row is None:
        return None

    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        # SQLite hands back naive datetimes; these were written as UTC.
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < utcnow():
        session.delete(row)
        session.commit()
        return None

    row.last_seen_at = utcnow()
    row.expires_at = utcnow() + timedelta(days=settings.session_days)
    session.add(row)
    session.commit()
    return row


def destroy_session(session: Session, token: Optional[str]) -> None:
    if not token:
        return
    row = session.exec(
        select(SessionRow).where(SessionRow.token_hash == _hash(token))
    ).first()
    if row is not None:
        session.delete(row)
        session.commit()


def purge_expired_sessions(session: Session) -> int:
    now = utcnow()
    removed = 0
    for row in session.exec(select(SessionRow)).all():
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            session.delete(row)
            removed += 1
    if removed:
        session.commit()
    return removed
