import { useEffect, useState } from "react";

import { TextArea } from "../components/Fields";
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
  toISODate,
  today,
} from "../lib/format";
import { discrepancyMessage, permitSummary, readinessBadge } from "../lib/immigration";
import type {
  ArrivalCardReading,
  TripCountry,
  Discrepancy,
  Leg,
  OnwardTicketReading,
  Passport,
  Readiness,
  Requirement,
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
  | { kind: "leg"; draft: LegDraft; id?: number; country: string };

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
  // Safe to seed from the prop: App keys this panel by trip.id, so switching
  // trips remounts it and the draft can never leak across trips.
  const [notesDraft, setNotesDraft] = useState(trip.notes);

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
      {/* No title block here: the trip list already shows the country, hotel,
          dates, and how soon, so repeating them wastes the panel's top. Only
          the actions remain. */}
      <header className="detail-head">
        <div className="detail-head-actions">
          {onClose && (
            <button className="icon-btn" onClick={onClose} aria-label="Close">
              ✕
            </button>
          )}
        </div>
      </header>

      {error && <p className="alert alert-danger">{error}</p>}

      {country === null && (
        <p className="empty">Nothing recorded yet. Add your first hotel.</p>
      )}

      {/* Every Leg is an arrival, so no legs means nothing records how you got
          to this country. Worth a loud banner for a trip still ahead of you --
          that is a flight you have yet to book or note. Past trips stay quiet:
          old flights often go un-backfilled and a banner there is just noise. */}
      {country &&
        country.legs.length === 0 &&
        (trip.status === "future" || trip.status === "ongoing") && (
          <div className="alert alert-warn missing-travel">
            <div>
              <strong>No travel recorded</strong>
              <span>
                Nothing shows how you get to {country.country_name}. Add the
                flight, train, or drive that brings you in.
              </span>
            </div>
            <button
              className="btn btn-sm"
              onClick={() =>
                setEditing({
                  kind: "leg",
                  country: country.country_code,
                  draft: { ...emptyLeg(), country_code: country.country_code },
                })
              }
            >
              + How you get there
            </button>
          </div>
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

      <ReadinessSection
        readiness={trip.readiness}
        requirements={trip.requirements}
        busy={busy}
        quiet={trip.status === "past"}
        onUpdateStatus={(reqId, status) =>
          void run(() =>
            api.trips.updateRequirement(trip.id, reqId, { status }),
          )
        }
      />

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

      <section>
        <h3>Notes</h3>
        <TextArea
          value={notesDraft}
          onChange={setNotesDraft}
          rows={4}
          placeholder="Anything worth remembering about this trip"
        />
        {notesDraft !== trip.notes && (
          <div className="notes-actions">
            <button
              className="btn btn-sm"
              disabled={busy}
              onClick={() => setNotesDraft(trip.notes)}
            >
              Discard
            </button>
            <button
              className="btn btn-primary btn-sm"
              disabled={busy}
              onClick={() =>
                void run(() => api.trips.update(trip.id, { notes: notesDraft }))
              }
            >
              {busy ? "Saving…" : "Save notes"}
            </button>
          </div>
        )}
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

    </div>
  );
}

/** Whether this trip is ready to cross the border, and what's still owed.
 *  `na` renders nothing -- no country recorded yet, or undated, so there is
 *  nothing to assess (mirrors the missing-travel banner's own quiet state).
 *  `quiet` (a past trip) suppresses everything except a loud discrepancy
 *  banner (decision 3) -- a stale checklist is noise, but a real passport
 *  mismatch on an accepted confirmation stays worth surfacing regardless. */
function ReadinessSection({
  readiness,
  requirements,
  busy,
  quiet,
  onUpdateStatus,
}: {
  readiness: Readiness;
  requirements: Requirement[];
  busy: boolean;
  quiet: boolean;
  onUpdateStatus: (reqId: number, status: string) => void;
}) {
  if (readiness.state === "na") return null;

  if (quiet) {
    if (!readiness.discrepancy) return null;
    return (
      <section className="readiness-section">
        <h3>Immigration readiness</h3>
        <DiscrepancyBanner discrepancy={readiness.discrepancy} />
      </section>
    );
  }

  if (readiness.state === "unknown") {
    return (
      <section className="readiness-section">
        <h3>Immigration readiness</h3>
        {readiness.discrepancy && <DiscrepancyBanner discrepancy={readiness.discrepancy} />}
        <p className="muted">
          Not checked yet for a {readiness.passport ?? "US"} passport.
        </p>
      </section>
    );
  }

  const badge = readinessBadge(readiness);
  const reqByKind = new Map(requirements.map((r) => [r.kind, r]));
  // Visa-free entry can't block you at the border, so the summary box stays
  // green even while checklist items (arrival card, onward ticket) are open —
  // those rows carry their own warnings below.
  const boxClass =
    readiness.permit === "visa_free"
      ? "readiness-ready"
      : (badge?.className ?? "");

  return (
    <section className="readiness-section">
      <h3>Immigration readiness</h3>

      <div className={`readiness-summary ${boxClass}`}>
        <span className="readiness-icon">
          {boxClass === "readiness-ready" ? "✅" : badge?.icon}
        </span>
        <div>
          <strong>{permitSummary(readiness) ?? "Nothing required to enter"}</strong>
          <span className="muted">
            {readiness.is_default_us
              ? "Assuming a US passport (none selected yet)"
              : `${readiness.passport} passport selected`}
          </span>
        </div>
      </div>

      {readiness.discrepancy && <DiscrepancyBanner discrepancy={readiness.discrepancy} />}

      {readiness.alternate_passport_hint && (
        <p className="readiness-hint">{readiness.alternate_passport_hint}</p>
      )}

      {readiness.checklist.map((item) => {
        // The arrival card and onward ticket are automated (decision 9/10):
        // read-only indicators, no dropdown. Everything else stays hand-set.
        if (item.kind === "entry_card") {
          return (
            <ArrivalCardRow
              key={item.kind}
              label={item.label}
              reading={readiness.arrival_card}
            />
          );
        }
        if (item.kind === "onward_ticket") {
          return (
            <OnwardTicketRow
              key={item.kind}
              label={item.label}
              reading={readiness.onward_ticket}
            />
          );
        }
        const req = reqByKind.get(item.kind);
        if (!req) return null;
        return (
          <div className="req-row" key={item.kind}>
            <div className="entry-main">
              <strong>{item.label}</strong>
            </div>
            <select
              value={req.status}
              disabled={busy}
              onChange={(e) => onUpdateStatus(req.id, e.target.value)}
            >
              <option value="todo">To do</option>
              <option value="submitted">Submitted</option>
              <option value="approved">Approved</option>
              <option value="not_required">Not required</option>
            </select>
          </div>
        );
      })}

      <p className="readiness-advisory muted">
        {readiness.checked_on ? `Checked ${formatDate(readiness.checked_on)}. ` : ""}
        {readiness.advisory}
      </p>
    </section>
  );
}

/** Decision 3: loud, not a quiet note -- an accepted immigration email
 *  (Phase 5) named a nationality that differs from the passport currently
 *  selected on this trip. */
function DiscrepancyBanner({ discrepancy }: { discrepancy: Discrepancy }) {
  return (
    <div className="alert alert-danger discrepancy-banner">
      <strong>Passport mismatch</strong>
      <span>{discrepancyMessage(discrepancy)}</span>
    </div>
  );
}

/** Automated arrival-card indicator (decision 9): no dropdown. Driven entirely
 *  by the immigration-email pipeline -- none / received (pending in Review) /
 *  confirmed (an email was accepted). */
function ArrivalCardRow({
  label,
  reading,
}: {
  label: string;
  reading: ArrivalCardReading | null;
}) {
  if (!reading) return null;
  const status = {
    none: { icon: "⚠️", text: "No confirmation email received", cls: "readiness-action" },
    received: {
      icon: "📥",
      text: "Confirmation email received — confirm it in Review",
      cls: "readiness-unknown",
    },
    confirmed: { icon: "✅", text: "Confirmed by email", cls: "readiness-ready" },
  }[reading.state];
  return (
    <div className="req-row">
      <div className="entry-main">
        <strong>{label}</strong>
        {reading.name && <span className="muted">{reading.name}</span>}
      </div>
      <span className={`readiness-status ${status.cls}`}>
        {status.icon} {status.text}
        {reading.state === "confirmed" && reading.reference
          ? ` · ${reading.reference}`
          : ""}
      </span>
    </div>
  );
}

/** Automated onward-ticket indicator (decision 10): no dropdown. Confirmed live
 *  from a booked journey leaving the country near the trip's end. */
function OnwardTicketRow({
  label,
  reading,
}: {
  label: string;
  reading: OnwardTicketReading | null;
}) {
  if (!reading) return null;
  const journey = reading.journey;
  const flight = journey
    ? [journey.carrier, journey.number].filter(Boolean).join(" ")
    : "";
  return (
    <div className="req-row">
      <div className="entry-main">
        <strong>{label}</strong>
        {reading.confirmed && journey && (
          <span className="muted">
            {[flight, journey.to_place].filter(Boolean).join(" → ")}
            {journey.depart_on ? ` (${formatDate(journey.depart_on)})` : ""}
          </span>
        )}
      </div>
      <span
        className={`readiness-status ${
          reading.confirmed ? "readiness-ready" : "readiness-action"
        }`}
      >
        {reading.confirmed ? "✅ Onward ticket confirmed" : "⚠️ No onward ticket confirmed"}
      </span>
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
        {/* No "+ How you get there" here any more: a trip missing its arrival
            journey is called out by the banner at the top of the panel, which
            carries the same shortcut. A second button was redundant. */}
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
