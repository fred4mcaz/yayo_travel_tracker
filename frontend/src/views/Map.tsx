import { geoNaturalEarth1, geoPath, type GeoPermissibleObjects } from "d3-geo";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { countryFlag } from "../lib/format";
import type { TripDetail, TripSummary } from "../types";

interface CountryFeature {
  type: "Feature";
  properties: { c: string; n: string };
  geometry: GeoPermissibleObjects;
}

interface Props {
  trips: TripSummary[];
  /** Loaded lazily so a pin can be drawn per city, not just per country. */
  loadDetail: (id: number) => Promise<TripDetail>;
  onSelect: (id: number) => void;
}

interface Pin {
  tripId: number;
  city: string;
  country: string;
  lat: number;
  lon: number;
  status: string;
}

/** The path through one trip's stays, in visit order. */
interface Route {
  status: string;
  points: [number, number][];
}

const STATUS_FILL: Record<string, string> = {
  ongoing: "#1d9e75",
  future: "#378add",
  past: "#888780",
  undated: "#ba7517",
};

export function MapView({ trips, loadDetail, onSelect }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [countries, setCountries] = useState<CountryFeature[] | null>(null);
  const [pins, setPins] = useState<Pin[]>([]);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState({ w: 800, h: 420 });
  const [hover, setHover] = useState<Pin | null>(null);

  // Country outlines: ~168KB, immutable, fetched once and cached by the browser.
  useEffect(() => {
    let cancelled = false;
    fetch("/geo/countries.geojson")
      .then((r) => {
        if (!r.ok) throw new Error(`Map data unavailable (${r.status})`);
        return r.json();
      })
      .then((data) => !cancelled && setCountries(data.features))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, []);

  // Pins need per-stay coordinates, which only the detail endpoint returns.
  useEffect(() => {
    let cancelled = false;
    Promise.all(trips.map((t) => loadDetail(t.id).catch(() => null)))
      .then((details) => {
        if (cancelled) return;
        const nextPins: Pin[] = [];
        const nextRoutes: Route[] = [];
        for (const detail of details) {
          if (!detail) continue;
          const located = detail.stays.filter(
            (s) => s.lat !== null && s.lon !== null,
          );
          for (const stay of located) {
            nextPins.push({
              tripId: detail.id,
              city: stay.city,
              country: stay.country_code,
              lat: stay.lat!,
              lon: stay.lon!,
              status: detail.status,
            });
          }
          // Stays already arrive ordered by check-in, so consecutive pairs are
          // the actual route through the trip.
          if (located.length > 1) {
            nextRoutes.push({
              status: detail.status,
              points: located.map((s) => [s.lon!, s.lat!] as [number, number]),
            });
          }
        }
        setPins(nextPins);
        setRoutes(nextRoutes);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [trips, loadDetail]);

  // Track the container width so the map fills whatever space it is given.
  // useLayoutEffect, not useEffect: this measures before the browser paints, so
  // the map never renders once at the placeholder size and then jumps.
  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      if (w > 0) setSize({ w, h: Math.max(240, Math.round(w * 0.52)) });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const visitedByCountry = useMemo(() => {
    const map = new Map<string, string>();
    // Rank so an ongoing trip wins over a past one when both touch a country.
    const rank: Record<string, number> = { past: 1, undated: 2, future: 3, ongoing: 4 };
    for (const trip of trips) {
      for (const code of trip.countries) {
        const current = map.get(code);
        if (!current || (rank[trip.status] ?? 0) > (rank[current] ?? 0)) {
          map.set(code, trip.status);
        }
      }
    }
    return map;
  }, [trips]);

  const projection = useMemo(() => {
    // Natural Earth 1: a compromise projection that keeps country shapes
    // recognisable without Mercator's absurd polar inflation.
    return geoNaturalEarth1().fitExtent(
      [
        [4, 4],
        [size.w - 4, size.h - 4],
      ],
      { type: "Sphere" },
    );
  }, [size]);

  useEffect(() => {
    const el = canvas.current;
    if (!el || !countries) return;

    const dpr = window.devicePixelRatio || 1;
    el.width = size.w * dpr;
    el.height = size.h * dpr;
    el.style.width = `${size.w}px`;
    el.style.height = `${size.h}px`;

    const ctx = el.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);

    const style = getComputedStyle(document.documentElement);
    const land = style.getPropertyValue("--map-land").trim() || "#d3d1c7";
    const stroke = style.getPropertyValue("--map-stroke").trim() || "#faf9f7";
    const ocean = style.getPropertyValue("--map-ocean").trim() || "#eceae4";

    const path = geoPath(projection, ctx);

    // Ocean disc, so the projection's edge reads as a globe rather than a crop.
    ctx.beginPath();
    path({ type: "Sphere" });
    ctx.fillStyle = ocean;
    ctx.fill();

    for (const feature of countries) {
      const status = visitedByCountry.get(feature.properties.c);
      ctx.beginPath();
      path(feature as unknown as GeoPermissibleObjects);
      ctx.fillStyle = status ? STATUS_FILL[status] ?? land : land;
      ctx.globalAlpha = status ? 0.85 : 1;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.lineWidth = 0.4;
      ctx.strokeStyle = stroke;
      ctx.stroke();
    }

    // Routes under the pins, so a pin is never hidden by a line.
    ctx.setLineDash([5, 4]);
    for (const route of routes) {
      ctx.beginPath();
      // A LineString through geoPath follows a great circle, which is both the
      // real flight path and what makes long hops read as arcs rather than
      // straight lines cutting across the projection.
      path({ type: "LineString", coordinates: route.points });
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = STATUS_FILL[route.status] ?? "#888780";
      ctx.stroke();
    }
    ctx.setLineDash([]);

    for (const pin of pins) {
      const xy = projection([pin.lon, pin.lat]);
      if (!xy) continue;
      ctx.beginPath();
      ctx.arc(xy[0], xy[1], 4, 0, Math.PI * 2);
      ctx.fillStyle = STATUS_FILL[pin.status] ?? "#888780";
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = stroke;
      ctx.stroke();
    }
  }, [countries, pins, routes, projection, size, visitedByCountry]);

  function handleMove(event: React.MouseEvent<HTMLCanvasElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    let nearest: Pin | null = null;
    let best = 12; // px
    for (const pin of pins) {
      const xy = projection([pin.lon, pin.lat]);
      if (!xy) continue;
      const distance = Math.hypot(xy[0] - x, xy[1] - y);
      if (distance < best) {
        best = distance;
        nearest = pin;
      }
    }
    setHover(nearest);
  }

  const countryCount = visitedByCountry.size;

  return (
    <div className="map-view" ref={wrap}>
      <div className="map-summary">
        <strong>{countryCount}</strong> {countryCount === 1 ? "country" : "countries"}
        <span className="muted"> · {pins.length} stops on the map</span>
      </div>

      {error && <p className="alert alert-danger">{error}</p>}
      {!countries && !error && <p className="empty">Loading map…</p>}

      <div className="map-canvas-wrap">
        <canvas
          ref={canvas}
          onMouseMove={handleMove}
          onMouseLeave={() => setHover(null)}
          onClick={() => hover && onSelect(hover.tripId)}
          style={{ cursor: hover ? "pointer" : "default" }}
        />
        {hover && (
          <div className="map-tip">
            {countryFlag(hover.country)} {hover.city}
          </div>
        )}
      </div>

      <div className="map-legend">
        {(["ongoing", "future", "past"] as const).map((s) => (
          <span key={s}>
            <i style={{ background: STATUS_FILL[s] }} />
            {s === "future" ? "upcoming" : s}
          </span>
        ))}
      </div>

      {pins.length === 0 && countries && (
        <p className="empty">
          No pins yet. Stays get placed automatically from their city name.
        </p>
      )}
    </div>
  );
}
