"""What a passport holder needs to enter a country -- asked once, cached forever.

Answers a single factual question: "what does an MX/US passport holder need to
enter country X" -- visa, arrival card, ETA, and (asked about but usually
irrelevant) insurance, vaccination, an onward ticket. The answer is cached in
`EntryPolicy`, keyed by (country_code, nationality), and **there is
deliberately no refresh path**: once a row exists it is never re-queried. This
is a conscious trade against staleness -- border policy changes without
notice, and every surface that shows a reading also shows when it was fetched,
so the risk stays visible rather than hidden. If a cached answer ever needs
correcting, that is a manual edit, not a feature.

This is a separate LLM call from the email pipeline in `extraction.py`, and
deliberately not the same client class: it asks a generic factual question
with no email content in it, so it sits outside the mail privacy boundary
those docs describe -- but it still costs money, hence the permanent cache.

The model is injected behind a Protocol exactly like `extraction.ExtractionModel`,
so `get_policy` is testable with a fake and never opens a socket in the test
suite; the real implementation is `OpenRouterPolicyModel` at the bottom.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional, Protocol

from sqlmodel import Session, select

from app.countries import country_name
from app.models import CountryEntry, EntryPolicy, Nationality, PermitType, utcnow

log = logging.getLogger("yayo.entry_policy")

# The default passport when a trip has none selected yet (decision 2). US,
# never MX -- MX only applies once a trip's CountryEntry explicitly says so.
DEFAULT_NATIONALITY = Nationality.US

PERMIT_TYPES = tuple(p.value for p in PermitType)


def readiness_passport(entry: Optional[CountryEntry]) -> Nationality:
    """The nationality readiness is computed for (decision 2).

    A trip's own CountryEntry.passport wins when one has been chosen; every
    other case -- no entry yet, or an entry with no passport picked -- reads as
    the US default. Never MX by default: MX only applies once a trip
    explicitly says so.
    """
    if entry is not None and entry.passport is not None:
        return entry.passport.nationality
    return DEFAULT_NATIONALITY


# --------------------------------------------------------------------------
# The strict tool schema
# --------------------------------------------------------------------------

ENTRY_POLICY_TOOL = {
    "name": "assess_entry_policy",
    "description": (
        "Record what a passport holder needs to enter a country as a tourist: "
        "the permit they enter under, and which of the usual pre-arrival "
        "requirements actually apply. Answer for a short leisure stay, not a "
        "residency or work application."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "permit_type",
            "permitted_days",
            "visa_required",
            "entry_card_required",
            "entry_card_name",
            "eta_required",
            "insurance_required",
            "vaccination_required",
            "onward_ticket_required",
            "summary",
            "advisory",
        ],
        "properties": {
            "permit_type": {
                "type": ["string", "null"],
                "enum": list(PERMIT_TYPES) + [None],
                "description": (
                    "How this passport holder enters as a tourist: visa_free, "
                    "evisa (apply online before travel), visa_on_arrival (pay "
                    "and get it at the border), visa (apply at an embassy "
                    "beforehand), residency, or citizen. Null if you don't know."
                ),
            },
            "permitted_days": {
                "type": ["integer", "null"],
                "description": "Days permitted to stay under that permit. Null if unknown.",
            },
            "visa_required": {
                "type": "boolean",
                "description": (
                    "True for evisa, visa_on_arrival, or visa permit types. "
                    "False for visa_free, residency, or citizen."
                ),
            },
            "entry_card_required": {
                "type": "boolean",
                "description": (
                    "True if this country requires a paper or electronic "
                    "arrival/entry/disembarkation card, or an electronic "
                    "customs declaration, separate from the visa itself."
                ),
            },
            "entry_card_name": {
                "type": ["string", "null"],
                "description": (
                    "The card's local name, e.g. 'Indonesia e-CD', 'SG Arrival "
                    "Card', 'Thailand TDAC'. Null if entry_card_required is false."
                ),
            },
            "eta_required": {
                "type": "boolean",
                "description": (
                    "True for an Electronic Travel Authorization distinct from "
                    "a visa (e.g. Canada eTA, UK ETA, US ESTA)."
                ),
            },
            "insurance_required": {
                "type": "boolean",
                "description": "True only if travel/health insurance is mandatory to enter, not merely recommended.",
            },
            "vaccination_required": {
                "type": "boolean",
                "description": "True only if a specific vaccination (e.g. yellow fever) is mandatory to enter.",
            },
            "onward_ticket_required": {
                "type": "boolean",
                "description": "True only if proof of onward/return travel is a routine, enforced entry requirement.",
            },
            "summary": {
                "type": "string",
                "description": "One short sentence: the permit and its length, e.g. 'Visa-on-arrival, 30 days.'",
            },
            "advisory": {
                "type": "string",
                "description": "One short sentence reminding the traveller this is advisory and rules change without notice.",
            },
        },
    },
}


# --------------------------------------------------------------------------
# The model, behind a seam
# --------------------------------------------------------------------------


class EntryPolicyModel(Protocol):
    """What get_policy needs from an LLM. The real one calls OpenRouter."""

    policy_model: str

    def assess_entry_policy(
        self, country_code: str, country_name: str, nationality: str
    ) -> Optional[dict]:
        """The raw tool input as a dict, or None if the model returned nothing."""
        ...


# --------------------------------------------------------------------------
# Validation -- trust nothing from the model, strict mode or not
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyReading:
    permit_type: Optional[PermitType]
    permitted_days: Optional[int]
    visa_required: bool
    entry_card_required: bool
    entry_card_name: str
    eta_required: bool
    insurance_required: bool
    vaccination_required: bool
    onward_ticket_required: bool
    summary: str
    advisory: str


def _clean_str(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _clean_bool(value) -> bool:
    return bool(value) if isinstance(value, bool) else False


def validate_policy(payload) -> Optional[PolicyReading]:
    """Coerce a raw tool input into a PolicyReading, or None if unusable.

    Mirrors extraction.validate_booking: strict mode from the real API
    guarantees shape, but a refusal, a fake, or provider drift might not, so
    nothing here is trusted blindly.
    """
    if not isinstance(payload, dict):
        return None
    try:
        permit_raw = payload.get("permit_type")
        permit_type: Optional[PermitType] = None
        if permit_raw is not None:
            if not isinstance(permit_raw, str) or permit_raw not in PERMIT_TYPES:
                return None
            permit_type = PermitType(permit_raw)

        days = payload.get("permitted_days")
        permitted_days: Optional[int] = None
        if days is not None:
            permitted_days = int(days)
            if not 0 <= permitted_days <= 3650:
                return None

        return PolicyReading(
            permit_type=permit_type,
            permitted_days=permitted_days,
            visa_required=_clean_bool(payload.get("visa_required")),
            entry_card_required=_clean_bool(payload.get("entry_card_required")),
            entry_card_name=_clean_str(payload.get("entry_card_name")),
            eta_required=_clean_bool(payload.get("eta_required")),
            insurance_required=_clean_bool(payload.get("insurance_required")),
            vaccination_required=_clean_bool(payload.get("vaccination_required")),
            onward_ticket_required=_clean_bool(payload.get("onward_ticket_required")),
            summary=_clean_str(payload.get("summary")),
            advisory=_clean_str(payload.get("advisory")),
        )
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Curated overrides -- human-verified ground truth that beats the LLM
# --------------------------------------------------------------------------
#
# The entry policy is, at bottom, an LLM factual-recall call, and models get
# border rules wrong -- confidently and plausibly enough that validate_policy
# (which only checks *shape*) can't catch it, after which decision 7 caches it
# forever. This layer is the answer: a committed file of hand-verified
# (country, nationality) readings that win over both the model and any cached
# row, and are never re-queried. Editing that file is the correction path, so
# a wrong reading no longer needs a raw DB edit -- just a reviewed commit.

OVERRIDE_SOURCE = "curated-override"


def overrides_path() -> Path:
    from app.config import get_settings

    return get_settings().data_dir / "rules" / "entry-policy-overrides.json"


def _override_row(raw: dict) -> Optional[EntryPolicy]:
    """Build one authoritative EntryPolicy from an override entry, or None if it
    doesn't validate. Trust nothing -- even committed data goes through
    validate_policy, so a typo'd permit_type is skipped, not shipped."""
    code = _clean_str(raw.get("country_code")).upper()
    nat_raw = _clean_str(raw.get("nationality")).upper()
    if len(code) != 2 or nat_raw not in (n.value for n in Nationality):
        return None
    reading = validate_policy(raw)
    if reading is None:
        return None
    when = raw.get("checked_on")
    try:
        fetched_at = datetime.fromisoformat(when) if isinstance(when, str) else utcnow()
    except ValueError:
        fetched_at = utcnow()
    return EntryPolicy(
        country_code=code,
        nationality=Nationality(nat_raw),
        source_model=OVERRIDE_SOURCE,
        fetched_at=fetched_at,
        **reading.__dict__,
    )


@lru_cache
def load_overrides(
    path: Optional[Path] = None,
) -> dict[tuple[str, Nationality], EntryPolicy]:
    """The committed overrides, keyed by (country_code upper, Nationality).

    Cached like email_filter.load_rules; call cache_clear() in tests. A single
    malformed entry is skipped (and logged), never fatal -- one bad row must not
    blank the whole authoritative set.
    """
    target = path or overrides_path()
    if not target.exists():
        return {}
    raw = json.loads(target.read_text(encoding="utf-8"))
    out: dict[tuple[str, Nationality], EntryPolicy] = {}
    for entry in raw.get("policies", []):
        row = _override_row(entry)
        if row is None:
            log.warning("skipping unusable entry-policy override: %r", entry)
            continue
        out[(row.country_code, row.nationality)] = row
    return out


# --------------------------------------------------------------------------
# The cache -- read-through, permanent, no refresh
# --------------------------------------------------------------------------


def cached_policy(session: Session, country_code: str, nationality: Nationality) -> Optional[EntryPolicy]:
    """A read-only cache lookup -- never fetches. Public so callers that must
    not trigger a model call (e.g. trip_readiness's alternate-passport hint)
    can still see what happens to already be cached.

    A curated override wins over everything: it is checked before the DB, so it
    beats a stale (or wrong) cached row, not just a missing one. Because
    get_policy returns on this hit, an overridden pair never triggers a model
    call and never gets a competing row baked into the cache.
    """
    override = load_overrides().get((country_code.upper(), nationality))
    if override is not None:
        return override
    return session.exec(
        select(EntryPolicy)
        .where(EntryPolicy.country_code == country_code.upper())
        .where(EntryPolicy.nationality == nationality)
    ).first()


def get_policy(
    session: Session,
    country_code: str,
    nationality: Nationality,
    model: Optional[EntryPolicyModel] = None,
) -> Optional[EntryPolicy]:
    """The cached policy for (country_code, nationality), fetching once if absent.

    Returns None when there is no cached row and no model was supplied (an
    unconfigured box) -- not an exception, so a missing OpenRouter key degrades
    the UI to "unknown" rather than breaking a trip-detail load. Never
    re-queries an existing row: once fetched, a reading is kept forever
    (see the module docstring).
    """
    code = country_code.upper()
    cached = cached_policy(session, code, nationality)
    if cached is not None:
        return cached
    if model is None:
        return None

    raw = model.assess_entry_policy(code, country_name(code), nationality.value)
    reading = validate_policy(raw)
    if reading is None:
        log.info("entry policy for %s/%s: model returned nothing usable", code, nationality.value)
        return None

    row = EntryPolicy(
        country_code=code,
        nationality=nationality,
        source_model=model.policy_model,
        **reading.__dict__,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    log.info("cached entry policy for %s/%s", code, nationality.value)
    return row


# --------------------------------------------------------------------------
# The real model -- Claude via OpenRouter's OpenAI-compatible API
# --------------------------------------------------------------------------


def _as_function_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "strict": tool.get("strict", False),
            "parameters": tool["input_schema"],
        },
    }


class OpenRouterPolicyModel:
    """A single strict tool call per (country, nationality), via OpenRouter."""

    def __init__(self, client, policy_model: str) -> None:
        self._client = client
        self.policy_model = policy_model

    @classmethod
    def from_settings(cls) -> "OpenRouterPolicyModel":
        from app.config import get_settings

        s = get_settings()
        if not s.openrouter_api_key:
            raise RuntimeError(
                "OpenRouter API key is not configured. Set "
                "YAYO_OPENROUTER_API_KEY in deploy/.env."
            )
        from openai import OpenAI

        client = OpenAI(
            base_url=s.openrouter_base_url,
            api_key=s.openrouter_api_key,
            default_headers={"X-Title": "Yayo travel"},
        )
        return cls(client, s.policy_model)

    def assess_entry_policy(
        self, country_code: str, country_name: str, nationality: str
    ) -> Optional[dict]:
        tool = ENTRY_POLICY_TOOL
        messages = [
            {
                "role": "user",
                "content": (
                    f"A traveller holding a {nationality} passport is visiting "
                    f"{country_name} ({country_code}) as a tourist for a short "
                    "leisure stay. What do they need to enter?"
                ),
            }
        ]
        response = self._client.chat.completions.create(
            model=self.policy_model,
            max_tokens=1024,
            tools=[_as_function_tool(tool)],
            tool_choice={"type": "function", "function": {"name": tool["name"]}},
            messages=messages,
        )
        message = response.choices[0].message
        if message.tool_calls:
            return json.loads(message.tool_calls[0].function.arguments)
        return None


def policy_model_or_none() -> Optional["OpenRouterPolicyModel"]:
    """A ready-to-use model, or None on an unconfigured box.

    Call sites (sync_requirements at trip/passport-mutation time) use this
    instead of deciding for themselves whether an OpenRouter key is set --
    mirroring how scheduler.py gates email extraction on the same setting,
    but degrading to None here rather than raising, since a missing key must
    never break a trip save.
    """
    from app.config import get_settings

    if not get_settings().openrouter_api_key:
        return None
    return OpenRouterPolicyModel.from_settings()
