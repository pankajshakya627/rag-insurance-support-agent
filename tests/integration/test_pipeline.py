"""
Integration test — end-to-end pipeline simulation with mocked AWS services.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from schemas.ticket import ChannelType, NormalizedTicket, TicketStatus
from schemas.classification import IntentClassification, IntentType


class TestEndToEndPipeline:
    """Simulates the full pipeline from ingestion through classification."""

    def test_email_normalization(self):
        """Test that email input produces a valid NormalizedTicket."""
        ticket = NormalizedTicket(
            channel=ChannelType.EMAIL,
            customer_id="CUST-001",
            customer_email="customer@example.com",
            subject="Question about my health insurance",
            message_body="What is my deductible for in-network providers?",
            metadata={"ses_message_id": "test-123"},
        )

        assert ticket.ticket_id  # UUID generated
        assert ticket.channel == ChannelType.EMAIL
        assert ticket.status == TicketStatus.RECEIVED
        assert ticket.timestamp  # ISO timestamp generated

    def test_webhook_normalization(self):
        """Test that webhook input produces a valid NormalizedTicket."""
        ticket = NormalizedTicket(
            channel=ChannelType.WHATSAPP,
            customer_id="WA-1234567890",
            message_body="I need to file a claim for my car accident",
        )

        assert ticket.channel == ChannelType.WHATSAPP
        assert ticket.customer_id == "WA-1234567890"

    def test_classification_routing_general(self):
        """General inquiry with high confidence → auto-eligible."""
        classification = IntentClassification(
            intent=IntentType.GENERAL_INQUIRY,
            confidence=0.95,
            reasoning="Standard coverage question",
        )

        assert classification.is_auto_eligible is True
        assert classification.priority == "low"
        assert classification.force_hitl is False

    def test_classification_routing_complaint(self):
        """Complaint → always requires HITL."""
        classification = IntentClassification(
            intent=IntentType.COMPLAINT_MISSELLING,
            confidence=0.90,
            reasoning="Customer complaining about product",
        )

        assert classification.is_auto_eligible is False
        assert classification.priority == "high"

    def test_classification_routing_escalation(self):
        """Escalation keywords → force HITL regardless of intent."""
        classification = IntentClassification(
            intent=IntentType.GENERAL_INQUIRY,
            confidence=0.99,
            escalation_triggered=True,
            escalation_keywords_found=["lawyer"],
            force_hitl=True,
        )

        assert classification.is_auto_eligible is False
        assert classification.force_hitl is True

    def test_ticket_dynamo_serialization(self):
        """Test DynamoDB serialization of a ticket."""
        ticket = NormalizedTicket(
            channel=ChannelType.EMAIL,
            customer_id="CUST-001",
            customer_email="test@example.com",
            subject="Test",
            message_body="Hello",
        )

        dynamo_item = ticket.to_dynamo_item()
        assert dynamo_item["ticket_id"]["S"] == ticket.ticket_id
        assert dynamo_item["channel"]["S"] == "email"
        assert dynamo_item["customer_id"]["S"] == "CUST-001"

    def test_pipeline_data_flow(self):
        """Simulate the data flow through the pipeline stages."""
        # Stage 1: Ingestion
        ticket = NormalizedTicket(
            channel=ChannelType.EMAIL,
            customer_id="CUST-TEST",
            customer_email="test@insurance.com",
            subject="Coverage Question",
            message_body=(
                "Hi, my policy number is POL-12345678. "
                "What does my plan cover for dental work? "
                "My SSN is 123-45-6789."
            ),
        )

        # Stage 2: PII Redaction (simulated)
        from lambdas.preprocessing.pii_redactor import _redact_with_regex

        redacted_text, pii_mapping = _redact_with_regex(ticket.message_body)
        assert "POL-12345678" not in redacted_text
        assert "123-45-6789" not in redacted_text
        assert len(pii_mapping) >= 2

        # Stage 3: Intent Classification (simulated)
        classification = IntentClassification(
            intent=IntentType.GENERAL_INQUIRY,
            confidence=0.92,
        )
        assert classification.is_auto_eligible is True

        # Stage 4 + 5: RAG + Generation would need AWS connection
        # Validated in unit tests with mocks

        # Stage 6: PII Restoration
        from lambdas.preprocessing.pii_redactor import restore_pii

        draft = f"Your policy {list(pii_mapping.keys())[0]} covers dental under Section 5."
        restored = restore_pii(draft, pii_mapping)
        assert "POL-12345678" in restored
