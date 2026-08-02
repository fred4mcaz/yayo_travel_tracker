"""Turning a candidate email into a pending proposal.

This is the only place mail leaves the box, and it is deliberately a two-stage
funnel:

  1. **Triage** with the cheap model (Claude Haiku): is this actually a booking
     worth reading closely? The local filter already gated on sender and
     keywords; this is the first time an LLM sees the text.
  2. **Extract** with the capable model (Claude Sonnet): pull the structured
     booking out, against a *strict* tool schema so the shape is guaranteed.

Both calls go through **OpenRouter's OpenAI-compatible API** rather than
Anthropic directly -- same models, one key, routed through OpenRouter. The tool
schemas are declared once here in a neutral form and translated to OpenAI
function-calling at the call site.

The output is an `Extraction` row with `status=pending`. **Nothing here touches
trip data.** A proposal is not a fact until it is accepted (phase 4).

The model is injected behind a Protocol, so every test in this suite runs
against a fake and never opens a socket. The real implementation lives at the
bottom of the file.
"""

import json
import logging
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional, Protocol

from sqlmodel import Session, select

from app.models import EmailMessage, Extraction, ExtractionStatus, utcnow

log = logging.getLogger("yayo.extraction")

BOOKING_KINDS = ("hotel", "flight", "train", "bus", "ferry", "car", "other")


# --------------------------------------------------------------------------
# The strict tool schemas
# --------------------------------------------------------------------------

# Strict tool use requires every property to appear in `required` and
# `additionalProperties: false`. Optionality is expressed by allowing null,
# never by omission -- so the model must decide each field explicitly rather
# than quietly leave it out.

TRIAGE_TOOL = {
    "name": "assess_email",
    "description": "Record whether this email is a travel booking confirmation.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["is_booking", "confidence", "reason"],
        "properties": {
            "is_booking": {
                "type": "boolean",
                "description": (
                    "True only for a confirmed reservation the traveller holds "
                    "-- a hotel stay, a flight, a train. False for marketing, "
                    "receipts for other things, or a booking that was cancelled."
                ),
            },
            "confidence": {
                "type": "number",
                "description": "0 to 1, how sure you are.",
            },
            "reason": {"type": "string", "description": "One short sentence."},
        },
    },
}

EXTRACT_TOOL = {
    "name": "record_booking",
    "description": (
        "Record one travel booking from a confirmation email. One email is one "
        "booking: a single hotel stay, or a single arrival journey. Do not "
        "invent fields you cannot see -- use null."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "kind",
            "country_code",
            "city",
            "start_date",
            "end_date",
            "hotel_name",
            "carrier",
            "confirmation_code",
            "confidence",
        ],
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(BOOKING_KINDS),
                "description": "hotel for a stay; the mode for an arrival journey.",
            },
            "country_code": {
                "type": ["string", "null"],
                "description": "ISO 3166-1 alpha-2, e.g. VN, TH, JP. Null if unclear.",
            },
            "city": {"type": ["string", "null"]},
            "start_date": {
                "type": ["string", "null"],
                "description": "YYYY-MM-DD. Check-in for a hotel, departure for a leg.",
            },
            "end_date": {
                "type": ["string", "null"],
                "description": "YYYY-MM-DD. Check-out for a hotel; null for a leg.",
            },
            "hotel_name": {"type": ["string", "null"]},
            "carrier": {
                "type": ["string", "null"],
                "description": "Airline or operator, for a leg.",
            },
            "confirmation_code": {"type": ["string", "null"]},
            "confidence": {
                "type": "number",
                "description": "0 to 1, how sure you are of this reading.",
            },
        },
    },
}


# --------------------------------------------------------------------------
# The model, behind a seam
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TriageResult:
    is_booking: bool
    confidence: float
    reason: str


class ExtractionModel(Protocol):
    """What the pipeline needs from an LLM. The real one calls OpenRouter."""

    # The extract model's id, recorded on each Extraction so a proposal can be
    # traced to what produced it.
    extract_model: str

    def triage(self, subject: str, body: str) -> TriageResult:
        ...

    def extract(
        self, subject: str, body: str, received_on: Optional[date] = None
    ) -> Optional[dict]:
        """The raw tool input, or None if the model would not produce one.

        `received_on` is the date the confirmation arrived: the one hard anchor
        for the year, since a booking is always for a stay on or after it.
        """
        ...


# --------------------------------------------------------------------------
# Validation -- strict mode guarantees shape from the real API, but a refusal,
# a fake, or a future schema drift might not, so nothing is trusted blindly.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Booking:
    kind: str
    country_code: Optional[str]
    city: Optional[str]
    start_date: Optional[str]
    end_date: Optional[str]
    hotel_name: Optional[str]
    carrier: Optional[str]
    confirmation_code: Optional[str]
    confidence: Optional[float]

    def payload(self) -> dict:
        d = self.__dict__.copy()
        d.pop("confidence")
        return d


def _clean_str(value) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected a string")
    value = value.strip()
    return value or None


def _clean_date(value) -> Optional[str]:
    """Validate an ISO date, returning it normalised, or None."""
    value = _clean_str(value)
    if value is None:
        return None
    # date.fromisoformat is strict about YYYY-MM-DD; a bad date raises.
    return date.fromisoformat(value).isoformat()


def validate_booking(payload) -> Optional[Booking]:
    """Coerce a raw tool input into a Booking, or None if it is unusable.

    Returning None is a rejection: the caller persists nothing. A booking that
    names no country and no date is not something phase 4 could ever match or
    place, so it is treated as unusable rather than stored as noise.
    """
    if not isinstance(payload, dict):
        return None
    try:
        kind = payload["kind"]
        if kind not in BOOKING_KINDS:
            return None

        country = _clean_str(payload.get("country_code"))
        if country is not None:
            country = country.upper()
            if len(country) != 2 or not country.isalpha():
                return None

        confidence = payload.get("confidence")
        if confidence is not None:
            confidence = float(confidence)
            if not 0.0 <= confidence <= 1.0:
                return None

        booking = Booking(
            kind=kind,
            country_code=country,
            city=_clean_str(payload.get("city")),
            start_date=_clean_date(payload.get("start_date")),
            end_date=_clean_date(payload.get("end_date")),
            hotel_name=_clean_str(payload.get("hotel_name")),
            carrier=_clean_str(payload.get("carrier")),
            confirmation_code=_clean_str(payload.get("confirmation_code")),
            confidence=confidence,
        )
    except (KeyError, TypeError, ValueError):
        return None

    # A proposal with nothing to match on is not worth reviewing.
    if booking.country_code is None and booking.start_date is None:
        return None
    return booking


# --------------------------------------------------------------------------
# Year sanity -- the second layer under the prompt anchor
# --------------------------------------------------------------------------

# A confirmation email is for a stay on or after the day it arrived, and this
# mailbox never backfills old mail (see email_ingest), so a check-in that lands
# a whole year or more before the email is not a real past booking -- it is the
# model having read the wrong year, the classic "what year is it" failure. The
# slack keeps ordinary cases untouched: a stay booked to start the evening the
# confirmation lands, or one straddling New Year booked days before, is only
# days on the wrong side, not a year.
YEAR_SANITY_SLACK_DAYS = 60


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # Feb 29 into a non-leap year; land on the 28th.
        return d.replace(year=d.year + years, day=28)


def correct_year(booking: Booking, received_on: Optional[date]) -> Booking:
    """Roll a deep-past check-in forward to the year the email implies.

    Only corrects a *cross-year* error -- a check-in in an earlier year than the
    email -- and only when it is well into the past, so a legitimately recent
    date is never rewritten. The checkout shifts by the same span, so the stay
    keeps its length. Returns the booking unchanged when there is nothing to fix.
    """
    if received_on is None or booking.start_date is None:
        return booking
    start = date.fromisoformat(booking.start_date)
    if (received_on - start).days <= YEAR_SANITY_SLACK_DAYS:
        return booking  # not in the deep past
    bump = received_on.year - start.year
    if bump <= 0:
        return booking  # same-year past date -- not the wrong-year bug
    new_start = _add_years(start, bump)
    shift = new_start - start
    new_end = booking.end_date
    if booking.end_date is not None:
        new_end = (date.fromisoformat(booking.end_date) + shift).isoformat()
    return replace(booking, start_date=new_start.isoformat(), end_date=new_end)


# --------------------------------------------------------------------------
# The pipeline
# --------------------------------------------------------------------------


def _pending(session: Session, limit: int) -> list[EmailMessage]:
    """Candidate messages not yet run through extraction, oldest first."""
    return list(
        session.exec(
            select(EmailMessage)
            .where(EmailMessage.looks_like_travel == True)  # noqa: E712
            .where(EmailMessage.processed_at.is_(None))
            .order_by(EmailMessage.received_at)
            .limit(limit)
        ).all()
    )


def process_email(
    session: Session, model: ExtractionModel, email: EmailMessage
) -> Optional[Extraction]:
    """Triage, extract, and record a pending proposal for one email.

    Marks the email processed either way, so a message that triages out or
    yields nothing is not retried on every poll. Returns the Extraction, or
    None if none was created.
    """
    subject, body = email.subject, email.snippet
    received_on = email.received_at.date() if email.received_at else None
    extraction: Optional[Extraction] = None

    triage = model.triage(subject, body)
    if triage.is_booking:
        raw = model.extract(subject, body, received_on)
        booking = validate_booking(raw)
        if booking is not None:
            corrected = correct_year(booking, received_on)
            if corrected.start_date != booking.start_date:
                log.info(
                    "email %s: corrected check-in year %s -> %s "
                    "(confirmation received %s)",
                    email.id,
                    booking.start_date,
                    corrected.start_date,
                    received_on,
                )
                booking = corrected
            extraction = Extraction(
                email_message_id=email.id,
                model=model.extract_model,
                payload_json=json.dumps(booking.payload(), sort_keys=True),
                confidence=booking.confidence,
                status=ExtractionStatus.pending,
            )
            session.add(extraction)
        elif raw is not None:
            log.info("email %s extracted but the result was unusable", email.id)
    else:
        log.debug("email %s triaged out: %s", email.id, triage.reason)

    email.processed_at = utcnow()
    session.add(email)
    return extraction


def run_extractions(
    session: Session, model: ExtractionModel, *, limit: int = 20
) -> dict:
    """Process the backlog of travel candidates. Returns a summary."""
    proposed = processed = 0
    for email in _pending(session, limit):
        if process_email(session, model, email) is not None:
            proposed += 1
        processed += 1
    session.commit()
    if processed:
        log.info("extraction: %d processed, %d proposals pending", processed, proposed)
    return {"processed": processed, "proposed": proposed}


# --------------------------------------------------------------------------
# The real model -- Claude via OpenRouter's OpenAI-compatible API
# --------------------------------------------------------------------------


def _as_function_tool(tool: dict) -> dict:
    """Our neutral tool schema, in OpenAI function-calling shape.

    `strict` and `additionalProperties: false` carry over unchanged -- the
    schemas were written strict-compatible for Anthropic and the requirements
    are the same here. Even so, `validate_booking` re-checks everything, so
    whether a given OpenRouter-routed provider enforces strict or not, a
    malformed tool call is caught rather than trusted.
    """
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "strict": tool.get("strict", False),
            "parameters": tool["input_schema"],
        },
    }


class OpenRouterModel:
    """Triage with Haiku, extract with Sonnet, both via OpenRouter."""

    def __init__(self, client, triage_model: str, extract_model: str) -> None:
        self._client = client
        self._triage_model = triage_model
        self.extract_model = extract_model

    @classmethod
    def from_settings(cls) -> "OpenRouterModel":
        from app.config import get_settings

        s = get_settings()
        if not s.openrouter_api_key:
            raise RuntimeError(
                "OpenRouter API key is not configured. Set YAYO_OPENROUTER_API_KEY "
                "in deploy/.env."
            )
        from openai import OpenAI

        client = OpenAI(
            base_url=s.openrouter_base_url,
            api_key=s.openrouter_api_key,
            # Shows up on the OpenRouter dashboard; harmless if ignored.
            default_headers={"X-Title": "Yayo travel"},
        )
        return cls(client, s.triage_model, s.extract_model)

    def triage(self, subject: str, body: str) -> TriageResult:
        result = self._call(self._triage_model, TRIAGE_TOOL, subject, body)
        if result is None:
            return TriageResult(False, 0.0, "no tool call returned")
        return TriageResult(
            is_booking=bool(result.get("is_booking")),
            confidence=float(result.get("confidence") or 0.0),
            reason=str(result.get("reason") or ""),
        )

    def extract(
        self, subject: str, body: str, received_on: Optional[date] = None
    ) -> Optional[dict]:
        return self._call(
            self.extract_model, EXTRACT_TOOL, subject, body, received_on
        )

    def _call(
        self,
        model: str,
        tool: dict,
        subject: str,
        body: str,
        received_on: Optional[date] = None,
    ) -> Optional[dict]:
        messages: list[dict] = []
        if received_on is not None:
            # The model has no other way to know the year. A confirmation is for
            # a future stay, so anchoring on the day it arrived stops the "wrong
            # year" default the extractor otherwise reaches for.
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"This confirmation email arrived on {received_on.isoformat()}. "
                        "The stay or journey it describes is on or after that date -- "
                        "a booking is never in the past. Read every date from the email "
                        "itself, and whenever a year is missing or ambiguous choose the "
                        "one that puts the trip on or after the arrival date. Never "
                        "return a check-in earlier than the day this email arrived."
                    ),
                }
            )
        messages.append({"role": "user", "content": f"Subject: {subject}\n\n{body}"})
        response = self._client.chat.completions.create(
            model=model,
            max_tokens=1024,
            tools=[_as_function_tool(tool)],
            tool_choice={"type": "function", "function": {"name": tool["name"]}},
            messages=messages,
        )
        message = response.choices[0].message
        if message.tool_calls:
            # OpenAI returns the arguments as a JSON string, not an object.
            return json.loads(message.tool_calls[0].function.arguments)
        return None
