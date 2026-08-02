# Plan: missing-travel alert + calendar travel connectors

Two features, phased. Each phase: clear objective, assumptions validated at the
phase, risks prepared for, tests that must pass before moving on. A phase is not
complete until it is committed and its line here carries the commit hash.

- **Phase 1 — Missing-travel banner on the Trip view (frontend).** A warn banner
  on `TripDetailPanel` when a future/ongoing trip has a country recorded but no
  arrival leg, with a button that opens the "How you get there" form. Tests:
  banner for a future trip with no legs; hidden once a leg exists; hidden for a
  past trip. — `74b3606`
- **Phase 2 — Backend: arrival travel mode on the trip summary.** `GET /api/trips`
  carries `arrival_mode: TravelMode | null` (the earliest arrival leg's mode);
  add it to the `TripSummary` type. Tests: flight leg → "flight"; no leg → null;
  earliest leg wins. — `4cafc71`
- **Phase 3 — Calendar: gap between consecutive trips.** Let an
  `end-day == next-start-day` pair share a lane and trim the shared boundary so a
  visible gap opens; truly overlapping stays still stack. Tests: adjacent pair
  shares a row with a horizontal gap; overlapping pair still stacks. — `<hash>`
- **Phase 4 — Calendar: travel indicator in the gap.** A small mode glyph
  (✈ 🚆 🚌 ⛴ 🚗) centered in the gap between consecutive same-lane trips, showing
  the later trip's arrival mode; a neutral marker when none is recorded. Tests:
  connector present with the right glyph/tooltip; count matches adjacencies; none
  for a lone trip or a cross-week pair. — `<hash>`
- **Phase 5 — Docs, browser verification, deploy.** README (calendar bullet,
  feature table, connector limitation), verify both features in the dev browser,
  then push + deploy. — `<hash>`

## Scope limits (documented, not built)
- Connectors are drawn only for consecutive trips on the **same row in the same
  week**. Cross-week and cross-lane pairs get no connector.
