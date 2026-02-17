"""
Intent classification schemas.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class IntentType(StrEnum):
    """Supported intent categories for insurance support queries."""

    GENERAL_INQUIRY = "general_inquiry"
    POLICY_CHANGE = "policy_change"
    COMPLAINT_MISSELLING = "complaint_misselling"
    CLAIM_ISSUE = "claim_issue"


# Maps intent to its auto-response eligibility and priority
INTENT_METADATA = {
    IntentType.GENERAL_INQUIRY: {
        "auto_respond": True,
        "priority": "low",
        "requires_verification": False,
    },
    IntentType.POLICY_CHANGE: {
        "auto_respond": True,
        "priority": "medium",
        "requires_verification": True,
    },
    IntentType.COMPLAINT_MISSELLING: {
        "auto_respond": False,
        "priority": "high",
        "requires_verification": True,
    },
    IntentType.CLAIM_ISSUE: {
        "auto_respond": False,
        "priority": "high",
        "requires_verification": True,
    },
}


class IntentClassification(BaseModel):
    """Result of classifying a customer message."""

    intent: IntentType = Field(description="Classified intent category")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Model confidence score",
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of classification decision",
    )
    escalation_triggered: bool = Field(
        default=False,
        description="True if escalation keywords were detected",
    )
    escalation_keywords_found: list[str] = Field(
        default_factory=list,
        description="Which escalation keywords matched, if any",
    )
    force_hitl: bool = Field(
        default=False,
        description="True if this ticket MUST go through human review",
    )

    @property
    def is_auto_eligible(self) -> bool:
        """Whether this classification allows auto-response (no human review)."""
        if self.force_hitl or self.escalation_triggered:
            return False
        meta = INTENT_METADATA.get(self.intent, {})
        return meta.get("auto_respond", False) and self.confidence >= 0.90

    @property
    def priority(self) -> str:
        """Priority level from intent metadata."""
        meta = INTENT_METADATA.get(self.intent, {})
        return meta.get("priority", "medium")
