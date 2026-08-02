import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { rangeFromDrag } from "../lib/calendarRange";
import type { StayDates } from "../lib/calendarRange";
import {
  countryFlag,
  formatRange,
  parseDate,
  toISODate,
  today,
} from "../lib/format";
import type { Note, StaySummary, TravelMode, TripSummary } from "../types";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];

/** The glyph and label for a hop's mode, shown in the gap between two trips. */
const MODE_GLYPH: Record<TravelMode, { glyph: string; label: string }> = {
  flight: { glyph: "✈", label: "Flight" },
  train: { glyph: "🚆", label: "Train" },
  bus: { glyph: "🚌", label: "Bus" },
  ferry: { glyph: "⛴", label: "Ferry" },
  car: { glyph: "🚗", label: "Car" },
};

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
 *  `lane` is the wrapper's own row; the hotel bars sit on the rows below, and
 *  `lanes` counts the pair so the next trip stacks clear of the whole block.
 *  `trimRight`/`trimLeft` shave a wrapper edge that abuts the next trip on the
 *  same row, so a hop marker has room to sit in the gap (see `layoutWeek`). */
interface Group extends Span {
  trip: TripSummary;
  lane: number;
  lanes: number;
  bars: StayBar[];
  trimLeft: number;
  trimRight: number;
}

/** A travel hop drawn in the gap between two consecutive trips that share a row
 *  in one week: how you travelled into `into`, centred at `at` (a fraction of
 *  the week, 0..7) on row `lane`. */
interface Connector {
  at: number;
  lane: number;
  into: TripSummary;
}

/** Row height in px, and how much of one a bar occupies. */
const LANE = 22;

/** How many days apart two same-row trips may sit and still get a hop marker
 *  between them. Beyond a day the gap is real empty time, not a connection. */
const CONNECTOR_MAX_GAP = 1;
/** Day-fraction shaved off each wrapper edge where two trips share a boundary
 *  day, so the wrappers separate and the marker has somewhere to sit. */
const BOUNDARY_TRIM = 0.3;

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
        const { groups, connectors } = layoutWeek(week, dated);
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
                const box = place(group, group.trimLeft, group.trimRight);
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
                        top: group.lane * LANE,
                        // Clear the last hotel bar by a couple of pixels, or
                        // the bar lands exactly on the wrapper's bottom border
                        // and the box loses its floor.
                        height: group.lanes * LANE - 1,
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
                          }${bar.continuesRight ? " cont-r" : ""}`}
                          style={{
                            left: b.left,
                            width: b.width,
                            top: (group.lane + 1 + bar.lane) * LANE,
                            background: barColor(bar.stay.id),
                          }}
                          title={`${stayLabel(bar.stay)}\n${formatRange(
                            bar.stay.check_in,
                            bar.stay.check_out,
                          )} · ${bar.stay.nights} night${
                            bar.stay.nights === 1 ? "" : "s"
                          }`}
                          onClick={() => onSelect(trip.id)}
                        >
                          <span>{stayLabel(bar.stay)}</span>
                        </button>
                      );
                    })}
                  </Fragment>
                );
              })}

              {/* A travel hop sits in the gap between two consecutive trips,
                  showing how you got into the later country. Purely a marker --
                  it never eats a click meant for a wrapper behind it. */}
              {connectors.map((c) => {
                const info = c.into.arrival_mode
                  ? MODE_GLYPH[c.into.arrival_mode]
                  : null;
                const where = c.into.country_name || c.into.label;
                return (
                  <span
                    key={`hop-${c.into.id}`}
                    className={"cal-hop" + (info ? "" : " unknown")}
                    style={{ left: `${(c.at / 7) * 100}%`, top: c.lane * LANE }}
                    title={
                      info
                        ? `${info.label} into ${where}`
                        : `Travel into ${where} not recorded`
                    }
                  >
                    {info ? info.glyph : "→"}
                  </span>
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
  const wrap = bounds(group, group.trimLeft, group.trimRight);
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
  return city || hotel || "Hotel";
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
 *  overlapping trips so no two blocks ever share a row. Two trips that only meet
 *  at a boundary day (one ends the day the next begins) do share a row -- they
 *  have no night in common -- and get a trimmed gap with a travel hop between. */
function layoutWeek(
  week: Date[],
  trips: TripSummary[],
): { groups: Group[]; connectors: Connector[] } {
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

    // The wrapper's own label row, then one row per lane of hotels. The whole
    // block is reserved at once -- reserving only the top row would let the
    // next trip's bars land inside this one's box.
    const lanes = 1 + innerEnds.length;
    const end = outer.start + outer.span - 1;
    let lane = 0;
    while (!rowsFree(rowEnds, lane, lanes, outer.start)) lane++;
    for (let i = 0; i < lanes; i++) rowEnds[lane + i] = end;

    groups.push({ ...outer, trip, lane, lanes, bars, trimLeft: 0, trimRight: 0 });
  }

  return { groups, connectors: connectBoundaries(groups) };
}

/** Mark the gap between consecutive trips that share a row this week with a
 *  travel hop, and shave the wrappers apart where they meet on a boundary day
 *  so the marker has room. The hop shows how you got into the *later* trip. */
function connectBoundaries(groups: Group[]): Connector[] {
  const byLane = new Map<number, Group[]>();
  for (const g of groups) {
    const list = byLane.get(g.lane) ?? [];
    list.push(g);
    byLane.set(g.lane, list);
  }

  const connectors: Connector[] = [];
  for (const list of byLane.values()) {
    list.sort((a, b) => a.start - b.start);
    for (let i = 1; i < list.length; i++) {
      const prev = list[i - 1];
      const next = list[i];
      // A wrapper spilling into an adjacent week has no edge here to anchor to.
      if (prev.continuesRight || next.continuesLeft) continue;
      // Same row means they never overlap, so this is >= 0.
      const gap = next.start - (prev.start + prev.span - 1);
      if (gap > CONNECTOR_MAX_GAP) continue;
      if (gap === 0) {
        prev.trimRight = BOUNDARY_TRIM;
        next.trimLeft = BOUNDARY_TRIM;
      }
      const prevEdge = prev.start + prev.span - 0.5 - prev.trimRight;
      const nextEdge = next.start + 0.5 + next.trimLeft;
      connectors.push({
        at: (prevEdge + nextEdge) / 2,
        lane: next.lane,
        into: next.trip,
      });
    }
  }
  return connectors;
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
