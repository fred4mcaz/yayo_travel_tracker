/** Rendering helpers for immigration readiness -- shared between the trip
 *  list's compact badge and the trip detail's full section, so the two never
 *  drift into describing the same state differently.
 */

import type { Discrepancy, PermitType, ReadinessSummary, RequirementKind } from "../types";

/** A trip within this many days of departure is "imminent" -- close enough that
 *  an unverified entry policy is itself worth a loud warning ("check before you
 *  fly"), not the quiet "not checked yet" a far-off trip gets. */
export const IMMINENT_DAYS = 30;

/** Short document names for the compact card badge, where "Electronic travel
 *  authorization" would swamp the card. The full names live in
 *  REQUIREMENT_KIND_LABEL and are used in the detail panel. */
const OUTSTANDING_SHORT: Record<RequirementKind, string> = {
  entry_card: "Arrival card",
  visa: "Visa",
  eta: "ETA",
  insurance: "Insurance",
  vaccination: "Vaccination",
  onward_ticket: "Onward ticket",
  custom: "Requirement",
};

export const PERMIT_LABEL: Record<PermitType, string> = {
  visa_free: "Visa-free",
  evisa: "E-visa required",
  visa_on_arrival: "Visa on arrival",
  visa: "Visa required",
  residency: "Residency",
  citizen: "Citizen",
};

export const REQUIREMENT_KIND_LABEL: Record<RequirementKind, string> = {
  entry_card: "Arrival card",
  visa: "Visa",
  eta: "Electronic travel authorization",
  insurance: "Travel insurance",
  vaccination: "Vaccination",
  onward_ticket: "Onward ticket",
  custom: "Requirement",
};

/** Decision 3's loud copy: one sentence naming the mismatch, never a quiet
 *  note. Shared so the trip card and the detail section say the same thing. */
export function discrepancyMessage(discrepancy: Discrepancy): string {
  const kind = REQUIREMENT_KIND_LABEL[discrepancy.kind].toLowerCase();
  return (
    `The ${kind} confirmation names a ${discrepancy.document_nationality} passport, ` +
    `but this trip has ${discrepancy.selected_passport} selected. Check which ` +
    `passport you're actually carrying.`
  );
}

/** "Visa on arrival · 30 days", or null when there is nothing to summarise. */
export function permitSummary(
  readiness: Pick<ReadinessSummary, "permit" | "permitted_days">,
): string | null {
  if (!readiness.permit) return null;
  const label = PERMIT_LABEL[readiness.permit];
  return readiness.permitted_days
    ? `${label} · ${readiness.permitted_days} days`
    : label;
}

export interface ReadinessBadge {
  icon: string;
  text: string;
  /** A CSS class naming the state, for colour -- reuses the app's existing
   *  ongoing/warn/past palette rather than inventing new tokens. */
  className: string;
}

/** The card badge. `daysUntil` is days from today to departure (null when
 *  undated or unknown), used only to decide whether an *unverified* policy on an
 *  imminent trip should shout rather than whisper.
 *
 *  null for `na`: a trip with no country recorded, or undated, has nothing worth
 *  badging -- the whole point of staying quiet until the trip is real. */
export function readinessBadge(
  readiness: ReadinessSummary,
  daysUntil: number | null = null,
): ReadinessBadge | null {
  if (readiness.state === "na") return null;

  if (readiness.state === "unknown") {
    // The silent hole closed: an imminent trip whose entry rules were never
    // checked is itself a warning -- the app never knows the documents, so it
    // tells the traveller to verify rather than staying quiet.
    if (daysUntil !== null && daysUntil <= IMMINENT_DAYS) {
      return {
        icon: "⚠️",
        text: "Entry rules not verified — check before you fly",
        className: "readiness-action",
      };
    }
    return { icon: "❔", text: "Not checked yet", className: "readiness-unknown" };
  }

  if (readiness.state === "ready") {
    // Nothing owed: show the default allowance, which is what the traveller
    // actually wants to know ("Visa-free · 90 days").
    const summary = permitSummary(readiness);
    return {
      icon: "✅",
      text: summary ? `Ready · ${summary}` : "Ready",
      className: "readiness-ready",
    };
  }

  // action: name the documents/authorizations still required. This is the fix
  // for the London near-miss -- the badge now says "Need: ETA", never a vague
  // "E-visa required" that hid the actual action.
  const names = readiness.outstanding.map(
    (o) => OUTSTANDING_SHORT[o.kind] ?? o.label,
  );
  return {
    icon: "⚠️",
    text: names.length ? `Need: ${names.join(", ")}` : "Action needed",
    className: "readiness-action",
  };
}
