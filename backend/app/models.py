"""Database schema.

Two conventions worth stating up front, because they shape everything else:

**Times are local wall-clock, stored naive.** A flight departs at 14:05 as
printed on the ticket, and that is what gets stored — not a UTC instant. A
tracker whose job is "what time do I need to be at the airport" should show the
number on the ticket, and converting through UTC only creates opportunities to
be an hour wrong. The IATA code records where that wall-clock reading belongs.

**A trip is one continuous journey.** It owns an ordered set of stays (where you
slept) and legs (how you moved). Gap detection is only possible because both
live under the same parent: a stay in Hanoi with no leg arriving in Hanoi is a
detectable hole.
"""

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


def utcnow() -> datetime:
    """Timestamps for rows themselves (created/updated) are genuine instants."""
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class TravelMode(str, Enum):
    flight = "flight"
    train = "train"
    bus = "bus"
    ferry = "ferry"
    car = "car"


class LegDirection(str, Enum):
    """Relative to the trip, not to any one country."""

    inbound = "inbound"
    internal = "internal"
    outbound = "outbound"


class RequirementKind(str, Enum):
    entry_card = "entry_card"
    visa = "visa"
    eta = "eta"
    insurance = "insurance"
    vaccination = "vaccination"
    onward_ticket = "onward_ticket"
    custom = "custom"


class RequirementStatus(str, Enum):
    todo = "todo"
    submitted = "submitted"
    approved = "approved"
    not_required = "not_required"


class Nationality(str, Enum):
    MX = "MX"
    US = "US"


class PermitType(str, Enum):
    visa_free = "visa_free"
    evisa = "evisa"
    visa_on_arrival = "visa_on_arrival"
    visa = "visa"
    residency = "residency"
    citizen = "citizen"


class NoteCategory(str, Enum):
    appointment = "appointment"
    reminder = "reminder"
    expense = "expense"
    idea = "idea"
    general = "general"


class ExtractionStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"


class Actor(str, Enum):
    """Whether a change was typed by hand or accepted from an email."""

    manual = "manual"
    email = "email"
    system = "system"


# --------------------------------------------------------------------------
# Core travel records
# --------------------------------------------------------------------------


class Trip(SQLModel, table=True):
    __tablename__ = "trip"

    id: Optional[int] = Field(default=None, primary_key=True)
    # Usually empty. A trip is identified by where it goes, derived on read by
    # services.trips.trip_label(); this only holds a name somebody set by hand
    # or that arrived with imported data.
    title: str = ""
    notes: str = ""
    archived: bool = Field(default=False, index=True)

    # Denormalised from the segments so the calendar and list views can sort and
    # range-query without loading every child row. Recomputed by
    # services.trips.refresh_trip_dates() on any segment change.
    start_date: Optional[date] = Field(default=None, index=True)
    end_date: Optional[date] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    stays: list["Stay"] = Relationship(
        back_populates="trip", cascade_delete=True
    )
    legs: list["Leg"] = Relationship(back_populates="trip", cascade_delete=True)
    requirements: list["Requirement"] = Relationship(
        back_populates="trip", cascade_delete=True
    )
    entries: list["CountryEntry"] = Relationship(
        back_populates="trip", cascade_delete=True
    )
    notes_list: list["Note"] = Relationship(back_populates="trip")


class Stay(SQLModel, table=True):
    __tablename__ = "stay"

    id: Optional[int] = Field(default=None, primary_key=True)
    trip_id: int = Field(foreign_key="trip.id", index=True, ondelete="CASCADE")

    country_code: str = Field(index=True, min_length=2, max_length=2)
    city: str
    lat: Optional[float] = None
    lon: Optional[float] = None

    hotel_name: str = ""
    address: str = ""

    check_in: date = Field(index=True)
    check_out: date = Field(index=True)

    confirmation_code: str = ""
    booking_source: str = ""
    cost: Optional[float] = None
    currency: str = ""
    notes: str = ""

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    trip: Optional[Trip] = Relationship(back_populates="stays")

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    @property
    def is_confirmed(self) -> bool:
        """A hotel with no name or no reference is an intention, not a booking."""
        return bool(self.hotel_name.strip()) and bool(self.confirmation_code.strip())


class Leg(SQLModel, table=True):
    __tablename__ = "leg"

    id: Optional[int] = Field(default=None, primary_key=True)
    trip_id: int = Field(foreign_key="trip.id", index=True, ondelete="CASCADE")

    mode: TravelMode = Field(default=TravelMode.flight)
    # Defaults to inbound: only international arrivals are recorded. Other
    # directions remain valid for imported data.
    direction: LegDirection = Field(default=LegDirection.inbound)

    # Which country this journey delivered you into. Set explicitly rather than
    # inferred from an airport code, so "how did I get into Thailand" is a fact
    # rather than a guess.
    country_code: str = Field(default="", index=True)

    carrier: str = ""
    number: str = ""

    from_place: str = ""
    from_iata: str = ""
    depart_at: Optional[datetime] = Field(default=None, index=True)

    to_place: str = ""
    to_iata: str = ""
    arrive_at: Optional[datetime] = Field(default=None, index=True)

    confirmation_code: str = ""
    seat: str = ""
    cost: Optional[float] = None
    currency: str = ""
    notes: str = ""

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    trip: Optional[Trip] = Relationship(back_populates="legs")


class Requirement(SQLModel, table=True):
    """Paperwork owed for a trip: entry card, visa, jab, onward ticket."""

    __tablename__ = "requirement"

    id: Optional[int] = Field(default=None, primary_key=True)
    trip_id: int = Field(foreign_key="trip.id", index=True, ondelete="CASCADE")

    kind: RequirementKind = Field(default=RequirementKind.custom)
    label: str = ""
    status: RequirementStatus = Field(default=RequirementStatus.todo, index=True)
    country_code: str = ""
    due_date: Optional[date] = Field(default=None, index=True)
    reference: str = ""
    note: str = ""

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    trip: Optional[Trip] = Relationship(back_populates="requirements")


# --------------------------------------------------------------------------
# Passports and border crossings
# --------------------------------------------------------------------------


class Passport(SQLModel, table=True):
    """Deliberately stores only the last four digits.

    The app's job is telling you which document to carry and whether it is about
    to expire. A full passport number on an internet-facing box is real exposure
    for no added benefit.
    """

    __tablename__ = "passport"

    id: Optional[int] = Field(default=None, primary_key=True)
    nationality: Nationality
    nickname: str = ""
    number_last4: str = Field(default="", max_length=4)
    issued_on: Optional[date] = None
    expires_on: Optional[date] = Field(default=None, index=True)
    is_default: bool = False

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    entries: list["CountryEntry"] = Relationship(back_populates="passport")


class CountryEntry(SQLModel, table=True):
    """One crossing into one country, on one passport.

    Created automatically when a trip's stays cross a border, then confirmed by
    you. Holding MX and US passports means the choice is real and consequential:
    it decides permitted stay, visa requirement, and which book you must carry.
    """

    __tablename__ = "country_entry"

    id: Optional[int] = Field(default=None, primary_key=True)
    trip_id: int = Field(foreign_key="trip.id", index=True, ondelete="CASCADE")
    passport_id: Optional[int] = Field(
        default=None, foreign_key="passport.id", index=True, ondelete="SET NULL"
    )

    country_code: str = Field(index=True, min_length=2, max_length=2)
    entered_on: date = Field(index=True)
    exited_on: Optional[date] = None
    port_of_entry: str = ""

    permit_type: Optional[PermitType] = None
    permitted_days: Optional[int] = None
    must_exit_by: Optional[date] = Field(default=None, index=True)

    stamp_note: str = ""

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    trip: Optional[Trip] = Relationship(back_populates="entries")
    passport: Optional[Passport] = Relationship(back_populates="entries")


# --------------------------------------------------------------------------
# Personal notes
# --------------------------------------------------------------------------


class Note(SQLModel, table=True):
    """A dated item that may or may not belong to a trip.

    "Dentist appointment in Osaka" is not travel, but it is the reason the trip
    has those dates. Notes with no trip_id stand alone on the calendar.
    """

    __tablename__ = "note"

    id: Optional[int] = Field(default=None, primary_key=True)
    trip_id: Optional[int] = Field(
        default=None, foreign_key="trip.id", index=True, ondelete="SET NULL"
    )

    # Named on_date, not date: a field called `date` shadows the `date` type
    # inside the class body and pydantic cannot resolve the annotation.
    on_date: date = Field(index=True)
    end_date: Optional[date] = None
    title: str
    body: str = ""
    category: NoteCategory = Field(default=NoteCategory.general, index=True)

    city: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None

    remind_at: Optional[datetime] = Field(default=None, index=True)
    done: bool = Field(default=False, index=True)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    trip: Optional[Trip] = Relationship(back_populates="notes_list")


# --------------------------------------------------------------------------
# Email ingest (populated from stage 8)
# --------------------------------------------------------------------------


class EmailMessage(SQLModel, table=True):
    __tablename__ = "email_message"

    id: Optional[int] = Field(default=None, primary_key=True)
    imap_uid: int = Field(index=True)
    message_id: str = Field(default="", index=True, unique=True)

    from_addr: str = ""
    subject: str = ""
    received_at: Optional[datetime] = Field(default=None, index=True)
    snippet: str = ""

    # Result of the cheap local pre-filter, before anything leaves the box.
    looks_like_travel: bool = Field(default=False, index=True)
    processed_at: Optional[datetime] = None

    extractions: list["Extraction"] = Relationship(
        back_populates="email", cascade_delete=True
    )


class Extraction(SQLModel, table=True):
    """A proposal, never a fact.

    Nothing here touches trip data until it is explicitly accepted. That
    boundary is the whole point of the review queue.
    """

    __tablename__ = "extraction"

    id: Optional[int] = Field(default=None, primary_key=True)
    email_message_id: int = Field(
        foreign_key="email_message.id", index=True, ondelete="CASCADE"
    )

    model: str = ""
    payload_json: str = "{}"
    confidence: Optional[float] = None
    status: ExtractionStatus = Field(default=ExtractionStatus.pending, index=True)

    suggested_trip_id: Optional[int] = Field(
        default=None, foreign_key="trip.id", ondelete="SET NULL"
    )
    applied_ids_json: str = "{}"

    created_at: datetime = Field(default_factory=utcnow)
    reviewed_at: Optional[datetime] = None

    email: Optional[EmailMessage] = Relationship(back_populates="extractions")


# --------------------------------------------------------------------------
# Auth (populated from stage 3)
# --------------------------------------------------------------------------


class PasskeyCredential(SQLModel, table=True):
    __tablename__ = "passkey_credential"

    id: Optional[int] = Field(default=None, primary_key=True)
    credential_id: str = Field(index=True, unique=True)
    public_key: str
    sign_count: int = 0
    transports: str = ""
    nickname: str = ""

    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: Optional[datetime] = None


class RecoveryCode(SQLModel, table=True):
    __tablename__ = "recovery_code"

    id: Optional[int] = Field(default=None, primary_key=True)
    code_hash: str = Field(index=True, unique=True)
    used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class Session(SQLModel, table=True):
    __tablename__ = "session"

    id: Optional[int] = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True, unique=True)
    expires_at: datetime = Field(index=True)
    user_agent: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# Bookkeeping
# --------------------------------------------------------------------------


class Setting(SQLModel, table=True):
    """Small mutable key/value state: last IMAP UID, ICS feed token, etc."""

    __tablename__ = "setting"

    key: str = Field(primary_key=True)
    value: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class AuditLog(SQLModel, table=True):
    """Every mutation, so you can always see whether you typed it or an email
    proposed it — and undo either."""

    __tablename__ = "audit_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    entity: str = Field(index=True)
    entity_id: Optional[int] = Field(default=None, index=True)
    action: str
    actor: Actor = Field(default=Actor.manual)
    before_json: Optional[str] = None
    after_json: Optional[str] = None
    at: datetime = Field(default_factory=utcnow, index=True)
