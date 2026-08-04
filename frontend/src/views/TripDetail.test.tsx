import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  waitFor,
  within,
} from "@testing-library/react";

import { TripDetailPanel } from "./TripDetail";
import { api } from "../lib/api";
import type { Leg, MergeCandidate, TripCountry, TripDetail } from "../types";

// Keep the real module (ApiError, every other call) and stub only the one
// network call the keep-separate tests drive.
vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      trips: { ...actual.api.trips, keepSeparate: vi.fn() },
    },
  };
});

afterEach(cleanup);

const TRIP: TripDetail = {
  id: 7,
  label: "New trip",
  notes: "",
  start_date: null,
  end_date: null,
  status: "undated",
  country_code: "",
  country_name: "",
  cities: [],
  stays: [],
  nights: 0,
  arrival_mode: null,
  unbooked_nights: 0,
  country: null,
  requirements: [],
  notes_list: [],
  mergeable: [],
  readiness: {
    state: "na",
    permit: null,
    permitted_days: null,
    arrival_card: null,
    checked_on: null,
    passport: null,
    is_default_us: false,
    checklist: [],
    advisory: "",
    alternate_passport_hint: null,
  },
};

function renderPanel(props: Partial<Parameters<typeof TripDetailPanel>[0]> = {}) {
  const onStayOpened = vi.fn();
  const view = render(
    <TripDetailPanel
      trip={TRIP}
      passports={[]}
      recentCountries={[]}
      onStayOpened={onStayOpened}
      onChange={vi.fn()}
      onDeleted={vi.fn()}
      {...props}
    />,
  );
  const sheetOpen = () =>
    Array.from(view.container.querySelectorAll("h2, h3")).some((h) =>
      /Add a country|Add hotel/.test(h.textContent ?? ""),
    );
  return { ...view, onStayOpened, sheetOpen };
}

describe("TripDetailPanel stay-form-on-mount", () => {
  it("opens the stay form when asked, and reports that it consumed the flag", () => {
    const { sheetOpen, onStayOpened } = renderPanel({ openStayOnMount: true });
    expect(sheetOpen()).toBe(true);
    expect(onStayOpened).toHaveBeenCalledTimes(1);
  });

  it("stays open after the flag is cleared underneath it", () => {
    // App drops justCreated the moment onStayOpened fires; the sheet must not
    // slam shut just because the prop flipped back to false.
    const { rerender, sheetOpen } = renderPanel({ openStayOnMount: true });
    rerender(
      <TripDetailPanel
        trip={TRIP}
        passports={[]}
        recentCountries={[]}
        openStayOnMount={false}
        onChange={vi.fn()}
        onDeleted={vi.fn()}
      />,
    );
    expect(sheetOpen()).toBe(true);
  });

  it("does not open the form on a remount once the flag is gone", () => {
    // The bug: leaving the Trips tab unmounts this panel, and coming back
    // remounted it with the flag still set, reopening "Add a country" on every
    // single visit.
    const first = renderPanel({ openStayOnMount: true });
    expect(first.sheetOpen()).toBe(true);
    first.unmount();

    const second = renderPanel({ openStayOnMount: false });
    expect(second.sheetOpen()).toBe(false);
  });

  it("leaves the form shut when it was never asked for", () => {
    const { sheetOpen, onStayOpened } = renderPanel();
    expect(sheetOpen()).toBe(false);
    expect(onStayOpened).not.toHaveBeenCalled();
  });
});

function tripCountry(over: Partial<TripCountry> = {}): TripCountry {
  return {
    country_code: "VN",
    country_name: "Vietnam",
    entry: null,
    passport_id: null,
    entered_on: "2026-09-10",
    leaving_on: null,
    starts_on: "2026-09-10",
    ends_on: "2026-09-14",
    nights: 4,
    unbooked: [],
    stays: [],
    legs: [],
    ...over,
  };
}

function leg(over: Partial<Leg> = {}): Leg {
  return {
    id: 1,
    trip_id: 7,
    mode: "flight",
    country_code: "VN",
    carrier: "",
    number: "",
    from_place: "Bangkok",
    from_iata: "",
    depart_at: null,
    to_place: "Hanoi",
    to_iata: "",
    arrive_at: null,
    confirmation_code: "",
    seat: "",
    cost: null,
    currency: "",
    notes: "",
    ...over,
  };
}

describe("TripDetailPanel missing-travel banner", () => {
  const hasBanner = (c: HTMLElement) =>
    Array.from(c.querySelectorAll(".missing-travel")).length > 0;

  it("warns when an upcoming trip records no arrival journey", () => {
    const { container } = renderPanel({
      trip: {
        ...TRIP,
        status: "future",
        country: tripCountry({ legs: [] }),
      },
    });
    expect(hasBanner(container)).toBe(true);
  });

  it("stays quiet once a journey is recorded", () => {
    const { container } = renderPanel({
      trip: {
        ...TRIP,
        status: "future",
        country: tripCountry({ legs: [leg()] }),
      },
    });
    expect(hasBanner(container)).toBe(false);
  });

  it("does not nag about a past trip with no journey", () => {
    // Old flights often go un-backfilled; a banner there is just noise.
    const { container } = renderPanel({
      trip: {
        ...TRIP,
        status: "past",
        country: tripCountry({ legs: [] }),
      },
    });
    expect(hasBanner(container)).toBe(false);
  });
});

function mergeCandidate(over: Partial<MergeCandidate> = {}): MergeCandidate {
  return {
    id: 99,
    label: "Hanoi · Sofitel Legend",
    start_date: "2026-09-10",
    end_date: "2026-09-14",
    ...over,
  };
}

/** Mirrors App: the panel is fed a trip and re-fed whatever the API returns
 *  through onChange, so a persisted keep-separate flows back as new mergeable. */
function MergeHarness({ initial }: { initial: TripDetail }) {
  const [trip, setTrip] = useState(initial);
  return (
    <TripDetailPanel
      trip={trip}
      passports={[]}
      recentCountries={[]}
      onStayOpened={vi.fn()}
      onChange={setTrip}
      onDeleted={vi.fn()}
    />
  );
}

describe("TripDetailPanel keep-separate", () => {
  const keepSeparate = vi.mocked(api.trips.keepSeparate);
  afterEach(() => keepSeparate.mockReset());

  const mergeHeading = (c: HTMLElement) =>
    Array.from(c.querySelectorAll("h3")).some(
      (h) => h.textContent === "Same trip as another?",
    );

  it("calls the API and drops the whole card once the pair is kept separate", async () => {
    const initial = { ...TRIP, mergeable: [mergeCandidate({ id: 42 })] };
    // The backend now returns no candidates: the pair is persisted as separate.
    keepSeparate.mockResolvedValue({ ...initial, mergeable: [] });

    const { container, getByText } = render(<MergeHarness initial={initial} />);
    expect(mergeHeading(container)).toBe(true);

    fireEvent.click(getByText("Keep separate"));

    expect(keepSeparate).toHaveBeenCalledWith(TRIP.id, 42);
    await waitFor(() => expect(mergeHeading(container)).toBe(false));
  });

  it("keeps the other suggestions when only one pair is dismissed", async () => {
    const first = mergeCandidate({ id: 1, label: "Hanoi · Sofitel" });
    const second = mergeCandidate({ id: 2, label: "Hue · Pilgrimage Village" });
    const initial = { ...TRIP, mergeable: [first, second] };
    // Dismissing the first leaves the backend still proposing the second.
    keepSeparate.mockResolvedValue({ ...initial, mergeable: [second] });

    const { container, getByText, queryByText } = render(
      <MergeHarness initial={initial} />,
    );

    const firstRow = getByText("Hanoi · Sofitel").closest(
      ".merge-row",
    ) as HTMLElement;
    fireEvent.click(within(firstRow).getByText("Keep separate"));

    expect(keepSeparate).toHaveBeenCalledWith(TRIP.id, 1);
    await waitFor(() => expect(queryByText("Hanoi · Sofitel")).toBeNull());
    expect(queryByText("Hue · Pilgrimage Village")).not.toBeNull();
    expect(mergeHeading(container)).toBe(true);
  });
});

/** Mirrors App's justCreated lifecycle: the panel is mounted only while the
 *  Trips tab is showing, so switching tabs unmounts and remounts it. This is
 *  the shape the "Add a country form pops up every time I click Trips" bug
 *  actually had — the panel alone cannot show it. */
function TabHarness({ createdId }: { createdId: number | null }) {
  const [justCreated, setJustCreated] = useState<number | null>(createdId);
  const [onTrips, setOnTrips] = useState(true);
  const [trip, setTrip] = useState<TripDetail>(TRIP);
  return (
    <>
      <button onClick={() => setOnTrips((v) => !v)}>toggle-tab</button>
      {/* Creating a second trip while this panel is already open. */}
      <button
        onClick={() => {
          const next = { ...TRIP, id: TRIP.id + 1 };
          setJustCreated(next.id);
          setTrip(next);
        }}
      >
        create-another
      </button>
      {onTrips && (
        <TripDetailPanel
          key={trip.id}
          trip={trip}
          passports={[]}
          recentCountries={[]}
          openStayOnMount={trip.id === justCreated}
          onStayOpened={() => setJustCreated(null)}
          onChange={vi.fn()}
          onDeleted={vi.fn()}
        />
      )}
    </>
  );
}

describe("returning to the Trips tab", () => {
  it("does not reopen the stay form after the trip was just created", () => {
    const { container, getByText } = render(<TabHarness createdId={TRIP.id} />);
    const open = () =>
      Array.from(container.querySelectorAll("h2, h3")).some((h) =>
        /Add a country|Add hotel/.test(h.textContent ?? ""),
      );

    // Created moments ago: the form is meant to be up on this first mount.
    expect(open()).toBe(true);

    // Leave Trips and come back, twice. Before the one-shot fix this reopened
    // "Add a country" on every return.
    for (let i = 0; i < 2; i++) {
      fireEvent.click(getByText("toggle-tab"));
      fireEvent.click(getByText("toggle-tab"));
      expect(open()).toBe(false);
    }
  });

  it("still opens the form for a trip created while another was open", () => {
    // The panel honours openStayOnMount from its state initializer, which only
    // runs on a real mount — so the panel must be keyed by trip id, or this
    // creation silently swaps props and no form ever appears.
    const { container, getByText } = render(<TabHarness createdId={null} />);
    const open = () =>
      Array.from(container.querySelectorAll("h2, h3")).some((h) =>
        /Add a country|Add hotel/.test(h.textContent ?? ""),
      );

    expect(open()).toBe(false);
    fireEvent.click(getByText("create-another"));
    expect(open()).toBe(true);
  });
});
