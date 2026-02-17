"""
PII Redaction Lambda — masks sensitive data before LLM invocation.

Supports two backends:
  1. Amazon Comprehend (managed, default)
  2. SageMaker custom NER endpoint (insurance-specific PII patterns)

The redactor returns masked text AND a PII mapping so approved responses
can have PII restored before sending to the customer.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import boto3

from config.settings import settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

comprehend = boto3.client("comprehend")
sagemaker_runtime = boto3.client("sagemaker-runtime")

# Insurance-specific PII regex patterns (fallback layer)
INSURANCE_PII_PATTERNS = {
    "POLICY_NUMBER": re.compile(r"\b(?:POL|INS|PLY)[-/]?\d{6,12}\b", re.IGNORECASE),
    "CLAIM_NUMBER": re.compile(r"\b(?:CLM|CLAIM)[-/]?\d{6,12}\b", re.IGNORECASE),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CREDIT_CARD": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    "PHONE": re.compile(r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "DATE_OF_BIRTH": re.compile(
        r"\b(?:DOB|Date of Birth)[:\s]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b",
        re.IGNORECASE,
    ),
}


def handler(event: dict[str, Any], context: Any) -> dict:
    """
    Lambda entry point for PII redaction.

    Input:  ticket dict with message_body (and optionally extracted_attachment_text)
    Output: ticket dict with redacted text + pii_mapping for later restoration
    """
    ticket = event.get("ticket", event)
    message_body = ticket.get("message_body", "")
    attachment_text = ticket.get("extracted_attachment_text", "")

    # Combine for full redaction
    full_text = message_body
    if attachment_text:
        full_text += f"\n\n[Attachment Content]\n{attachment_text}"

    # Run redaction pipeline
    if settings.use_sagemaker_pii:
        redacted_text, pii_mapping = _redact_with_sagemaker(full_text)
    else:
        redacted_text, pii_mapping = _redact_with_comprehend(full_text)

    # Apply insurance-specific regex patterns as additional layer
    redacted_text, regex_pii = _redact_with_regex(redacted_text)
    pii_mapping.update(regex_pii)

    # Update ticket
    ticket["message_body_redacted"] = redacted_text
    ticket["pii_mapping"] = pii_mapping
    ticket["pii_detected_count"] = len(pii_mapping)

    logger.info(
        "Redacted %d PII entities for ticket %s",
        len(pii_mapping),
        ticket.get("ticket_id"),
    )

    return ticket


def _redact_with_comprehend(text: str) -> tuple[str, dict[str, str]]:
    """
    Use Amazon Comprehend to detect and mask PII entities.

    Comprehend supports: NAME, ADDRESS, SSN, CREDIT_DEBIT_NUMBER, etc.
    """
    pii_mapping: dict[str, str] = {}

    # Comprehend has a 100KB limit per call
    if len(text.encode("utf-8")) > 100_000:
        chunks = _chunk_text(text, max_bytes=90_000)
        results_text = []
        for chunk in chunks:
            redacted, mapping = _comprehend_detect_and_mask(chunk)
            results_text.append(redacted)
            pii_mapping.update(mapping)
        return "\n".join(results_text), pii_mapping

    return _comprehend_detect_and_mask(text)


def _comprehend_detect_and_mask(text: str) -> tuple[str, dict[str, str]]:
    """Call Comprehend DetectPiiEntities and mask the results."""
    pii_mapping: dict[str, str] = {}

    response = comprehend.detect_pii_entities(
        Text=text,
        LanguageCode="en",
    )

    # Sort entities by offset (reverse) to replace from end to avoid index shifts
    entities = sorted(
        response.get("Entities", []),
        key=lambda e: e["BeginOffset"],
        reverse=True,
    )

    redacted = text
    for entity in entities:
        start = entity["BeginOffset"]
        end = entity["EndOffset"]
        entity_type = entity["Type"]
        original = text[start:end]
        placeholder = f"[{entity_type}_{len(pii_mapping)}]"

        pii_mapping[placeholder] = original
        redacted = redacted[:start] + placeholder + redacted[end:]

    return redacted, pii_mapping


def _redact_with_sagemaker(text: str) -> tuple[str, dict[str, str]]:
    """
    Use a custom SageMaker NER endpoint for insurance-specific PII detection.

    The endpoint is expected to return entities in the format:
    [{"text": "...", "label": "POLICY_NUMBER", "start": 0, "end": 12}, ...]
    """
    pii_mapping: dict[str, str] = {}

    try:
        response = sagemaker_runtime.invoke_endpoint(
            EndpointName=settings.sagemaker.pii_endpoint_name,
            ContentType="application/json",
            Body=json.dumps({"text": text}),
        )

        result = json.loads(response["Body"].read().decode("utf-8"))
        entities = result.get("entities", [])

        # Sort by offset (reverse)
        entities.sort(key=lambda e: e["start"], reverse=True)

        redacted = text
        for entity in entities:
            start = entity["start"]
            end = entity["end"]
            label = entity["label"]
            original = entity["text"]
            placeholder = f"[{label}_{len(pii_mapping)}]"

            pii_mapping[placeholder] = original
            redacted = redacted[:start] + placeholder + redacted[end:]

        return redacted, pii_mapping

    except Exception as e:
        logger.error("SageMaker PII endpoint failed, falling back to Comprehend: %s", e)
        return _redact_with_comprehend(text)


def _redact_with_regex(text: str) -> tuple[str, dict[str, str]]:
    """Apply insurance-specific regex patterns for PII the ML models might miss."""
    pii_mapping: dict[str, str] = {}
    redacted = text

    for pii_type, pattern in INSURANCE_PII_PATTERNS.items():
        for match in pattern.finditer(redacted):
            original = match.group()
            # Skip if already redacted (inside square brackets)
            if f"[{pii_type}" in redacted[max(0, match.start() - 20) : match.start()]:
                continue
            placeholder = f"[{pii_type}_{len(pii_mapping)}]"
            pii_mapping[placeholder] = original
            redacted = redacted[: match.start()] + placeholder + redacted[match.end() :]

    return redacted, pii_mapping


def _chunk_text(text: str, max_bytes: int = 90_000) -> list[str]:
    """Split text into chunks that fit within Comprehend's byte limit."""
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_size = 0

    for line in text.split("\n"):
        line_bytes = len(line.encode("utf-8")) + 1  # +1 for newline
        if current_size + line_bytes > max_bytes and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_size = 0
        current_chunk.append(line)
        current_size += line_bytes

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


def restore_pii(text: str, pii_mapping: dict[str, str]) -> str:
    """
    Restore PII placeholders in the final approved response.

    Called AFTER human review and before sending to the customer.
    """
    restored = text
    for placeholder, original in pii_mapping.items():
        restored = restored.replace(placeholder, original)
    return restored
