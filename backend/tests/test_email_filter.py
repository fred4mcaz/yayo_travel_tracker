"""Phase 2: the privacy boundary.

The filter's job is to keep the wrong mail on the box. So the tests that matter
most are the ones proving a message is *refused* -- an unlisted sender, a
lookalike domain, a friend forwarding a booking. Those are the failure modes
that leak personal mail to a third party.

The rules are exercised through the real committed file, not a fixture, so a
careless edit to `data/rules/email-filter.json` shows up here.
"""

import pytest

from app.services.email_filter import (
    FilterRules,
    classify,
    load_rules,
    sender_domain,
)

# A small explicit rule set for the logic tests, so they do not shift when the
# shipped allow-list grows. The real file is exercised separately below.
RULES = FilterRules(
    allow_sender_domains=frozenset({"booking.com", "vietnamairlines.com"}),
    allow_sender_addresses=frozenset({"trips@friend.example"}),
    confirmation_keywords=("confirmation", "your booking", "itinerary"),
    subject_deny_keywords=("newsletter", "% off", "survey"),
)


def allowed(from_addr, subject="Your booking confirmation", body="Details inside"):
    return classify(from_addr, subject, body, RULES)


# --------------------------------------------------------------------------
# Refusals -- the whole point of the filter
# --------------------------------------------------------------------------


def test_unlisted_sender_is_refused_even_when_it_looks_like_a_booking():
    v = classify(
        "friend@gmail.com",
        "Fwd: our hotel booking confirmation",
        "Here's the itinerary for our trip, see you there!",
        RULES,
    )
    assert not v
    assert v.reason == "sender_not_allowed:gmail.com"


def test_a_friend_forwarding_a_real_confirmation_is_still_refused():
    """The wording is perfect; the sender is a person. Refused, by design."""
    v = classify(
        "pal@fastmail.com",
        "Booking confirmation - Sofitel Legend Metropole Hanoi",
        "Reservation 4471, check-in 30 Aug. Forwarding you the itinerary.",
        RULES,
    )
    assert not v


def test_lookalike_domain_does_not_satisfy_an_allowed_domain():
    """"notbooking.com" must not ride in on the "booking.com" rule."""
    assert not allowed("no-reply@notbooking.com")
    assert not allowed("no-reply@booking.com.phish.ru")


def test_allowed_domain_as_a_bare_substring_is_not_enough():
    assert not allowed("hello@mybooking.com")


def test_unparseable_from_header_is_refused():
    v = allowed("")
    assert not v
    assert "unparseable" in v.reason


# --------------------------------------------------------------------------
# Within trusted senders, keywords narrow the field
# --------------------------------------------------------------------------


def test_trusted_sender_with_confirmation_wording_is_allowed():
    v = allowed("no-reply@booking.com")
    assert v
    assert v.reason.startswith("allowed:booking.com")


def test_trusted_sender_matched_by_display_name_header():
    assert allowed("Booking.com <no-reply@booking.com>")


def test_trusted_subdomain_is_allowed():
    assert allowed("noreply@mail.booking.com")


def test_explicit_address_allowlist_is_honoured():
    assert classify("trips@friend.example", "Your booking", "x", RULES)


def test_trusted_sender_without_any_confirmation_keyword_is_refused():
    """An allowed sender's non-booking mail should stay on the box too."""
    v = classify("no-reply@booking.com", "Welcome to Booking.com", "Hi there", RULES)
    assert not v
    assert v.reason == "no_confirmation_keyword"


def test_confirmation_keyword_in_the_body_counts():
    v = classify("no-reply@booking.com", "Update", "Your booking is confirmed", RULES)
    assert v


# --------------------------------------------------------------------------
# Denials veto even a trusted sender with a confirmation keyword
# --------------------------------------------------------------------------


def test_marketing_subject_is_denied_despite_a_confirmation_word():
    v = classify(
        "no-reply@booking.com",
        "Your booking deals newsletter - 20% off",
        "Confirmation of nothing",
        RULES,
    )
    assert not v
    assert v.reason.startswith("subject_denied:")


def test_denials_are_checked_against_the_subject_not_the_body():
    """Real confirmations carry an unsubscribe/newsletter footer in the body;
    matching denials there would veto the mail we want."""
    v = classify(
        "no-reply@booking.com",
        "Your booking confirmation",
        "Reservation 12. To stop our newsletter, unsubscribe here.",
        RULES,
    )
    assert v


def test_matching_is_case_and_whitespace_insensitive():
    assert allowed("NO-REPLY@BOOKING.COM", subject="YOUR   BOOKING\nCONFIRMATION")


# --------------------------------------------------------------------------
# sender_domain helper
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        ("no-reply@booking.com", "booking.com"),
        ("Booking <no-reply@Booking.com>", "booking.com"),
        # No @ at all: the whole string parses as a local part, leaving no
        # domain to match. Whatever it is, it is not an allowed sender.
        ("garbage", "garbage"),
        ("", ""),
        # Malformed multi-@ addresses fail RFC parsing outright -> empty, which
        # also matches nothing. Both odd inputs land on a safe refusal.
        ("a@b@c.com", ""),
    ],
)
def test_sender_domain(header, expected):
    assert sender_domain(header) == expected


def test_odd_sender_headers_are_refused_whatever_they_parse_to():
    for header in ("garbage", "a@b@c.com", "@booking.com"):
        assert not classify(header, "Your booking confirmation", "x", RULES)


# --------------------------------------------------------------------------
# The shipped rules file must load and behave
# --------------------------------------------------------------------------


def test_committed_rules_file_loads():
    load_rules.cache_clear()
    rules = load_rules()
    assert "booking.com" in rules.allow_sender_domains
    assert rules.confirmation_keywords
    assert rules.subject_deny_keywords
    # Commentary keys (prefixed _) must not leak in as domains.
    assert not any(d.startswith("_") for d in rules.allow_sender_domains)


def test_a_real_looking_booking_passes_the_shipped_rules():
    load_rules.cache_clear()
    v = classify(
        "Booking.com <no-reply@booking.com>",
        "Your booking is confirmed - Hanoi",
        "Booking reference 4471. Check-in 30 August 2026.",
    )
    assert v


def test_a_personal_email_fails_the_shipped_rules():
    load_rules.cache_clear()
    assert not classify(
        "mum@gmail.com",
        "call me when you land",
        "have a safe trip sweetheart",
    )


def test_ticket_only_subject_from_an_allowed_sender_passes_the_shipped_rules():
    """redBus and similar operators confirm with "ticket", not "booking" or
    "reservation" -- broadened here so that wording alone doesn't sink them."""
    load_rules.cache_clear()
    v = classify(
        "ticketmaster@redbus.sg",
        "Your ferry ticket - Batam to Malaysia",
        "PNR RB998877",
    )
    assert v
