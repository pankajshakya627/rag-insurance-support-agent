"""
Unit tests for the PII Redactor Lambda.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from lambdas.preprocessing.pii_redactor import (
    INSURANCE_PII_PATTERNS,
    _redact_with_regex,
    restore_pii,
)


class TestRegexPIIRedaction:
    """Tests for the regex-based PII detection layer."""

    def test_ssn_detection(self):
        text = "My SSN is 123-45-6789 and I need help."
        redacted, mapping = _redact_with_regex(text)
        assert "123-45-6789" not in redacted
        assert any("SSN" in k for k in mapping)

    def test_credit_card_detection(self):
        text = "Card number: 4111-2222-3333-4444"
        redacted, mapping = _redact_with_regex(text)
        assert "4111-2222-3333-4444" not in redacted
        assert any("CREDIT_CARD" in k for k in mapping)

    def test_policy_number_detection(self):
        text = "My policy number is POL-12345678."
        redacted, mapping = _redact_with_regex(text)
        assert "POL-12345678" not in redacted
        assert any("POLICY_NUMBER" in k for k in mapping)

    def test_claim_number_detection(self):
        text = "Regarding claim CLM-987654321."
        redacted, mapping = _redact_with_regex(text)
        assert "CLM-987654321" not in redacted
        assert any("CLAIM_NUMBER" in k for k in mapping)

    def test_email_detection(self):
        text = "Contact me at john.doe@example.com please."
        redacted, mapping = _redact_with_regex(text)
        assert "john.doe@example.com" not in redacted
        assert any("EMAIL" in k for k in mapping)

    def test_phone_detection(self):
        text = "My phone is (555) 123-4567."
        redacted, mapping = _redact_with_regex(text)
        assert "(555) 123-4567" not in redacted
        assert any("PHONE" in k for k in mapping)

    def test_no_pii(self):
        text = "I would like to know about your health insurance plans."
        redacted, mapping = _redact_with_regex(text)
        assert redacted == text
        assert len(mapping) == 0

    def test_multiple_pii(self):
        text = "SSN: 111-22-3333, Policy: POL-11111111"
        redacted, mapping = _redact_with_regex(text)
        assert "111-22-3333" not in redacted
        assert "POL-11111111" not in redacted
        assert len(mapping) >= 2


class TestPIIRestoration:
    """Tests for restoring PII in approved responses."""

    def test_restore_single(self):
        text = "Your policy [POLICY_NUMBER_0] is active."
        mapping = {"[POLICY_NUMBER_0]": "POL-12345678"}
        restored = restore_pii(text, mapping)
        assert restored == "Your policy POL-12345678 is active."

    def test_restore_multiple(self):
        text = "Dear [NAME_0], your policy [POLICY_NUMBER_1] is active."
        mapping = {
            "[NAME_0]": "John Doe",
            "[POLICY_NUMBER_1]": "POL-99999999",
        }
        restored = restore_pii(text, mapping)
        assert "John Doe" in restored
        assert "POL-99999999" in restored

    def test_restore_empty_mapping(self):
        text = "No PII here."
        restored = restore_pii(text, {})
        assert restored == text

    def test_restore_preserves_unmatched(self):
        text = "Hello [UNKNOWN_0], your account is active."
        mapping = {"[NAME_0]": "Jane"}
        restored = restore_pii(text, mapping)
        assert "[UNKNOWN_0]" in restored  # Not in mapping, so kept as-is
