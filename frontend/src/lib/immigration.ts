/** Rendering helpers for immigration readiness -- shared between the trip
 *  list's compact badge and the trip detail's full section, so the two never
 *  drift into describing the same state differently.
 */

import type { PermitType, ReadinessSummary } from "../types";

export const PERMIT_LABEL: Record<PermitType, string> = {
  visa_free: "Visa-free",
  evisa: "E-visa required",
  visa_on_arrival: "Visa on arrival",
  visa: "Visa required",
  residency: "Residency",
  citizen: "Citizen",
};

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

/** null for `na`: a trip with no country recorded, or undated, has nothing
 *  worth badging -- the whole point of staying quiet until the trip is real. */
export function readinessBadge(readiness: ReadinessSummary): ReadinessBadge | null {
  if (readiness.state === "na") return null;

  if (readiness.state === "unknown") {
    return { icon: "❔", text: "Not checked yet", className: "readiness-unknown" };
  }

  const summary = permitSummary(readiness);

  if (readiness.state === "ready") {
    return {
      icon: "✅",
      text: summary ? `Ready · ${summary}` : "Ready",
      className: "readiness-ready",
    };
  }

  // action
  const cardNote =
    readiness.arrival_card && !readiness.arrival_card.confirmed
      ? "arrival card not yet confirmed"
      : null;
  return {
    icon: "⚠️",
    text: [summary, cardNote].filter(Boolean).join(" · ") || "Action needed",
    className: "readiness-action",
  };
}
