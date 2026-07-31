import { useState } from "react";

import { Field, Text } from "../components/Fields";
import { Sheet } from "../components/Sheet";
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
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const trip = await api.trips.create({ title: title.trim() });
      setAdding(false);
      setTitle("");
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
        <button className="btn btn-sm btn-primary" onClick={() => setAdding(true)}>
          New trip
        </button>
      </div>

      {trips.length === 0 && (
        <div className="empty-state">
          <p>No trips yet.</p>
          <button className="btn btn-primary" onClick={() => setAdding(true)}>
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
                  <strong>{trip.title}</strong>
                </div>
                <div className="trip-card-meta">
                  {trip.start_date
                    ? `${formatRange(trip.start_date, trip.end_date)}`
                    : "No dates"}
                </div>
                <div className="trip-card-meta">
                  {trip.cities.length > 0 && <span>{trip.cities.join(" · ")}</span>}
                  {trip.nights > 0 && (
                    <span>
                      {" "}
                      · {trip.nights} night{trip.nights === 1 ? "" : "s"}
                    </span>
                  )}
                </div>
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

      {adding && (
        <Sheet
          title="New trip"
          onClose={() => setAdding(false)}
          footer={
            <>
              <button className="btn" onClick={() => setAdding(false)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={busy || !title.trim()}
                onClick={create}
              >
                {busy ? "Creating…" : "Create"}
              </button>
            </>
          }
        >
          {error && <p className="alert alert-danger">{error}</p>}
          <Field
            label="What should this trip be called?"
            hint="Dates fill in automatically once you add a stay or a flight."
            wide
          >
            <Text
              value={title}
              onChange={setTitle}
              placeholder="Vietnam · Hanoi"
            />
          </Field>
        </Sheet>
      )}
    </div>
  );
}
