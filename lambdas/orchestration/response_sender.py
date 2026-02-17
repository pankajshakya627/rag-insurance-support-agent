"""
Response Sender Lambda — sends approved responses to customers.

Restores PII in the response, sends via SES, and updates DynamoDB.
Also logs the interaction for audit and fine-tuning.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

from lambdas.preprocessing.pii_redactor import restore_pii
from schemas.ticket import TicketStatus

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ses = boto3.client("ses")
dynamodb = boto3.client("dynamodb")
s3 = boto3.client("s3")

TICKETS_TABLE = os.environ.get("DYNAMODB_TICKETS_TABLE", "InsuranceAI-Tickets")
AUDIT_BUCKET = os.environ.get("S3_AUDIT_LOGS_BUCKET", "insurance-ai-audit-logs")
SENDER_EMAIL = os.environ.get("SES_SENDER_EMAIL", "support@insurance-ai.example.com")


def handler(event: dict[str, Any], context: Any) -> dict:
    """
    Lambda entry point for sending the final response.

    Input: Approved response with ticket, draft, and PII mapping
    Output: Confirmation with message ID
    """
    ticket = event.get("ticket", {})
    draft = event.get("draft", {})
    approved_by = event.get("approved_by", "auto")

    ticket_id = ticket.get("ticket_id", "unknown")
    customer_email = ticket.get("customer_email", "")
    subject = ticket.get("subject", "Insurance Support Response")
    channel = ticket.get("channel", "email")

    # Get the approved response text
    response_text = draft.get("draft_text", "")

    # Restore PII in the response (replace placeholders with originals)
    pii_mapping = ticket.get("pii_mapping", {})
    if pii_mapping:
        response_text = restore_pii(response_text, pii_mapping)

    # Send based on channel
    message_id = None
    if channel == "email" and customer_email:
        message_id = _send_email(customer_email, subject, response_text)
    else:
        logger.info(
            "Channel %s — response stored but not sent via email (ticket: %s)",
            channel,
            ticket_id,
        )

    # Update DynamoDB ticket to Resolved
    _update_ticket_resolved(ticket_id, response_text, approved_by)

    # Audit log
    _store_audit_log(ticket_id, ticket, draft, response_text, approved_by)

    logger.info("Response sent for ticket %s (message_id=%s)", ticket_id, message_id)

    return {
        "ticket_id": ticket_id,
        "status": "sent",
        "message_id": message_id,
        "channel": channel,
    }


def _send_email(to_address: str, subject: str, body: str) -> str | None:
    """Send response email via Amazon SES."""
    try:
        response = ses.send_email(
            Source=SENDER_EMAIL,
            Destination={"ToAddresses": [to_address]},
            Message={
                "Subject": {"Data": f"Re: {subject}", "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": body, "Charset": "UTF-8"},
                    "Html": {
                        "Data": _format_html_email(body),
                        "Charset": "UTF-8",
                    },
                },
            },
            Tags=[
                {"Name": "purpose", "Value": "ai-support-response"},
            ],
        )
        return response.get("MessageId")

    except Exception as e:
        logger.error("SES send failed for %s: %s", to_address, e)
        return None


def _format_html_email(text: str) -> str:
    """Convert plain text response to simple HTML email."""
    paragraphs = text.split("\n\n")
    html_parts = [f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs if p.strip()]

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
    <div style="border-bottom: 3px solid #0052CC; padding-bottom: 10px; margin-bottom: 20px;">
        <h2 style="color: #0052CC; margin: 0;">Insurance Support</h2>
    </div>
    {''.join(html_parts)}
    <div style="margin-top: 30px; padding-top: 15px; border-top: 1px solid #ddd; color: #666; font-size: 12px;">
        <p>This response was generated with AI assistance and reviewed for accuracy.</p>
        <p>If you need further help, reply to this email.</p>
    </div>
</body>
</html>
"""


def _update_ticket_resolved(
    ticket_id: str, response_text: str, approved_by: str
) -> None:
    """Update ticket status to Resolved in DynamoDB."""
    try:
        dynamodb.update_item(
            TableName=TICKETS_TABLE,
            Key={"ticket_id": {"S": ticket_id}},
            UpdateExpression=(
                "SET #s = :status, response_text = :response, "
                "approved_by = :approver"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": {"S": TicketStatus.RESOLVED.value},
                ":response": {"S": response_text},
                ":approver": {"S": approved_by},
            },
        )
    except Exception as e:
        logger.error("Failed to update ticket %s to resolved: %s", ticket_id, e)


def _store_audit_log(
    ticket_id: str,
    ticket: dict,
    draft: dict,
    final_response: str,
    approved_by: str,
) -> None:
    """Store complete audit record to S3 for compliance."""
    audit_record = {
        "ticket_id": ticket_id,
        "customer_id": ticket.get("customer_id"),
        "channel": ticket.get("channel"),
        "original_query": ticket.get("message_body"),
        "redacted_query": ticket.get("message_body_redacted"),
        "ai_draft": draft.get("draft_text"),
        "final_response": final_response,
        "confidence": draft.get("confidence"),
        "approved_by": approved_by,
        "cited_sections": draft.get("cited_sections", []),
        "pii_detected": ticket.get("pii_detected_count", 0),
        "classification": ticket.get("classification"),
    }

    try:
        s3.put_object(
            Bucket=AUDIT_BUCKET,
            Key=f"responses/{ticket_id}.json",
            Body=json.dumps(audit_record, default=str),
            ContentType="application/json",
            ServerSideEncryption="aws:kms",
        )
    except Exception as e:
        logger.error("Failed to store audit log for %s: %s", ticket_id, e)
