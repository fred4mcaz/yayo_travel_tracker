import { useEffect, useRef } from "react";

import { parseDate, toISODate } from "../lib/format";

interface Props {
  value: string;
  onChange: (iso: string) => void;
  min?: string;
  required?: boolean;
}

/** A date input with day up/down buttons beside it.
 *
 * Nudging a booking by a day is the most common edit there is, and doing it
 * through a date picker takes several taps. The text field still works exactly
 * as before; these just save the round trip.
 */
export function DateField({ value, onChange, min, required }: Props) {
  // React batches state updates, so several clicks in one tick would each read
  // the same stale `value` prop and the date would advance a single day no
  // matter how many times you pressed. Stepping from a ref that is updated
  // immediately makes rapid clicking accumulate properly.
  const latest = useRef(value);
  useEffect(() => {
    latest.current = value;
  }, [value]);

  function shift(days: number) {
    // Step from today when the field is empty, so the buttons always do
    // something sensible rather than nothing.
    const base = latest.current ? parseDate(latest.current) : new Date();
    base.setDate(base.getDate() + days);
    const next = toISODate(base);
    if (min && next < min) return;
    latest.current = next;
    onChange(next);
  }

  return (
    <div className="datefield">
      <input
        type="date"
        value={value}
        min={min}
        onChange={(e) => onChange(e.target.value)}
        required={required}
      />
      <div className="stepper">
        <button
          type="button"
          aria-label="Later by one day"
          title="Later by one day"
          onClick={() => shift(1)}
        >
          ▲
        </button>
        <button
          type="button"
          aria-label="Earlier by one day"
          title="Earlier by one day"
          onClick={() => shift(-1)}
        >
          ▼
        </button>
      </div>
    </div>
  );
}
