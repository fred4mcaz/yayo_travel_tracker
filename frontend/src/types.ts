export type TripStatus = "past" | "ongoing" | "future" | "undated";

export type TravelMode = "flight" | "train" | "bus" | "ferry" | "car";

export type RequirementKind =
  | "entry_card"
  | "visa"
  | "eta"
  | "insurance"
  | "vaccination"
  | "onward_ticket"
  | "custom";

export type RequirementStatus = "todo" | "submitted" | "approved" | "not_required";

/** Whether a Requirement row was typed by hand, materialized from an
 *  EntryPolicy reading, or confirmed from an accepted immigration email. */
export type Actor = "manual" | "email" | "system";

export type NoteCategory =
  | "appointment"
  | "reminder"
  | "expense"
  | "idea"
  | "general";

export type Nationality = "MX" | "US";

export type PermitType =
  | "visa_free"
  | "evisa"
  | "visa_on_arrival"
  | "visa"
  | "residency"
  | "citizen";

export interface Stay {
  id: number;
  trip_id: number;
  country_code: string;
  city: string;
  lat: number | null;
  lon: number | null;
  hotel_name: string;
  address: string;
  check_in: string;
  check_out: string;
  confirmation_code: string;
  booking_source: string;
  cost: number | null;
  currency: string;
  notes: string;
  nights: number;
}

export interface Leg {
  id: number;
  trip_id: number;
  mode: TravelMode;
  /** Which country this journey delivered you into. */
  country_code: string;
  carrier: string;
  number: string;
  from_place: string;
  from_iata: string;
  depart_at: string | null;
  to_place: string;
  to_iata: string;
  arrive_at: string | null;
  confirmation_code: string;
  seat: string;
  cost: number | null;
  currency: string;
  notes: string;
}

export interface Requirement {
  id: number;
  trip_id: number;
  kind: RequirementKind;
  label: string;
  status: RequirementStatus;
  country_code: string;
  due_date: string | null;
  reference: string;
  note: string;
  /** `system` rows are materialised by the entry-policy checklist and get
   *  reconciled automatically; `manual` and `email` rows never are. */
  source: Actor;
  /** Set when a Phase 5 immigration-document extraction read a nationality
   *  off the email -- the raw fact, not a live verdict (see Readiness.discrepancy,
   *  which compares this against the trip's *currently* selected passport). */
  discrepancy_nationality: Nationality | null;
}

export interface CountryEntry {
  id: number;
  trip_id: number;
  passport_id: number | null;
  country_code: string;
  entered_on: string;
  exited_on: string | null;
  port_of_entry: string;
  permit_type: PermitType | null;
  permitted_days: number | null;
  must_exit_by: string | null;
  stamp_note: string;
}

export interface Note {
  id: number;
  trip_id: number | null;
  trip_title?: string | null;
  on_date: string;
  end_date: string | null;
  title: string;
  body: string;
  category: NoteCategory;
  city: string;
  lat: number | null;
  lon: number | null;
  remind_at: string | null;
  done: boolean;
}

/** Just enough of a hotel booking to draw it on the calendar. */
export interface StaySummary {
  id: number;
  city: string;
  hotel_name: string;
  check_in: string;
  check_out: string;
  nights: number;
}

/** ready: settled (or nothing required). action: something still owed.
 *  unknown: dated + has a country, but no policy reading exists yet (an
 *  unconfigured box, not an error). na: no country recorded, or undated --
 *  nothing to assess. */
export type ReadinessState = "ready" | "action" | "unknown" | "na";

/** The automated arrival-card indicator (no dropdown). Driven by the
 *  immigration-email pipeline, never a manual status pick:
 *  - none: no confirmation email has matched this trip.
 *  - received: a confirmation matched and is waiting in the Review queue --
 *    surfaced the moment mail lands, but it only *counts* once accepted.
 *  - confirmed: the entry_card requirement was accepted (approved). */
export type ArrivalCardState = "none" | "received" | "confirmed";

export interface ArrivalCardReading {
  name: string;
  state: ArrivalCardState;
  reference: string;
}

/** The automated onward-ticket indicator (no dropdown). Confirmed live from
 *  booked journeys: a Leg departing the trip's country near its end date.
 *  Present only when the entry policy requires onward proof. */
export interface OnwardTicketReading {
  required: boolean;
  confirmed: boolean;
  journey: {
    carrier: string;
    number: string;
    depart_on: string;
    to_place: string;
  } | null;
}

/** Decision 3's loud flag: an accepted immigration email (Phase 5) named a
 *  nationality that differs from the trip's *currently* selected passport.
 *  A live comparison, not a stored verdict -- flipping the passport to match
 *  clears this on the next read. */
export interface Discrepancy {
  kind: RequirementKind;
  document_nationality: Nationality;
  selected_passport: Nationality;
}

/** What the trip list's compact badge needs -- the full checklist and
 *  advisory text only render on the trip's own detail panel. */
export interface ReadinessSummary {
  state: ReadinessState;
  permit: PermitType | null;
  permitted_days: number | null;
  arrival_card: ArrivalCardReading | null;
  onward_ticket: OnwardTicketReading | null;
  checked_on: string | null;
  discrepancy: Discrepancy | null;
}

export interface ReadinessChecklistItem {
  kind: RequirementKind;
  label: string;
  status: RequirementStatus;
}

/** The full immigration-readiness reading for one trip: what a passport
 *  holder needs to enter this country, and how much of it is done. Advisory
 *  only -- border policy changes without notice, hence `checked_on` rather
 *  than a refresh control. */
export interface Readiness extends ReadinessSummary {
  passport: Nationality | null;
  /** True when no CountryEntry.passport has been chosen and this reading
   *  fell back to the US default (decision 2). */
  is_default_us: boolean;
  checklist: ReadinessChecklistItem[];
  advisory: string;
  /** "A US passport would be visa-free here" -- cache-only, so it only ever
   *  speaks up when the other nationality's policy already happens to be
   *  cached from some other trip. */
  alternate_passport_hint: string | null;
}

export interface TripSummary {
  id: number;
  /** Always derived from the hotels; trips are never named by hand. */
  label: string;
  notes: string;
  start_date: string | null;
  end_date: string | null;
  status: TripStatus;
  country_code: string;
  country_name: string;
  cities: string[];
  /** The hotels booked inside this country stay, earliest check-in first. */
  stays: StaySummary[];
  nights: number;
  /** The mode of the arrival journey (earliest leg), or null if none is
   *  recorded. The calendar labels the gap to the next trip with it. */
  arrival_mode: TravelMode | null;
  /** Nights inside this stay with no hotel booked. */
  unbooked_nights: number;
  readiness: ReadinessSummary;
}

/** One country within a journey: how you got in, on which passport, and every
 *  hotel you stayed in while there. */
export interface UnbookedStretch {
  from: string;
  to: string;
  nights: number;
}

export interface TripCountry {
  country_code: string;
  country_name: string;
  entry: CountryEntry | null;
  passport_id: number | null;
  entered_on: string | null;
  /** When you leave the country. Optional, but without it the stay can only
   *  end at the last checkout, hiding an unbooked tail. */
  leaving_on: string | null;
  starts_on: string | null;
  ends_on: string | null;
  nights: number;
  /** Stretches inside this country with no hotel booked. */
  unbooked: UnbookedStretch[];
  stays: Stay[];
  legs: Leg[];
}

/** Another trip that is plausibly the same stay as this one: same country,
 *  dates touching or a few weeks apart. Offered for merging, never automatic. */
export interface MergeCandidate {
  id: number;
  label: string;
  start_date: string;
  end_date: string;
}

export interface TripDetail extends TripSummary {
  /** The country this trip is a stay in. Null until something is recorded. */
  country: TripCountry | null;
  requirements: Requirement[];
  /** Note records. The trip's own memo text is `notes`, inherited above. */
  notes_list: Note[];
  /** Same-country, near-dated trips this one could be merged with. */
  mergeable: MergeCandidate[];
  readiness: Readiness;
}

export interface Passport {
  id: number;
  nationality: Nationality;
  nickname: string;
  number_last4: string;
  issued_on: string | null;
  expires_on: string | null;
  is_default: boolean;
  countries_entered?: string[];
  entry_count?: number;
}

export type ExtractionStatus = "pending" | "accepted" | "rejected";

export type BookingKind =
  | "hotel"
  | "flight"
  | "train"
  | "bus"
  | "ferry"
  | "car"
  | "other";

/** A booking as the model read it. Every field the model was unsure of is null;
 *  the reviewer fills those in before accepting. */
export interface ReviewBooking {
  kind: BookingKind;
  country_code: string | null;
  country_name: string | null;
  city: string | null;
  start_date: string | null;
  end_date: string | null;
  hotel_name: string | null;
  carrier: string | null;
  confirmation_code: string | null;
}

export type ExtractionKind = "booking" | "immigration";

/** An immigration confirmation match: proposes confirming one requirement
 *  kind on the suggested trip. `nationality`/`reference` are populated only
 *  by Phase 5's manual, model-read extraction -- a Phase 4 local match (a
 *  bare sender+date match, always `entry_card`) leaves both null/empty. */
export interface ImmigrationProposal {
  requirement_kind: RequirementKind;
  nationality: Nationality | null;
  reference: string;
}

/** One proposal awaiting review: what the email said, what was read out of it,
 *  and which trip it would join. `kind` says which of `booking` /
 *  `immigration` is populated -- the other is always null, never omitted. */
export interface ReviewItem {
  id: number;
  kind: ExtractionKind;
  status: ExtractionStatus;
  model: string;
  confidence: number | null;
  created_at: string;
  email: {
    id: number | null;
    from_addr: string;
    subject: string;
    snippet: string;
    received_at: string | null;
  };
  booking: ReviewBooking | null;
  immigration: ImmigrationProposal | null;
  suggestion: { trip_id: number; label: string } | null;
}

/** One stored email from the last few days -- the manual safety net's raw
 *  material. `has_pending` is true once it already has a proposal sitting in
 *  the review queue, whether that came from the automatic filter or a
 *  previous manual extract. */
export interface RecentEmail {
  id: number;
  from_addr: string;
  subject: string;
  snippet: string;
  received_at: string | null;
  looks_like_travel: boolean;
  looks_like_immigration: boolean;
  has_pending: boolean;
}

export interface AuthStatus {
  authenticated: boolean;
  enrolled: boolean;
  recovery_codes_left: number | null;
  passkey_count: number | null;
}
