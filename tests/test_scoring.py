"""Relevance-gate and hierarchy tests for the deterministic scoring layer.

Run from the repo root with:  python -m unittest tests.test_scoring
"""

import unittest

from scoring import (compute_relevance_score, relevance_pass, score_place,
                     RELEVANCE_GATE)


def make_place(name, amenities, tags=None):
    tags = tags or {}
    full_tags = {"amenity": amenities, "name": name}
    full_tags.update(tags)
    full_tags["description"] = name
    return {"id": name, "name": name, "lat": 5.6, "lon": -0.18,
            "tags": full_tags, "category_label": name}


class TestRelevanceGate(unittest.TestCase):
    def test_mismatch_sinks(self):
        # A pharmacy is irrelevant to a construction query.
        place = make_place("Ashanti Pharmacy", "pharmacy")
        score, labels = compute_relevance_score(place, ["construction_company"], [])
        self.assertEqual(score, 0.15)
        self.assertFalse(relevance_pass(score, labels, specific=True))

    def test_direct_match_passes(self):
        place = make_place("Quick Fix Plumbers", "plumber")
        score, labels = compute_relevance_score(place, ["plumber"], [])
        self.assertGreaterEqual(score, RELEVANCE_GATE)
        self.assertTrue(relevance_pass(score, labels, specific=True))

    def test_family_match_ignores_extras(self):
        # A mosque is not a "church" (wrong religion extra), but family
        # matching still recognises it as a place of worship (0.70).
        place = make_place("Central Mosque", "place_of_worship",
                           {"religion": "muslim", "denomination": "sunni"})
        score, labels = compute_relevance_score(place, ["church"], [])
        self.assertEqual(score, 0.70)
        self.assertTrue(labels)
        self.assertTrue(relevance_pass(score, labels, specific=True))

    def test_direct_match_with_keyword_boost(self):
        place = make_place("iPhone Repair Shop", "electronics_store",
                           {"craft": "electronics_repair", "name": "iPhone Repair Shop",
                            "cuisine": "iphone"})
        score, _ = compute_relevance_score(
            place, ["phone_repair"], [], keyword_tokens=["iphone", "cracked"])
        # "iphone" in cuisine boosts; "cracked" isn't in the place text.
        self.assertGreater(score, 0.88)

    def test_unknown_atom_family_falls_through(self):
        # Specific intent with an unmapped category must not hard-pass.
        place = make_place("Corner Pharmacy", "pharmacy")
        _, labels = compute_relevance_score(place, ["taxidermist_shop"], [])
        self.assertFalse(relevance_pass(0.7, labels, specific=True))

    def test_vague_query_neutral(self):
        # No derived intent -> neutral, label-less (old refine behaviour),
        # and broad/vague queries are exempt from the hard gate.
        place = make_place("Some Place", "cafe")
        score, labels = compute_relevance_score(place, [], [])
        self.assertEqual(score, 0.7)
        self.assertEqual(labels, [])
        self.assertTrue(relevance_pass(score, labels, specific=False))

    def test_interest_based_relevance(self):
        place = make_place("Accra Kitchen", "restaurant")
        score, labels = compute_relevance_score(place, [], ["food"])
        self.assertGreaterEqual(score, 0.85)
        self.assertTrue(labels)

    def test_gate_blocks_low_relevance_but_high_quality(self):
        # 4.9-star pharmacy must not pass a specific construction intent.
        place = make_place("Top Rated Pharmacy", "pharmacy")
        score, labels = compute_relevance_score(place, ["construction_company"], [])
        self.assertFalse(relevance_pass(score, labels, specific=True))


class TestScorePlace(unittest.TestCase):
    def test_score_place_accepts_relevance_keywords(self):
        place = make_place("FixIt iPhone Repair", "electronics_store",
                           {"craft": "electronics_repair", "name": "FixIt iPhone Repair",
                            "cuisine": "iphone"})
        scored = score_place(
            place,
            {"lat": 5.6, "lon": -0.18},
            max_distance_m=5000,
            available_hours=4,
            budget_amount=50,
            currency="GHS",
            alone=True,
            interests=[],
            intent_categories=["phone_repair"],
            relevance_keywords=["iphone", "cracked"])
        self.assertGreater(scored.breakdown["relevance_score"], 0.0)

    def test_score_place_relevance_gate_field(self):
        place = make_place("Bus Stop Kiosk", "bus_station")
        scored = score_place(
            place,
            {"lat": 5.6, "lon": -0.18},
            max_distance_m=5000,
            available_hours=4,
            budget_amount=50,
            currency="GHS",
            alone=True,
            interests=[],
            intent_categories=["construction_company"])
        self.assertEqual(scored.breakdown["relevance_score"], 0.15)


if __name__ == "__main__":
    unittest.main()