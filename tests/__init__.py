"""LocalLens test suite for the deterministic NL intent layer."""

import unittest

from tests.test_intent_parser import TestCoreCategories, TestBudgetExtraction, TestCurrencyDetect  # noqa: F401
from tests.test_scoring import (TestRelevanceGate, TestScorePlace)  # noqa: F401

if __name__ == "__main__":
    unittest.main()