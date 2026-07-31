import { CityInput } from "./CityInput";
import { CountrySelect } from "./CountrySelect";
import { DateField } from "./DateField";
import { Field, Row, Text, TextArea } from "./Fields";
import { addDays, nightsBetween, toISODate, today } from "../lib/format";
import type { Stay } from "../types";

/** Draft mirrors the API shape. Address and cost are still columns -- the Gmail
 *  extractor fills them when a confirmation email has them -- but they are not
 *  worth typing by hand, so they are absent from this form entirely. */
export interface StayDraft {
  country_code: string;
  city: string;
  hotel_name: string;
  check_in: string;
  check_out: string;
  confirmation_code: string;
  notes: string;
}

/** A blank stay, dated today to tomorrow unless it is chaining onto an
 *  earlier stay in the same trip. Most entries are made close to the dates
 *  they describe, so this is usually right or one nudge away. */
export function emptyStay(checkIn = "", checkOut = ""): StayDraft {
  const start = checkIn || toISODate(today());
  return {
    country_code: "",
    city: "",
    hotel_name: "",
    check_in: start,
    check_out: checkOut || addDays(start, 1),
    confirmation_code: "",
    notes: "",
  };
}

export function stayToDraft(stay: Stay): StayDraft {
  return {
    country_code: stay.country_code,
    city: stay.city,
    hotel_name: stay.hotel_name,
    check_in: stay.check_in,
    check_out: stay.check_out,
    confirmation_code: stay.confirmation_code,
    notes: stay.notes,
  };
}

export function draftToPayload(draft: StayDraft): Record<string, unknown> {
  return { ...draft, country_code: draft.country_code.toUpperCase() };
}

export function StayForm({
  draft,
  onChange,
  recentCountries = [],
}: {
  draft: StayDraft;
  onChange: (d: StayDraft) => void;
  recentCountries?: string[];
}) {
  const set = <K extends keyof StayDraft>(key: K, value: StayDraft[K]) =>
    onChange({ ...draft, [key]: value });

  const nights =
    draft.check_in && draft.check_out
      ? nightsBetween(draft.check_in, draft.check_out)
      : null;

  return (
    <>
      <Field label="Country" wide>
        <CountrySelect
          value={draft.country_code}
          onChange={(v) => set("country_code", v)}
          recent={recentCountries}
        />
      </Field>

      <Field label="City" hint="Places the pin on the map" wide>
        <CityInput
          country={draft.country_code}
          value={draft.city}
          onChange={(v) => set("city", v)}
        />
      </Field>

      <Row>
        <Field label="Check in">
          <DateField
            value={draft.check_in}
            onChange={(check_in) => {
              // Never let check-in pass check-out. Carrying the stay length
              // across keeps a booked duration intact while you move the dates.
              const span = Math.max(1, nights ?? 1);
              onChange({
                ...draft,
                check_in,
                check_out:
                  draft.check_out && draft.check_out > check_in
                    ? draft.check_out
                    : addDays(check_in, span),
              });
            }}
            required
          />
        </Field>
        <Field
          label="Check out"
          hint={
            nights === null
              ? undefined
              : `${nights} night${nights === 1 ? "" : "s"}`
          }
        >
          <DateField
            value={draft.check_out}
            min={draft.check_in || undefined}
            onChange={(v) => set("check_out", v)}
            required
          />
        </Field>
      </Row>

      <Field label="Notes" wide>
        <TextArea
          value={draft.notes}
          onChange={(v) => set("notes", v)}
          placeholder="Anything worth remembering about this stop"
        />
      </Field>

      {/* Hotel and reference normally arrive from a confirmation email. Kept
          reachable for the times you want to fill them in yourself, but folded
          away so the common case is four fields and a note. */}
      <details className="more" open={Boolean(draft.hotel_name || draft.confirmation_code)}>
        <summary>Hotel details — usually filled in from your email</summary>
        <Field label="Hotel" wide>
          <Text
            value={draft.hotel_name}
            onChange={(v) => set("hotel_name", v)}
            placeholder="Sofitel Legend Metropole"
          />
        </Field>
        <Field
          label="Confirmation"
          hint="A stay with no reference counts as unconfirmed"
          wide
        >
          <Text
            value={draft.confirmation_code}
            onChange={(v) => set("confirmation_code", v)}
            placeholder="4417-88213"
          />
        </Field>
      </details>
    </>
  );
}
