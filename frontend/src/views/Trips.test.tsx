import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, within } from "@testing-library/react";

import { TripList } from "./Trips";
import type { TripSummary } from "../types";

afterEach(cleanup);

/** A trip summary with only the fields the list actually reads. */
function trip(over: Partial<TripSummary>): TripSummary {
  return {
    id: 0,
    label: "Trip",
    notes: "",
    start_date: null,
    end_date: null,
    status: "future",
    country_code: "",
    country_name: "",
    cities: [],
    stays: [],
    nights: 0,
    arrival_mode: null,
    unbooked_nights: 0,
    readiness: {
      state: "na",
      permit: null,
      permitted_days: null,
      arrival_card: null,
      checked_on: null,
    },
    ...over,
  };
}

function renderList(trips: TripSummary[]) {
  return render(
    <TripList
      trips={trips}
      selectedId={null}
      onSelect={vi.fn()}
      onCreated={vi.fn()}
    />,
  );
}

/** The labels of the cards under a given group heading, in DOM order. */
function cardsUnder(container: HTMLElement, groupLabel: string): string[] {
  const heading = within(container)
    .getAllByRole("heading", { level: 3 })
    .find((h) => h.textContent === groupLabel);
  if (!heading) return [];
  const group = heading.closest(".trip-group") as HTMLElement;
  return Array.from(group.querySelectorAll(".trip-card-title strong")).map(
    (el) => el.textContent ?? "",
  );
}

describe("TripList upcoming order", () => {
  it("puts the soonest upcoming trip on top, then each later one below", () => {
    // Fed in the order the API returns (start_date descending), so the list has
    // to flip them, not just pass them through.
    const { container } = renderList([
      trip({ id: 3, label: "Japan", start_date: "2026-12-01", end_date: "2026-12-10" }),
      trip({ id: 2, label: "Thailand", start_date: "2026-10-01", end_date: "2026-10-08" }),
      trip({ id: 1, label: "Vietnam", start_date: "2026-09-01", end_date: "2026-09-07" }),
    ]);

    expect(cardsUnder(container, "Upcoming")).toEqual([
      "Vietnam",
      "Thailand",
      "Japan",
    ]);
  });

  it("leaves Past newest-first (unchanged from the API order)", () => {
    const { container } = renderList([
      trip({ id: 2, label: "Recent", status: "past", start_date: "2025-06-01", end_date: "2025-06-05" }),
      trip({ id: 1, label: "Older", status: "past", start_date: "2024-01-01", end_date: "2024-01-05" }),
    ]);

    expect(cardsUnder(container, "Past")).toEqual(["Recent", "Older"]);
  });
});
