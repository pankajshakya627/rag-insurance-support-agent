"""
Response schemas — draft, approved, and feedback models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class ReviewDecision(StrEnum):
    """Human reviewer decisions."""

    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class FeedbackType(StrEnum):
    """Customer feedback signals."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    REOPEN = "reopen"


class DraftResponse(BaseModel):
    """AI-generated draft response awaiting review or auto-approval."""

    ticket_id: str
    draft_text: str = Field(description="Generated response text")
    cited_sections: list[str] = Field(
        default_factory=list,
        description="Policy sections referenced in the response",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Generation confidence score",
    )
    requires_escalation: bool = Field(default=False)
    escalation_reason: str | None = Field(default=None)
    context_chunks_used: int = Field(
        default=0,
        description="Number of RAG chunks that contributed to the response",
    )
    is_grounded: bool = Field(
        default=True,
        description="Whether hallucination check passed",
    )
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    step_functions_task_token: str = Field(
        default="",
        description="Step Functions callback token for HITL approval",
    )


class ApprovedResponse(BaseModel):
    """Final response approved for sending to the customer."""

    ticket_id: str
    final_text: str = Field(description="Approved response text (PII restored)")
    reviewed_by: str = Field(
        default="auto",
        description="'auto' for auto-approved, or reviewer's user ID",
    )
    review_decision: ReviewDecision = Field(default=ReviewDecision.APPROVED)
    edit_diff: str = Field(
        default="",
        description="If edited, what was changed from the draft",
    )
    approved_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )

    def to_dynamo_item(self) -> dict:
        """Serialize for DynamoDB storage."""
        return {
            "ticket_id": {"S": self.ticket_id},
            "final_text": {"S": self.final_text},
            "reviewed_by": {"S": self.reviewed_by},
            "review_decision": {"S": self.review_decision.value},
            "approved_at": {"S": self.approved_at},
        }


class FeedbackSignal(BaseModel):
    """Captures customer feedback on the response for fine-tuning loop."""

    ticket_id: str
    feedback_type: FeedbackType
    customer_message: str = Field(
        default="",
        description="Customer's follow-up message (if any)",
    )
    original_query: str = Field(description="The original customer query")
    ai_response: str = Field(description="The response that was sent")
    human_edited: bool = Field(
        default=False,
        description="Whether a human edited the response before sending",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )

    def to_finetuning_record(self) -> dict:
        """Format as a training record for SageMaker fine-tuning."""
        return {
            "query": self.original_query,
            "response": self.ai_response,
            "feedback": self.feedback_type.value,
            "human_edited": self.human_edited,
            "timestamp": self.timestamp,
        }
