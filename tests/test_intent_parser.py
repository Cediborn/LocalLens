"""Parser accuracy tests for the deterministic intent layer.

Run from the repo root with:  python -m unittest tests.test_intent_parser
"""

import unittest

from intent import Intent
from intent_parser import parse_query
import intent_parser


class TestCoreCategories(unittest.TestCase):
    def test_construction_firm(self):
        i = Intent("construction firm that does residential buildings")
        self.assertEqual(i.structured.category, "construction_company")
        self.assertEqual(i.structured.subcategory, "Residential construction")
        self.assertGreaterEqual(i.confidence, 0.7)

    def test_phone_repair(self):
        i = Intent("cheap place to fix my cracked iPhone near me")
        self.assertEqual(i.structured.category, "phone_repair")
        self.assertEqual(i.structured.subcategory, "iPhone repair")
        self.assertIn("cracked", i.keywords)
        self.assertEqual(i.budget_hint, "low")
        self.assertEqual(i.distance_hint, "close")

    def test_hotel_near_airport(self):
        i = Intent("hotel under GHc300 near the airport")
        self.assertEqual(i.structured.category, "hotel")
        self.assertEqual(i.structured.subcategory, "Near airport")
        self.assertEqual(i.budget, 300.0)
        self.assertEqual(i.currency, "GHS")
        loc = i.structured.location
        self.assertEqual(loc.get("type"), "named_place")
        self.assertEqual(loc.get("query"), "airport")
        self.assertEqual(i.distance_hint, "close")

    def test_budget_formats_equivalent(self):
        a = parse_query("hotel under GHc300")
        b = parse_query("hotel under GHc 300")
        c = parse_query("hotel under GHS300")
        for p in (a, b, c):
            self.assertEqual(p.budget_amount, 300.0)
            self.assertEqual(p.currency, "GHS")

    def test_plumber(self):
        i = Intent("need a plumber")
        self.assertEqual(i.structured.category, "plumber")
        self.assertFalse(i.ambiguous)

    def test_leaking_roof_hedged(self):
        i = Intent("plumber to fix leaking roof")
        self.assertIn("roofer", i.categories)
        self.assertIn("plumber", i.categories)
        self.assertTrue(i.ambiguous)
        self.assertEqual(i.structured.category, "roofer")

    def test_pharmacy_no_keyword_leak(self):
        i = Intent("pharmacy")
        self.assertEqual(i.structured.category, "pharmacy")
        self.assertEqual(i.keywords, [])

    def test_driving_school_school_token_not_boosted(self):
        i = Intent("driving school")
        self.assertTrue(i.structured.ambiguous)
        self.assertIn("driving_school", i.categories)

    def test_stem_not_overmatching(self):
        # "roof" is a roofer synonym (bare token) - that's intended. The stem
        # matcher regression is below: "fix leaking roof" must stay hedged.
        i = Intent("roof")
        self.assertEqual(i.structured.category, "roofer")

    def test_exact_roofer_phrase(self):
        i = Intent("roofer")
        self.assertEqual(i.structured.category, "roofer")

    def test_jollof_restaurant(self):
        i = Intent("place to eat jollof")
        self.assertEqual(i.structured.category, "restaurant")
        self.assertEqual(i.structured.subcategory, "Jollof")

    def test_apple_brand_ambiguity(self):
        i = Intent("apple")
        self.assertTrue(i.ambiguous)
        self.assertIn("phone_shop", i.categories)

    def test_somewhere_to_sleep(self):
        i = Intent("somewhere to sleep tonight")
        self.assertEqual(i.structured.category, "hotel")

    def test_structured_payload_bridge(self):
        i = Intent("hotel under GHc300 near the airport")
        structured = i.to_structured()
        for key in ("category", "subcategory", "keywords", "budget",
                    "distance", "location", "rating", "confidence",
                    "ambiguous", "specific", "distance_hint"):
            self.assertIn(key, structured)


class TestBudgetExtraction(unittest.TestCase):
    def test_ghs_numeric(self):
        self.assertEqual(parse_query("restaurant under GHS120").budget_amount, 120.0)

    def test_gh_c_prefix(self):
        self.assertEqual(parse_query("hotel under GHc50").budget_amount, 50.0)

    def test_cedis(self):
        p = parse_query("something under 30 cedis")
        self.assertEqual(p.budget_amount, 30.0)
        self.assertEqual(p.currency, "GHS")


class TestCurrencyDetect(unittest.TestCase):
    def test_symbol_detection(self):
        self.assertEqual(intent_parser.detect_currency("under $20"), "USD")
        self.assertEqual(intent_parser.detect_currency("under GHc20"), "GHS")


if __name__ == "__main__":
    unittest.main()