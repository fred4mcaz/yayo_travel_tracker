import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { Calendar } from "./Calendar";
import type { TripSummary } from "../types";

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

  it("selects a trip from its bar without starting a range", () => {
    const trip: TripSummary = {
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
    };
    const { container, onSelect, onCreateRange } = renderCalendar({ trips: [trip] });
    const bar = container.querySelector<HTMLElement>(".cal-bar")!;
    fireEvent.click(bar);
    fireEvent.mouseUp(document.body);
    expect(onSelect).toHaveBeenCalledWith(42);
    expect(onCreateRange).not.toHaveBeenCalled();
  });
});
