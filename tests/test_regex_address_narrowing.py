"""Tests for the ADDRESS regex's negative-lookahead narrowing.

Two requirements:
1. Labelled form-style documents (Floor No.:, Building No.:, Locality:, City:,
   State:, PIN Code:, Road/Street:, District:, Name Of) must SKIP the ADDRESS
   regex so each address line flows through GLiNER for per-line redaction.
2. Unlabelled free-form multi-line addresses ending in a valid Indian PIN must
   STILL match (no regression on the original failure mode the regex solves).
"""

from __future__ import annotations

import unittest

from redactor import regex_redactor


# A realistic labelled certificate address block. The model is expected to
# redact the geographic values per line; the regex must NOT collapse the block.
LABELLED_FORM = """INDEPENDENT PRACTITIONER'S CERTIFICATE
To,
John Doe
Floor No.: 7TH Floor
Building No./Flat No.: E-703
Name Of Premises/Building: ND Passion Elite
Road/Street: Haralur Main Road, Birla Circle,
Locality/Sub Locality: Koramangala
City/Town/Village: Bengaluru
District: Bengaluru Urban
State: Karnataka
PIN Code: 560068"""

# Unlabelled free-form multi-line address. Must still be caught by the regex.
FREE_FORM = """Flat 3B, Rosewood Apartments,
4th Cross, Indiranagar,
Bengaluru 560068"""


class AddressNarrowingTests(unittest.TestCase):
    def test_labelled_form_is_skipped(self) -> None:
        r = regex_redactor.redact(LABELLED_FORM)
        self.assertNotIn(
            "ADDRESS",
            r.counts,
            "Labelled form-style address must fall through to per-line GLiNER redaction",
        )
        # The block labels must remain intact in the output.
        self.assertIn("Floor No.:", r.text)
        self.assertIn("Locality/Sub Locality:", r.text)
        self.assertIn("PIN Code:", r.text)

    def test_free_form_address_still_matches(self) -> None:
        # PIN 560068 is in the India Post registry; this is the regression guard.
        r = regex_redactor.redact(FREE_FORM)
        self.assertEqual(r.counts, {"ADDRESS": 1})
        self.assertIn("[ADDRESS]", r.text)


if __name__ == "__main__":
    unittest.main()
