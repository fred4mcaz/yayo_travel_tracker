import { addDays } from "./format";

export interface StayDates {
  check_in: string;
  check_out: string;
}

/** Turn the two ISO days a calendar drag anchored on and released over into the
 *  dates for a new stay.
 *
 *  `check_in` is the earlier day and `check_out` the later one, so dragging
 *  cells Aug 5 → Aug 8 yields Aug 5 → Aug 8 — exactly the span the resulting
 *  trip bar re-covers on the grid, so what you drag is what you get. A drag that
 *  never left its starting cell becomes a single night (a stay cannot be zero
 *  nights), landing check_out on the following day.
 *
 *  ISO `YYYY-MM-DD` strings sort chronologically, so a plain comparison orders
 *  the two ends without parsing them into Dates.
 */
export function rangeFromDrag(anchor: string, focus: string): StayDates {
  const [lo, hi] = anchor <= focus ? [anchor, focus] : [focus, anchor];
  return { check_in: lo, check_out: hi > lo ? hi : addDays(lo, 1) };
}
