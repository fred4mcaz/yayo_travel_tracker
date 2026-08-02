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
import type { Note, StaySummary, TripSummary } from "../types";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];

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
 *  `lanes` counts the pair so the next trip stacks clear of the whole block. */
interface Group extends Span {
  trip: TripSummary;
  lane: number;
  lanes: number;
  bars: StayBar[];
}

/** Row height in px, and how much of one a bar occupies. */
const LANE = 22;

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
        const groups = layoutWeek(week, dated);
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
                        top: group.lane * LANE,
                        height: group.lanes * LANE - 3,
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
                      const b = place(bar);
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
function place(s: Span): { left: string; width: string } {
  const startFrac = s.continuesLeft ? s.start : s.start + 0.5;
  const endFrac = s.continuesRight ? s.start + s.span : s.start + s.span - 0.5;
  return {
    left: `${(startFrac / 7) * 100}%`,
    // Never let a same-day span collapse to nothing to click.
    width: `${Math.max(((endFrac - startFrac) / 7) * 100, 3.5)}%`,
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
 *  overlapping trips so no two blocks ever share a row. */
function layoutWeek(week: Date[], trips: TripSummary[]): Group[] {
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

    groups.push({ ...outer, trip, lane, lanes, bars });
  }
  return groups;
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
    if (rowEnds[i] !== undefined && rowEnds[i] >= start) return false;
  }
  return true;
}

function dayIndex(weekStart: Date, date: Date): number {
  return Math.round((date.getTime() - weekStart.getTime()) / 86_400_000);
}
