import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import main
from app.api.auth import require_auth
from app.db import get_session
from app.main import app


@pytest.fixture(name="engine")
def engine_fixture():
    # StaticPool keeps a single in-memory database alive across connections;
    # without it each connection gets its own empty database.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(name="session")
def session_fixture(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture(name="_patched_lifespan")
def patched_lifespan_fixture(engine, monkeypatch):
    """Point the startup hook at the test database.

    main.py binds `engine` at import time, so the lifespan (which purges expired
    sessions and mints an enrollment token) would otherwise run against the real
    var/travel.db. Patching the name on the module is what redirects it.
    """
    monkeypatch.setattr(main, "engine", engine)


@pytest.fixture(name="anon_client")
def anon_client_fixture(session: Session, _patched_lifespan):
    """No auth override — used to prove the data routes are actually gated."""
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture(name="client")
def client_fixture(session: Session, _patched_lifespan):
    """Authenticated client for the CRUD tests.

    Auth itself is exercised for real in test_auth.py; overriding it here keeps
    the data tests about data.
    """
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_auth] = lambda: True
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
