import { useMemo, useState } from "react";

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

export function Calendar({ trips, notes, onSelect }: Props) {
  const now = today();
  const [cursor, setCursor] = useState(
    () => new Date(now.getFullYear(), now.getMonth(), 1),
  );

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
    <div className="calendar">
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
              style={{ minHeight: 32 + laneCount * 22 }}
            >
              {week.map((day) => {
                const iso = toISODate(day);
                const dayNotes = notesByDate.get(iso) ?? [];
                return (
                  <div
                    key={iso}
                    className={
                      "cal-day" +
                      (day.getMonth() !== cursor.getMonth() ? " outside" : "") +
                      (iso === todayIso ? " today" : "")
                    }
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
              {bars.map((bar) => (
                <button
                  key={`${bar.trip.id}-${bar.start}`}
                  className={`cal-bar status-${bar.trip.status}${
                    bar.continuesLeft ? " cont-l" : ""
                  }${bar.continuesRight ? " cont-r" : ""}`}
                  style={{
                    left: `${(bar.start / 7) * 100}%`,
                    width: `${(bar.span / 7) * 100}%`,
                    top: bar.lane * 22,
                  }}
                  title={`${bar.trip.title}\n${formatRange(
                    bar.trip.start_date,
                    bar.trip.end_date,
                  )}`}
                  onClick={() => onSelect(bar.trip.id)}
                >
                  <span>{bar.trip.title}</span>
                </button>
              ))}
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
