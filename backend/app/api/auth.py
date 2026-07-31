"""Passkey registration and login.

The browser half of WebAuthn is handled by the frontend; these endpoints issue
challenges and verify the signed responses.
"""

import base64
import json
import logging
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import Session, select
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import get_settings
from app.db import get_session
from app.models import PasskeyCredential, utcnow
from app.services.auth import (
    SESSION_COOKIE,
    check_enrollment_token,
    consume_enrollment_token,
    create_session,
    destroy_session,
    generate_recovery_codes,
    has_any_passkey,
    redeem_recovery_code,
    resolve_session,
    store_challenge,
    take_challenge,
    unused_recovery_code_count,
)

log = logging.getLogger("yayo.auth")
settings = get_settings()
router = APIRouter(prefix="/api/auth", tags=["auth"])

# A single-user app still needs a stable WebAuthn user handle.
USER_ID = b"yayo-travel-owner"
USER_NAME = "yayo"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


# --------------------------------------------------------------------------
# The dependency every other router hangs off
# --------------------------------------------------------------------------


def require_auth(
    session: Session = Depends(get_session),
    yayo_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> bool:
    """Reject anything without a valid session cookie.

    Applied at the router level rather than per-endpoint so a new route cannot
    accidentally ship unprotected.
    """
    if resolve_session(session, yayo_session) is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return True


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        # Lax rather than Strict: Strict would drop the cookie when following a
        # link into the site from elsewhere, logging you out for no benefit on a
        # site with no cross-origin state-changing GETs.
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


@router.get("/status")
def auth_status(
    session: Session = Depends(get_session),
    yayo_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    """Unauthenticated by design — the login screen needs to read this."""
    authenticated = resolve_session(session, yayo_session) is not None
    return {
        "authenticated": authenticated,
        "enrolled": has_any_passkey(session),
        "recovery_codes_left": unused_recovery_code_count(session)
        if authenticated
        else None,
        "passkey_count": len(session.exec(select(PasskeyCredential)).all())
        if authenticated
        else None,
    }


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


class RegisterBeginRequest(BaseModel):
    enrollment_token: Optional[str] = None
    nickname: str = ""


@router.post("/register/begin")
def register_begin(
    payload: RegisterBeginRequest,
    session: Session = Depends(get_session),
    yayo_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    """Start passkey registration.

    Two ways to be allowed here: an already-valid session (adding a second
    device), or the one-time enrollment token (registering the very first one).
    """
    logged_in = resolve_session(session, yayo_session) is not None
    if not logged_in:
        if not check_enrollment_token(session, payload.enrollment_token or ""):
            raise HTTPException(status_code=401, detail="Invalid enrollment token")

    existing = session.exec(select(PasskeyCredential)).all()
    options = generate_registration_options(
        rp_id=settings.rp_id,
        rp_name=settings.rp_name,
        user_id=USER_ID,
        user_name=USER_NAME,
        user_display_name=settings.rp_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            # Discoverable credentials let the browser offer the passkey without
            # the user typing any identifier first.
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=_unb64(c.credential_id)) for c in existing
        ],
    )
    store_challenge(session, options.challenge, "register")
    # options_to_json returns a string; hand the client a real object so it
    # does not have to double-parse.
    return {"options": json.loads(options_to_json(options))}


class RegisterFinishRequest(BaseModel):
    credential: dict
    nickname: str = ""


@router.post("/register/finish")
def register_finish(
    payload: RegisterFinishRequest,
    response: Response,
    request: Request,
    session: Session = Depends(get_session),
    yayo_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    challenge = take_challenge(session, "register")
    if challenge is None:
        raise HTTPException(status_code=400, detail="Challenge expired, start again")

    was_first = not has_any_passkey(session)
    logged_in = resolve_session(session, yayo_session) is not None
    if not logged_in and not was_first:
        # Only the first passkey may be registered without a session; after that
        # the enrollment token alone must not be enough.
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        verified = verify_registration_response(
            credential=payload.credential,
            expected_challenge=challenge,
            expected_origin=settings.site_origin,
            expected_rp_id=settings.rp_id,
        )
    except InvalidRegistrationResponse as exc:
        log.warning("passkey registration rejected: %s", exc)
        raise HTTPException(status_code=400, detail="Registration failed") from exc

    session.add(
        PasskeyCredential(
            credential_id=_b64(verified.credential_id),
            public_key=_b64(verified.credential_public_key),
            sign_count=verified.sign_count,
            nickname=payload.nickname[:60],
        )
    )
    session.commit()

    result: dict = {"registered": True}
    if was_first:
        consume_enrollment_token(session)
        # Issue recovery codes with the first passkey: this is the only moment
        # we can be sure the user is present and paying attention.
        result["recovery_codes"] = generate_recovery_codes(session)
        token = create_session(session, request.headers.get("user-agent", ""))
        _set_session_cookie(response, token)
    return result


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------


@router.post("/login/begin")
def login_begin(session: Session = Depends(get_session)) -> dict:
    if not has_any_passkey(session):
        raise HTTPException(status_code=409, detail="No passkey registered yet")
    creds = session.exec(select(PasskeyCredential)).all()
    options = generate_authentication_options(
        rp_id=settings.rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=_unb64(c.credential_id)) for c in creds
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    store_challenge(session, options.challenge, "login")
    return {"options": json.loads(options_to_json(options))}


class LoginFinishRequest(BaseModel):
    credential: dict


@router.post("/login/finish")
def login_finish(
    payload: LoginFinishRequest,
    response: Response,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    challenge = take_challenge(session, "login")
    if challenge is None:
        raise HTTPException(status_code=400, detail="Challenge expired, start again")

    raw_id = payload.credential.get("rawId") or payload.credential.get("id")
    if not raw_id:
        raise HTTPException(status_code=400, detail="Malformed credential")

    stored = session.exec(
        select(PasskeyCredential).where(PasskeyCredential.credential_id == raw_id)
    ).first()
    if stored is None:
        raise HTTPException(status_code=401, detail="Unknown passkey")

    try:
        verified = verify_authentication_response(
            credential=payload.credential,
            expected_challenge=challenge,
            expected_origin=settings.site_origin,
            expected_rp_id=settings.rp_id,
            credential_public_key=_unb64(stored.public_key),
            credential_current_sign_count=stored.sign_count,
        )
    except InvalidAuthenticationResponse as exc:
        log.warning("passkey login rejected: %s", exc)
        raise HTTPException(status_code=401, detail="Authentication failed") from exc

    stored.sign_count = verified.new_sign_count
    stored.last_used_at = utcnow()
    session.add(stored)
    session.commit()

    token = create_session(session, request.headers.get("user-agent", ""))
    _set_session_cookie(response, token)
    return {"authenticated": True}


# --------------------------------------------------------------------------
# Recovery
# --------------------------------------------------------------------------


class RecoveryRequest(BaseModel):
    code: str


@router.post("/recover")
def recover(
    payload: RecoveryRequest,
    response: Response,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    """Burn a recovery code for a session, so a new passkey can be registered."""
    if not redeem_recovery_code(session, payload.code):
        raise HTTPException(status_code=401, detail="Invalid or already-used code")
    token = create_session(session, request.headers.get("user-agent", ""))
    _set_session_cookie(response, token)
    return {
        "authenticated": True,
        "recovery_codes_left": unused_recovery_code_count(session),
    }


@router.post("/recovery-codes/regenerate")
def regenerate_recovery_codes(
    session: Session = Depends(get_session), _: bool = Depends(require_auth)
) -> dict:
    return {"recovery_codes": generate_recovery_codes(session)}


# --------------------------------------------------------------------------
# Passkey management and logout
# --------------------------------------------------------------------------


@router.get("/passkeys")
def list_passkeys(
    session: Session = Depends(get_session), _: bool = Depends(require_auth)
) -> list[dict]:
    return [
        {
            "id": c.id,
            "nickname": c.nickname,
            "created_at": c.created_at,
            "last_used_at": c.last_used_at,
        }
        for c in session.exec(select(PasskeyCredential)).all()
    ]


@router.delete("/passkeys/{passkey_id}")
def delete_passkey(
    passkey_id: int,
    session: Session = Depends(get_session),
    _: bool = Depends(require_auth),
) -> dict:
    cred = session.get(PasskeyCredential, passkey_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="Passkey not found")
    remaining = len(session.exec(select(PasskeyCredential)).all()) - 1
    if remaining == 0 and unused_recovery_code_count(session) == 0:
        # Removing the last way in would lock the account permanently.
        raise HTTPException(
            status_code=409,
            detail="Refusing to delete the only passkey with no recovery codes left",
        )
    session.delete(cred)
    session.commit()
    return {"deleted": True, "remaining": remaining}


@router.post("/logout")
def logout(
    response: Response,
    session: Session = Depends(get_session),
    yayo_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    destroy_session(session, yayo_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False}
