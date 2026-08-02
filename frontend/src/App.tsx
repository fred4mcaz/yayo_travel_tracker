import { useCallback, useEffect, useState } from "react";

import { api } from "./lib/api";
import type { StayDates } from "./lib/calendarRange";
import type { AuthStatus, Note, Passport, TripDetail, TripSummary } from "./types";
import { Auth } from "./views/Auth";
import { Calendar } from "./views/Calendar";
import { MapView } from "./views/Map";
import { ReviewQueue } from "./views/Review";
import { Settings } from "./views/Settings";
import { TripDetailPanel } from "./views/TripDetail";
import { TripList } from "./views/Trips";

type Tab = "trips" | "calendar" | "map" | "review" | "settings";

export function App() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [trips, setTrips] = useState<TripSummary[]>([]);
  const [selected, setSelected] = useState<TripDetail | null>(null);
  const [passports, setPassports] = useState<Passport[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [tab, setTab] = useState<Tab>("trips");
  // Set for a trip created moments ago, so its detail opens on the stay form.
  const [justCreated, setJustCreated] = useState<number | null>(null);
  // Dates to seed that stay form with, when the trip was born from a calendar
  // drag rather than the New-trip button.
  const [pendingStayDates, setPendingStayDates] = useState<StayDates | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Proposals awaiting review, for the tab badge.
  const [reviewCount, setReviewCount] = useState(0);

  // The enrollment link carries its token in the query string.
  const enrollmentToken = new URLSearchParams(window.location.search).get("token");

  const refreshStatus = useCallback(() => {
    api.auth
      .status()
      .then(setStatus)
      .catch(() => setStatus({ authenticated: false, enrolled: false, recovery_codes_left: null, passkey_count: null }));
  }, []);

  useEffect(refreshStatus, [refreshStatus]);

  const loadTrips = useCallback(async () => {
    try {
      const [list, ps, ns] = await Promise.all([
        api.trips.list(),
        api.passports.list(),
        api.notes.list(),
      ]);
      setTrips(list);
      setPassports(ps);
      setNotes(ns);
      setLoadError(null);
      return list;
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
      return [];
    }
  }, []);

  useEffect(() => {
    if (status?.authenticated) void loadTrips();
  }, [status?.authenticated, loadTrips]);

  const refreshReviewCount = useCallback(() => {
    api.review
      .count()
      .then((c) => setReviewCount(c.pending))
      .catch(() => setReviewCount(0));
  }, []);

  useEffect(() => {
    if (status?.authenticated) refreshReviewCount();
  }, [status?.authenticated, refreshReviewCount]);

  const openTrip = useCallback(async (id: number) => {
    const detail = await api.trips.get(id);
    setSelected(detail);
  }, []);

  const selectTrip = useCallback(
    (id: number) => {
      setJustCreated(null);
      setPendingStayDates(null);
      void openTrip(id);
    },
    [openTrip],
  );

  // A calendar drag: create the trip, then open it straight on the stay form
  // with those dates already filled in — the same path New-trip takes, only
  // dated. Cancelling leaves an empty undated trip, exactly as New-trip does.
  const createTripForRange = useCallback(
    async (dates: StayDates) => {
      try {
        const trip = await api.trips.create();
        setPendingStayDates(dates);
        setJustCreated(trip.id);
        setTab("trips");
        await loadTrips();
        void openTrip(trip.id);
      } catch (e) {
        setLoadError(e instanceof Error ? e.message : String(e));
      }
    },
    [loadTrips, openTrip],
  );

  if (status === null) {
    return (
      <div className="boot">
        <span className="pill">Loading…</span>
      </div>
    );
  }

  if (!status.authenticated) {
    return (
      <Auth
        enrolled={status.enrolled}
        enrollmentToken={enrollmentToken}
        onAuthenticated={() => {
          // Drop the one-time token from the URL so it is not left in history.
          window.history.replaceState({}, "", window.location.pathname);
          refreshStatus();
        }}
      />
    );
  }

  // Every country already on record, so the picker offers them first.
  const recentCountries = Array.from(
    new Set(trips.map((t) => t.country_code).filter(Boolean)),
  );

  const detailPanel = selected ? (
    <TripDetailPanel
      trip={selected}
      passports={passports}
      recentCountries={recentCountries}
      openStayOnMount={selected.id === justCreated}
      initialStayDates={
        selected.id === justCreated ? (pendingStayDates ?? undefined) : undefined
      }
      onChange={(trip) => {
        setSelected(trip);
        void loadTrips();
      }}
      onDeleted={() => {
        setSelected(null);
        setJustCreated(null);
        setPendingStayDates(null);
        void loadTrips();
      }}
      onClose={() => {
        setSelected(null);
        setJustCreated(null);
        setPendingStayDates(null);
      }}
    />
  ) : (
    <div className="detail-empty">
      <p>Select a trip, or create one.</p>
    </div>
  );

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">Yayo travel</span>
        <nav className="tabs">
          {(["trips", "calendar", "map", "review", "settings"] as Tab[]).map((t) => (
            <button
              key={t}
              className={tab === t ? "tab active" : "tab"}
              onClick={() => setTab(t)}
            >
              {t[0].toUpperCase() + t.slice(1)}
              {t === "review" && reviewCount > 0 && (
                <span className="tab-badge">{reviewCount}</span>
              )}
            </button>
          ))}
        </nav>
      </header>

      {loadError && <p className="alert alert-danger">{loadError}</p>}

      <main className={`layout${selected ? " has-detail" : ""}`}>
        {tab === "trips" && (
          <>
            <div className="pane pane-list">
              <TripList
                trips={trips}
                selectedId={selected?.id ?? null}
                onSelect={selectTrip}
                onCreated={async (id) => {
                  setJustCreated(id);
                  setPendingStayDates(null);
                  await loadTrips();
                  void openTrip(id);
                }}
              />
            </div>
            <div className="pane pane-detail">{detailPanel}</div>
          </>
        )}

        {tab === "calendar" && (
          <div className="pane placeholder">
            <Calendar
              trips={trips}
              notes={notes}
              onSelect={(id) => {
                setTab("trips");
                selectTrip(id);
              }}
              onCreateRange={createTripForRange}
            />
          </div>
        )}

        {tab === "map" && (
          <div className="pane placeholder">
            <MapView
              trips={trips}
              loadDetail={api.trips.get}
              onSelect={(id) => {
                setTab("trips");
                selectTrip(id);
              }}
            />
          </div>
        )}

        {tab === "review" && (
          <div className="pane">
            <ReviewQueue
              onReviewed={() => {
                refreshReviewCount();
                void loadTrips();
              }}
            />
          </div>
        )}

        {tab === "settings" && (
          <div className="pane">
            <Settings
              passports={passports}
              onPassportsChanged={() => void loadTrips()}
              onLoggedOut={refreshStatus}
            />
          </div>
        )}
      </main>

      <nav className="bottom-tabs">
        {(["trips", "calendar", "map", "review", "settings"] as Tab[]).map((t) => (
          <button
            key={t}
            className={tab === t ? "btab active" : "btab"}
            onClick={() => {
              setTab(t);
              if (t !== "trips") setSelected(null);
            }}
          >
            {t[0].toUpperCase() + t.slice(1)}
            {t === "review" && reviewCount > 0 && (
              <span className="tab-badge">{reviewCount}</span>
            )}
          </button>
        ))}
      </nav>
    </div>
  );
}
