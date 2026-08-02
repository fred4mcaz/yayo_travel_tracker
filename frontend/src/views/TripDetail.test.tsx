import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { TripDetailPanel } from "./TripDetail";
import type { TripDetail } from "../types";

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
  nights: 0,
  unbooked_nights: 0,
  country: null,
  requirements: [],
  notes_list: [],
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
