import { useMemo } from "react";

import { COUNTRIES } from "../lib/countries";

interface Props {
  value: string;
  onChange: (code: string) => void;
  /** Countries already used in your trips, floated to the top. */
  recent?: string[];
}

/** A native <select>, deliberately.
 *
 * 249 options is a lot, but the native control gives type-ahead on desktop
 * (typing "vie" jumps to Vietnam) and a proper wheel picker on iOS, both for
 * free and both more usable than a hand-rolled combobox. Countries you have
 * already visited are floated to the top, since the next trip is often a
 * repeat.
 */
export function CountrySelect({ value, onChange, recent = [] }: Props) {
  const recentSet = useMemo(
    () => new Set(recent.map((c) => c.toUpperCase()).filter(Boolean)),
    [recent],
  );
  const recentList = useMemo(
    () => COUNTRIES.filter((c) => recentSet.has(c.code)),
    [recentSet],
  );

  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Choose a country…</option>

      {recentList.length > 0 && (
        <optgroup label="Been there">
          {recentList.map((c) => (
            <option key={`r-${c.code}`} value={c.code}>
              {c.name}
            </option>
          ))}
        </optgroup>
      )}

      <optgroup label="All countries">
        {COUNTRIES.map((c) => (
          <option key={c.code} value={c.code}>
            {c.name}
          </option>
        ))}
      </optgroup>
    </select>
  );
}
