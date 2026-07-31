"""Deciding, on this box, whether a message may leave it.

This is a **privacy control**, not a cost optimisation. Most of his inbox must
never reach a third party, so the question is not "might this be travel?" but
"am I certain enough to send it away?".

That asymmetry sets the design. A missed booking costs him one hotel typed by
hand. A personal email handed to an API cannot be taken back. So:

    An unlisted sender is never a candidate, whatever the message says.

Keywords only narrow the field *within* senders already trusted -- they can
never promote a stranger. A friend forwarding "here is our hotel booking" is
correctly refused, and that is the control working, not a bug.

The rules are committed read-only data under `data/rules/`, so what shipped is
what runs, and the tests exercise the real file rather than a fixture.
"""

import json
import re
from dataclasses import dataclass
from email.utils import parseaddr
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.config import get_settings


@dataclass(frozen=True)
class FilterRules:
    allow_sender_domains: frozenset[str]
    allow_sender_addresses: frozenset[str]
    confirmation_keywords: tuple[str, ...]
    subject_deny_keywords: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    """Why a message was or was not allowed out. The reason is for the log."""

    is_candidate: bool
    reason: str

    def __bool__(self) -> bool:
        return self.is_candidate


def rules_path() -> Path:
    return get_settings().data_dir / "rules" / "email-filter.json"


@lru_cache
def load_rules(path: Optional[Path] = None) -> FilterRules:
    """Read and cache the rule set. Keys prefixed `_` are commentary."""
    target = path or rules_path()
    raw = json.loads(target.read_text(encoding="utf-8"))
    return FilterRules(
        allow_sender_domains=frozenset(
            d.strip().lower().lstrip("@") for d in raw.get("allow_sender_domains", [])
        ),
        allow_sender_addresses=frozenset(
            a.strip().lower() for a in raw.get("allow_sender_addresses", [])
        ),
        confirmation_keywords=tuple(
            k.lower() for k in raw.get("confirmation_keywords", [])
        ),
        subject_deny_keywords=tuple(
            k.lower() for k in raw.get("subject_deny_keywords", [])
        ),
    )


def sender_domain(from_addr: str) -> str:
    """The domain of a From header, whether or not it carries a display name.

    `parseaddr` handles both "Booking.com <no-reply@booking.com>" and the bare
    address; anything it cannot parse yields "", which matches nothing.
    """
    _, address = parseaddr(from_addr or "")
    _, _, domain = address.rpartition("@")
    return domain.strip().lower()


def _domain_allowed(domain: str, allowed: frozenset[str]) -> bool:
    """Exact match, or a subdomain of an allowed domain.

    The dot matters: without it "notbooking.com" satisfies a rule written for
    "booking.com", which is precisely the hole this file exists to close.
    """
    if not domain:
        return False
    return any(domain == a or domain.endswith("." + a) for a in allowed)


def _contains(haystack: str, needles: tuple[str, ...]) -> Optional[str]:
    for needle in needles:
        if needle in haystack:
            return needle
    return None


def classify(
    from_addr: str,
    subject: str,
    body: str,
    rules: Optional[FilterRules] = None,
) -> Verdict:
    """Whether this message may be sent for extraction."""
    rules = rules or load_rules()

    _, address = parseaddr(from_addr or "")
    address = address.strip().lower()
    local, _, domain = address.partition("@")
    domain = domain.strip().lower()

    # A real sender has both halves. An empty local part ("@booking.com") would
    # otherwise ride in on the domain rule; no legitimate message looks like it.
    if not local or not domain:
        return Verdict(False, f"sender_not_allowed:{domain or 'unparseable'}")

    if address not in rules.allow_sender_addresses and not _domain_allowed(
        domain, rules.allow_sender_domains
    ):
        # The load-bearing branch. Everything from here down has already been
        # decided to be a sender he books travel with.
        return Verdict(False, f"sender_not_allowed:{domain or 'unparseable'}")

    subject_norm = _normalise(subject)
    denied = _contains(subject_norm, rules.subject_deny_keywords)
    if denied:
        return Verdict(False, f"subject_denied:{denied}")

    hit = _contains(subject_norm, rules.confirmation_keywords) or _contains(
        _normalise(body), rules.confirmation_keywords
    )
    if not hit:
        return Verdict(False, "no_confirmation_keyword")

    return Verdict(True, f"allowed:{domain}:{hit}")


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()
