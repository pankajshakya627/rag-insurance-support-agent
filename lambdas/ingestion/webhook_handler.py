"""
Webhook Ingestion Lambda — handles WhatsApp and Chatbot messages via API Gateway.

Trigger: API Gateway (REST) POST /webhook/{channel}
Output:  NormalizedTicket JSON published to orchestration SNS topic
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import boto3

from schemas.ticket import ChannelType, NormalizedTicket, TicketStatus

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
sns = boto3.client("sns")
dynamodb = boto3.client("dynamodb")

RAW_BUCKET = os.environ.get("S3_RAW_MESSAGES_BUCKET", "insurance-ai-raw-messages")
ORCHESTRATION_TOPIC = os.environ.get("SNS_ORCHESTRATION_TOPIC", "")
TICKETS_TABLE = os.environ.get("DYNAMODB_TICKETS_TABLE", "InsuranceAI-Tickets")


def handler(event: dict[str, Any], context: Any) -> dict:
    """
    Lambda entry point for API Gateway webhook events.

    Supports WhatsApp (via Twilio/Meta) and custom chatbot payloads.
    """
    logger.info("Webhook ingestion triggered")

    try:
        # Extract channel from path parameters
        channel_str = (
            event.get("pathParameters", {}).get("channel", "chatbot").lower()
        )
        body = json.loads(event.get("body", "{}"))

        # Route to channel-specific parser
        if channel_str == "whatsapp":
            ticket = _parse_whatsapp(body)
        elif channel_str == "chatbot":
            ticket = _parse_chatbot(body)
        else:
            return _api_response(400, {"error": f"Unsupported channel: {channel_str}"})

        # Store raw payload for audit
        _store_raw_payload(ticket.ticket_id, channel_str, body)

        # Save ticket and publish
        _save_ticket(ticket)
        _publish_to_pipeline(ticket)

        logger.info("Webhook ticket created: %s via %s", ticket.ticket_id, channel_str)

        return _api_response(200, {
            "ticket_id": ticket.ticket_id,
            "status": "received",
        })

    except json.JSONDecodeError:
        return _api_response(400, {"error": "Invalid JSON body"})
    except Exception as e:
        logger.exception("Webhook processing failed: %s", e)
        return _api_response(500, {"error": "Internal processing error"})


def _parse_whatsapp(body: dict) -> NormalizedTicket:
    """
    Parse WhatsApp webhook payload (Meta Cloud API format).

    Expected structure:
    {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "phone_number",
                        "text": {"body": "message text"},
                        "type": "text"
                    }],
                    "contacts": [{"profile": {"name": "Customer Name"}}]
                }
            }]
        }]
    }
    """
    entries = body.get("entry", [{}])
    changes = entries[0].get("changes", [{}]) if entries else [{}]
    value = changes[0].get("value", {}) if changes else {}
    messages = value.get("messages", [])
    contacts = value.get("contacts", [])

    if not messages:
        raise ValueError("No messages found in WhatsApp webhook payload")

    msg = messages[0]
    phone_number = msg.get("from", "unknown")
    message_text = msg.get("text", {}).get("body", "")
    msg_type = msg.get("type", "text")

    # Handle media messages
    attachments: list[str] = []
    if msg_type in ("image", "document", "audio", "video"):
        media = msg.get(msg_type, {})
        media_id = media.get("id", "")
        if media_id:
            attachments.append(f"whatsapp-media://{media_id}")

    contact_name = ""
    if contacts:
        contact_name = contacts[0].get("profile", {}).get("name", "")

    return NormalizedTicket(
        channel=ChannelType.WHATSAPP,
        customer_id=f"WA-{phone_number}",
        customer_email="",
        subject=f"WhatsApp from {contact_name or phone_number}",
        message_body=message_text,
        attachments=attachments,
        metadata={
            "phone_number": phone_number,
            "contact_name": contact_name,
            "message_type": msg_type,
            "wa_message_id": msg.get("id", ""),
        },
        status=TicketStatus.RECEIVED,
    )


def _parse_chatbot(body: dict) -> NormalizedTicket:
    """
    Parse custom chatbot payload.

    Expected structure:
    {
        "session_id": "...",
        "customer_id": "...",
        "message": "...",
        "metadata": { ... }
    }
    """
    customer_id = body.get("customer_id", f"CHAT-{uuid.uuid4().hex[:8]}")
    message = body.get("message", "")

    if not message:
        raise ValueError("Empty message in chatbot payload")

    return NormalizedTicket(
        channel=ChannelType.CHATBOT,
        customer_id=customer_id,
        customer_email=body.get("email", ""),
        subject=f"Chat session {body.get('session_id', 'unknown')}",
        message_body=message,
        metadata={
            "session_id": body.get("session_id", ""),
            "user_agent": body.get("user_agent", ""),
            "page_url": body.get("page_url", ""),
            **body.get("metadata", {}),
        },
        status=TicketStatus.RECEIVED,
    )


def _store_raw_payload(ticket_id: str, channel: str, payload: dict) -> None:
    """Store raw webhook payload to S3 for audit."""
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=f"audit/{channel}/{ticket_id}.json",
        Body=json.dumps(payload, default=str),
        ContentType="application/json",
    )


def _save_ticket(ticket: NormalizedTicket) -> None:
    """Persist ticket to DynamoDB."""
    dynamodb.put_item(
        TableName=TICKETS_TABLE,
        Item=ticket.to_dynamo_item(),
    )


def _publish_to_pipeline(ticket: NormalizedTicket) -> None:
    """Publish normalized ticket to orchestration SNS topic."""
    if not ORCHESTRATION_TOPIC:
        logger.warning("No orchestration topic configured, skipping publish")
        return

    sns.publish(
        TopicArn=ORCHESTRATION_TOPIC,
        Message=ticket.model_dump_json(),
        MessageAttributes={
            "channel": {"DataType": "String", "StringValue": ticket.channel.value},
        },
    )


def _api_response(status_code: int, body: dict) -> dict:
    """Format an API Gateway proxy response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
