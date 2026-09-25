import { describe, expect, it } from "vitest";

import { discrepancyMessage, readinessBadge } from "./immigration";
import type { Discrepancy, ReadinessSummary } from "../types";

function summary(over: Partial<ReadinessSummary>): ReadinessSummary {
  return {
    state: "ready",
    permit: null,
    permitted_days: null,
    outstanding: [],
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

  it("shouts to verify when an imminent trip's rules were never checked", () => {
    // The silent hole closed: unknown + imminent must warn, not whisper.
    const badge = readinessBadge(summary({ state: "unknown" }), 10);
    expect(badge).toEqual({
      icon: "⚠️",
      text: "Entry rules not verified — check before you fly",
      className: "readiness-action",
    });
  });

  it("stays a quiet not-checked for a far-off unknown trip", () => {
    const badge = readinessBadge(summary({ state: "unknown" }), 200);
    expect(badge?.text).toBe("Not checked yet");
  });

  it("names the document required (the London ETA fix)", () => {
    const badge = readinessBadge(
      summary({
        state: "action",
        permit: "visa_free",
        permitted_days: 180,
        outstanding: [{ kind: "eta", label: "Electronic travel authorization" }],
      }),
    );
    expect(badge).toEqual({
      icon: "⚠️",
      text: "Need: ETA",
      className: "readiness-action",
    });
  });

  it("lists every outstanding document, short-named for the card", () => {
    const badge = readinessBadge(
      summary({
        state: "action",
        outstanding: [
          { kind: "eta", label: "Electronic travel authorization" },
          { kind: "entry_card", label: "Arrival card" },
        ],
      }),
    );
    expect(badge?.text).toBe("Need: ETA, Arrival card");
  });

  it("falls back to a generic message if action has no named documents", () => {
    const badge = readinessBadge(summary({ state: "action" }));
    expect(badge?.text).toBe("Action needed");
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
