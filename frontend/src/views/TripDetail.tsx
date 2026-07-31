import { useState } from "react";

import { Field, TextArea } from "../components/Fields";
import { LegForm, emptyLeg, legDraftToPayload, legToDraft } from "../components/LegForm";
import type { LegDraft } from "../components/LegForm";
import { Sheet } from "../components/Sheet";
import {
  StayForm,
  draftToPayload,
  emptyStay,
  stayToDraft,
} from "../components/StayForm";
import type { StayDraft } from "../components/StayForm";
import { api, ApiError } from "../lib/api";
import { countryName } from "../lib/countries";
import {
  countryFlag,
  formatDate,
  formatDateShort,
  formatRange,
  formatTime,
  formatMoney,
  relativeDays,
} from "../lib/format";
import type { Leg, Passport, Stay, TripDetail as Detail } from "../types";

interface Props {
  trip: Detail;
  passports: Passport[];
  /** Countries from every trip, floated to the top of the country picker. */
  recentCountries: string[];
  /** Set when the trip was just created, so the first stay form opens straight
   *  away rather than landing on an empty panel. */
  openStayOnMount?: boolean;
  onChange: (trip: Detail) => void;
  onDeleted: () => void;
  onClose?: () => void;
}

type Editing =
  | { kind: "none" }
  | { kind: "stay"; draft: StayDraft; id?: number }
  | { kind: "leg"; draft: LegDraft; id?: number }
  | { kind: "trip"; notes: string };

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
  onChange,
  onDeleted,
  onClose,
}: Props) {
  const [editing, setEditing] = useState<Editing>(() =>
    openStayOnMount ? { kind: "stay", draft: emptyStay() } : { kind: "none" },
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const lastStay = trip.stays[trip.stays.length - 1];

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
              : "No dates yet — add a stay or a flight"}
          </p>
        </div>
        <div className="detail-head-actions">
          <button
            className="icon-btn"
            title="Trip notes"
            onClick={() => setEditing({ kind: "trip", notes: trip.notes })}
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

      {/* --- stays --------------------------------------------------- */}
      <section>
        <div className="section-head">
          <h3>Stays</h3>
          <button
            className="btn btn-sm"
            onClick={() =>
              setEditing({
                kind: "stay",
                // Chain onto the end of the trip so consecutive stays are quick.
                draft: emptyStay(lastStay?.check_out ?? "", ""),
              })
            }
          >
            Add stay
          </button>
        </div>

        {trip.stays.length === 0 && (
          <p className="empty">No stays yet.</p>
        )}

        {trip.stays.map((stay) => (
          <StayRow
            key={stay.id}
            stay={stay}
            onEdit={() =>
              setEditing({ kind: "stay", draft: stayToDraft(stay), id: stay.id })
            }
            onDelete={() =>
              run(() => api.trips.removeStay(trip.id, stay.id))
            }
          />
        ))}
      </section>

      {/* --- legs ---------------------------------------------------- */}
      <section>
        <div className="section-head">
          <h3>Travel</h3>
          <button
            className="btn btn-sm"
            onClick={() =>
              setEditing({
                kind: "leg",
                draft: emptyLeg(),
              })
            }
          >
            Add travel
          </button>
        </div>

        {trip.legs.length === 0 && <p className="empty">Nothing booked yet.</p>}

        {trip.legs.map((leg) => (
          <LegRow
            key={leg.id}
            leg={leg}
            onEdit={() =>
              setEditing({ kind: "leg", draft: legToDraft(leg), id: leg.id })
            }
            onDelete={() => run(() => api.trips.removeLeg(trip.id, leg.id))}
          />
        ))}
      </section>

      {/* --- country entries / passports ----------------------------- */}
      {trip.entries.length > 0 && (
        <section>
          <h3>Passport used</h3>
          {trip.entries.map((entry) => (
            <div className="entry-row" key={entry.id}>
              <span className="flag">{countryFlag(entry.country_code)}</span>
              <div className="entry-main">
                <strong>{countryName(entry.country_code)}</strong>
                <span className="muted">
                  entered {formatDateShort(entry.entered_on)}
                </span>
              </div>
              <select
                value={entry.passport_id ?? ""}
                onChange={(e) =>
                  run(() =>
                    api.trips.updateEntry(trip.id, entry.id, {
                      passport_id: e.target.value ? Number(e.target.value) : null,
                    }),
                  )
                }
              >
                <option value="">Not recorded</option>
                {passports.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.nationality}
                    {p.number_last4 ? ` ····${p.number_last4}` : ""}
                  </option>
                ))}
              </select>
            </div>
          ))}
          {passports.length === 0 && (
            <p className="empty">
              Add your passports in Settings to record which one you used.
            </p>
          )}
        </section>
      )}

      {/* --- requirements -------------------------------------------- */}
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

      {/* --- notes --------------------------------------------------- */}
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
                `Delete "${trip.title}" and all its stays and travel? This cannot be undone.`,
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
          title={editing.id ? "Edit stay" : "Add stay"}
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
          />
        </Sheet>
      )}

      {editing.kind === "leg" && (
        <Sheet
          title={editing.id ? "Edit travel" : "Add travel"}
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

      {editing.kind === "trip" && (
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
      <span className="flag">{countryFlag(stay.country_code)}</span>
      <div className="entry-main">
        <strong>{stay.city}</strong>
        <span className="muted">
          {formatRange(stay.check_in, stay.check_out)} · {stay.nights} night
          {stay.nights === 1 ? "" : "s"}
        </span>
        <span className={confirmed ? "muted" : "warn"}>
          {stay.hotel_name || "No hotel yet"}
          {stay.confirmation_code ? ` · ${stay.confirmation_code}` : " · unconfirmed"}
        </span>
        {stay.lat === null && (
          // Say so rather than just quietly omitting the pin, so an unrecognised
          // city name is something you can notice and correct.
          <span className="muted">
            Not on the map — try the nearest larger city
          </span>
        )}
        {stay.cost !== null && (
          <span className="muted">{formatMoney(stay.cost, stay.currency)}</span>
        )}
      </div>
      <div className="item-actions">
        <button className="icon-btn" onClick={onEdit} aria-label="Edit stay">
          ✎
        </button>
        <button className="icon-btn" onClick={onDelete} aria-label="Delete stay">
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
  const from = leg.from_iata || leg.from_place || "?";
  const to = leg.to_iata || leg.to_place || "?";
  return (
    <div className="item-row">
      <span className={`dir dir-${leg.direction}`}>
        {leg.direction === "inbound" ? "↓" : leg.direction === "outbound" ? "↑" : "→"}
      </span>
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
