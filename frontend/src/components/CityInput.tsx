import { useEffect, useRef, useState } from "react";

import { api, type CitySuggestion } from "../lib/api";

interface Props {
  country: string;
  value: string;
  onChange: (city: string) => void;
}

/** City field with suggestions from the bundled GeoNames set.
 *
 * A free-text city that the dataset does not recognise gets no map pin, and
 * the near-misses are the common case -- "Ha Long Bay" for Hạ Long, "Saigon"
 * for Ho Chi Minh City. Offering the names that actually resolve turns a silent
 * failure into a pick. Free text is still allowed: not every place worth
 * recording is a city over 15,000 people.
 */
export function CityInput({ country, value, onChange }: Props) {
  const [suggestions, setSuggestions] = useState<CitySuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [chosen, setChosen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // Debounced so typing does not fire a request per keystroke.
  useEffect(() => {
    if (!country || chosen) {
      setSuggestions([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      api.geo
        .cities(country, value)
        .then((results) => {
          if (cancelled) return;
          setSuggestions(results);
          setActive(-1);
        })
        .catch(() => !cancelled && setSuggestions([]));
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [country, value, chosen]);

  // Close when focus or a click leaves the control.
  useEffect(() => {
    function onDocDown(e: MouseEvent) {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, []);

  function choose(suggestion: CitySuggestion) {
    setChosen(true);
    onChange(suggestion.name);
    setOpen(false);
    setSuggestions([]);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || suggestions.length === 0) return;
    if (e.key === "Escape") {
      // Dismiss the list only. Without stopping propagation this also reaches
      // Sheet's document-level Escape handler, closing the whole form and
      // throwing away everything typed so far.
      e.stopPropagation();
      setOpen(false);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i <= 0 ? suggestions.length - 1 : i - 1));
    } else if (e.key === "Enter" && active >= 0) {
      // Only swallow Enter when a suggestion is highlighted, so Enter still
      // submits the form when the typed value is what you meant.
      e.preventDefault();
      choose(suggestions[active]);
    }
  }

  const showList = open && suggestions.length > 0;
  const exactMatch = suggestions.some(
    (s) => s.name.toLowerCase() === value.trim().toLowerCase(),
  );

  return (
    <div className="combo" ref={box}>
      <input
        value={value}
        placeholder={country ? "Hanoi" : "Choose a country first"}
        disabled={!country}
        spellCheck={false}
        autoComplete="off"
        role="combobox"
        aria-expanded={showList}
        aria-autocomplete="list"
        onChange={(e) => {
          setChosen(false);
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        required
      />

      {showList && (
        <ul className="combo-list" role="listbox">
          {suggestions.map((s, i) => (
            <li key={s.name}>
              <button
                type="button"
                role="option"
                aria-selected={i === active}
                className={i === active ? "active" : ""}
                // mousedown, not click: the input's blur would close the list
                // before a click ever lands.
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(s);
                }}
                onMouseEnter={() => setActive(i)}
              >
                {s.name}
              </button>
            </li>
          ))}
        </ul>
      )}

      {country && value.trim().length > 1 && !exactMatch && !showList && (
        <small className="combo-warn">
          Not a city we know — it will save, but without a map pin.
        </small>
      )}
    </div>
  );
}
