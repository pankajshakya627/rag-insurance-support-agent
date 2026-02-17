"""
Intent Classification Lambda — categorizes customer queries for routing.

Supports two backends:
  1. Amazon Bedrock (Claude) — default, zero-shot classification
  2. SageMaker endpoint — fine-tuned classifier for production

Also applies keyword-based escalation rules for compliance-critical topics.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

from config.prompts import INTENT_CLASSIFICATION_TEMPLATE
from config.settings import settings
from schemas.classification import IntentClassification, IntentType

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

bedrock_runtime = boto3.client("bedrock-runtime")
sagemaker_runtime = boto3.client("sagemaker-runtime")


def handler(event: dict[str, Any], context: Any) -> dict:
    """
    Lambda entry point for intent classification.

    Input:  ticket dict with message_body_redacted
    Output: ticket dict with classification results added
    """
    ticket = event.get("ticket", event)
    message = ticket.get("message_body_redacted", ticket.get("message_body", ""))

    if not message:
        logger.warning("Empty message for ticket %s", ticket.get("ticket_id"))
        classification = IntentClassification(
            intent=IntentType.GENERAL_INQUIRY,
            confidence=0.0,
            reasoning="Empty message — defaulting to general inquiry",
        )
    elif settings.use_sagemaker_classifier:
        classification = _classify_with_sagemaker(message)
    else:
        classification = _classify_with_bedrock(message)

    # Apply escalation keyword rules (overrides model classification)
    classification = _apply_escalation_rules(message, classification)

    # Determine HITL requirement
    classification.force_hitl = _requires_human_review(classification)

    # Attach to ticket
    ticket["classification"] = classification.model_dump()

    logger.info(
        "Ticket %s classified as %s (confidence=%.2f, hitl=%s)",
        ticket.get("ticket_id"),
        classification.intent.value,
        classification.confidence,
        classification.force_hitl,
    )

    return ticket


def _classify_with_bedrock(message: str) -> IntentClassification:
    """
    Zero-shot classification using Bedrock Claude.

    Uses the INTENT_CLASSIFICATION_TEMPLATE prompt to get structured output.
    """
    from jinja2 import Template

    prompt = Template(INTENT_CLASSIFICATION_TEMPLATE).render(message=message)

    try:
        response = bedrock_runtime.invoke_model(
            modelId=settings.bedrock.generation_model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 512,
                "temperature": 0.1,
                "messages": [
                    {"role": "user", "content": prompt},
                ],
            }),
        )

        result = json.loads(response["body"].read().decode("utf-8"))
        content = result.get("content", [{}])[0].get("text", "{}")

        # Parse JSON from response
        parsed = _extract_json(content)

        intent_str = parsed.get("intent", "GENERAL_INQUIRY").upper()
        intent_map = {
            "GENERAL_INQUIRY": IntentType.GENERAL_INQUIRY,
            "POLICY_CHANGE": IntentType.POLICY_CHANGE,
            "COMPLAINT_MISSELLING": IntentType.COMPLAINT_MISSELLING,
            "CLAIM_ISSUE": IntentType.CLAIM_ISSUE,
        }

        return IntentClassification(
            intent=intent_map.get(intent_str, IntentType.GENERAL_INQUIRY),
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", ""),
        )

    except Exception as e:
        logger.error("Bedrock classification failed: %s", e)
        return IntentClassification(
            intent=IntentType.GENERAL_INQUIRY,
            confidence=0.0,
            reasoning=f"Classification failed: {e}",
            force_hitl=True,
        )


def _classify_with_sagemaker(message: str) -> IntentClassification:
    """
    Classification using a fine-tuned SageMaker model.

    The endpoint returns: {"label": "GENERAL_INQUIRY", "score": 0.95}
    """
    try:
        response = sagemaker_runtime.invoke_endpoint(
            EndpointName=settings.sagemaker.classifier_endpoint_name,
            ContentType="application/json",
            Body=json.dumps({"text": message}),
        )

        result = json.loads(response["Body"].read().decode("utf-8"))

        intent_str = result.get("label", "GENERAL_INQUIRY").upper()
        intent_map = {
            "GENERAL_INQUIRY": IntentType.GENERAL_INQUIRY,
            "POLICY_CHANGE": IntentType.POLICY_CHANGE,
            "COMPLAINT_MISSELLING": IntentType.COMPLAINT_MISSELLING,
            "CLAIM_ISSUE": IntentType.CLAIM_ISSUE,
        }

        return IntentClassification(
            intent=intent_map.get(intent_str, IntentType.GENERAL_INQUIRY),
            confidence=float(result.get("score", 0.5)),
            reasoning=result.get("reasoning", "SageMaker classification"),
        )

    except Exception as e:
        logger.error("SageMaker classification failed, falling back to Bedrock: %s", e)
        return _classify_with_bedrock(message)


def _apply_escalation_rules(
    message: str, classification: IntentClassification
) -> IntentClassification:
    """
    Check message for escalation keywords that require mandatory human review.

    These keywords indicate potential legal, compliance, or fraud issues.
    """
    message_lower = message.lower()
    found_keywords: list[str] = []

    for keyword in settings.hitl.escalation_keywords:
        if keyword.lower() in message_lower:
            found_keywords.append(keyword)

    if found_keywords:
        classification.escalation_triggered = True
        classification.escalation_keywords_found = found_keywords

        # Override to complaint if escalation words found
        if classification.intent not in (
            IntentType.COMPLAINT_MISSELLING,
            IntentType.CLAIM_ISSUE,
        ):
            classification.intent = IntentType.COMPLAINT_MISSELLING
            classification.reasoning += (
                f" [ESCALATED: keywords detected — {', '.join(found_keywords)}]"
            )

        logger.warning(
            "Escalation keywords detected: %s", ", ".join(found_keywords)
        )

    return classification


def _requires_human_review(classification: IntentClassification) -> bool:
    """Determine if this ticket must go through HITL review."""
    # Always HITL for escalated tickets
    if classification.escalation_triggered:
        return True

    # Always HITL for complaints and claim issues
    if classification.intent in (
        IntentType.COMPLAINT_MISSELLING,
        IntentType.CLAIM_ISSUE,
    ):
        return True

    # HITL if confidence is too low
    if classification.confidence < settings.hitl.auto_approve_confidence:
        return True

    return False


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response text, handling markdown code blocks."""
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    import re

    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding JSON-like structure
    brace_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group())
        except json.JSONDecodeError:
            pass

    logger.warning("Could not extract JSON from: %s", text[:200])
    return {}
