"""G1 check (D21): a reviewer fail whose quote has any part shorter than CITE_MIN is not a verdict."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CARD = ("TASK: Fix add\nINTENT: Return the sum.\nFILE: calc.py  SYMBOL: add\nCHECK: tests -> ok (exit 0)\n"
        "GATE: paths inside task files: yes; diff characters: 40\n"
        "BEFORE:\ndef add(a, b):\n    return a - b\nAFTER:\ndef add(a, b):\n    return a + b\n")


def fail_quoting(quote: str) -> dict:
    return {"verdict": "fail", "reasons": ["wrong result"], "quote": quote}


class CitationPartsTests(unittest.TestCase):
    def test_a_short_part_is_rejected_even_when_the_total_is_long_enough(self):
        from local_memory_lab.team.steps import CITE_MIN, require_citation
        validate = require_citation(CARD)
        short = "a"  # a substring of a card line, shorter than CITE_MIN
        self.assertLess(len(short), CITE_MIN)
        with self.assertRaises(ValueError):
            validate(fail_quoting("return a + b\n" + short))

    def test_quotes_of_whole_card_lines_still_count(self):
        from local_memory_lab.team.steps import require_citation
        validate = require_citation(CARD)
        validate(fail_quoting("return a + b\ndef add(a, b):"))


if __name__ == "__main__":
    unittest.main()
