import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ReviewQueue } from "./Review";
import { api } from "../lib/api";
import type { RecentEmail, ReviewItem } from "../types";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      review: {
        ...actual.api.review,
        list: vi.fn(),
        accept: vi.fn(),
        reject: vi.fn(),
        recentEmails: vi.fn(),
        extractEmail: vi.fn(),
      },
    },
  };
});

afterEach(cleanup);

const BOOKING_ITEM: ReviewItem = {
  id: 1,
  kind: "booking",
  status: "pending",
  model: "claude-sonnet-5",
  confidence: 0.9,
  created_at: "2026-08-01T00:00:00",
  email: {
    id: 1,
    from_addr: "no-reply@booking.com",
    subject: "Your booking is confirmed",
    snippet: "Reservation 4471",
    received_at: "2026-08-01T00:00:00",
  },
  booking: {
    kind: "hotel",
    country_code: "VN",
    country_name: "Vietnam",
    city: "Hanoi",
    start_date: "2026-09-10",
    end_date: "2026-09-14",
    hotel_name: "Sofitel Legend",
    carrier: null,
    confirmation_code: "4471",
  },
  immigration: null,
  suggestion: null,
};

const FLIGHT_ITEM: ReviewItem = {
  id: 3,
  kind: "booking",
  status: "pending",
  model: "claude-sonnet-5",
  confidence: 0.9,
  created_at: "2026-09-05T00:00:00",
  email: {
    id: 3,
    from_addr: "pegasus@flypgs.com",
    subject: "PC1162 Astana: Your booking is confirmed!",
    snippet: "PNR 2DS3JN",
    received_at: "2026-09-05T00:00:00",
  },
  booking: {
    kind: "flight",
    country_code: "KZ",
    country_name: "Kazakhstan",
    city: "Astana",
    start_date: "2026-09-29",
    end_date: null,
    hotel_name: null,
    carrier: "Pegasus",
    confirmation_code: "2DS3JN",
    flight_numbers: ["PC1162", "PC228"],
    from_place: "London-Stansted",
    from_iata: "STN",
    to_place: "Astana",
    to_iata: "NQZ",
    depart_at: "2026-09-29T14:40:00",
    arrive_at: "2026-09-30T04:45:00",
    seat: "10A, 11A",
  },
  immigration: null,
  suggestion: null,
};

const IMMIGRATION_ITEM: ReviewItem = {
  id: 2,
  kind: "immigration",
  status: "pending",
  model: "",
  confidence: null,
  created_at: "2026-09-11T09:00:00",
  email: {
    id: 2,
    from_addr: "no-reply@imigrasi.go.id",
    subject: "Your Indonesia Arrival Card is confirmed",
    snippet: "Your e-CD reference is ECD-123456.",
    received_at: "2026-09-11T09:00:00",
  },
  booking: null,
  immigration: { requirement_kind: "entry_card", nationality: null, reference: "" },
  suggestion: { trip_id: 7, label: "Batam · Oakwood Grand Batam" },
};

describe("ReviewQueue", () => {
  it("renders a booking proposal and an immigration proposal distinctly", async () => {
    vi.mocked(api.review.list).mockResolvedValue([BOOKING_ITEM, IMMIGRATION_ITEM]);

    render(<ReviewQueue onReviewed={vi.fn()} />);

    await waitFor(() => expect(screen.getByDisplayValue("Sofitel Legend")).toBeTruthy());
    expect(screen.getByText("Immigration")).toBeTruthy();
    expect(screen.getByText(/Arrival card confirmation/)).toBeTruthy();
    expect(screen.getByText(/Batam · Oakwood Grand Batam/)).toBeTruthy();
  });

  it("shows the captured leg detail on a flight proposal", async () => {
    vi.mocked(api.review.list).mockResolvedValue([FLIGHT_ITEM]);

    render(<ReviewQueue onReviewed={vi.fn()} />);

    // The whole point of the feature: flight numbers, route, times, and seat
    // are visible on the proposal, not silently dropped.
    await waitFor(() => expect(screen.getByText("PC1162, PC228")).toBeTruthy());
    expect(screen.getByText(/London-Stansted \(STN\)/)).toBeTruthy();
    expect(screen.getByText(/Astana \(NQZ\)/)).toBeTruthy();
    expect(screen.getByText("10A, 11A")).toBeTruthy();
    expect(screen.getByText(/Sep 29 14:40/)).toBeTruthy();
    expect(screen.getByText(/Sep 30 04:45/)).toBeTruthy();
  });

  it("accepting an immigration proposal sends the typed-in reference", async () => {
    vi.mocked(api.review.list).mockResolvedValue([IMMIGRATION_ITEM]);
    vi.mocked(api.review.accept).mockResolvedValue({
      accepted: true,
      trip_id: 7,
      created_new_trip: false,
      stay_id: null,
      leg_id: null,
      requirement_id: 42,
      learned_domain: null,
    });

    render(<ReviewQueue onReviewed={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("Immigration")).toBeTruthy());

    fireEvent.change(screen.getByPlaceholderText("e.g. ECD-123456"), {
      target: { value: "ECD-99887766" },
    });
    fireEvent.click(screen.getByText("Accept"));

    await waitFor(() =>
      expect(api.review.accept).toHaveBeenCalledWith(2, { reference: "ECD-99887766" }),
    );
  });

  it("dismissing an immigration proposal calls reject with its id", async () => {
    vi.mocked(api.review.list).mockResolvedValue([IMMIGRATION_ITEM]);
    vi.mocked(api.review.reject).mockResolvedValue({ rejected: true });

    render(<ReviewQueue onReviewed={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("Immigration")).toBeTruthy());

    fireEvent.click(screen.getByText("Dismiss"));

    await waitFor(() => expect(api.review.reject).toHaveBeenCalledWith(2));
  });
});

const RECENT_EMAIL: RecentEmail = {
  id: 30,
  from_addr: "no-reply@imigrasi.go.id",
  subject: "Your Indonesia Arrival Card is confirmed",
  snippet: "Your e-CD reference is ECD-123456.",
  received_at: "2026-09-11T09:00:00",
  looks_like_travel: false,
  looks_like_immigration: true,
  has_pending: false,
};

describe("RecentEmailsPanel", () => {
  it("extracting 'as immigration doc' calls extractEmail with kind=immigration", async () => {
    vi.mocked(api.review.list).mockResolvedValue([]);
    vi.mocked(api.review.recentEmails).mockResolvedValue([RECENT_EMAIL]);
    vi.mocked(api.review.extractEmail).mockResolvedValue([]);

    render(<ReviewQueue onReviewed={vi.fn()} />);
    fireEvent.click(await screen.findByText("Find recent emails"));

    await waitFor(() =>
      expect(screen.getByText("Your Indonesia Arrival Card is confirmed")).toBeTruthy(),
    );
    // The local immigration classifier flagged this one -- shown so the
    // reviewer knows without opening the email.
    expect(screen.getByText("Gov mail")).toBeTruthy();

    fireEvent.click(screen.getByText("As immigration doc"));

    await waitFor(() =>
      expect(api.review.extractEmail).toHaveBeenCalledWith(30, "immigration"),
    );
  });

  it("the plain Extract button still defaults to kind=booking", async () => {
    vi.mocked(api.review.list).mockResolvedValue([]);
    vi.mocked(api.review.recentEmails).mockResolvedValue([
      { ...RECENT_EMAIL, looks_like_immigration: false, looks_like_travel: true },
    ]);
    vi.mocked(api.review.extractEmail).mockResolvedValue([]);

    render(<ReviewQueue onReviewed={vi.fn()} />);
    fireEvent.click(await screen.findByText("Find recent emails"));
    await waitFor(() => expect(screen.getByText("Extract")).toBeTruthy());

    fireEvent.click(screen.getByText("Extract"));

    await waitFor(() =>
      expect(api.review.extractEmail).toHaveBeenCalledWith(30, "booking"),
    );
  });
});
