/** Date formatting.
 *
 * Dates from the API are plain YYYY-MM-DD with no timezone. `new Date("2026-03-18")`
 * parses that as UTC midnight, which renders as the 17th anywhere west of
 * Greenwich — so every date would be a day early for the user. Parsing the
 * components by hand into a local Date avoids that entirely.
 */

export function parseDate(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function today(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

export function addDays(iso: string, days: number): string {
  const d = parseDate(iso);
  d.setDate(d.getDate() + days);
  return toISODate(d);
}

export function toISODate(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = parseDate(iso);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
}

export function formatDateShort(iso: string | null): string {
  if (!iso) return "—";
  const d = parseDate(iso);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

/** "Mar 18–24, 2026", collapsing the repeated month and year where possible. */
export function formatRange(from: string | null, to: string | null): string {
  if (!from || !to) return formatDate(from ?? to);
  const a = parseDate(from);
  const b = parseDate(to);
  if (a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth()) {
    return `${MONTHS[a.getMonth()]} ${a.getDate()}–${b.getDate()}, ${a.getFullYear()}`;
  }
  if (a.getFullYear() === b.getFullYear()) {
    return `${MONTHS[a.getMonth()]} ${a.getDate()} – ${MONTHS[b.getMonth()]} ${b.getDate()}, ${a.getFullYear()}`;
  }
  return `${formatDate(from)} – ${formatDate(to)}`;
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  // Server times are naive local wall-clock; strip any stray zone marker so the
  // browser cannot shift them.
  const clean = iso.replace(/(Z|[+-]\d{2}:\d{2})$/, "");
  const [datePart, timePart = "00:00"] = clean.split("T");
  const d = parseDate(datePart);
  const [hh, mm] = timePart.split(":");
  return `${MONTHS[d.getMonth()]} ${d.getDate()} ${hh}:${mm}`;
}

export function formatTime(iso: string | null): string {
  if (!iso) return "";
  const clean = iso.replace(/(Z|[+-]\d{2}:\d{2})$/, "");
  const timePart = clean.split("T")[1] ?? "";
  return timePart.slice(0, 5);
}

/** Whole days from today. Negative means in the past. */
export function daysFromToday(iso: string | null): number | null {
  if (!iso) return null;
  const ms = parseDate(iso).getTime() - today().getTime();
  return Math.round(ms / 86_400_000);
}

export function relativeDays(iso: string | null): string {
  const days = daysFromToday(iso);
  if (days === null) return "";
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  if (days > 0) return `in ${days} days`;
  return `${Math.abs(days)} days ago`;
}

export function nightsBetween(from: string, to: string): number {
  return Math.round(
    (parseDate(to).getTime() - parseDate(from).getTime()) / 86_400_000,
  );
}

/** ISO 3166-1 alpha-2 to the regional-indicator flag emoji. */
export function countryFlag(code: string): string {
  if (!code || code.length !== 2) return "";
  return String.fromCodePoint(
    ...code
      .toUpperCase()
      .split("")
      .map((c) => 0x1f1e6 + c.charCodeAt(0) - 65),
  );
}

export function formatMoney(amount: number | null, currency: string): string {
  if (amount === null) return "";
  const rounded = Math.round(amount * 100) / 100;
  return currency ? `${rounded.toFixed(2)} ${currency}` : rounded.toFixed(2);
}
