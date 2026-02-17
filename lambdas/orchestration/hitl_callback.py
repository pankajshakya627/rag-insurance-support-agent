"""
HITL Callback Lambda — processes human reviewer decisions from the dashboard.

Receives the reviewer's decision (approve/edit/reject/escalate) and
sends the callback to Step Functions to resume the workflow.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

from schemas.response import ApprovedResponse, ReviewDecision

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sfn = boto3.client("stepfunctions")
dynamodb = boto3.client("dynamodb")

TICKETS_TABLE = os.environ.get("DYNAMODB_TICKETS_TABLE", "InsuranceAI-Tickets")


def handler(event: dict[str, Any], context: Any) -> dict:
    """
    Lambda entry point for HITL callback processing.

    Called by the review dashboard when a human agent makes a decision.

    Input:
    {
        "task_token": "...",
        "ticket_id": "...",
        "decision": "approved|edited|rejected|escalated",
        "edited_text": "...",     (if edited)
        "reviewer_id": "...",
        "notes": "..."
    }
    """
    body = event
    if "body" in event:
        body = json.loads(event["body"])

    task_token = body.get("task_token")
    ticket_id = body.get("ticket_id")
    decision = body.get("decision", "approved")
    reviewer_id = body.get("reviewer_id", "unknown")

    if not task_token:
        return _api_response(400, {"error": "task_token is required"})

    logger.info(
        "HITL callback: ticket=%s, decision=%s, reviewer=%s",
        ticket_id,
        decision,
        reviewer_id,
    )

    try:
        if decision in ("approved", "edited"):
            # Build approved response
            final_text = body.get("edited_text", body.get("draft_text", ""))
            approved = ApprovedResponse(
                ticket_id=ticket_id,
                final_text=final_text,
                reviewed_by=reviewer_id,
                review_decision=(
                    ReviewDecision.EDITED if decision == "edited"
                    else ReviewDecision.APPROVED
                ),
                edit_diff=body.get("edit_diff", ""),
            )

            # Send task success to Step Functions
            sfn.send_task_success(
                taskToken=task_token,
                output=json.dumps({
                    "ticket_id": ticket_id,
                    "draft": {
                        "draft_text": approved.final_text,
                        "confidence": 1.0,
                        "requires_escalation": False,
                    },
                    "approved_by": reviewer_id,
                    "review_decision": decision,
                }),
            )

            # Update DynamoDB
            _update_ticket_status(ticket_id, "approved", reviewer_id)

        elif decision == "rejected":
            sfn.send_task_failure(
                taskToken=task_token,
                error="ReviewRejected",
                cause=body.get("notes", "Rejected by human reviewer"),
            )
            _update_ticket_status(ticket_id, "rejected", reviewer_id)

        elif decision == "escalated":
            sfn.send_task_failure(
                taskToken=task_token,
                error="EscalatedToSpecialist",
                cause=body.get("notes", "Escalated to specialist team"),
            )
            _update_ticket_status(ticket_id, "escalated", reviewer_id)

        return _api_response(200, {
            "ticket_id": ticket_id,
            "decision": decision,
            "status": "callback_sent",
        })

    except Exception as e:
        logger.exception("HITL callback failed: %s", e)
        return _api_response(500, {"error": str(e)})


def _update_ticket_status(ticket_id: str, status: str, reviewer: str) -> None:
    """Update ticket status in DynamoDB."""
    try:
        dynamodb.update_item(
            TableName=TICKETS_TABLE,
            Key={"ticket_id": {"S": ticket_id}},
            UpdateExpression="SET #s = :status, reviewed_by = :reviewer",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": {"S": status},
                ":reviewer": {"S": reviewer},
            },
        )
    except Exception as e:
        logger.error("Failed to update ticket %s: %s", ticket_id, e)


def _api_response(status_code: int, body: dict) -> dict:
    """Format API Gateway response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
