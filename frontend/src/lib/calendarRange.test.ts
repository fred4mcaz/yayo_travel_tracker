import { describe, expect, it } from "vitest";

import { rangeFromDrag } from "./calendarRange";

describe("rangeFromDrag", () => {
  it("maps a forward drag straight to check_in..check_out", () => {
    expect(rangeFromDrag("2026-08-05", "2026-08-08")).toEqual({
      check_in: "2026-08-05",
      check_out: "2026-08-08",
    });
  });

  it("normalises a backward drag to the same range", () => {
    expect(rangeFromDrag("2026-08-08", "2026-08-05")).toEqual({
      check_in: "2026-08-05",
      check_out: "2026-08-08",
    });
  });

  it("turns a single-cell selection into a one-night stay", () => {
    expect(rangeFromDrag("2026-08-05", "2026-08-05")).toEqual({
      check_in: "2026-08-05",
      check_out: "2026-08-06",
    });
  });

  it("rolls a single-day selection across a month boundary", () => {
    expect(rangeFromDrag("2026-08-31", "2026-08-31")).toEqual({
      check_in: "2026-08-31",
      check_out: "2026-09-01",
    });
  });

  it("spans across months on a real drag", () => {
    expect(rangeFromDrag("2026-08-30", "2026-09-02")).toEqual({
      check_in: "2026-08-30",
      check_out: "2026-09-02",
    });
  });
});
