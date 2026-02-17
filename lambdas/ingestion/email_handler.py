"""
Email Ingestion Lambda — handles incoming emails from Amazon SES.

Trigger: SES Receipt Rule → SNS → Lambda
Output:  NormalizedTicket JSON published to orchestration SNS topic
"""

from __future__ import annotations

import email
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from email import policy as email_policy
from typing import Any

import boto3

from schemas.ticket import ChannelType, NormalizedTicket, TicketStatus

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
sns = boto3.client("sns")
dynamodb = boto3.client("dynamodb")

RAW_BUCKET = os.environ.get("S3_RAW_MESSAGES_BUCKET", "insurance-ai-raw-messages")
ATTACHMENTS_BUCKET = os.environ.get("S3_ATTACHMENTS_BUCKET", "insurance-ai-attachments")
ORCHESTRATION_TOPIC = os.environ.get("SNS_ORCHESTRATION_TOPIC", "")
TICKETS_TABLE = os.environ.get("DYNAMODB_TICKETS_TABLE", "InsuranceAI-Tickets")


def handler(event: dict[str, Any], context: Any) -> dict:
    """
    Lambda entry point for SES-triggered email ingestion.

    The event comes via SNS wrapping the SES notification. We parse
    the raw email from S3 (SES action stores it there), normalize it,
    and publish downstream.
    """
    logger.info("Email ingestion triggered with %d record(s)", len(event.get("Records", [])))

    results = []
    for record in event.get("Records", []):
        try:
            ticket = _process_ses_record(record)
            results.append({"ticket_id": ticket.ticket_id, "status": "success"})
        except Exception as e:
            logger.exception("Failed to process SES record: %s", e)
            results.append({"error": str(e), "status": "failed"})

    return {"statusCode": 200, "body": json.dumps(results)}


def _process_ses_record(record: dict) -> NormalizedTicket:
    """Parse a single SES notification record into a NormalizedTicket."""
    # Extract SES notification from SNS wrapper
    sns_message = json.loads(record.get("Sns", {}).get("Message", "{}"))
    ses_notification = sns_message.get("receipt", {})
    mail_meta = sns_message.get("mail", {})

    # Get the raw email from S3 (SES stores it via S3 action)
    message_id = mail_meta.get("messageId", str(uuid.uuid4()))
    s3_key = f"incoming-emails/{message_id}"

    # Parse email content
    sender = mail_meta.get("source", "")
    subject = mail_meta.get("commonHeaders", {}).get("subject", "No Subject")

    # Try to fetch raw email from S3 if configured
    body_text = ""
    attachment_keys: list[str] = []

    try:
        raw_obj = s3.get_object(Bucket=RAW_BUCKET, Key=s3_key)
        raw_bytes = raw_obj["Body"].read()
        body_text, attachment_keys = _parse_mime_email(raw_bytes, message_id)
    except Exception:
        # Fallback: extract content from SES notification directly
        content = sns_message.get("content", "")
        if content:
            body_text, attachment_keys = _parse_mime_email(
                content.encode("utf-8"), message_id
            )
        else:
            body_text = f"[Email from {sender}] Subject: {subject}"
            logger.warning("Could not retrieve raw email from S3, using fallback")

    # Derive customer ID from email address
    customer_id = _resolve_customer_id(sender)

    # Build normalized ticket
    ticket = NormalizedTicket(
        channel=ChannelType.EMAIL,
        customer_id=customer_id,
        customer_email=sender,
        subject=subject,
        message_body=body_text,
        attachments=attachment_keys,
        metadata={
            "ses_message_id": message_id,
            "recipients": mail_meta.get("destination", []),
            "spf_verdict": ses_notification.get("spfVerdict", {}).get("status"),
            "dkim_verdict": ses_notification.get("dkimVerdict", {}).get("status"),
        },
        status=TicketStatus.RECEIVED,
        raw_s3_key=s3_key,
    )

    # Persist raw event to S3 for audit
    _store_raw_event(ticket.ticket_id, sns_message)

    # Save ticket to DynamoDB
    _save_ticket(ticket)

    # Publish to orchestration pipeline
    _publish_to_pipeline(ticket)

    logger.info("Email ticket created: %s from %s", ticket.ticket_id, sender)
    return ticket


def _parse_mime_email(raw_bytes: bytes, message_id: str) -> tuple[str, list[str]]:
    """
    Parse a MIME email into body text and uploaded attachment S3 keys.
    """
    msg = email.message_from_bytes(raw_bytes, policy=email_policy.default)
    body_parts: list[str] = []
    attachment_keys: list[str] = []

    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", ""))

        if "attachment" in disposition:
            # Upload attachment to S3
            filename = part.get_filename() or f"attachment_{uuid.uuid4().hex[:8]}"
            s3_key = f"attachments/{message_id}/{filename}"
            payload = part.get_payload(decode=True)
            if payload:
                s3.put_object(
                    Bucket=ATTACHMENTS_BUCKET,
                    Key=s3_key,
                    Body=payload,
                    ContentType=content_type,
                )
                attachment_keys.append(f"s3://{ATTACHMENTS_BUCKET}/{s3_key}")
        elif content_type == "text/plain":
            text = part.get_payload(decode=True)
            if text:
                body_parts.append(text.decode("utf-8", errors="replace"))
        elif content_type == "text/html" and not body_parts:
            # Fallback to HTML if no plain text
            html = part.get_payload(decode=True)
            if html:
                body_parts.append(f"[HTML Content]: {html.decode('utf-8', errors='replace')}")

    return "\n".join(body_parts), attachment_keys


def _resolve_customer_id(email_address: str) -> str:
    """
    Look up customer ID from DynamoDB by email, or create a new one.
    In production, this would integrate with the CRM system.
    """
    try:
        response = dynamodb.query(
            TableName=os.environ.get(
                "DYNAMODB_CUSTOMER_PROFILES_TABLE", "InsuranceAI-CustomerProfiles"
            ),
            IndexName="email-index",
            KeyConditionExpression="customer_email = :email",
            ExpressionAttributeValues={":email": {"S": email_address}},
            Limit=1,
        )
        items = response.get("Items", [])
        if items:
            return items[0]["customer_id"]["S"]
    except Exception:
        logger.warning("Customer lookup failed for %s, generating ID", email_address)

    return f"CUST-{uuid.uuid4().hex[:8].upper()}"


def _store_raw_event(ticket_id: str, raw_event: dict) -> None:
    """Store raw SES event to S3 audit bucket."""
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=f"audit/email/{ticket_id}.json",
        Body=json.dumps(raw_event, default=str),
        ContentType="application/json",
    )


def _save_ticket(ticket: NormalizedTicket) -> None:
    """Persist ticket to DynamoDB."""
    dynamodb.put_item(
        TableName=TICKETS_TABLE,
        Item=ticket.to_dynamo_item(),
    )


def _publish_to_pipeline(ticket: NormalizedTicket) -> None:
    """Publish normalized ticket to the orchestration SNS topic."""
    if not ORCHESTRATION_TOPIC:
        logger.warning("No orchestration topic configured, skipping publish")
        return

    sns.publish(
        TopicArn=ORCHESTRATION_TOPIC,
        Message=ticket.model_dump_json(),
        MessageAttributes={
            "channel": {"DataType": "String", "StringValue": ticket.channel.value},
            "priority": {"DataType": "String", "StringValue": "normal"},
        },
    )
