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
  /** Nights inside this stay with no hotel booked. */
  unbooked_nights: number;
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

export interface TripDetail extends TripSummary {
  /** The country this trip is a stay in. Null until something is recorded. */
  country: TripCountry | null;
  requirements: Requirement[];
  /** Note records. The trip's own memo text is `notes`, inherited above. */
  notes_list: Note[];
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

/** One proposal awaiting review: what the email said, what was read out of it,
 *  and which trip it would join. */
export interface ReviewItem {
  id: number;
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
  booking: ReviewBooking;
  suggestion: { trip_id: number; label: string } | null;
}

export interface AuthStatus {
  authenticated: boolean;
  enrolled: boolean;
  recovery_codes_left: number | null;
  passkey_count: number | null;
}
