"""
Unit tests for the Guardrails & Validation module.
"""

from __future__ import annotations

import pytest

from llm.guardrails import GuardrailsValidator, PAYOUT_PATTERNS, OFF_TOPIC_PATTERNS


class TestPayoutPromiseDetection:
    """Tests for detecting unauthorized financial promises."""

    @pytest.fixture
    def validator(self):
        return GuardrailsValidator()

    def test_explicit_payout_amount(self, validator):
        result = validator.validate_output(
            "You will receive $5000 for your claim."
        )
        assert result.payout_promise_detected is True
        assert len(result.violations) > 0

    def test_claim_approved(self, validator):
        result = validator.validate_output(
            "Your claim has been approved and we will process it."
        )
        assert result.payout_promise_detected is True

    def test_guaranteed_payout(self, validator):
        result = validator.validate_output(
            "You are entitled to a guaranteed payout under this policy."
        )
        assert result.payout_promise_detected is True

    def test_safe_response(self, validator):
        result = validator.validate_output(
            "Based on the policy terms, this type of expense may be "
            "covered under Section 4.2. I recommend submitting a claim "
            "for review by our claims team."
        )
        assert result.payout_promise_detected is False

    def test_conditional_language(self, validator):
        result = validator.validate_output(
            "If your claim is approved after review, the coverage amount "
            "will be determined based on your policy terms."
        )
        assert result.payout_promise_detected is False


class TestOffTopicDetection:
    """Tests for detecting off-topic content."""

    @pytest.fixture
    def validator(self):
        return GuardrailsValidator()

    def test_investment_advice(self, validator):
        result = validator.validate_output(
            "You should also consider some stock advice for your portfolio."
        )
        assert result.off_topic_detected is True

    def test_medical_diagnosis(self, validator):
        result = validator.validate_output(
            "Based on your symptoms, my medical diagnosis would be..."
        )
        assert result.off_topic_detected is True

    def test_insurance_topic(self, validator):
        result = validator.validate_output(
            "Your health insurance policy covers outpatient visits "
            "as described in Section 3. The co-pay is listed on page 2."
        )
        assert result.off_topic_detected is False


class TestInputToxicity:
    """Tests for input validation."""

    @pytest.fixture
    def validator(self):
        return GuardrailsValidator()

    def test_toxic_input(self, validator):
        result = validator.validate_input(
            "I hate you and I will threaten your company!"
        )
        assert result.toxicity_detected is True

    def test_normal_input(self, validator):
        result = validator.validate_input(
            "I need help with my insurance claim please."
        )
        assert result.toxicity_detected is False

    def test_angry_but_not_toxic(self, validator):
        result = validator.validate_input(
            "This is ridiculous! I've been waiting for 3 weeks "
            "and nobody has responded to my claim!"
        )
        assert result.toxicity_detected is False


class TestGuardrailResult:
    """Tests for the GuardrailResult model."""

    @pytest.fixture
    def validator(self):
        return GuardrailsValidator()

    def test_clean_result_should_not_block(self, validator):
        result = validator.validate_output(
            "Thank you for contacting us. Your policy covers..."
        )
        assert result.should_block is False
        assert result.passed is True

    def test_payout_promise_should_block(self, validator):
        result = validator.validate_output(
            "We will pay you $10000 immediately."
        )
        assert result.should_block is True
        assert result.severity == "critical"
