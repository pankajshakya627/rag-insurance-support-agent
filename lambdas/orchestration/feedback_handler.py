"""
Feedback Handler Lambda — processes customer follow-up replies.

Detects negative feedback (re-open triggers), stores training triplets
for SageMaker fine-tuning, and manages the feedback loop.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

from schemas.response import FeedbackSignal, FeedbackType
from schemas.ticket import TicketStatus

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.client("dynamodb")
s3 = boto3.client("s3")
sns = boto3.client("sns")

TICKETS_TABLE = os.environ.get("DYNAMODB_TICKETS_TABLE", "InsuranceAI-Tickets")
FINETUNING_BUCKET = os.environ.get("S3_FINETUNING_BUCKET", "insurance-ai-finetuning-data")
ORCHESTRATION_TOPIC = os.environ.get("SNS_ORCHESTRATION_TOPIC", "")

# Phrases that indicate the customer is unsatisfied
NEGATIVE_INDICATORS = [
    "didn't help",
    "did not help",
    "not helpful",
    "wrong answer",
    "incorrect",
    "not what i asked",
    "still have the issue",
    "still having",
    "doesn't answer",
    "does not answer",
    "try again",
    "not satisfied",
    "unsatisfied",
    "terrible",
    "useless",
    "worst",
]


def handler(event: dict[str, Any], context: Any) -> dict:
    """
    Lambda entry point for feedback processing.

    Handles customer follow-up messages to previously resolved tickets.
    Determines if the feedback is positive or negative, and takes
    appropriate action (re-open or store for training).
    """
    ticket_id = event.get("ticket_id", "")
    customer_message = event.get("customer_message", "")

    if not ticket_id:
        return {"status": "error", "message": "ticket_id required"}

    # Look up the original ticket and response
    original = _get_ticket(ticket_id)
    if not original:
        return {"status": "error", "message": f"Ticket {ticket_id} not found"}

    # Determine feedback type
    feedback_type = _classify_feedback(customer_message)

    # Build feedback signal
    feedback = FeedbackSignal(
        ticket_id=ticket_id,
        feedback_type=feedback_type,
        customer_message=customer_message,
        original_query=original.get("message_body", {}).get("S", ""),
        ai_response=original.get("response_text", {}).get("S", ""),
        human_edited=original.get("approved_by", {}).get("S", "") != "auto",
    )

    # Store training record (always, for both positive and negative)
    _store_training_record(feedback)

    # Handle negative feedback → re-open
    if feedback_type in (FeedbackType.NEGATIVE, FeedbackType.REOPEN):
        _reopen_ticket(ticket_id, customer_message)
        logger.info("Ticket %s re-opened due to negative feedback", ticket_id)

        return {
            "status": "reopened",
            "ticket_id": ticket_id,
            "feedback_type": feedback_type.value,
        }

    logger.info("Positive feedback recorded for ticket %s", ticket_id)
    return {
        "status": "recorded",
        "ticket_id": ticket_id,
        "feedback_type": feedback_type.value,
    }


def _classify_feedback(message: str) -> FeedbackType:
    """Classify feedback as positive, negative, or re-open request."""
    message_lower = message.lower()

    # Check for explicit re-open requests
    if any(phrase in message_lower for phrase in ["reopen", "re-open", "open again"]):
        return FeedbackType.REOPEN

    # Check for negative indicators
    negative_count = sum(
        1 for indicator in NEGATIVE_INDICATORS if indicator in message_lower
    )

    if negative_count >= 1:
        return FeedbackType.NEGATIVE

    return FeedbackType.POSITIVE


def _get_ticket(ticket_id: str) -> dict | None:
    """Retrieve ticket from DynamoDB."""
    try:
        response = dynamodb.get_item(
            TableName=TICKETS_TABLE,
            Key={"ticket_id": {"S": ticket_id}},
        )
        return response.get("Item")
    except Exception as e:
        logger.error("Failed to get ticket %s: %s", ticket_id, e)
        return None


def _store_training_record(feedback: FeedbackSignal) -> None:
    """Store (query, response, feedback) triplet for SageMaker fine-tuning."""
    record = feedback.to_finetuning_record()

    try:
        s3.put_object(
            Bucket=FINETUNING_BUCKET,
            Key=f"feedback/{feedback.ticket_id}.json",
            Body=json.dumps(record, default=str),
            ContentType="application/json",
        )
    except Exception as e:
        logger.error("Failed to store training record for %s: %s", feedback.ticket_id, e)


def _reopen_ticket(ticket_id: str, customer_message: str) -> None:
    """Re-open a ticket and notify the pipeline for re-processing."""
    # Update status in DynamoDB
    try:
        dynamodb.update_item(
            TableName=TICKETS_TABLE,
            Key={"ticket_id": {"S": ticket_id}},
            UpdateExpression="SET #s = :status, reopen_message = :msg",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": {"S": TicketStatus.REOPENED.value},
                ":msg": {"S": customer_message},
            },
        )
    except Exception as e:
        logger.error("Failed to re-open ticket %s: %s", ticket_id, e)

    # Notify pipeline for re-processing (with higher priority)
    if ORCHESTRATION_TOPIC:
        try:
            sns.publish(
                TopicArn=ORCHESTRATION_TOPIC,
                Message=json.dumps({
                    "ticket_id": ticket_id,
                    "action": "reopen",
                    "customer_message": customer_message,
                }),
                MessageAttributes={
                    "priority": {"DataType": "String", "StringValue": "high"},
                    "action": {"DataType": "String", "StringValue": "reopen"},
                },
            )
        except Exception as e:
            logger.error("Failed to publish reopen event for %s: %s", ticket_id, e)
