"""Phase 1: the entry-policy service.

No network. The model is injected behind a Protocol, same as extraction; one
test exercises the real OpenRouter client against a mock HTTP transport so the
strict tool schema is validated end to end without a socket.

The line under test: a policy is fetched once per (country, nationality) and
never again -- there is no refresh path, by design (see the module docstring
in services/entry_policy.py). A malformed model response is rejected rather
than persisted, and an unconfigured box degrades to "unknown" (None) instead
of raising.
"""

import json

import httpx
from sqlmodel import Session, select

from app.models import EntryPolicy, Nationality, PermitType
from app.services.entry_policy import (
    ENTRY_POLICY_TOOL,
    OpenRouterPolicyModel,
    get_policy,
    validate_policy,
)


# --------------------------------------------------------------------------
# A fake model
# --------------------------------------------------------------------------


class FakeModel:
    policy_model = "anthropic/claude-sonnet-5"

    def __init__(self, result=None):
        self._result = result
        self.calls: list[tuple[str, str, str]] = []

    def assess_entry_policy(self, country_code, country_name, nationality):
        self.calls.append((country_code, country_name, nationality))
        return self._result


VALID_POLICY = {
    "permit_type": "visa_on_arrival",
    "permitted_days": 30,
    "visa_required": True,
    "entry_card_required": True,
    "entry_card_name": "Indonesia e-CD",
    "eta_required": False,
    "insurance_required": False,
    "vaccination_required": False,
    "onward_ticket_required": False,
    "summary": "Visa-on-arrival, 30 days.",
    "advisory": "Border rules change without notice -- verify before you fly.",
}


# --------------------------------------------------------------------------
# validate_policy
# --------------------------------------------------------------------------


def test_validate_policy_accepts_a_full_reading():
    reading = validate_policy(VALID_POLICY)
    assert reading is not None
    assert reading.permit_type == PermitType.visa_on_arrival
    assert reading.permitted_days == 30
    assert reading.visa_required is True
    assert reading.entry_card_required is True
    assert reading.entry_card_name == "Indonesia e-CD"
    assert reading.insurance_required is False


def test_validate_policy_accepts_visa_free_with_no_extras_required():
    payload = {
        **VALID_POLICY,
        "permit_type": "visa_free",
        "visa_required": False,
        "entry_card_required": False,
        "entry_card_name": None,
    }
    reading = validate_policy(payload)
    assert reading is not None
    assert reading.permit_type == PermitType.visa_free
    assert reading.visa_required is False
    assert reading.entry_card_required is False
    assert reading.entry_card_name == ""


def test_validate_policy_rejects_bad_permit_type():
    assert validate_policy({**VALID_POLICY, "permit_type": "backpacker_visa"}) is None


def test_validate_policy_rejects_out_of_range_days():
    assert validate_policy({**VALID_POLICY, "permitted_days": 9999}) is None


def test_validate_policy_rejects_not_a_dict():
    assert validate_policy(None) is None
    assert validate_policy("nope") is None
    assert validate_policy([VALID_POLICY]) is None


def test_validate_policy_tolerates_missing_optional_fields():
    minimal = {
        "permit_type": None,
        "permitted_days": None,
        "visa_required": False,
        "entry_card_required": False,
        "entry_card_name": None,
        "eta_required": False,
        "insurance_required": False,
        "vaccination_required": False,
        "onward_ticket_required": False,
        "summary": "",
        "advisory": "",
    }
    reading = validate_policy(minimal)
    assert reading is not None
    assert reading.permit_type is None
    assert reading.permitted_days is None


# --------------------------------------------------------------------------
# get_policy -- the cache
# --------------------------------------------------------------------------


def test_get_policy_fetches_once_and_caches(session: Session):
    model = FakeModel(VALID_POLICY)

    first = get_policy(session, "ID", Nationality.US, model)
    assert first is not None
    assert first.entry_card_required is True
    assert len(model.calls) == 1
    assert model.calls[0] == ("ID", "Indonesia", "US")

    second = get_policy(session, "ID", Nationality.US, model)
    assert second is not None
    assert second.id == first.id
    # The whole point: a cache hit never calls the model again.
    assert len(model.calls) == 1


def test_get_policy_is_scoped_per_nationality(session: Session):
    model = FakeModel(VALID_POLICY)
    get_policy(session, "ID", Nationality.US, model)
    get_policy(session, "ID", Nationality.MX, model)
    assert len(model.calls) == 2

    rows = session.exec(select(EntryPolicy).where(EntryPolicy.country_code == "ID")).all()
    assert {r.nationality for r in rows} == {Nationality.US, Nationality.MX}


def test_get_policy_normalises_country_code_case(session: Session):
    model = FakeModel(VALID_POLICY)
    get_policy(session, "id", Nationality.US, model)
    cached = get_policy(session, "ID", Nationality.US, model)
    assert cached is not None
    assert len(model.calls) == 1  # the lowercase and uppercase calls hit the same row


def test_get_policy_unconfigured_returns_none_without_raising(session: Session):
    assert get_policy(session, "ID", Nationality.US, model=None) is None
    assert session.exec(select(EntryPolicy)).all() == []


def test_get_policy_malformed_model_output_persists_nothing(session: Session):
    model = FakeModel({"permit_type": "not_a_real_type"})
    result = get_policy(session, "ID", Nationality.US, model)
    assert result is None
    assert session.exec(select(EntryPolicy)).all() == []


def test_get_policy_no_tool_call_persists_nothing(session: Session):
    model = FakeModel(None)
    result = get_policy(session, "ID", Nationality.US, model)
    assert result is None
    assert session.exec(select(EntryPolicy)).all() == []


# --------------------------------------------------------------------------
# The real client, wire-level
# --------------------------------------------------------------------------


def _tool_response(tool_name: str, tool_input: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "anthropic/claude-sonnet-5",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_test",
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(tool_input),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        },
    )


def _model_with_transport(handler) -> OpenRouterPolicyModel:
    from openai import OpenAI

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return OpenRouterPolicyModel(client, "anthropic/claude-sonnet-5")


def test_real_client_sends_strict_tool_and_reads_the_tool_call():
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent.update(payload)
        return _tool_response(ENTRY_POLICY_TOOL["name"], VALID_POLICY)

    model = _model_with_transport(handler)
    raw = model.assess_entry_policy("ID", "Indonesia", "US")

    assert raw == VALID_POLICY
    assert sent["tools"][0]["function"]["strict"] is True
    assert sent["tools"][0]["function"]["name"] == ENTRY_POLICY_TOOL["name"]
    assert sent["tool_choice"]["function"]["name"] == ENTRY_POLICY_TOOL["name"]
    assert "US" in sent["messages"][0]["content"]
    assert "Indonesia" in sent["messages"][0]["content"]


def test_real_client_end_to_end_caches_via_get_policy(session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        return _tool_response(ENTRY_POLICY_TOOL["name"], VALID_POLICY)

    model = _model_with_transport(handler)
    row = get_policy(session, "ID", Nationality.US, model)

    assert row is not None
    assert row.source_model == "anthropic/claude-sonnet-5"
    assert row.entry_card_required is True
    assert row.fetched_at is not None
