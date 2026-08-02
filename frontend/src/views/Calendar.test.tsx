import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { Calendar } from "./Calendar";
import type { StaySummary, TripSummary } from "../types";

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
    nights: 4,
    unbooked_nights: 0,
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
    expect(bars[0].style.background).not.toBe(bars[1].style.background);
    expect(container.querySelectorAll(".cal-country")).toHaveLength(1);
  });

  it("spans each bar over its own hotel dates, not the trip's", () => {
    const { container } = renderCalendar({
      // The trip runs the 10th to the 14th; the hotel covers only the 10th–12th.
      trips: [trip({ stays: [stay()] })],
    });
    const wrapper = container.querySelector<HTMLElement>(".cal-country")!;
    const bar = container.querySelector<HTMLElement>(".cal-bar")!;
    // Same start, but the bar stops short -- that bare tail is the unbooked
    // stretch, and it is the whole reason the wrapper is still drawn.
    expect(bar.style.left).toBe(wrapper.style.left);
    expect(parseFloat(bar.style.width)).toBeLessThan(parseFloat(wrapper.style.width));
  });

  it("still shows the country for a dated trip with no hotel booked", () => {
    const { container } = renderCalendar({ trips: [trip()] });
    const wrapper = container.querySelector<HTMLElement>(".cal-country")!;
    expect(wrapper.textContent).toContain("Vietnam");
    expect(container.querySelectorAll(".cal-bar")).toHaveLength(0);
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
