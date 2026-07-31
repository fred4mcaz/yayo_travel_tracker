export type TripStatus = "past" | "ongoing" | "future" | "undated";

export type TravelMode = "flight" | "train" | "bus" | "ferry" | "car";
export type LegDirection = "inbound" | "internal" | "outbound";

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
  direction: LegDirection;
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

export interface TripSummary {
  id: number;
  title: string;
  notes: string;
  archived: boolean;
  start_date: string | null;
  end_date: string | null;
  status: TripStatus;
  countries: string[];
  cities: string[];
  nights: number;
}

export interface TripDetail extends TripSummary {
  stays: Stay[];
  legs: Leg[];
  requirements: Requirement[];
  entries: CountryEntry[];
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

export interface AuthStatus {
  authenticated: boolean;
  enrolled: boolean;
  recovery_codes_left: number | null;
  passkey_count: number | null;
}
