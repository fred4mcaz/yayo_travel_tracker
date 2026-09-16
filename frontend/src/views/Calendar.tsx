import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { rangeFromDrag } from "../lib/calendarRange";
import type { StayDates } from "../lib/calendarRange";
import {
  clockShort,
  countryFlag,
  formatRange,
  isoDatePart,
  parseDate,
  toISODate,
  today,
} from "../lib/format";
import type {
  LegSummary,
  Note,
  StaySummary,
  TravelMode,
  TripSummary,
} from "../types";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];

/** The glyph and label for a journey's mode, shown on its flight band. */
const MODE_GLYPH: Record<TravelMode, { glyph: string; label: string }> = {
  flight: { glyph: "✈", label: "Flight" },
  train: { glyph: "🚆", label: "Train" },
  bus: { glyph: "🚌", label: "Bus" },
  ferry: { glyph: "⛴", label: "Ferry" },
  car: { glyph: "🚗", label: "Car" },
};

/** Marks a connecting journey (more than one segment) on the band. No segment
 *  detail -- just a signal that there is a stop along the way. */
const CONNECTION_GLYPH = "⇢";

interface Props {
  trips: TripSummary[];
  notes: Note[];
  onSelect: (id: number) => void;
  /** A drag across day cells finished: start a new trip over these dates. */
  onCreateRange: (dates: StayDates) => void;
}

/** Something laid out on one week's row: which column it starts at, how wide,
 *  and whether it was cut off by the week's edge. */
interface Span {
  start: number;
  span: number;
  continuesLeft: boolean;
  continuesRight: boolean;
}

/** One hotel booking clipped to this week, on its own row inside the country. */
interface StayBar extends Span {
  stay: StaySummary;
  lane: number;
}

/** One country stay clipped to this week, wrapping the hotels booked inside it.
 *  `lane` is the block's top row. When the trip has an inbound journey in this
 *  week, `flightLanes` is 1 and that top row is a slim strip reserved for the
 *  flight band; the wrapper sits on the next row down, with the hotel bars below
 *  it. `lanes` counts the whole block so the next trip stacks clear of it. */
interface Group extends Span {
  trip: TripSummary;
  lane: number;
  lanes: number;
  flightLanes: number;
  bars: StayBar[];
}

/** One journey drawn as a slim band spanning departure → arrival, clipped to
 *  this week. It sits on its destination group's label row, in the approach
 *  space to the left of the wrapper, docking into the wrapper's left edge.
 *  `left`/`right` are day positions (0..7) already time-of-day aware. */
interface FlightBar {
  leg: LegSummary;
  tripId: number;
  lane: number;
  left: number;
  right: number;
  continuesLeft: boolean;
  continuesRight: boolean;
}

/** Row height in px, and how much of one a bar occupies. */
const LANE = 22;

/** Flight band height in px -- slimmer than a LANE so it reads as movement,
 *  not a night booked somewhere. */
const FLIGHT_H = 15;
/** Smallest flight band, in day-fractions. A short hop is widened to at least
 *  this so its times stay readable; growth is always leftward, and capped at
 *  the start of the departure day so the band never reads as leaving early.
 *  (Both times show regardless -- the band also grows via CSS min-width to fit
 *  its label -- but this keeps a lone-time band a sensible size.) */
const FLIGHT_MIN_SPAN = 1.1;
/** At or above this width (days) the band also has room for airport codes
 *  alongside the two times; below it, just the times. */
const FLIGHT_FULL_SPAN = 1.9;

export function Calendar({ trips, notes, onSelect, onCreateRange }: Props) {
  const now = today();
  const [cursor, setCursor] = useState(
    () => new Date(now.getFullYear(), now.getMonth(), 1),
  );

  // Drag-to-create. The ref is the source of truth the window mouseup reads
  // (its listener is registered once, so it must not close over stale state);
  // the state copy exists only to re-render the highlight as the drag grows.
  const [drag, setDrag] = useState<{ anchor: string; focus: string } | null>(null);
  const dragRef = useRef<{ anchor: string; focus: string } | null>(null);

  const beginDrag = useCallback((iso: string) => {
    dragRef.current = { anchor: iso, focus: iso };
    setDrag(dragRef.current);
  }, []);
  const extendDrag = useCallback((iso: string) => {
    const cur = dragRef.current;
    if (!cur || cur.focus === iso) return;
    dragRef.current = { anchor: cur.anchor, focus: iso };
    setDrag(dragRef.current);
  }, []);

  // One window-level mouseup so a release anywhere — over a trip bar, off the
  // grid — still finalizes the selection rather than leaving it stuck.
  useEffect(() => {
    function finish() {
      const d = dragRef.current;
      dragRef.current = null;
      setDrag(null);
      if (d) onCreateRange(rangeFromDrag(d.anchor, d.focus));
    }
    window.addEventListener("mouseup", finish);
    return () => window.removeEventListener("mouseup", finish);
  }, [onCreateRange]);

  const sel = drag
    ? drag.anchor <= drag.focus
      ? { lo: drag.anchor, hi: drag.focus }
      : { lo: drag.focus, hi: drag.anchor }
    : null;

  const weeks = useMemo(() => buildWeeks(cursor), [cursor]);
  const dated = useMemo(
    () => trips.filter((t) => t.start_date && t.end_date),
    [trips],
  );

  const notesByDate = useMemo(() => {
    const map = new Map<string, Note[]>();
    for (const note of notes) {
      const list = map.get(note.on_date) ?? [];
      list.push(note);
      map.set(note.on_date, list);
    }
    return map;
  }, [notes]);

  const todayIso = toISODate(now);

  return (
    <div className={"calendar" + (drag ? " dragging" : "")}>
      <div className="cal-head">
        <button
          className="icon-btn"
          aria-label="Previous month"
          onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
        >
          ‹
        </button>
        <h2>
          {MONTHS[cursor.getMonth()]} {cursor.getFullYear()}
        </h2>
        <button
          className="icon-btn"
          aria-label="Next month"
          onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
        >
          ›
        </button>
        <button
          className="btn btn-sm"
          onClick={() => setCursor(new Date(now.getFullYear(), now.getMonth(), 1))}
        >
          Today
        </button>
      </div>

      <div className="cal-weekdays">
        {WEEKDAYS.map((d, i) => (
          <span key={i}>{d}</span>
        ))}
      </div>

      <p className="cal-hint">Drag across days to start a trip on those dates.</p>

      {weeks.map((week, wi) => {
        const { groups, flights } = layoutWeek(week, dated);
        const laneCount = groups.reduce((m, g) => Math.max(m, g.lane + g.lanes), 0);
        return (
          <div className="cal-week" key={wi}>
            {/* Day cells grow to fit however many trip bars overlap this week,
                and the bars are overlaid on top of them rather than sitting in
                a strip below — otherwise a bar reads as belonging between two
                weeks instead of to the week above it. */}
            <div
              className="cal-days"
              style={{ minHeight: 64 + laneCount * LANE }}
            >
              {week.map((day) => {
                const iso = toISODate(day);
                const dayNotes = notesByDate.get(iso) ?? [];
                const selecting = sel !== null && iso >= sel.lo && iso <= sel.hi;
                return (
                  <div
                    key={iso}
                    className={
                      "cal-day" +
                      (day.getMonth() !== cursor.getMonth() ? " outside" : "") +
                      (iso === todayIso ? " today" : "") +
                      (selecting ? " selecting" : "")
                    }
                    onMouseDown={(e) => {
                      // Left button only; preventDefault stops the drag from
                      // turning into a text selection of the day numbers.
                      if (e.button !== 0) return;
                      e.preventDefault();
                      beginDrag(iso);
                    }}
                    onMouseEnter={() => extendDrag(iso)}
                  >
                    <span className="cal-daynum">{day.getDate()}</span>
                    {dayNotes.length > 0 && (
                      <span className="cal-note-dot" title={dayNotes.map((n) => n.title).join("\n")}>
                        ●
                      </span>
                    )}
                  </div>
                );
              })}
            </div>

            <div className="cal-bars">
              {groups.map((group) => {
                const { trip } = group;
                const box = place(group);
                return (
                  <Fragment key={trip.id}>
                    {/* The country stay. It wraps its hotels rather than
                        standing in for them: any part of it not covered by a
                        bar is a night in the country with nowhere booked. */}
                    <button
                      className={`cal-country status-${trip.status}${
                        group.continuesLeft ? " cont-l" : ""
                      }${group.continuesRight ? " cont-r" : ""}`}
                      style={{
                        left: box.left,
                        width: box.width,
                        // Sits below the flight strip (if any) reserved on top.
                        top: (group.lane + group.flightLanes) * LANE,
                        // Clear the last hotel bar by a couple of pixels, or
                        // the bar lands exactly on the wrapper's bottom border
                        // and the box loses its floor.
                        height: (group.lanes - group.flightLanes) * LANE - 1,
                        borderColor: countryEdge(trip.id),
                        background: countryFill(trip.id),
                      }}
                      title={`${trip.country_name} · ${trip.label}\n${formatRange(
                        trip.start_date,
                        trip.end_date,
                      )}`}
                      onClick={() => onSelect(trip.id)}
                    >
                      <span>
                        {countryFlag(trip.country_code)}{" "}
                        {trip.country_name || trip.label}
                      </span>
                    </button>

                    {group.bars.map((bar) => {
                      const b = placeBar(group, bar);
                      return (
                        <button
                          key={`${bar.stay.id}-${bar.start}`}
                          className={`cal-bar status-${trip.status}${
                            bar.continuesLeft ? " cont-l" : ""
                          }${bar.continuesRight ? " cont-r" : ""}${
                            bar.stay.confirmed ? "" : " unconfirmed"
                          }`}
                          // backgroundColor, not the `background` shorthand, so
                          // the hatch (a CSS background-image on .unconfirmed) is
                          // not clobbered by this inline rule.
                          style={{
                            left: b.left,
                            width: b.width,
                            top: (group.lane + group.flightLanes + 1 + bar.lane) * LANE,
                            backgroundColor: barColor(bar.stay.id),
                          }}
                          title={`${stayLabel(bar.stay)}\n${formatRange(
                            bar.stay.check_in,
                            bar.stay.check_out,
                          )} · ${bar.stay.nights} night${
                            bar.stay.nights === 1 ? "" : "s"
                          }${bar.stay.confirmed ? "" : " · not yet confirmed"}`}
                          onClick={() => onSelect(trip.id)}
                        >
                          <span>{stayLabel(bar.stay)}</span>
                        </button>
                      );
                    })}
                  </Fragment>
                );
              })}

              {/* A flight band spans one journey's departure → arrival, sitting
                  in the approach space to the left of its country wrapper. Both
                  times always show (the band grows to fit via min-width rather
                  than dropping them); airport codes are added only when wide.
                  Full detail on hover. */}
              {flights.map((f) => {
                const span = f.right - f.left;
                const info = MODE_GLYPH[f.leg.mode] ?? MODE_GLYPH.flight;
                const wide = span >= FLIGHT_FULL_SPAN;
                const dep = clockShort(f.leg.depart_at);
                const arr = clockShort(f.leg.arrive_at);
                const plus = dayOffset(f.leg.depart_at, f.leg.arrive_at);
                const arrLabel = arr ? `${arr}${plus > 0 ? `⁺${plus}` : ""}` : "";
                return (
                  <button
                    key={`flight-${f.leg.id}`}
                    className={`cal-flight${f.continuesLeft ? " cont-l" : ""}${
                      f.continuesRight ? " cont-r" : ""
                    }`}
                    style={{
                      left: `${(f.left / 7) * 100}%`,
                      width: `${(span / 7) * 100}%`,
                      top: f.lane * LANE + (LANE - FLIGHT_H) / 2,
                      height: FLIGHT_H,
                    }}
                    title={flightTooltip(f.leg, info.label)}
                    onClick={() => onSelect(f.tripId)}
                  >
                    <span className="cal-flight-inner">
                      {dep && <span className="cal-flight-t">{dep}</span>}
                      {wide && f.leg.from_iata && (
                        <span className="cal-flight-code">{f.leg.from_iata}</span>
                      )}
                      <span className="cal-flight-g" aria-hidden="true">
                        {info.glyph}
                        {f.leg.is_connection && (
                          <sup className="cal-flight-conn">{CONNECTION_GLYPH}</sup>
                        )}
                      </span>
                      {wide && f.leg.to_iata && (
                        <span className="cal-flight-code">{f.leg.to_iata}</span>
                      )}
                      {arrLabel && <span className="cal-flight-t">{arrLabel}</span>}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}

      {dated.length === 0 && (
        <p className="empty">
          Nothing dated yet. Trips appear here once they have a stay or a flight.
        </p>
      )}
    </div>
  );
}

/** Where a span sits across the week, as CSS percentages.
 *
 *  Starts at the right half of the arrival day and ends at the left half of the
 *  departure day: you check in in the afternoon and out in the morning. A span
 *  continuing from an adjacent week keeps its cut edge flush so it still reads
 *  as one run. */
function bounds(
  s: Span,
  trimLeft = 0,
  trimRight = 0,
): { start: number; end: number } {
  return {
    start: (s.continuesLeft ? s.start : s.start + 0.5) + trimLeft,
    end: (s.continuesRight ? s.start + s.span : s.start + s.span - 0.5) - trimRight,
  };
}

/** Smallest span worth drawing, in day-fractions -- a same-day booking must
 *  still be wide enough to click. ~3.5% of a week. */
const MIN_SPAN = 0.25;
/** How far a hotel bar sits inside its wrapper's real edges, so it never
 *  touches the outline or pokes past a boundary-day trim. In day-fractions --
 *  a hair of breathing room, not a wide margin. */
const BAR_INSET = 0.05;

function place(
  s: Span,
  trimLeft = 0,
  trimRight = 0,
): { left: string; width: string } {
  const { start, end } = bounds(s, trimLeft, trimRight);
  return {
    left: `${(start / 7) * 100}%`,
    // Never let a same-day span collapse to nothing to click.
    width: `${Math.max(((end - start) / 7) * 100, (MIN_SPAN / 7) * 100)}%`,
  };
}

/** A hotel bar, clamped to sit cleanly inside its country wrapper. It never
 *  reaches the outline or spills past a trimmed edge; a run continuing into the
 *  next week stays flush there so it still reads as one bar across the seam. */
function placeBar(group: Group, bar: StayBar): { left: string; width: string } {
  const wrap = bounds(group);
  const lo = wrap.start + (group.continuesLeft ? 0 : BAR_INSET);
  const hi = wrap.end - (group.continuesRight ? 0 : BAR_INSET);
  const b = bounds(bar);
  let start = Math.min(Math.max(b.start, lo), hi);
  let end = Math.min(Math.max(b.end, lo), hi);
  if (end - start < MIN_SPAN) {
    // Grow to the clickable minimum without breaching the wrapper: rightward
    // first, then left if the right edge is what's tight.
    end = Math.min(hi, start + MIN_SPAN);
    start = Math.max(lo, end - MIN_SPAN);
  }
  return {
    left: `${(start / 7) * 100}%`,
    width: `${((end - start) / 7) * 100}%`,
  };
}

/** "Hanoi · Sofitel Legend", or just the city while the name is still unknown
 *  (a stay created by a calendar drag has neither yet). */
function stayLabel(stay: StaySummary): string {
  const hotel = stay.hotel_name.trim();
  const city = stay.city.trim();
  if (city && hotel) return `${city} · ${hotel}`;
  return city || hotel || "Lodging";
}

/** A distinct, stable hue per id. Golden-angle rotation keeps neighbours far
 *  apart, so two hotels booked back to back never come out the same colour. */
function hue(id: number): string {
  return (((id * 137.508) % 360) + 360).toFixed(1);
}

/** One hotel booking's fill. Fixed saturation/lightness keeps every one of them
 *  legible under white text in both themes. */
function barColor(id: number): string {
  return `hsl(${hue(id)}, 58%, 42%)`;
}

/** The country wrapper: its own hue, but drawn as an outline over a wash so the
 *  hotel bars inside stay the thing you actually read. */
function countryEdge(id: number): string {
  return `hsl(${hue(id)}, 48%, 46%)`;
}
function countryFill(id: number): string {
  return `hsla(${hue(id)}, 48%, 46%, 0.13)`;
}

/** Six weeks starting on the Sunday on or before the 1st. */
function buildWeeks(cursor: Date): Date[][] {
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  // getDay() is already 0=Sunday, which is the first column.
  const offset = first.getDay();
  const start = new Date(first);
  start.setDate(first.getDate() - offset);

  const weeks: Date[][] = [];
  for (let w = 0; w < 6; w++) {
    const week: Date[] = [];
    for (let d = 0; d < 7; d++) {
      const day = new Date(start);
      day.setDate(start.getDate() + w * 7 + d);
      week.push(day);
    }
    weeks.push(week);
  }
  return weeks;
}

/** Clip each country stay to this week, lay its hotels out inside it, and stack
 *  overlapping trips so no two blocks ever share a row. Then lay each trip's
 *  journeys out as flight bands sitting in the approach space to the left of
 *  their wrapper. */
function layoutWeek(
  week: Date[],
  trips: TripSummary[],
): { groups: Group[]; flights: FlightBar[] } {
  const weekStart = week[0];
  const weekEnd = week[6];
  const groups: Group[] = [];
  // Rightmost column each row is occupied through, so a later trip can tell
  // whether it fits above or has to drop below.
  const rowEnds: number[] = [];

  const candidates = trips
    .map((trip) => ({
      trip,
      from: parseDate(trip.start_date!),
      to: parseDate(trip.end_date!),
    }))
    .filter((c) => c.to >= weekStart && c.from <= weekEnd)
    // Longest first so the big blocks claim the top rows and read as continuous.
    .sort(
      (a, b) =>
        a.from.getTime() - b.from.getTime() ||
        b.to.getTime() - b.from.getTime() - (a.to.getTime() - a.from.getTime()),
    );

  const flights: FlightBar[] = [];

  for (const { trip, from, to } of candidates) {
    const outer = clip(weekStart, weekEnd, from, to);
    if (!outer) continue;

    // Hotels inside this country stay, each on its own row. Two bookings that
    // do not overlap share a row, so a five-hotel trip is not five rows tall.
    const bars: StayBar[] = [];
    const innerEnds: number[] = [];
    for (const stay of trip.stays ?? []) {
      const inner = clip(
        weekStart,
        weekEnd,
        parseDate(stay.check_in),
        parseDate(stay.check_out),
      );
      if (!inner) continue;
      let lane = 0;
      while (innerEnds[lane] !== undefined && innerEnds[lane] >= inner.start) lane++;
      innerEnds[lane] = inner.start + inner.span - 1;
      bars.push({ ...inner, stay, lane });
    }

    // Journeys into this country that touch this week, each becoming a band on a
    // slim strip reserved above the wrapper. Reserving that strip is what keeps
    // the band readable (times and all) even when the trip butts right up
    // against the previous one -- the band never has to fight for the seam.
    const wrapLeft = bounds(outer).start;
    const geoms = (trip.legs ?? [])
      .map((leg) => flightGeom(weekStart, leg, wrapLeft))
      .filter((g): g is FlightGeom => g !== null);
    const flightLanes = geoms.length > 0 ? 1 : 0;

    // A slim top strip (if any flights), the wrapper's own row, then one row per
    // lane of hotels. The whole block is reserved at once -- reserving only the
    // top row would let the next trip's bars land inside this one's box. The
    // reservation reaches back to the earliest band edge so nothing lands where
    // a band pokes into the approach before the country begins.
    const lanes = flightLanes + 1 + innerEnds.length;
    const end = outer.start + outer.span - 1;
    const leftExtent = geoms.reduce((m, g) => Math.min(m, g.left), outer.start);
    let lane = 0;
    while (!rowsFree(rowEnds, lane, lanes, leftExtent)) lane++;
    for (let i = 0; i < lanes; i++) rowEnds[lane + i] = end;

    groups.push({ ...outer, trip, lane, lanes, flightLanes, bars });
    for (const g of geoms) flights.push({ ...g, tripId: trip.id, lane });
  }

  return { groups, flights };
}

/** The geometry (without its row) of one leg's band in this week, or null if it
 *  carries no time or does not touch the week. */
interface FlightGeom {
  leg: LegSummary;
  left: number;
  right: number;
  continuesLeft: boolean;
  continuesRight: boolean;
}

/** Place one leg's band across this week. The arrival end docks into the
 *  wrapper's left edge so the band reads as arriving into the country; a short
 *  hop is widened leftward to stay readable, never rightward into the block. */
function flightGeom(
  weekStart: Date,
  leg: LegSummary,
  wrapLeft: number,
): FlightGeom | null {
  if (!leg.depart_at && !leg.arrive_at) return null;
  const depPos = leg.depart_at ? instantPos(weekStart, leg.depart_at) : null;
  const arrPos = leg.arrive_at ? instantPos(weekStart, leg.arrive_at) : null;
  const startPos = depPos ?? arrPos!; // departure instant (fallback: arrival)
  const arrivePos = arrPos ?? depPos!; // arrival instant (fallback: departure)
  // Dock the arrival into the wrapper's left edge, never past it into the block.
  const endPos = Math.min(arrivePos, wrapLeft);
  if (endPos < 0 || startPos > 7) return null;

  const continuesLeft = startPos < 0;
  const continuesRight = arrivePos > 7;
  let left = Math.max(0, startPos);
  const right = Math.min(7, Math.max(endPos, left));
  // Widen a sliver leftward for readability (the strip above is ours, so there
  // is room), but never earlier than the start of the departure day -- crossing
  // into the day before would make the band read as leaving a day too early.
  if (right - left < FLIGHT_MIN_SPAN) {
    const departFloor = Math.max(0, Math.floor(startPos));
    left = Math.max(departFloor, right - FLIGHT_MIN_SPAN);
  }

  return { leg, left, right, continuesLeft, continuesRight };
}

/** A datetime's position across the week, in days from `weekStart`, carrying
 *  its time of day (so a 10pm departure sits near the right of its column). */
function instantPos(weekStart: Date, iso: string): number {
  const day = parseDate(isoDatePart(iso));
  const dayIdx = Math.round((day.getTime() - weekStart.getTime()) / 86_400_000);
  const timePart = iso.replace(/(Z|[+-]\d{2}:\d{2})$/, "").split("T")[1] ?? "00:00";
  const [hh, mm] = timePart.split(":").map(Number);
  const frac = ((Number.isNaN(hh) ? 0 : hh) + (mm || 0) / 60) / 24;
  return dayIdx + frac;
}

/** Whole calendar days from departure to arrival, for the "⁺1" next-day mark.
 *  Zero when either time is missing or they land the same day. */
function dayOffset(depart: string | null, arrive: string | null): number {
  if (!depart || !arrive) return 0;
  const d0 = parseDate(isoDatePart(depart)).getTime();
  const d1 = parseDate(isoDatePart(arrive)).getTime();
  return Math.max(0, Math.round((d1 - d0) / 86_400_000));
}

/** The hover text for a flight band: mode (and whether it connects), carrier
 *  and segment numbers, then the timed route. */
function flightTooltip(leg: LegSummary, modeLabel: string): string {
  const dep = clockShort(leg.depart_at);
  const arr = clockShort(leg.arrive_at);
  const plus = dayOffset(leg.depart_at, leg.arrive_at);
  const arrLabel = arr ? `${arr}${plus > 0 ? `⁺${plus}` : ""}` : "";
  const from = leg.from_place || leg.from_iata;
  const to = leg.to_place || leg.to_iata;
  const route = [
    from && `${from}${dep ? ` ${dep}` : ""}`,
    to && `${to}${arrLabel ? ` ${arrLabel}` : ""}`,
  ]
    .filter(Boolean)
    .join(" → ");
  const head = [
    modeLabel,
    leg.is_connection ? "(connection)" : "",
    leg.carrier,
    leg.number,
  ]
    .filter(Boolean)
    .join(" · ");
  return [head, route].filter(Boolean).join("\n");
}

/** Where `from`–`to` falls inside this week, or null if it misses it entirely. */
function clip(weekStart: Date, weekEnd: Date, from: Date, to: Date): Span | null {
  if (to < weekStart || from > weekEnd) return null;
  const start = Math.max(0, dayIndex(weekStart, from));
  const end = Math.min(6, dayIndex(weekStart, to));
  if (end < start) return null;
  return {
    start,
    span: end - start + 1,
    continuesLeft: from < weekStart,
    continuesRight: to > weekEnd,
  };
}

/** Are `count` consecutive rows from `top` clear at column `start`? */
function rowsFree(
  rowEnds: number[],
  top: number,
  count: number,
  start: number,
): boolean {
  for (let i = top; i < top + count; i++) {
    // `> start`, not `>=`: a trip may share a row with one that ends the very
    // day it begins -- they have no night in common, only a boundary day.
    if (rowEnds[i] !== undefined && rowEnds[i] > start) return false;
  }
  return true;
}

function dayIndex(weekStart: Date, date: Date): number {
  return Math.round((date.getTime() - weekStart.getTime()) / 86_400_000);
}
