"""
LocalLens - Query intent resolution (facade).

This module is a thin, backward-compatible facade over ``intent_parser``.
The real parser lives in ``intent_parser.py``; this keeps the old
``Intent`` class + ``hour_from_hint`` API stable so existing callers
(``app.py``) keep working while the smarter interpretation is used.

Everything is deterministic and runs locally - no AI calls are made here.
"""

from __future__ import annotations

from typing import Optional

from intent_parser import (  # noqa: F401  (re-export for back-compat)
    SearchIntent,
    detect_currency,
    expand_categories,
    hour_from_hint,
    parse_query,
)

# Currency symbol table kept for compatibility with older consumers.
CURRENCY_SYMBOL = {
    "GHS": "GH\u20b5", "USD": "$", "EUR": "\u20ac", "GBP": "\u00a3",
    "NGN": "\u20a6", "KES": "KSh", "ZAR": "R", "AUD": "A$", "CAD": "C$",
    "INR": "\u20b9", "JPY": "\u00a5",
}


class Intent:
    __slots__ = (
        "query", "structured", "categories", "budget", "currency",
        "budget_hint", "open_now", "time_hint", "distance_hint", "sort",
        "start_time", "alone", "time_available_hours", "max_distance_km",
    )

    def __init__(self, query: str):
        self.query = query or ""
        self.structured = parse_query(self.query)
        si = self.structured

        self.categories = list(si.categories)
        self.currency = si.currency
        self.budget = si.budget_amount
        self.budget_hint = si.budget_hint
        self.open_now = si.open_now
        self.time_hint = si.time_hint

        dpref = si.distance.get("preference")
        if dpref in ("nearby", "moderate"):
            self.distance_hint = "close"
        elif dpref == "far":
            self.distance_hint = "far"
        else:
            self.distance_hint = None

        self.sort = si.sort
        self.start_time = None
        self.alone = si.alone
        self.time_available_hours = si.time_available_hours

        dmax = si.distance.get("max_distance")
        if dmax is not None and dmax > 0:
            self.max_distance_km = min(dmax, 50.0)
        elif self.distance_hint == "far":
            self.max_distance_km = 10.0
        elif self.distance_hint == "close":
            self.max_distance_km = 3.0
        else:
            self.max_distance_km = 5.0

    @property
    def specific(self) -> bool:
        return self.structured.specific

    @property
    def ambiguous(self) -> bool:
        return self.structured.ambiguous

    @property
    def subcategory(self) -> Optional[str]:
        return self.structured.subcategory

    @property
    def subcategory_keywords(self) -> list:
        return list(self.structured.subcategory_keywords)

    @property
    def keywords(self) -> list:
        return list(self.structured.keywords)

    @property
    def confidence(self) -> float:
        return self.structured.confidence

    @property
    def location(self) -> dict:
        return self.structured.location

    def to_overrides(self) -> dict:
        """Return an overrides dict merged over explicit frontend controls."""
        overrides = {
            "categories": list(self.categories),
            "currency": self.currency,
            "budget": self.budget,
            "budget_hint": self.budget_hint,
            "open_now": self.open_now,
            "time_hint": self.time_hint,
            "sort": self.sort,
            "distance_hint": self.distance_hint,
        }
        if self.budget_hint == "free":
            overrides["budget"] = 0
        return overrides

    def to_structured(self) -> dict:
        """Full structured-intent JSON with the compatibility bridge fields."""
        out = self.structured.to_structured()
        out["specific"] = self.specific
        out["distance_hint"] = self.distance_hint
        return out


def all_american_keywords() -> list:
    """Kept for documentation/tests; now sourced from the new parser layer."""
    from intent_parser import CATEGORY_SYNONYMS, SUBCATEGORY_SYNONYMS, USECASES
    words = set()
    for phrases in CATEGORY_SYNONYMS.values():
        words.update(phrases)
    for subcat in SUBCATEGORY_SYNONYMS.values():
        for _, phrases in subcat:
            words.update(phrases)
    for usecase in USECASES.values():
        words.update(usecase)
    return sorted(words)