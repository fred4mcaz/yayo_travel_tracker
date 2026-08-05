import { describe, expect, it } from "vitest";

import { discrepancyMessage, readinessBadge } from "./immigration";
import type { Discrepancy, ReadinessSummary } from "../types";

function summary(over: Partial<ReadinessSummary>): ReadinessSummary {
  return {
    state: "ready",
    permit: null,
    permitted_days: null,
    arrival_card: null,
    onward_ticket: null,
    checked_on: null,
    discrepancy: null,
    ...over,
  };
}

describe("readinessBadge", () => {
  it("is null for na -- nothing to assess yet", () => {
    expect(readinessBadge(summary({ state: "na" }))).toBeNull();
  });

  it("reads as not-checked when unknown", () => {
    const badge = readinessBadge(summary({ state: "unknown" }));
    expect(badge).toEqual({
      icon: "❔",
      text: "Not checked yet",
      className: "readiness-unknown",
    });
  });

  it("summarises the permit when ready", () => {
    const badge = readinessBadge(
      summary({ state: "ready", permit: "visa_free", permitted_days: 90 }),
    );
    expect(badge).toEqual({
      icon: "✅",
      text: "Ready · Visa-free · 90 days",
      className: "readiness-ready",
    });
  });

  it("reads ready with no permit summary when nothing is required at all", () => {
    const badge = readinessBadge(summary({ state: "ready" }));
    expect(badge?.text).toBe("Ready");
  });

  it("calls out an unconfirmed arrival card when action is needed", () => {
    const badge = readinessBadge(
      summary({
        state: "action",
        permit: "visa_on_arrival",
        permitted_days: 30,
        arrival_card: {
          name: "Indonesia e-CD",
          state: "none",
          reference: "",
        },
      }),
    );
    expect(badge).toEqual({
      icon: "⚠️",
      text: "Visa on arrival · 30 days · arrival card not yet confirmed",
      className: "readiness-action",
    });
  });

  it("still calls out the arrival card while a confirmation only sits in Review", () => {
    const badge = readinessBadge(
      summary({
        state: "action",
        permit: "visa_on_arrival",
        arrival_card: { name: "Indonesia e-CD", state: "received", reference: "" },
      }),
    );
    expect(badge?.text).toBe("Visa on arrival · arrival card not yet confirmed");
  });

  it("drops the arrival-card note once it's confirmed", () => {
    const badge = readinessBadge(
      summary({
        state: "action",
        permit: "visa",
        arrival_card: { name: "", state: "confirmed", reference: "" },
      }),
    );
    expect(badge?.text).toBe("Visa required");
  });

  it("calls out an unconfirmed onward ticket when action is needed", () => {
    const badge = readinessBadge(
      summary({
        state: "action",
        permit: "visa_free",
        permitted_days: 30,
        onward_ticket: { required: true, confirmed: false, journey: null },
      }),
    );
    expect(badge?.text).toBe("Visa-free · 30 days · onward ticket not confirmed");
  });

  it("drops the onward note once a journey confirms it", () => {
    const badge = readinessBadge(
      summary({
        state: "ready",
        permit: "visa_free",
        onward_ticket: {
          required: true,
          confirmed: true,
          journey: { carrier: "SQ", number: "123", depart_on: "2026-09-15", to_place: "Singapore" },
        },
      }),
    );
    expect(badge?.text).toBe("Ready · Visa-free");
  });
});

describe("discrepancyMessage", () => {
  it("names the requirement kind and both nationalities in one loud sentence", () => {
    const discrepancy: Discrepancy = {
      kind: "entry_card",
      document_nationality: "MX",
      selected_passport: "US",
    };
    expect(discrepancyMessage(discrepancy)).toBe(
      "The arrival card confirmation names a MX passport, but this trip has " +
        "US selected. Check which passport you're actually carrying.",
    );
  });
});
