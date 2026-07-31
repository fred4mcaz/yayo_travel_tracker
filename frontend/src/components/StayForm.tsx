import { useState } from "react";

import { Field, Money, Row, Text, TextArea } from "./Fields";
import { nightsBetween } from "../lib/format";
import type { Stay } from "../types";

export interface StayDraft {
  country_code: string;
  city: string;
  hotel_name: string;
  address: string;
  check_in: string;
  check_out: string;
  confirmation_code: string;
  booking_source: string;
  cost: string;
  currency: string;
  notes: string;
}

export function emptyStay(checkIn = "", checkOut = ""): StayDraft {
  return {
    country_code: "",
    city: "",
    hotel_name: "",
    address: "",
    check_in: checkIn,
    check_out: checkOut,
    confirmation_code: "",
    booking_source: "",
    cost: "",
    currency: "",
    notes: "",
  };
}

export function stayToDraft(stay: Stay): StayDraft {
  return {
    country_code: stay.country_code,
    city: stay.city,
    hotel_name: stay.hotel_name,
    address: stay.address,
    check_in: stay.check_in,
    check_out: stay.check_out,
    confirmation_code: stay.confirmation_code,
    booking_source: stay.booking_source,
    cost: stay.cost === null ? "" : String(stay.cost),
    currency: stay.currency,
    notes: stay.notes,
  };
}

export function draftToPayload(draft: StayDraft): Record<string, unknown> {
  return {
    ...draft,
    country_code: draft.country_code.toUpperCase(),
    cost: draft.cost === "" ? null : Number(draft.cost),
  };
}

export function StayForm({
  draft,
  onChange,
}: {
  draft: StayDraft;
  onChange: (d: StayDraft) => void;
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
      <Row>
        <Field label="Country" hint="Two-letter code, e.g. VN">
          <Text
            value={draft.country_code}
            onChange={(v) => set("country_code", v.toUpperCase().slice(0, 2))}
            placeholder="VN"
            maxLength={2}
            required
          />
        </Field>
        <Field label="City">
          <Text
            value={draft.city}
            onChange={(v) => set("city", v)}
            placeholder="Hanoi"
            required
          />
        </Field>
      </Row>

      <Row>
        <Field label="Check in">
          <input
            type="date"
            value={draft.check_in}
            onChange={(e) => {
              const check_in = e.target.value;
              // Default the checkout to the next day the first time a check-in
              // is picked, since a one-night stay is the common minimum.
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

      <Field label="Hotel" wide>
        <Text
          value={draft.hotel_name}
          onChange={(v) => set("hotel_name", v)}
          placeholder="Sofitel Legend Metropole"
        />
      </Field>

      <Field label="Address" wide>
        <Text value={draft.address} onChange={(v) => set("address", v)} />
      </Field>

      <Row>
        <Field
          label="Confirmation"
          hint="Without a reference this counts as unconfirmed"
        >
          <Text
            value={draft.confirmation_code}
            onChange={(v) => set("confirmation_code", v)}
            placeholder="4417-88213"
          />
        </Field>
        <Field label="Booked via">
          <Text
            value={draft.booking_source}
            onChange={(v) => set("booking_source", v)}
            placeholder="Agoda"
          />
        </Field>
      </Row>

      <Field label="Cost">
        <Money
          amount={draft.cost}
          currency={draft.currency}
          onAmount={(v) => set("cost", v)}
          onCurrency={(v) => set("currency", v)}
        />
      </Field>

      <Field label="Notes" wide>
        <TextArea value={draft.notes} onChange={(v) => set("notes", v)} />
      </Field>
    </>
  );
}
