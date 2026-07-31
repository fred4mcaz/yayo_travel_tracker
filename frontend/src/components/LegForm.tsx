import { Field, Row, Select, Text, TextArea } from "./Fields";
import type { Leg, LegDirection, TravelMode } from "../types";

/** Cost and currency remain columns for the Gmail extractor to fill, but are
 *  not worth typing by hand, so they are absent from this form. */
export interface LegDraft {
  mode: TravelMode;
  direction: LegDirection;
  carrier: string;
  number: string;
  from_place: string;
  from_iata: string;
  depart_at: string;
  to_place: string;
  to_iata: string;
  arrive_at: string;
  confirmation_code: string;
  seat: string;
  notes: string;
}

/** Always inbound. This form records how you got to the destination; a trip is
 *  one international journey, and movement within it is not tracked as legs. */
export function emptyLeg(): LegDraft {
  return {
    mode: "flight",
    direction: "inbound",
    carrier: "",
    number: "",
    from_place: "",
    from_iata: "",
    depart_at: "",
    to_place: "",
    to_iata: "",
    arrive_at: "",
    confirmation_code: "",
    seat: "",
    notes: "",
  };
}

/** datetime-local wants "YYYY-MM-DDTHH:MM"; the API returns seconds too. */
const trim = (v: string | null) => (v ? v.slice(0, 16) : "");

export function legToDraft(leg: Leg): LegDraft {
  return {
    mode: leg.mode,
    direction: leg.direction,
    carrier: leg.carrier,
    number: leg.number,
    from_place: leg.from_place,
    from_iata: leg.from_iata,
    depart_at: trim(leg.depart_at),
    to_place: leg.to_place,
    to_iata: leg.to_iata,
    arrive_at: trim(leg.arrive_at),
    confirmation_code: leg.confirmation_code,
    seat: leg.seat,
    notes: leg.notes,
  };
}

export function legDraftToPayload(draft: LegDraft): Record<string, unknown> {
  return {
    ...draft,
    from_iata: draft.from_iata.toUpperCase(),
    to_iata: draft.to_iata.toUpperCase(),
    depart_at: draft.depart_at || null,
    arrive_at: draft.arrive_at || null,
  };
}

const MODES: { value: TravelMode; label: string }[] = [
  { value: "flight", label: "Flight" },
  { value: "train", label: "Train" },
  { value: "bus", label: "Bus" },
  { value: "ferry", label: "Ferry" },
  { value: "car", label: "Car" },
];

export function LegForm({
  draft,
  onChange,
}: {
  draft: LegDraft;
  onChange: (d: LegDraft) => void;
}) {
  const set = <K extends keyof LegDraft>(key: K, value: LegDraft[K]) =>
    onChange({ ...draft, [key]: value });

  return (
    <>
      <Field label="How" wide>
        <Select value={draft.mode} onChange={(v) => set("mode", v)} options={MODES} />
      </Field>

      <Row>
        <Field label="From">
          <Text
            value={draft.from_place}
            onChange={(v) => set("from_place", v)}
            placeholder="Bangkok"
          />
        </Field>
        <Field label="To">
          <Text
            value={draft.to_place}
            onChange={(v) => set("to_place", v)}
            placeholder="Hanoi"
          />
        </Field>
      </Row>

      <Row>
        <Field label="Departs" hint="Local time, as printed on the ticket">
          <input
            type="datetime-local"
            value={draft.depart_at}
            onChange={(e) => set("depart_at", e.target.value)}
          />
        </Field>
        <Field label="Arrives" hint="Local time at the destination">
          <input
            type="datetime-local"
            value={draft.arrive_at}
            min={draft.depart_at || undefined}
            onChange={(e) => set("arrive_at", e.target.value)}
          />
        </Field>
      </Row>

      <Field label="Notes" wide>
        <TextArea value={draft.notes} onChange={(v) => set("notes", v)} />
      </Field>

      {/* Everything an e-ticket already tells us. */}
      <details
        className="more"
        open={Boolean(
          draft.carrier || draft.number || draft.confirmation_code || draft.seat,
        )}
      >
        <summary>Ticket details — usually filled in from your email</summary>
        <Row>
          <Field label="Carrier">
            <Text
              value={draft.carrier}
              onChange={(v) => set("carrier", v)}
              placeholder="Vietnam Airlines"
            />
          </Field>
          <Field label="Number">
            <Text
              value={draft.number}
              onChange={(v) => set("number", v)}
              placeholder="VN610"
            />
          </Field>
        </Row>
        {draft.mode === "flight" && (
          <Row>
            <Field label="From code">
              <Text
                value={draft.from_iata}
                onChange={(v) => set("from_iata", v.toUpperCase().slice(0, 4))}
                placeholder="BKK"
                maxLength={4}
              />
            </Field>
            <Field label="To code">
              <Text
                value={draft.to_iata}
                onChange={(v) => set("to_iata", v.toUpperCase().slice(0, 4))}
                placeholder="HAN"
                maxLength={4}
              />
            </Field>
          </Row>
        )}
        <Row>
          <Field label="Confirmation">
            <Text
              value={draft.confirmation_code}
              onChange={(v) => set("confirmation_code", v)}
              placeholder="VN77KD"
            />
          </Field>
          <Field label="Seat">
            <Text value={draft.seat} onChange={(v) => set("seat", v)} placeholder="34K" />
          </Field>
        </Row>
      </details>
    </>
  );
}
