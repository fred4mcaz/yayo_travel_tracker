import { useState } from "react";

import { CountrySelect } from "./CountrySelect";
import { Field, Row, Text, TextArea } from "./Fields";
import { nightsBetween } from "../lib/format";
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

export function emptyStay(checkIn = "", checkOut = ""): StayDraft {
  return {
    country_code: "",
    city: "",
    hotel_name: "",
    check_in: checkIn,
    check_out: checkOut,
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

  const [touchedOut, setTouchedOut] = useState(false);
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
        <Text
          value={draft.city}
          onChange={(v) => set("city", v)}
          placeholder="Hanoi"
          required
        />
      </Field>

      <Row>
        <Field label="Check in">
          <input
            type="date"
            value={draft.check_in}
            onChange={(e) => {
              const check_in = e.target.value;
              // Default checkout to the next day the first time a check-in is
              // picked, since one night is the common minimum.
              if (!touchedOut && check_in && !draft.check_out) {
                const d = new Date(check_in);
                d.setDate(d.getDate() + 1);
                onChange({
                  ...draft,
                  check_in,
                  check_out: d.toISOString().slice(0, 10),
                });
                return;
              }
              set("check_in", check_in);
            }}
            required
          />
        </Field>
        <Field
          label="Check out"
          hint={
            nights === null
              ? undefined
              : nights < 0
                ? "Before check-in"
                : `${nights} night${nights === 1 ? "" : "s"}`
          }
        >
          <input
            type="date"
            value={draft.check_out}
            min={draft.check_in || undefined}
            onChange={(e) => {
              setTouchedOut(true);
              set("check_out", e.target.value);
            }}
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
