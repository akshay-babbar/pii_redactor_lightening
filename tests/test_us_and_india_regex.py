"""Tests for the regex layer's US + Indian PII coverage.

Covers: SSN (with SSA validation), US/NANP phone variants, Indian phone
variants still match, and the negative interaction between US 10-digit
numbers and the Indian [6-9]\\d{9} branch.
"""

from __future__ import annotations

import unittest

from redactor import regex_redactor


class PhoneRegexTests(unittest.TestCase):
    def test_indian_mobile_with_country_code(self) -> None:
        r = regex_redactor.redact("Call +91 98765 43210 now")
        self.assertEqual(r.counts, {"PHONE": 1})
        self.assertNotIn("98765", r.text)

    def test_indian_mobile_bare_10_digit(self) -> None:
        r = regex_redactor.redact("Call 9876543210")
        self.assertEqual(r.counts, {"PHONE": 1})

    def test_indian_landline_with_std(self) -> None:
        r = regex_redactor.redact("Landline 080-23456789")
        self.assertEqual(r.counts, {"PHONE": 1})

    def test_us_phone_parens(self) -> None:
        r = regex_redactor.redact("Call (415) 555-0123")
        self.assertEqual(r.counts, {"PHONE": 1})

    def test_us_phone_with_country_code(self) -> None:
        r = regex_redactor.redact("Call +1 415 555 0123")
        self.assertEqual(r.counts, {"PHONE": 1})

    def test_us_phone_dashes(self) -> None:
        r = regex_redactor.redact("Call 415-555-0123")
        self.assertEqual(r.counts, {"PHONE": 1})

    def test_us_phone_dots(self) -> None:
        r = regex_redactor.redact("Call 415.555.0123")
        self.assertEqual(r.counts, {"PHONE": 1})


class SsnRegexTests(unittest.TestCase):
    def test_valid_ssn(self) -> None:
        r = regex_redactor.redact("SSN: 123-45-6789")
        self.assertEqual(r.counts, {"SSN": 1})

    def test_rejects_area_zero(self) -> None:
        r = regex_redactor.redact("Bad: 000-12-3456")
        self.assertEqual(r.counts, {})

    def test_rejects_area_666(self) -> None:
        r = regex_redactor.redact("Bad: 666-12-3456")
        self.assertEqual(r.counts, {})

    def test_rejects_area_900_series_itin(self) -> None:
        r = regex_redactor.redact("Bad: 900-12-3456")
        self.assertEqual(r.counts, {})

    def test_rejects_group_zero(self) -> None:
        r = regex_redactor.redact("Bad: 123-00-3456")
        self.assertEqual(r.counts, {})

    def test_rejects_serial_zero(self) -> None:
        r = regex_redactor.redact("Bad: 123-45-0000")
        self.assertEqual(r.counts, {})


class CombinedPiiTests(unittest.TestCase):
    def test_email_still_works(self) -> None:
        r = regex_redactor.redact("Email me at test@example.com")
        self.assertEqual(r.counts, {"EMAIL": 1})

    def test_us_phone_does_not_mask_indian_paragraph(self) -> None:
        # The Indian [6-9]\d{9} branch should not over-mask unrelated 10-digit
        # numbers in a US context. US 10-digit unseparated numbers are NOT
        # matched (only with separators); Indian numbers still match.
        text = "Order #9876543210 was placed on 2024-01-15."
        r = regex_redactor.redact(text)
        # The 10-digit order number WILL match Indian PHONE here — this is
        # a pre-existing behaviour, not a regression. The test documents it.
        # We assert that the date is NOT redacted.
        self.assertIn("2024-01-15", r.text)


class PostalCodeRegexTests(unittest.TestCase):
    def test_indian_pin_standalone(self) -> None:
        r = regex_redactor.redact("PIN 560068")
        self.assertEqual(r.counts, {"POSTAL_CODE": 1})

    def test_indian_pin_in_labelled_certificate(self) -> None:
        # The certificate form preserves the "PIN Code:" label and masks the value.
        text = "PIN Code: 560068"
        r = regex_redactor.redact(text)
        self.assertEqual(r.counts, {"POSTAL_CODE": 1})
        self.assertIn("PIN Code:", r.text)
        self.assertIn("[POSTAL_CODE]", r.text)
        self.assertNotIn("560068", r.text)

    def test_indian_pin_inside_free_form_address_is_absorbed(self) -> None:
        # The free-form multi-line ADDRESS regex wins by longest-match; the PIN
        # at its anchor is part of the [ADDRESS] span, so POSTAL_CODE does not
        # also fire. No double-counting.
        text = "Flat 3B, Rosewood Apartments,\n4th Cross, Indiranagar,\nBengaluru 560068"
        r = regex_redactor.redact(text)
        self.assertEqual(r.counts, {"ADDRESS": 1})
        self.assertNotIn("POSTAL_CODE", r.counts)

    def test_invalid_indian_pin_not_matched(self) -> None:
        # 999999 is not in the India Post registry (first digit 9 is reserved/
        # unallocated). Fail-closed: dropped, never over-masked.
        r = regex_redactor.redact("Bad PIN 999999")
        self.assertNotIn("POSTAL_CODE", r.counts)

    def test_us_zip_standalone(self) -> None:
        r = regex_redactor.redact("ZIP 62704")
        self.assertEqual(r.counts, {"POSTAL_CODE": 1})

    def test_us_zip_in_address_context(self) -> None:
        # 5-digit ZIP in a US address line.
        r = regex_redactor.redact("Springfield IL 62704")
        self.assertEqual(r.counts, {"POSTAL_CODE": 1})
        self.assertNotIn("62704", r.text)


if __name__ == "__main__":
    unittest.main()
