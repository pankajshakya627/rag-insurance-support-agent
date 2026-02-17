"""
HITL Review Dashboard — Streamlit web app for human agents.

Features:
  - Login via Cognito (simplified for MVP)
  - View pending review queue from DynamoDB
  - Review AI drafts with context and confidence scores
  - Approve, Edit & Approve, Reject, or Escalate
  - Sends callback to Step Functions to resume workflow
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import boto3
import streamlit as st

# ---- Page Config ----
st.set_page_config(
    page_title="Insurance AI — Review Dashboard",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---- AWS Clients ----
dynamodb = boto3.client("dynamodb")
sqs = boto3.client("sqs")
sfn = boto3.client("stepfunctions")

TICKETS_TABLE = os.environ.get("DYNAMODB_TICKETS_TABLE", "InsuranceAI-Tickets")
HITL_QUEUE_URL = os.environ.get("HITL_QUEUE_URL", "")


# ---- Authentication (Simplified for MVP) ----
def check_auth() -> bool:
    """Simple session-based auth. Replace with Cognito in production."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
        st.session_state.reviewer_id = ""

    if not st.session_state.authenticated:
        st.sidebar.title("🔐 Login")
        reviewer_id = st.sidebar.text_input("Agent ID")
        password = st.sidebar.text_input("Password", type="password")

        if st.sidebar.button("Login"):
            # MVP: hardcoded check. Production: use Cognito
            if reviewer_id and password:
                st.session_state.authenticated = True
                st.session_state.reviewer_id = reviewer_id
                st.rerun()
            else:
                st.sidebar.error("Invalid credentials")
        return False

    return True


# ---- Data Loading ----
def load_pending_reviews() -> list[dict]:
    """Load tickets awaiting review from DynamoDB."""
    try:
        response = dynamodb.scan(
            TableName=TICKETS_TABLE,
            FilterExpression="#s = :status",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": {"S": "awaiting_review"}},
            Limit=50,
        )

        tickets = []
        for item in response.get("Items", []):
            tickets.append({
                "ticket_id": item.get("ticket_id", {}).get("S", ""),
                "customer_id": item.get("customer_id", {}).get("S", ""),
                "channel": item.get("channel", {}).get("S", ""),
                "subject": item.get("subject", {}).get("S", ""),
                "message_body": item.get("message_body", {}).get("S", ""),
                "timestamp": item.get("timestamp", {}).get("S", ""),
                "classification": json.loads(
                    item.get("classification", {}).get("S", "{}")
                )
                if "classification" in item
                else {},
                "draft_response": item.get("draft_response", {}).get("S", ""),
                "confidence": float(item.get("confidence", {}).get("N", "0")),
                "task_token": item.get("task_token", {}).get("S", ""),
            })

        return sorted(tickets, key=lambda t: t["timestamp"], reverse=True)

    except Exception as e:
        st.error(f"Failed to load reviews: {e}")
        return []


def load_queue_messages() -> list[dict]:
    """Load pending messages from the HITL SQS queue."""
    if not HITL_QUEUE_URL:
        return []

    try:
        response = sqs.receive_message(
            QueueUrl=HITL_QUEUE_URL,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=1,
            MessageAttributeNames=["All"],
        )

        messages = []
        for msg in response.get("Messages", []):
            body = json.loads(msg.get("Body", "{}"))
            messages.append({
                "receipt_handle": msg["ReceiptHandle"],
                "message_id": msg["MessageId"],
                **body,
            })

        return messages

    except Exception as e:
        st.error(f"Failed to load queue: {e}")
        return []


# ---- Review Actions ----
def submit_review(
    task_token: str,
    ticket_id: str,
    decision: str,
    edited_text: str = "",
    notes: str = "",
) -> bool:
    """Submit reviewer decision to Step Functions."""
    try:
        if decision in ("approved", "edited"):
            output = {
                "ticket_id": ticket_id,
                "draft": {
                    "draft_text": edited_text or "",
                    "confidence": 1.0,
                    "requires_escalation": False,
                },
                "approved_by": st.session_state.reviewer_id,
                "review_decision": decision,
            }

            sfn.send_task_success(
                taskToken=task_token,
                output=json.dumps(output),
            )
        else:
            sfn.send_task_failure(
                taskToken=task_token,
                error=f"Review{decision.title()}",
                cause=notes or f"Ticket {decision} by {st.session_state.reviewer_id}",
            )

        return True

    except Exception as e:
        st.error(f"Failed to submit review: {e}")
        return False


# ---- Main UI ----
def main() -> None:
    """Main dashboard layout."""
    if not check_auth():
        return

    # ---- Sidebar ----
    st.sidebar.title("🛡️ Insurance AI")
    st.sidebar.markdown(f"**Agent:** {st.session_state.reviewer_id}")

    if st.sidebar.button("🔄 Refresh"):
        st.rerun()

    if st.sidebar.button("🚪 Logout"):
        st.session_state.authenticated = False
        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Statistics")

    # ---- Main Content ----
    st.title("📋 Review Dashboard")
    st.markdown("Review AI-generated responses before they are sent to customers.")

    # Load reviews
    reviews = load_pending_reviews()
    queue_messages = load_queue_messages()

    all_items = reviews or []
    for msg in queue_messages:
        if msg.get("ticket", {}).get("ticket_id") not in [
            r["ticket_id"] for r in all_items
        ]:
            ticket = msg.get("ticket", {})
            all_items.append({
                "ticket_id": ticket.get("ticket_id", ""),
                "customer_id": ticket.get("customer_id", ""),
                "channel": ticket.get("channel", ""),
                "subject": ticket.get("subject", ""),
                "message_body": ticket.get("message_body", ""),
                "timestamp": ticket.get("timestamp", ""),
                "classification": ticket.get("classification", {}),
                "draft_response": msg.get("draft", {}).get("draft_text", ""),
                "confidence": msg.get("draft", {}).get("confidence", 0),
                "task_token": msg.get("task_token", ""),
                "review_type": msg.get("review_type", "draft_review"),
                "validation": msg.get("validation", {}),
            })

    # Stats
    st.sidebar.metric("Pending Reviews", len(all_items))

    if not all_items:
        st.success("✅ No pending reviews — all caught up!")
        return

    # ---- Review Cards ----
    for idx, item in enumerate(all_items):
        with st.expander(
            f"{'🔴' if item.get('review_type') == 'immediate_escalation' else '🟡'} "
            f"Ticket: {item['ticket_id'][:8]}... | "
            f"{item.get('channel', 'N/A')} | "
            f"Confidence: {item.get('confidence', 0):.0%}",
            expanded=(idx == 0),
        ):
            # ---- Ticket Info ----
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(f"**Customer:** `{item.get('customer_id', 'N/A')}`")
            with col2:
                st.markdown(f"**Channel:** `{item.get('channel', 'N/A')}`")
            with col3:
                classification = item.get("classification", {})
                intent = classification.get("intent", "N/A")
                st.markdown(f"**Intent:** `{intent}`")

            # Show escalation warning
            if classification.get("escalation_triggered"):
                keywords = classification.get("escalation_keywords_found", [])
                st.warning(
                    f"⚠️ **Escalation triggered** — Keywords: {', '.join(keywords)}"
                )

            # ---- Customer Message ----
            st.markdown("#### 📩 Customer Message")
            st.info(item.get("message_body", "No message body"))

            # ---- AI Draft ----
            st.markdown("#### 🤖 AI Draft Response")
            draft_text = item.get("draft_response", "")

            # Editable text area
            edited_text = st.text_area(
                "Edit response (or approve as-is):",
                value=draft_text,
                height=200,
                key=f"edit_{item['ticket_id']}",
            )

            # ---- Validation Info ----
            validation = item.get("validation", {})
            if validation:
                if validation.get("passed", True):
                    st.success("✅ All guardrails passed")
                else:
                    violations = validation.get("violations", [])
                    for v in violations:
                        st.error(f"❌ {v}")

            # ---- Action Buttons ----
            st.markdown("---")
            notes = st.text_input(
                "Notes (optional):", key=f"notes_{item['ticket_id']}"
            )

            cola, colb, colc, cold = st.columns(4)
            task_token = item.get("task_token", "")

            with cola:
                if st.button("✅ Approve", key=f"approve_{item['ticket_id']}"):
                    decision = "edited" if edited_text != draft_text else "approved"
                    if submit_review(
                        task_token, item["ticket_id"], decision,
                        edited_text, notes,
                    ):
                        st.success("Approved!")
                        st.rerun()

            with colb:
                if st.button("✏️ Edit & Approve", key=f"edit_approve_{item['ticket_id']}"):
                    if submit_review(
                        task_token, item["ticket_id"], "edited",
                        edited_text, notes,
                    ):
                        st.success("Edited and approved!")
                        st.rerun()

            with colc:
                if st.button("❌ Reject", key=f"reject_{item['ticket_id']}"):
                    if submit_review(
                        task_token, item["ticket_id"], "rejected",
                        notes=notes,
                    ):
                        st.warning("Rejected")
                        st.rerun()

            with cold:
                if st.button("🔺 Escalate", key=f"escalate_{item['ticket_id']}"):
                    if submit_review(
                        task_token, item["ticket_id"], "escalated",
                        notes=notes,
                    ):
                        st.info("Escalated to specialist")
                        st.rerun()


if __name__ == "__main__":
    main()
