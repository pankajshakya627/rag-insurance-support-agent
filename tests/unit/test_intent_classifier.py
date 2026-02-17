"""
Unit tests for the Intent Classifier Lambda.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from schemas.classification import IntentClassification, IntentType


class TestIntentClassification:
    """Tests for the IntentClassification model."""

    def test_general_inquiry_auto_eligible(self):
        classification = IntentClassification(
            intent=IntentType.GENERAL_INQUIRY,
            confidence=0.95,
        )
        assert classification.is_auto_eligible is True

    def test_general_inquiry_low_confidence_not_eligible(self):
        classification = IntentClassification(
            intent=IntentType.GENERAL_INQUIRY,
            confidence=0.85,
        )
        assert classification.is_auto_eligible is False

    def test_complaint_never_auto_eligible(self):
        classification = IntentClassification(
            intent=IntentType.COMPLAINT_MISSELLING,
            confidence=0.99,
        )
        assert classification.is_auto_eligible is False

    def test_claim_issue_never_auto_eligible(self):
        classification = IntentClassification(
            intent=IntentType.CLAIM_ISSUE,
            confidence=0.99,
        )
        assert classification.is_auto_eligible is False

    def test_policy_change_auto_eligible(self):
        classification = IntentClassification(
            intent=IntentType.POLICY_CHANGE,
            confidence=0.95,
        )
        assert classification.is_auto_eligible is True

    def test_escalation_overrides_auto(self):
        classification = IntentClassification(
            intent=IntentType.GENERAL_INQUIRY,
            confidence=0.99,
            escalation_triggered=True,
            escalation_keywords_found=["lawyer"],
        )
        assert classification.is_auto_eligible is False

    def test_force_hitl_overrides_auto(self):
        classification = IntentClassification(
            intent=IntentType.GENERAL_INQUIRY,
            confidence=0.99,
            force_hitl=True,
        )
        assert classification.is_auto_eligible is False

    def test_priority_mapping(self):
        assert IntentClassification(
            intent=IntentType.GENERAL_INQUIRY, confidence=0.9
        ).priority == "low"
        assert IntentClassification(
            intent=IntentType.POLICY_CHANGE, confidence=0.9
        ).priority == "medium"
        assert IntentClassification(
            intent=IntentType.COMPLAINT_MISSELLING, confidence=0.9
        ).priority == "high"
        assert IntentClassification(
            intent=IntentType.CLAIM_ISSUE, confidence=0.9
        ).priority == "high"


class TestEscalationKeywords:
    """Tests for keyword-based escalation detection."""

    def test_keyword_detection(self):
        from lambdas.preprocessing.intent_classifier import _apply_escalation_rules

        classification = IntentClassification(
            intent=IntentType.GENERAL_INQUIRY,
            confidence=0.95,
        )
        result = _apply_escalation_rules(
            "I want to sue you for mis-selling!", classification
        )
        assert result.escalation_triggered is True
        assert "sue" in result.escalation_keywords_found
        assert result.intent == IntentType.COMPLAINT_MISSELLING

    def test_no_keywords(self):
        from lambdas.preprocessing.intent_classifier import _apply_escalation_rules

        classification = IntentClassification(
            intent=IntentType.GENERAL_INQUIRY,
            confidence=0.95,
        )
        result = _apply_escalation_rules(
            "What is my deductible amount?", classification
        )
        assert result.escalation_triggered is False
        assert result.intent == IntentType.GENERAL_INQUIRY

    def test_multiple_keywords(self):
        from lambdas.preprocessing.intent_classifier import _apply_escalation_rules

        classification = IntentClassification(
            intent=IntentType.GENERAL_INQUIRY,
            confidence=0.95,
        )
        result = _apply_escalation_rules(
            "This is fraud! I will contact my lawyer and the ombudsman!",
            classification,
        )
        assert result.escalation_triggered is True
        assert len(result.escalation_keywords_found) >= 3
