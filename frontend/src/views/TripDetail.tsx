import { useEffect, useState } from "react";

import { Field, TextArea } from "../components/Fields";
import { LegForm, emptyLeg, legDraftToPayload, legToDraft } from "../components/LegForm";
import type { LegDraft } from "../components/LegForm";
import { DateField } from "../components/DateField";
import { Sheet } from "../components/Sheet";
import type { StayDates } from "../lib/calendarRange";
import {
  StayForm,
  draftToPayload,
  emptyStay,
  stayToDraft,
} from "../components/StayForm";
import type { StayDraft } from "../components/StayForm";
import { api, ApiError } from "../lib/api";
import {
  countryFlag,
  formatDate,
  formatDateShort,
  formatRange,
  formatTime,
  formatMoney,
  relativeDays,
  toISODate,
  today,
} from "../lib/format";
import type {
  TripCountry,
  Leg,
  Passport,
  Stay,
  TripDetail as Detail,
} from "../types";

interface Props {
  trip: Detail;
  passports: Passport[];
  recentCountries: string[];
  openStayOnMount?: boolean;
  /** When opening straight onto the stay form (a just-created trip), seed its
   *  dates — e.g. from a calendar drag. Ignored unless openStayOnMount. */
  initialStayDates?: StayDates;
  /** Fired once, when openStayOnMount has actually been acted on, so the caller
   *  can drop the flag. Without it the flag stays set and every later remount
   *  of this panel — switching to Calendar and back — reopens the form. */
  onStayOpened?: () => void;
  onChange: (trip: Detail) => void;
  onDeleted: () => void;
  onClose?: () => void;
}

type Editing =
  | { kind: "none" }
  | { kind: "stay"; draft: StayDraft; id?: number; lockCountry?: boolean }
  | { kind: "leg"; draft: LegDraft; id?: number; country: string }
  | { kind: "notes"; notes: string };

const MODE_LABEL: Record<string, string> = {
  flight: "Flight",
  train: "Train",
  bus: "Bus",
  ferry: "Ferry",
  car: "Car",
};

export function TripDetailPanel({
  trip,
  passports,
  recentCountries,
  openStayOnMount,
  initialStayDates,
  onStayOpened,
  onChange,
  onDeleted,
  onClose,
}: Props) {
  const [editing, setEditing] = useState<Editing>(() =>
    openStayOnMount
      ? {
          kind: "stay",
          draft: emptyStay(initialStayDates?.check_in, initialStayDates?.check_out),
        }
      : { kind: "none" },
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Burn the one-shot as soon as it has been used. `editing` above is state, so
  // the sheet stays open once the flag flips back off; only a later remount is
  // affected, which is exactly what we want to stop.
  useEffect(() => {
    if (openStayOnMount) onStayOpened?.();
    // Mount only: re-firing on a prop change would defeat the point.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function run(fn: () => Promise<Detail | void>) {
    setBusy(true);
    setError(null);
    try {
      const result = await fn();
      if (result) onChange(result);
      setEditing({ kind: "none" });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const country = trip.country;

  return (
    <div className="detail">
      <header className="detail-head">
        <div>
          <h2>{trip.label}</h2>
          <p className="muted">
            {trip.start_date
              ? `${formatRange(trip.start_date, trip.end_date)} · ${relativeDays(
                  trip.status === "past" ? trip.end_date : trip.start_date,
                )}`
              : "No dates yet — add your first country"}
          </p>
        </div>
        <div className="detail-head-actions">
          <button
            className="icon-btn"
            title="Trip notes"
            onClick={() => setEditing({ kind: "notes", notes: trip.notes })}
          >
            ✎
          </button>
          {onClose && (
            <button className="icon-btn" onClick={onClose} aria-label="Close">
              ✕
            </button>
          )}
        </div>
      </header>

      {error && <p className="alert alert-danger">{error}</p>}
      {trip.notes && <p className="trip-memo">{trip.notes}</p>}

      {country === null && (
        <p className="empty">Nothing recorded yet. Add your first hotel.</p>
      )}

      {country && (
        <CountryBlock
          segment={country}
          passports={passports}
          busy={busy}
          onSetLeaving={(iso) => {
            if (!country.entry) return;
            void run(() =>
              api.trips.updateEntry(trip.id, country.entry!.id, {
                exited_on: iso || null,
              }),
            );
          }}
          onSetPassport={(passportId) => {
            if (!country.entry) return;
            void run(() =>
              api.trips.updateEntry(trip.id, country.entry!.id, {
                passport_id: passportId,
              }),
            );
          }}
          onAddHotel={() =>
            setEditing({
              kind: "stay",
              lockCountry: true,
              draft: {
                ...emptyStay(
                  country.stays[country.stays.length - 1]?.check_out ?? "",
                ),
                country_code: country.country_code,
              },
            })
          }
          onEditHotel={(stay) =>
            setEditing({
              kind: "stay",
              draft: stayToDraft(stay),
              id: stay.id,
              // Changing one hotel's country would split the trip across two
              // countries. Only the trip's sole hotel may still move, since
              // that is just correcting which country the trip is in.
              lockCountry: (country?.stays.length ?? 0) > 1,
            })
          }
          onDeleteHotel={(stay) =>
            void run(() => api.trips.removeStay(trip.id, stay.id))
          }
          onAddTravel={() =>
            setEditing({
              kind: "leg",
              country: country.country_code,
              draft: { ...emptyLeg(), country_code: country.country_code },
            })
          }
          onEditTravel={(leg) =>
            setEditing({
              kind: "leg",
              draft: legToDraft(leg),
              id: leg.id,
              country: country.country_code,
            })
          }
          onDeleteTravel={(leg) =>
            void run(() => api.trips.removeLeg(trip.id, leg.id))
          }
        />
      )}

      {trip.mergeable.length > 0 && (
        <section className="merge-suggest">
          <h3>Same trip as another?</h3>
          <p className="muted">
            {trip.mergeable.length === 1 ? "This trip is" : "These trips are"} in{" "}
            {country?.country_name ?? "the same country"} around the same dates.
            Merge to keep one stay with every hotel — any nights with nowhere to
            sleep will then show up.
          </p>
          {trip.mergeable.map((cand) => (
            <div className="merge-row" key={cand.id}>
              <div className="entry-main">
                <strong>{cand.label}</strong>
                <span className="muted">
                  {formatRange(cand.start_date, cand.end_date)}
                </span>
              </div>
              <div className="item-actions">
                <button
                  className="btn btn-sm"
                  disabled={busy}
                  onClick={() => {
                    if (
                      confirm(
                        `Merge "${cand.label}" into "${trip.label}"? ` +
                          `"${cand.label}" will be absorbed and removed.`,
                      )
                    ) {
                      void run(() => api.trips.merge(trip.id, cand.id));
                    }
                  }}
                >
                  Merge in
                </button>
                {/* Kept separate is the persistent opposite of a merge: the
                    backend records the pair so the suggestion does not return
                    on the next load. */}
                <button
                  className="btn btn-sm"
                  disabled={busy}
                  onClick={() =>
                    void run(() => api.trips.keepSeparate(trip.id, cand.id))
                  }
                >
                  Keep separate
                </button>
              </div>
            </div>
          ))}
        </section>
      )}

      {trip.requirements.length > 0 && (
        <section>
          <h3>Paperwork</h3>
          {trip.requirements.map((req) => (
            <div className="req-row" key={req.id}>
              <div className="entry-main">
                <strong>{req.label || req.kind.replace(/_/g, " ")}</strong>
                {req.due_date && (
                  <span className="muted">due {formatDate(req.due_date)}</span>
                )}
              </div>
              <select
                value={req.status}
                onChange={(e) =>
                  run(() =>
                    api.trips.updateRequirement(trip.id, req.id, {
                      status: e.target.value,
                    }),
                  )
                }
              >
                <option value="todo">To do</option>
                <option value="submitted">Submitted</option>
                <option value="approved">Approved</option>
                <option value="not_required">Not required</option>
              </select>
            </div>
          ))}
        </section>
      )}

      {trip.notes_list.length > 0 && (
        <section>
          <h3>Notes</h3>
          {trip.notes_list.map((note) => (
            <div className="note-row" key={note.id}>
              <span className="note-date">{formatDateShort(note.on_date)}</span>
              <div className="entry-main">
                <strong>{note.title}</strong>
                {note.body && <span className="muted">{note.body}</span>}
              </div>
            </div>
          ))}
        </section>
      )}

      <section className="danger-zone">
        <button
          className="btn btn-danger btn-sm"
          disabled={busy}
          onClick={() => {
            if (
              confirm(
                `Delete "${trip.label}" and everything in it? This cannot be undone.`,
              )
            ) {
              run(async () => {
                await api.trips.remove(trip.id);
                onDeleted();
              });
            }
          }}
        >
          Delete trip
        </button>
      </section>

      {/* --- sheets -------------------------------------------------- */}
      {editing.kind === "stay" && (
        <Sheet
          title={
            editing.id
              ? "Edit hotel"
              : editing.lockCountry
                ? "Add hotel"
                : "Add a country"
          }
          onClose={() => setEditing({ kind: "none" })}
          footer={
            <>
              <button className="btn" onClick={() => setEditing({ kind: "none" })}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={busy || !editing.draft.city || !editing.draft.country_code}
                onClick={() =>
                  run(() =>
                    editing.id
                      ? api.trips.updateStay(
                          trip.id,
                          editing.id,
                          draftToPayload(editing.draft),
                        )
                      : api.trips.addStay(trip.id, draftToPayload(editing.draft)),
                  )
                }
              >
                {busy ? "Saving…" : "Save"}
              </button>
            </>
          }
        >
          {error && <p className="alert alert-danger">{error}</p>}
          <StayForm
            draft={editing.draft}
            onChange={(draft) => setEditing({ ...editing, draft })}
            recentCountries={recentCountries}
            lockCountry={editing.lockCountry}
          />
        </Sheet>
      )}

      {editing.kind === "leg" && (
        <Sheet
          title={editing.id ? "Edit travel" : "How you get there"}
          onClose={() => setEditing({ kind: "none" })}
          footer={
            <>
              <button className="btn" onClick={() => setEditing({ kind: "none" })}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() =>
                  run(() =>
                    editing.id
                      ? api.trips.updateLeg(
                          trip.id,
                          editing.id,
                          legDraftToPayload(editing.draft),
                        )
                      : api.trips.addLeg(trip.id, legDraftToPayload(editing.draft)),
                  )
                }
              >
                {busy ? "Saving…" : "Save"}
              </button>
            </>
          }
        >
          {error && <p className="alert alert-danger">{error}</p>}
          <LegForm
            draft={editing.draft}
            onChange={(draft) => setEditing({ ...editing, draft })}
          />
        </Sheet>
      )}

      {editing.kind === "notes" && (
        <Sheet
          title="Trip notes"
          onClose={() => setEditing({ kind: "none" })}
          footer={
            <>
              <button className="btn" onClick={() => setEditing({ kind: "none" })}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() =>
                  run(() => api.trips.update(trip.id, { notes: editing.notes }))
                }
              >
                {busy ? "Saving…" : "Save"}
              </button>
            </>
          }
        >
          <Field label="Notes about this trip" wide>
            <TextArea
              value={editing.notes}
              onChange={(notes) => setEditing({ ...editing, notes })}
              rows={5}
            />
          </Field>
        </Sheet>
      )}
    </div>
  );
}

/** One country: passport at the top, then how you got in, then every hotel. */
function CountryBlock({
  segment,
  passports,
  busy,
  onSetPassport,
  onSetLeaving,
  onAddHotel,
  onEditHotel,
  onDeleteHotel,
  onAddTravel,
  onEditTravel,
  onDeleteTravel,
}: {
  segment: TripCountry;
  passports: Passport[];
  busy: boolean;
  onSetPassport: (id: number | null) => void;
  onSetLeaving: (iso: string) => void;
  onAddHotel: () => void;
  onEditHotel: (stay: Stay) => void;
  onDeleteHotel: (stay: Stay) => void;
  onAddTravel: () => void;
  onEditTravel: (leg: Leg) => void;
  onDeleteTravel: (leg: Leg) => void;
}) {
  const noPassport = segment.passport_id === null;

  // The leaving date drives unbooked-night detection, so start it somewhere
  // useful rather than blank. Today, normally -- but for a trip that has not
  // started yet, today is before you even arrive (and below the field's min),
  // so fall back to the last checkout, which is the natural day to leave.
  const todayIso = toISODate(today());
  const leavingValue =
    segment.leaving_on ??
    (segment.starts_on && todayIso < segment.starts_on
      ? (segment.ends_on ?? segment.starts_on)
      : todayIso);

  return (
    <section className="country-block">
      <div className="country-head">
        <span className="flag flag-lg">{countryFlag(segment.country_code)}</span>
        <div className="country-title">
          <strong>{segment.country_name}</strong>
          <span className="muted">
            {segment.entered_on
              ? `entered ${formatDateShort(segment.entered_on)}`
              : "no arrival date yet"}
            {segment.nights > 0 &&
              ` · ${segment.nights} night${segment.nights === 1 ? "" : "s"}`}
          </span>
        </div>
        <select
          className={noPassport ? "passport-select needs-choice" : "passport-select"}
          value={segment.passport_id ?? ""}
          disabled={busy || passports.length === 0}
          onChange={(e) =>
            onSetPassport(e.target.value ? Number(e.target.value) : null)
          }
        >
          <option value="">
            {passports.length === 0 ? "No passports yet" : "Which passport?"}
          </option>
          {passports.map((p) => (
            <option key={p.id} value={p.id}>
              {p.nationality}
              {p.number_last4 ? ` ····${p.number_last4}` : ""}
            </option>
          ))}
        </select>
      </div>

      {segment.unbooked.length > 0 && (
        <div className="unbooked">
          <strong>
            {segment.unbooked.reduce((n, g) => n + g.nights, 0)} night
            {segment.unbooked.reduce((n, g) => n + g.nights, 0) === 1 ? "" : "s"} with
            no hotel booked
          </strong>
          {segment.unbooked.map((gap) => (
            <span key={gap.from}>
              {formatRange(gap.from, gap.to)} · {gap.nights} night
              {gap.nights === 1 ? "" : "s"}
            </span>
          ))}
          <button className="btn btn-sm" onClick={onAddHotel}>
            Book something for these nights
          </button>
        </div>
      )}

      {segment.legs.map((leg) => (
        <LegRow
          key={leg.id}
          leg={leg}
          onEdit={() => onEditTravel(leg)}
          onDelete={() => onDeleteTravel(leg)}
        />
      ))}

      {segment.stays.map((stay) => (
        <StayRow
          key={stay.id}
          stay={stay}
          onEdit={() => onEditHotel(stay)}
          onDelete={() => onDeleteHotel(stay)}
        />
      ))}

      {segment.stays.length === 0 && (
        <p className="empty">No hotels here yet.</p>
      )}

      <div className="country-actions">
        <button className="btn btn-sm" onClick={onAddHotel}>
          + Add hotel
        </button>
        {segment.legs.length === 0 && (
          <button className="btn btn-sm" onClick={onAddTravel}>
            + How you get there
          </button>
        )}
      </div>

      <div className="leaving-row">
        <label htmlFor={`leaving-${segment.country_code}`}>Leaving Country On</label>
        <DateField
          value={leavingValue}
          min={segment.starts_on ?? undefined}
          onChange={onSetLeaving}
        />
        {!segment.leaving_on && (
          <small>
            Set this to when you actually leave, and any nights you have not
            booked will show up.
          </small>
        )}
      </div>
    </section>
  );
}

function StayRow({
  stay,
  onEdit,
  onDelete,
}: {
  stay: Stay;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const confirmed = stay.hotel_name.trim() && stay.confirmation_code.trim();
  return (
    <div className="item-row">
      <span className="row-icon">🏨</span>
      <div className="entry-main">
        <strong>
          {stay.hotel_name || "No hotel yet"} · {stay.city}
        </strong>
        <span className="muted">
          {formatRange(stay.check_in, stay.check_out)} · {stay.nights} night
          {stay.nights === 1 ? "" : "s"}
        </span>
        <span className={confirmed ? "muted" : "warn"}>
          {stay.confirmation_code || "Not confirmed"}
        </span>
        {stay.cost !== null && (
          <span className="muted">{formatMoney(stay.cost, stay.currency)}</span>
        )}
        {stay.lat === null && (
          <span className="muted">
            Not on the map — try the nearest larger city
          </span>
        )}
      </div>
      <div className="item-actions">
        <button className="icon-btn" onClick={onEdit} aria-label="Edit hotel">
          ✎
        </button>
        <button className="icon-btn" onClick={onDelete} aria-label="Delete hotel">
          🗑
        </button>
      </div>
    </div>
  );
}

function LegRow({
  leg,
  onEdit,
  onDelete,
}: {
  leg: Leg;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const from = leg.from_place || leg.from_iata || "?";
  const to = leg.to_place || leg.to_iata || "?";
  return (
    <div className="item-row">
      <span className="row-icon">✈</span>
      <div className="entry-main">
        <strong>
          {from} → {to}
        </strong>
        <span className="muted">
          {MODE_LABEL[leg.mode] ?? leg.mode}
          {leg.carrier ? ` · ${leg.carrier}` : ""}
          {leg.number ? ` ${leg.number}` : ""}
        </span>
        <span className="muted">
          {leg.depart_at ? formatDateShort(leg.depart_at.slice(0, 10)) : "No date"}
          {leg.depart_at ? ` ${formatTime(leg.depart_at)}` : ""}
          {leg.arrive_at ? ` → ${formatTime(leg.arrive_at)}` : ""}
          {leg.seat ? ` · seat ${leg.seat}` : ""}
        </span>
      </div>
      <div className="item-actions">
        <button className="icon-btn" onClick={onEdit} aria-label="Edit travel">
          ✎
        </button>
        <button className="icon-btn" onClick={onDelete} aria-label="Delete travel">
          🗑
        </button>
      </div>
    </div>
  );
}
