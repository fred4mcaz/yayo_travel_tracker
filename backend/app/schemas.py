"""Request and response shapes.

Create/Update payloads are kept separate from the table models so the API never
accepts client-supplied ids, timestamps, or denormalised fields. Update payloads
are all-optional for PATCH semantics.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models import (
    LegDirection,
    Nationality,
    NoteCategory,
    PermitType,
    RequirementKind,
    RequirementStatus,
    TravelMode,
)


class TripStatus(str):
    past = "past"
    ongoing = "ongoing"
    future = "future"
    undated = "undated"


# --------------------------------------------------------------------------
# Trip
# --------------------------------------------------------------------------


class TripCreate(BaseModel):
    # Optional: trips are normally identified by where they go, not by a name
    # somebody had to invent before the trip had any content.
    title: str = Field(default="", max_length=200)
    notes: str = ""


class TripUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = None
    archived: Optional[bool] = None


# --------------------------------------------------------------------------
# Stay
# --------------------------------------------------------------------------


class StayCreate(BaseModel):
    country_code: str = Field(min_length=2, max_length=2)
    city: str = Field(min_length=1, max_length=120)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    hotel_name: str = ""
    address: str = ""
    check_in: date
    check_out: date
    confirmation_code: str = ""
    booking_source: str = ""
    cost: Optional[float] = Field(default=None, ge=0)
    currency: str = Field(default="", max_length=3)
    notes: str = ""

    @field_validator("country_code")
    @classmethod
    def upper_country(cls, v: str) -> str:
        return v.upper()

    @field_validator("check_out")
    @classmethod
    def checkout_after_checkin(cls, v: date, info) -> date:
        check_in = info.data.get("check_in")
        # Same-day is allowed (a day room); earlier is always a mistake.
        if check_in and v < check_in:
            raise ValueError("check_out cannot be before check_in")
        return v


class StayUpdate(BaseModel):
    country_code: Optional[str] = Field(default=None, min_length=2, max_length=2)
    city: Optional[str] = Field(default=None, min_length=1, max_length=120)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    hotel_name: Optional[str] = None
    address: Optional[str] = None
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    confirmation_code: Optional[str] = None
    booking_source: Optional[str] = None
    cost: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, max_length=3)
    notes: Optional[str] = None

    @field_validator("country_code")
    @classmethod
    def upper_country(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v


# --------------------------------------------------------------------------
# Leg
# --------------------------------------------------------------------------


class LegCreate(BaseModel):
    mode: TravelMode = TravelMode.flight
    direction: LegDirection = LegDirection.inbound
    country_code: str = Field(default="", max_length=2)
    carrier: str = ""
    number: str = ""
    from_place: str = ""
    from_iata: str = Field(default="", max_length=4)
    depart_at: Optional[datetime] = None
    to_place: str = ""
    to_iata: str = Field(default="", max_length=4)
    arrive_at: Optional[datetime] = None
    confirmation_code: str = ""
    seat: str = ""
    cost: Optional[float] = Field(default=None, ge=0)
    currency: str = Field(default="", max_length=3)
    notes: str = ""

    @field_validator("from_iata", "to_iata")
    @classmethod
    def upper_iata(cls, v: str) -> str:
        return v.upper()


class LegUpdate(BaseModel):
    mode: Optional[TravelMode] = None
    direction: Optional[LegDirection] = None
    country_code: Optional[str] = Field(default=None, max_length=2)
    carrier: Optional[str] = None
    number: Optional[str] = None
    from_place: Optional[str] = None
    from_iata: Optional[str] = Field(default=None, max_length=4)
    depart_at: Optional[datetime] = None
    to_place: Optional[str] = None
    to_iata: Optional[str] = Field(default=None, max_length=4)
    arrive_at: Optional[datetime] = None
    confirmation_code: Optional[str] = None
    seat: Optional[str] = None
    cost: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, max_length=3)
    notes: Optional[str] = None

    @field_validator("from_iata", "to_iata")
    @classmethod
    def upper_iata(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v


# --------------------------------------------------------------------------
# Requirement
# --------------------------------------------------------------------------


class RequirementCreate(BaseModel):
    kind: RequirementKind = RequirementKind.custom
    label: str = ""
    status: RequirementStatus = RequirementStatus.todo
    country_code: str = Field(default="", max_length=2)
    due_date: Optional[date] = None
    reference: str = ""
    note: str = ""


class RequirementUpdate(BaseModel):
    kind: Optional[RequirementKind] = None
    label: Optional[str] = None
    status: Optional[RequirementStatus] = None
    country_code: Optional[str] = Field(default=None, max_length=2)
    due_date: Optional[date] = None
    reference: Optional[str] = None
    note: Optional[str] = None


# --------------------------------------------------------------------------
# Passport and country entry
# --------------------------------------------------------------------------


def _only_last4(v: Optional[str]) -> Optional[str]:
    """Refuse a full passport number outright.

    No max_length constraint here on purpose: that would fire first and produce
    a generic "at most 4 characters" message. Someone pasting a whole passport
    number deserves to be told the app stores only the last four, and why.
    Silently truncating would be worse still — it would leave them believing
    the full number was saved.
    """
    if v is None:
        return v
    v = v.strip()
    if v and (len(v) > 4 or not v.isalnum()):
        raise ValueError(
            "store only the last 4 characters of the passport number, "
            "never the full number"
        )
    return v


class PassportCreate(BaseModel):
    nationality: Nationality
    nickname: str = ""
    number_last4: str = ""
    issued_on: Optional[date] = None
    expires_on: Optional[date] = None
    is_default: bool = False

    _check_last4 = field_validator("number_last4")(_only_last4)


class PassportUpdate(BaseModel):
    nationality: Optional[Nationality] = None
    nickname: Optional[str] = None
    number_last4: Optional[str] = None
    issued_on: Optional[date] = None
    expires_on: Optional[date] = None
    is_default: Optional[bool] = None

    _check_last4 = field_validator("number_last4")(_only_last4)


class CountryEntryCreate(BaseModel):
    country_code: str = Field(min_length=2, max_length=2)
    passport_id: Optional[int] = None
    entered_on: date
    exited_on: Optional[date] = None
    port_of_entry: str = ""
    permit_type: Optional[PermitType] = None
    permitted_days: Optional[int] = Field(default=None, ge=0, le=3650)
    must_exit_by: Optional[date] = None
    stamp_note: str = ""

    @field_validator("country_code")
    @classmethod
    def upper_country(cls, v: str) -> str:
        return v.upper()


class CountryEntryUpdate(BaseModel):
    country_code: Optional[str] = Field(default=None, min_length=2, max_length=2)
    passport_id: Optional[int] = None
    entered_on: Optional[date] = None
    exited_on: Optional[date] = None
    port_of_entry: Optional[str] = None
    permit_type: Optional[PermitType] = None
    permitted_days: Optional[int] = Field(default=None, ge=0, le=3650)
    must_exit_by: Optional[date] = None
    stamp_note: Optional[str] = None


# --------------------------------------------------------------------------
# Note
# --------------------------------------------------------------------------


class NoteCreate(BaseModel):
    trip_id: Optional[int] = None
    on_date: date
    end_date: Optional[date] = None
    title: str = Field(min_length=1, max_length=200)
    body: str = ""
    category: NoteCategory = NoteCategory.general
    city: str = ""
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    remind_at: Optional[datetime] = None


class NoteUpdate(BaseModel):
    trip_id: Optional[int] = None
    on_date: Optional[date] = None
    end_date: Optional[date] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    body: Optional[str] = None
    category: Optional[NoteCategory] = None
    city: Optional[str] = None
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    remind_at: Optional[datetime] = None
    done: Optional[bool] = None
