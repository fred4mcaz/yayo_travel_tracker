import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { rangeFromDrag } from "../lib/calendarRange";
import type { StayDates } from "../lib/calendarRange";
import {
  formatRange,
  parseDate,
  toISODate,
  today,
} from "../lib/format";
import type { Note, TripSummary } from "../types";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const WEEKDAYS = ["M", "T", "W", "T", "F", "S", "S"];

interface Props {
  trips: TripSummary[];
  notes: Note[];
  onSelect: (id: number) => void;
  /** A drag across day cells finished: start a new trip over these dates. */
  onCreateRange: (dates: StayDates) => void;
}

/** A trip laid out on one week's row: which column it starts at and how wide. */
interface Bar {
  trip: TripSummary;
  start: number;
  span: number;
  continuesLeft: boolean;
  continuesRight: boolean;
  lane: number;
}

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
        const bars = layoutWeek(week, dated);
        const laneCount = bars.reduce((m, b) => Math.max(m, b.lane + 1), 0);
        return (
          <div className="cal-week" key={wi}>
            {/* Day cells grow to fit however many trip bars overlap this week,
                and the bars are overlaid on top of them rather than sitting in
                a strip below — otherwise a bar reads as belonging between two
                weeks instead of to the week above it. */}
            <div
              className="cal-days"
              style={{ minHeight: 64 + laneCount * 24 }}
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
              {bars.map((bar) => {
                // Start at the right half of the arrival day and end at the left
                // half of the departure day: you check in in the afternoon and
                // out in the morning. A bar continuing from an adjacent week
                // keeps its cut edge flush so it still reads as one span.
                const startFrac = bar.continuesLeft ? bar.start : bar.start + 0.5;
                const endFrac = bar.continuesRight
                  ? bar.start + bar.span
                  : bar.start + bar.span - 0.5;
                const leftPct = (startFrac / 7) * 100;
                // Never let a same-day span collapse to nothing to click.
                const widthPct = Math.max(((endFrac - startFrac) / 7) * 100, 3.5);
                return (
                  <button
                    key={`${bar.trip.id}-${bar.start}`}
                    className={`cal-bar status-${bar.trip.status}${
                      bar.continuesLeft ? " cont-l" : ""
                    }${bar.continuesRight ? " cont-r" : ""}`}
                    style={{
                      left: `${leftPct}%`,
                      width: `${widthPct}%`,
                      top: bar.lane * 24,
                      background: barColor(bar.trip.id),
                    }}
                    title={`${bar.trip.country_name} · ${bar.trip.label}\n${formatRange(
                      bar.trip.start_date,
                      bar.trip.end_date,
                    )}`}
                    onClick={() => onSelect(bar.trip.id)}
                  >
                    <span>{bar.trip.label}</span>
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

/** A distinct, stable colour per trip, so overlapping stays are easy to tell
 *  apart at a glance. Golden-angle hue rotation keeps neighbours far apart, and
 *  a fixed saturation/lightness keeps every fill legible under white text in
 *  both themes. */
function barColor(id: number): string {
  const hue = (id * 137.508) % 360;
  return `hsl(${hue.toFixed(1)}, 58%, 42%)`;
}

/** Six weeks starting on the Monday on or before the 1st. */
function buildWeeks(cursor: Date): Date[][] {
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  // getDay() is 0=Sunday; shift so Monday is the first column.
  const offset = (first.getDay() + 6) % 7;
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

/** Clip each trip to this week and stack overlapping ones into lanes. */
function layoutWeek(week: Date[], trips: TripSummary[]): Bar[] {
  const weekStart = week[0];
  const weekEnd = week[6];
  const bars: Bar[] = [];
  const laneEnds: number[] = [];

  const candidates = trips
    .map((trip) => ({
      trip,
      from: parseDate(trip.start_date!),
      to: parseDate(trip.end_date!),
    }))
    .filter((c) => c.to >= weekStart && c.from <= weekEnd)
    // Longest first so the big bars claim the top lanes and read as continuous.
    .sort(
      (a, b) =>
        a.from.getTime() - b.from.getTime() ||
        b.to.getTime() - b.from.getTime() - (a.to.getTime() - a.from.getTime()),
    );

  for (const { trip, from, to } of candidates) {
    const startIdx = Math.max(0, dayIndex(weekStart, from));
    const endIdx = Math.min(6, dayIndex(weekStart, to));
    const span = endIdx - startIdx + 1;
    if (span <= 0) continue;

    let lane = 0;
    while (laneEnds[lane] !== undefined && laneEnds[lane] >= startIdx) lane++;
    laneEnds[lane] = endIdx;

    bars.push({
      trip,
      start: startIdx,
      span,
      continuesLeft: from < weekStart,
      continuesRight: to > weekEnd,
      lane,
    });
  }
  return bars;
}

function dayIndex(weekStart: Date, date: Date): number {
  return Math.round((date.getTime() - weekStart.getTime()) / 86_400_000);
}
