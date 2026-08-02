"""Auth tests.

The WebAuthn signature exchange itself needs a real authenticator, so these
cover everything around it: that the data routes are gated, that enrollment
tokens are single-use, that recovery codes work exactly once, and that the app
refuses to lock you out permanently.
"""

import pytest
from sqlmodel import Session

from app.services.auth import (
    check_enrollment_token,
    consume_enrollment_token,
    create_session,
    destroy_session,
    generate_recovery_codes,
    issue_enrollment_token,
    redeem_recovery_code,
    resolve_session,
    unused_recovery_code_count,
)

PROTECTED = [
    ("get", "/api/trips"),
    ("post", "/api/trips"),
    ("get", "/api/passports"),
    ("get", "/api/notes"),
    ("get", "/api/auth/passkeys"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_data_routes_require_auth(anon_client, method, path):
    # httpx.get() takes no json kwarg, so only send a body where it belongs.
    kwargs = {"json": {"title": "x"}} if method == "post" else {}
    r = getattr(anon_client, method)(path, **kwargs)
    assert r.status_code == 401, f"{method.upper()} {path} was not gated"


def test_health_and_status_are_open(anon_client):
    assert anon_client.get("/api/health").status_code == 200
    r = anon_client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json()["authenticated"] is False
    assert r.json()["enrolled"] is False


def test_local_dev_bypasses_the_passkey_wall(anon_client, monkeypatch):
    """On local http, review the site with no passkey and no session cookie.

    The autouse fixture neutralizes the bypass for the rest of the suite; turn
    it back on here to exercise the real local-dev behavior.
    """
    from app.config import Settings

    monkeypatch.setattr(Settings, "auth_optional", property(lambda self: True))

    # No cookie, no passkey registered — local dev still gets in.
    assert anon_client.get("/api/trips").status_code == 200
    status = anon_client.get("/api/auth/status").json()
    assert status["authenticated"] is True


def test_login_begin_refuses_before_enrollment(anon_client):
    """Nothing to authenticate against yet; say so rather than 500."""
    assert anon_client.post("/api/auth/login/begin").status_code == 409


def test_register_begin_rejects_bad_enrollment_token(anon_client):
    r = anon_client.post("/api/auth/register/begin", json={"enrollment_token": "wrong"})
    assert r.status_code == 401


def test_register_begin_accepts_the_real_token(anon_client, session):
    token = issue_enrollment_token(session)
    r = anon_client.post(
        "/api/auth/register/begin", json={"enrollment_token": token}
    )
    assert r.status_code == 200
    assert "options" in r.json()


# --- enrollment token ------------------------------------------------------


def test_enrollment_token_is_not_stored_in_plaintext(session: Session):
    from app.services.auth import ENROLLMENT_SETTING_KEY, get_setting

    token = issue_enrollment_token(session)
    stored = get_setting(session, ENROLLMENT_SETTING_KEY)
    assert stored is not None
    assert token not in stored
    assert check_enrollment_token(session, token)


def test_enrollment_token_is_single_use(session: Session):
    token = issue_enrollment_token(session)
    assert check_enrollment_token(session, token)
    consume_enrollment_token(session)
    assert not check_enrollment_token(session, token)


def test_empty_token_never_validates(session: Session):
    """A blank token must not slip through when none has been issued."""
    assert not check_enrollment_token(session, "")
    issue_enrollment_token(session)
    assert not check_enrollment_token(session, "")


# --- recovery codes --------------------------------------------------------


def test_recovery_codes_are_hashed_and_single_use(session: Session):
    from app.models import RecoveryCode
    from sqlmodel import select

    codes = generate_recovery_codes(session)
    assert len(codes) == 10
    assert len(set(codes)) == 10

    stored = [r.code_hash for r in session.exec(select(RecoveryCode)).all()]
    for code in codes:
        assert code not in stored

    assert unused_recovery_code_count(session) == 10
    assert redeem_recovery_code(session, codes[0])
    assert unused_recovery_code_count(session) == 9
    # Second use of the same code must fail.
    assert not redeem_recovery_code(session, codes[0])
    assert unused_recovery_code_count(session) == 9


def test_regenerating_invalidates_the_old_set(session: Session):
    old = generate_recovery_codes(session)
    new = generate_recovery_codes(session)
    assert not redeem_recovery_code(session, old[0])
    assert redeem_recovery_code(session, new[0])


def test_recovery_endpoint_grants_a_session(anon_client, session):
    codes = generate_recovery_codes(session)
    r = anon_client.post("/api/auth/recover", json={"code": codes[3]})
    assert r.status_code == 200
    assert r.json()["recovery_codes_left"] == 9
    # The cookie it set must actually unlock the gated routes.
    assert anon_client.get("/api/trips").status_code == 200


def test_bad_recovery_code_rejected(anon_client, session):
    generate_recovery_codes(session)
    r = anon_client.post("/api/auth/recover", json={"code": "0000000-00000000"})
    assert r.status_code == 401
    assert anon_client.get("/api/trips").status_code == 401


# --- sessions --------------------------------------------------------------


def test_session_token_is_hashed_and_resolvable(session: Session):
    from app.models import Session as SessionRow
    from sqlmodel import select

    token = create_session(session, "pytest")
    rows = session.exec(select(SessionRow)).all()
    assert len(rows) == 1
    assert rows[0].token_hash != token

    assert resolve_session(session, token) is not None
    assert resolve_session(session, "not-a-real-token") is None
    assert resolve_session(session, None) is None


def test_expired_session_is_rejected_and_cleaned_up(session: Session):
    from datetime import timedelta

    from app.models import Session as SessionRow
    from app.models import utcnow
    from sqlmodel import select

    token = create_session(session)
    row = session.exec(select(SessionRow)).one()
    row.expires_at = utcnow() - timedelta(days=1)
    session.add(row)
    session.commit()

    assert resolve_session(session, token) is None
    assert session.exec(select(SessionRow)).all() == []


def test_logout_destroys_the_session(session: Session):
    token = create_session(session)
    destroy_session(session, token)
    assert resolve_session(session, token) is None


def test_logout_endpoint_clears_access(anon_client, session):
    codes = generate_recovery_codes(session)
    anon_client.post("/api/auth/recover", json={"code": codes[0]})
    assert anon_client.get("/api/trips").status_code == 200

    anon_client.post("/api/auth/logout")
    assert anon_client.get("/api/trips").status_code == 401


# --- lockout protection ----------------------------------------------------


def test_refuses_to_delete_last_passkey_with_no_recovery_left(client, session):
    """Deleting the only way in would brick the account."""
    from app.models import PasskeyCredential

    session.add(
        PasskeyCredential(credential_id="abc", public_key="def", nickname="only")
    )
    session.commit()
    cred_id = session.exec(__import__("sqlmodel").select(PasskeyCredential)).one().id

    r = client.delete(f"/api/auth/passkeys/{cred_id}")
    assert r.status_code == 409
    assert "Refusing" in r.json()["detail"]


def test_deleting_last_passkey_allowed_when_recovery_codes_exist(client, session):
    from app.models import PasskeyCredential

    session.add(
        PasskeyCredential(credential_id="abc", public_key="def", nickname="only")
    )
    session.commit()
    generate_recovery_codes(session)
    cred_id = session.exec(__import__("sqlmodel").select(PasskeyCredential)).one().id

    assert client.delete(f"/api/auth/passkeys/{cred_id}").status_code == 200
