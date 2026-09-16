import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { Calendar } from "./Calendar";
import type { LegSummary, StaySummary, TripSummary } from "../types";

function trip(over: Partial<TripSummary> = {}): TripSummary {
  return {
    id: 42,
    label: "Hanoi",
    notes: "",
    start_date: "2026-08-10",
    end_date: "2026-08-14",
    status: "future",
    country_code: "VN",
    country_name: "Vietnam",
    cities: ["Hanoi"],
    stays: [],
    legs: [],
    nights: 4,
    arrival_mode: null,
    unbooked_nights: 0,
    readiness: {
      state: "na",
      permit: null,
      permitted_days: null,
      arrival_card: null,
      onward_ticket: null,
      checked_on: null,
      discrepancy: null,
    },
    ...over,
  };
}

function stay(over: Partial<StaySummary> = {}): StaySummary {
  return {
    id: 1,
    city: "Hanoi",
    hotel_name: "Sofitel Legend",
    check_in: "2026-08-10",
    check_out: "2026-08-12",
    nights: 2,
    confirmed: true,
    ...over,
  };
}

function leg(over: Partial<LegSummary> = {}): LegSummary {
  return {
    id: 1,
    mode: "flight",
    country_code: "VN",
    carrier: "British Airways",
    number: "BA6331",
    from_place: "London Heathrow",
    from_iata: "LHR",
    to_place: "Hanoi",
    to_iata: "HAN",
    depart_at: "2026-08-10T22:05:00",
    arrive_at: "2026-08-11T06:30:00",
    is_connection: false,
    ...over,
  };
}

/** The grid follows the system clock, so pin it to a known month (August 2026)
 *  and target in-month cells, whose day numbers are unique. */
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 7, 15, 12, 0, 0));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function renderCalendar(overrides: Partial<Parameters<typeof Calendar>[0]> = {}) {
  const onSelect = vi.fn();
  const onCreateRange = vi.fn();
  const { container } = render(
    <Calendar
      trips={[]}
      notes={[]}
      onSelect={onSelect}
      onCreateRange={onCreateRange}
      {...overrides}
    />,
  );
  // Only current-month cells; their day numbers don't collide with the
  // greyed-out days spilling in from the neighbouring months.
  const inMonth = Array.from(
    container.querySelectorAll<HTMLElement>(".cal-day:not(.outside)"),
  );
  const cell = (day: number) =>
    inMonth.find((c) => c.querySelector(".cal-daynum")?.textContent === String(day))!;
  return { container, onSelect, onCreateRange, cell };
}

describe("Calendar week layout", () => {
  it("runs Sunday to Saturday", () => {
    const { container } = renderCalendar();
    const heads = Array.from(
      container.querySelectorAll(".cal-weekdays span"),
    ).map((s) => s.textContent);
    expect(heads).toEqual(["S", "M", "T", "W", "T", "F", "S"]);
  });

  it("starts the grid on the Sunday on or before the 1st", () => {
    const { container } = renderCalendar();
    // August 2026 opens on a Saturday, so the grid must lead with Jul 26 --
    // the Sunday before it -- and Aug 1 must land in the last column.
    const days = Array.from(container.querySelectorAll(".cal-day"));
    expect(days[0].querySelector(".cal-daynum")?.textContent).toBe("26");
    expect(days[0].className).toContain("outside");
    expect(days[6].querySelector(".cal-daynum")?.textContent).toBe("1");
    expect(days[6].className).not.toContain("outside");
  });
});

describe("Calendar drag-to-create", () => {
  it("creates a range from a forward drag", () => {
    const { onCreateRange, cell } = renderCalendar();
    fireEvent.mouseDown(cell(5), { button: 0 });
    fireEvent.mouseEnter(cell(8));
    fireEvent.mouseUp(document.body);
    expect(onCreateRange).toHaveBeenCalledWith({
      check_in: "2026-08-05",
      check_out: "2026-08-08",
    });
  });

  it("normalises a backward drag", () => {
    const { onCreateRange, cell } = renderCalendar();
    fireEvent.mouseDown(cell(8), { button: 0 });
    fireEvent.mouseEnter(cell(5));
    fireEvent.mouseUp(document.body);
    expect(onCreateRange).toHaveBeenCalledWith({
      check_in: "2026-08-05",
      check_out: "2026-08-08",
    });
  });

  it("treats a single-cell click as a one-night stay", () => {
    const { onCreateRange, cell } = renderCalendar();
    fireEvent.mouseDown(cell(5), { button: 0 });
    fireEvent.mouseUp(document.body);
    expect(onCreateRange).toHaveBeenCalledWith({
      check_in: "2026-08-05",
      check_out: "2026-08-06",
    });
  });

  it("ignores a non-primary (right) button press", () => {
    const { onCreateRange, cell } = renderCalendar();
    fireEvent.mouseDown(cell(5), { button: 2 });
    fireEvent.mouseUp(document.body);
    expect(onCreateRange).not.toHaveBeenCalled();
  });

  it("highlights the swept cells while dragging", () => {
    const { cell } = renderCalendar();
    fireEvent.mouseDown(cell(5), { button: 0 });
    fireEvent.mouseEnter(cell(7));
    expect(cell(5).className).toContain("selecting");
    expect(cell(6).className).toContain("selecting");
    expect(cell(7).className).toContain("selecting");
    expect(cell(9).className).not.toContain("selecting");
  });

  it("selects a trip from its hotel bar without starting a range", () => {
    const { container, onSelect, onCreateRange } = renderCalendar({
      trips: [trip({ stays: [stay()] })],
    });
    const bar = container.querySelector<HTMLElement>(".cal-bar")!;
    fireEvent.click(bar);
    fireEvent.mouseUp(document.body);
    expect(onSelect).toHaveBeenCalledWith(42);
    expect(onCreateRange).not.toHaveBeenCalled();
  });

  it("selects a trip from the country wrapper too", () => {
    const { container, onSelect } = renderCalendar({ trips: [trip()] });
    fireEvent.click(container.querySelector<HTMLElement>(".cal-country")!);
    fireEvent.mouseUp(document.body);
    expect(onSelect).toHaveBeenCalledWith(42);
  });
});

describe("Calendar hotel bars", () => {
  it("draws one distinctly-coloured bar per hotel, inside the country", () => {
    const { container } = renderCalendar({
      trips: [
        trip({
          stays: [
            stay({ id: 1, city: "Hanoi", hotel_name: "Sofitel Legend" }),
            stay({
              id: 2,
              city: "Hue",
              hotel_name: "Azerai",
              check_in: "2026-08-12",
              check_out: "2026-08-14",
            }),
          ],
        }),
      ],
    });

    const bars = Array.from(container.querySelectorAll<HTMLElement>(".cal-bar"));
    expect(bars.map((b) => b.textContent)).toEqual([
      "Hanoi · Sofitel Legend",
      "Hue · Azerai",
    ]);
    // Each hotel gets its own colour, not the trip's.
    expect(bars[0].style.backgroundColor).not.toBe(bars[1].style.backgroundColor);
    expect(container.querySelectorAll(".cal-country")).toHaveLength(1);
  });

  it("hatches an unconfirmed stay and leaves a confirmed one solid", () => {
    const { container } = renderCalendar({
      trips: [
        trip({
          stays: [
            stay({ id: 1, city: "Hanoi", hotel_name: "Sofitel Legend", confirmed: true }),
            stay({
              id: 2,
              city: "Hue",
              hotel_name: "",
              confirmed: false,
              check_in: "2026-08-12",
              check_out: "2026-08-14",
            }),
          ],
        }),
      ],
    });

    const bars = Array.from(container.querySelectorAll<HTMLElement>(".cal-bar"));
    // The confirmed booking is solid (no hatch class); the unconfirmed one is
    // hatched. Both keep their own colour either way.
    expect(bars[0].classList.contains("unconfirmed")).toBe(false);
    expect(bars[1].classList.contains("unconfirmed")).toBe(true);
    expect(bars[1].style.backgroundColor).not.toBe("");
  });

  it("keeps each hotel bar cleanly inside its country wrapper", () => {
    const { container } = renderCalendar({
      // The trip runs the 10th to the 14th; the hotel covers only the 10th–12th.
      trips: [trip({ stays: [stay()] })],
    });
    const wrapper = container.querySelector<HTMLElement>(".cal-country")!;
    const bar = container.querySelector<HTMLElement>(".cal-bar")!;
    const wl = parseFloat(wrapper.style.left);
    const wr = wl + parseFloat(wrapper.style.width);
    const bl = parseFloat(bar.style.left);
    const br = bl + parseFloat(bar.style.width);
    // Inset from the wrapper's left edge, and safely within its right edge --
    // never touching the outline or spilling past it.
    expect(bl).toBeGreaterThan(wl);
    expect(br).toBeLessThanOrEqual(wr);
    // Still stops short of the far edge: that bare tail is the unbooked stretch,
    // the whole reason the wrapper is drawn.
    expect(parseFloat(bar.style.width)).toBeLessThan(parseFloat(wrapper.style.width));
  });

  it("keeps hotel bars inside a wrapper trimmed for a boundary day", () => {
    // Vietnam ends Aug 12, Thailand begins Aug 12, each fully booked. The trim
    // that opens the hop gap (Phase 3) must not leave a bar poking out.
    const { container } = renderCalendar({
      trips: [
        trip({
          id: 1,
          country_code: "VN",
          country_name: "Vietnam",
          start_date: "2026-08-10",
          end_date: "2026-08-12",
          stays: [stay({ id: 1, check_in: "2026-08-10", check_out: "2026-08-12" })],
        }),
        trip({
          id: 2,
          country_code: "TH",
          country_name: "Thailand",
          start_date: "2026-08-12",
          end_date: "2026-08-14",
          stays: [stay({ id: 2, check_in: "2026-08-12", check_out: "2026-08-14" })],
        }),
      ],
    });
    const wraps = Array.from(
      container.querySelectorAll<HTMLElement>(".cal-country"),
    );
    const bars = Array.from(container.querySelectorAll<HTMLElement>(".cal-bar"));
    // DOM order pairs each wrapper with its own bar: VN, then TH.
    for (let i = 0; i < 2; i++) {
      const wl = parseFloat(wraps[i].style.left);
      const wr = wl + parseFloat(wraps[i].style.width);
      const bl = parseFloat(bars[i].style.left);
      const br = bl + parseFloat(bars[i].style.width);
      expect(bl).toBeGreaterThanOrEqual(wl);
      expect(br).toBeLessThanOrEqual(wr);
    }
  });

  it("still shows the country for a dated trip with no hotel booked", () => {
    const { container } = renderCalendar({ trips: [trip()] });
    const wrapper = container.querySelector<HTMLElement>(".cal-country")!;
    expect(wrapper.textContent).toContain("Vietnam");
    expect(container.querySelectorAll(".cal-bar")).toHaveLength(0);
  });

  it("shares a row for trips that meet on a boundary day", () => {
    // Vietnam ends Aug 12, Thailand begins Aug 12: no night in common, so they
    // sit on one row, touching at the boundary but never overlapping.
    const { container } = renderCalendar({
      trips: [
        trip({
          id: 1,
          country_code: "VN",
          country_name: "Vietnam",
          start_date: "2026-08-10",
          end_date: "2026-08-12",
        }),
        trip({
          id: 2,
          country_code: "TH",
          country_name: "Thailand",
          start_date: "2026-08-12",
          end_date: "2026-08-14",
        }),
      ],
    });
    const wraps = Array.from(
      container.querySelectorAll<HTMLElement>(".cal-country"),
    );
    expect(wraps).toHaveLength(2);
    const [vn, th] = wraps;
    // Same row, and the earlier trip never spills over the later one's start.
    expect(vn.style.top).toBe(th.style.top);
    const vnRight = parseFloat(vn.style.left) + parseFloat(vn.style.width);
    const thLeft = parseFloat(th.style.left);
    expect(vnRight).toBeLessThanOrEqual(thLeft + 0.01);
  });

  it("draws no flight band, nor any old glyph, for a trip with no journeys", () => {
    const { container } = renderCalendar({ trips: [trip()] });
    expect(container.querySelectorAll(".cal-flight")).toHaveLength(0);
    // The old hop connector is gone entirely.
    expect(container.querySelectorAll(".cal-hop")).toHaveLength(0);
  });

  it("stacks two overlapping trips clear of each other", () => {
    const { container } = renderCalendar({
      trips: [
        trip({ id: 1, stays: [stay({ id: 1 })] }),
        trip({
          id: 2,
          country_name: "Thailand",
          country_code: "TH",
          start_date: "2026-08-11",
          end_date: "2026-08-13",
          stays: [
            stay({ id: 2, check_in: "2026-08-11", check_out: "2026-08-13" }),
          ],
        }),
      ],
    });
    const tops = Array.from(
      container.querySelectorAll<HTMLElement>(".cal-country, .cal-bar"),
    ).map((e) => parseInt(e.style.top, 10));
    // Wrapper 1, its bar, wrapper 2, its bar: the second block must start
    // below the first one's bar, never inside it.
    expect(tops).toEqual([...new Set(tops)]);
    expect(tops[2]).toBeGreaterThan(tops[1]);
  });
});

describe("Calendar flight bands", () => {
  const kzTrip = (legs: ReturnType<typeof leg>[], over = {}) =>
    trip({
      id: 5,
      country_code: "KZ",
      country_name: "Kazakhstan",
      start_date: "2026-08-11",
      end_date: "2026-08-14",
      legs,
      ...over,
    });

  it("draws a band showing the departure and arrival times", () => {
    const { container } = renderCalendar({ trips: [kzTrip([leg()])] });
    const band = container.querySelector<HTMLElement>(".cal-flight")!;
    expect(band).not.toBeNull();
    // Departs 22:05 the night before, lands 06:30 the next day (⁺1).
    expect(band.textContent).toContain("10:05p");
    expect(band.textContent).toContain("6:30a⁺1");
    // Full route and carrier live in the hover text, not the cramped band.
    expect(band.title).toContain("British Airways");
    expect(band.title).toContain("London Heathrow");
  });

  it("bridges from the departure side into the block without covering it", () => {
    const { container } = renderCalendar({ trips: [kzTrip([leg()])] });
    const band = container.querySelector<HTMLElement>(".cal-flight")!;
    const wrap = container.querySelector<HTMLElement>(".cal-country")!;
    const bandLeft = parseFloat(band.style.left);
    const bandRight = bandLeft + parseFloat(band.style.width);
    const wrapLeft = parseFloat(wrap.style.left);
    // Reaches back into the approach, left of the country block ...
    expect(bandLeft).toBeLessThan(wrapLeft);
    // ... and docks at the block's edge rather than overlapping it.
    expect(bandRight).toBeLessThanOrEqual(wrapLeft + 0.01);
    // On its own slim strip reserved just above the wrapper.
    expect(parseInt(band.style.top, 10)).toBeLessThan(
      parseInt(wrap.style.top, 10),
    );
  });

  it("shows airport codes only when the band is wide enough", () => {
    const { container } = renderCalendar({
      trips: [
        kzTrip(
          [leg({ depart_at: "2026-08-11T06:00:00", arrive_at: "2026-08-13T06:00:00" })],
          { start_date: "2026-08-13", end_date: "2026-08-16" },
        ),
      ],
    });
    const band = container.querySelector<HTMLElement>(".cal-flight")!;
    expect(band.querySelectorAll(".cal-flight-code").length).toBeGreaterThan(0);
    expect(band.textContent).toContain("LHR");
  });

  it("marks a connecting journey and leaves a direct one unmarked", () => {
    const { container } = renderCalendar({
      trips: [kzTrip([leg({ id: 1, is_connection: true, number: "BA1, KC2" })])],
    });
    expect(container.querySelector(".cal-flight-conn")).not.toBeNull();

    cleanup();
    const { container: c2 } = renderCalendar({
      trips: [kzTrip([leg({ id: 2, is_connection: false })])],
    });
    expect(c2.querySelector(".cal-flight-conn")).toBeNull();
  });

  it("still draws a band when only the departure time is known", () => {
    const { container } = renderCalendar({
      trips: [kzTrip([leg({ arrive_at: null })])],
    });
    expect(container.querySelectorAll(".cal-flight")).toHaveLength(1);
  });

  it("keeps every band above the lodging bars, even one leaving a prior country", () => {
    // London stay ends Aug 13; the inbound flight to Kazakhstan departs London
    // on the 12th (mid-UK-stay) and lands the 13th. The band must sit above the
    // UK block it departs from, not overlap it.
    const { container } = renderCalendar({
      trips: [
        trip({
          id: 1,
          country_code: "GB",
          country_name: "United Kingdom",
          start_date: "2026-08-10",
          end_date: "2026-08-13",
          stays: [
            stay({ id: 1, city: "London", check_in: "2026-08-10", check_out: "2026-08-13" }),
          ],
        }),
        trip({
          id: 2,
          country_code: "KZ",
          country_name: "Kazakhstan",
          start_date: "2026-08-13",
          end_date: "2026-08-16",
          stays: [
            stay({ id: 2, city: "Astana", check_in: "2026-08-13", check_out: "2026-08-16" }),
          ],
          legs: [leg({ id: 5, depart_at: "2026-08-12T14:00:00", arrive_at: "2026-08-13T04:00:00" })],
        }),
      ],
    });
    // Tops are relative to each week's own bar layer, so compare within the
    // week that holds the band.
    const week = [...container.querySelectorAll(".cal-week")].find((w) =>
      w.querySelector(".cal-flight"),
    )!;
    const bands = [...week.querySelectorAll<HTMLElement>(".cal-flight")];
    expect(bands.length).toBeGreaterThan(0);
    const bandTop = Math.min(...bands.map((f) => parseInt(f.style.top, 10)));
    const blockTops = [
      ...week.querySelectorAll<HTMLElement>(".cal-bar, .cal-country"),
    ].map((e) => parseInt(e.style.top, 10));
    for (const t of blockTops) expect(bandTop).toBeLessThan(t);
  });

  it("keeps a same-day flight's band on its departure day, not the day before", () => {
    // Aug 17 2026 is a Monday (index 1 of its Sun–Sat week). A short morning
    // flight must not have its band widened back into Sunday the 16th.
    const { container } = renderCalendar({
      trips: [
        kzTrip(
          [leg({ depart_at: "2026-08-17T03:15:00", arrive_at: "2026-08-17T13:15:00" })],
          { start_date: "2026-08-17", end_date: "2026-08-20" },
        ),
      ],
    });
    const band = container.querySelector<HTMLElement>(".cal-flight")!;
    const leftFrac = (parseFloat(band.style.left) / 100) * 7;
    expect(leftFrac).toBeGreaterThanOrEqual(1 - 1e-6); // Monday, not Sunday
    expect(leftFrac).toBeLessThan(2);
    // ...and the times still show.
    expect(band.textContent).toContain("3:15a");
    expect(band.textContent).toContain("1:15p");
  });
});
