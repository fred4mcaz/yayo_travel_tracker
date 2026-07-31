import { useState } from "react";

import { api, ApiError } from "../lib/api";
import { countryFlag, formatRange, relativeDays } from "../lib/format";
import type { TripStatus, TripSummary } from "../types";

const GROUPS: { status: TripStatus; label: string }[] = [
  { status: "ongoing", label: "Ongoing" },
  { status: "future", label: "Upcoming" },
  { status: "undated", label: "No dates yet" },
  { status: "past", label: "Past" },
];

interface Props {
  trips: TripSummary[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onCreated: (id: number) => void;
}

export function TripList({ trips, selectedId, onSelect, onCreated }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** No name prompt: a trip is identified by where it goes, which nobody knows
   *  yet. Create it empty and go straight to the first stay. */
  async function startTrip() {
    setBusy(true);
    setError(null);
    try {
      const trip = await api.trips.create();
      onCreated(trip.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="trip-list">
      <div className="section-head sticky">
        <h2>Trips</h2>
        <button className="btn btn-sm btn-primary" onClick={startTrip} disabled={busy}>
          {busy ? "…" : "New trip"}
        </button>
      </div>

      {error && <p className="alert alert-danger">{error}</p>}

      {trips.length === 0 && (
        <div className="empty-state">
          <p>No trips yet.</p>
          <button className="btn btn-primary" onClick={startTrip} disabled={busy}>
            Add your first trip
          </button>
        </div>
      )}

      {GROUPS.map(({ status, label }) => {
        const group = trips.filter((t) => t.status === status);
        if (group.length === 0) return null;
        return (
          <div className="trip-group" key={status}>
            <h3 className="group-label">{label}</h3>
            {group.map((trip) => (
              <button
                key={trip.id}
                className={`trip-card status-${trip.status}${
                  trip.id === selectedId ? " selected" : ""
                }`}
                onClick={() => onSelect(trip.id)}
              >
                <div className="trip-card-title">
                  {trip.countries.map((c) => (
                    <span className="flag" key={c}>
                      {countryFlag(c)}
                    </span>
                  ))}
                  <strong>{trip.label}</strong>
                </div>
                <div className="trip-card-meta">
                  {trip.start_date
                    ? formatRange(trip.start_date, trip.end_date)
                    : "No dates"}
                </div>
                {trip.cities.length > 1 && (
                  <div className="trip-card-meta">
                    {trip.cities.join(" · ")}
                    {trip.nights > 0 && ` · ${trip.nights} nights`}
                  </div>
                )}
                {trip.start_date && trip.status !== "past" && (
                  <div className="trip-card-when">
                    {trip.status === "ongoing"
                      ? `ends ${relativeDays(trip.end_date)}`
                      : relativeDays(trip.start_date)}
                  </div>
                )}
              </button>
            ))}
          </div>
        );
      })}
    </div>
  );
}
