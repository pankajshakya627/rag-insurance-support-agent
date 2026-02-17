"""
Ticket schemas — the canonical data models flowing through the pipeline.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class ChannelType(StrEnum):
    """Supported ingestion channels."""

    EMAIL = "email"
    WHATSAPP = "whatsapp"
    CHATBOT = "chatbot"


class TicketStatus(StrEnum):
    """Lifecycle states for a support ticket."""

    RECEIVED = "received"
    PROCESSING = "processing"
    AWAITING_CLASSIFICATION = "awaiting_classification"
    CLASSIFIED = "classified"
    RETRIEVING_CONTEXT = "retrieving_context"
    GENERATING_RESPONSE = "generating_response"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    SENT = "sent"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    ESCALATED = "escalated"
    FAILED = "failed"


class NormalizedTicket(BaseModel):
    """
    Standard schema for all incoming customer messages, regardless of channel.

    Every ingestion handler (email, WhatsApp, chatbot) MUST produce this
    schema before publishing to the orchestration pipeline.
    """

    ticket_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique ticket identifier (UUID v4)",
    )
    channel: ChannelType = Field(
        description="Source channel of the message",
    )
    customer_id: str = Field(
        description="Customer identifier from CRM or contact info",
    )
    customer_email: str = Field(
        default="",
        description="Customer email for response delivery",
    )
    subject: str = Field(
        default="",
        description="Email subject line or conversation title",
    )
    message_body: str = Field(
        description="Full text of the customer message",
    )
    attachments: list[str] = Field(
        default_factory=list,
        description="S3 URIs for any attached files",
    )
    extracted_attachment_text: str = Field(
        default="",
        description="Text extracted from attachments via Textract",
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Channel-specific metadata (headers, webhook payload, etc.)",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 timestamp of message receipt",
    )
    status: TicketStatus = Field(
        default=TicketStatus.RECEIVED,
        description="Current ticket lifecycle status",
    )
    raw_s3_key: str = Field(
        default="",
        description="S3 key where the raw incoming message is stored",
    )

    def to_dynamo_item(self) -> dict:
        """Serialize to a DynamoDB-compatible dict."""
        return {
            "ticket_id": {"S": self.ticket_id},
            "channel": {"S": self.channel.value},
            "customer_id": {"S": self.customer_id},
            "customer_email": {"S": self.customer_email},
            "subject": {"S": self.subject},
            "message_body": {"S": self.message_body},
            "attachments": {"L": [{"S": a} for a in self.attachments]},
            "timestamp": {"S": self.timestamp},
            "status": {"S": self.status.value},
            "raw_s3_key": {"S": self.raw_s3_key},
        }
