"""
Attachment Processor Lambda — extracts text from PDF/image attachments using Textract.

Trigger: Called by Step Functions after ingestion
Input:   Ticket with attachment S3 URIs
Output:  Ticket with extracted_attachment_text populated
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

textract = boto3.client("textract")
s3 = boto3.client("s3")

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif"}


def handler(event: dict[str, Any], context: Any) -> dict:
    """
    Lambda entry point for attachment text extraction.

    Expects event with the normalized ticket JSON.
    Returns the ticket with extracted_attachment_text populated.
    """
    ticket = event.get("ticket", event)
    attachments = ticket.get("attachments", [])

    if not attachments:
        logger.info("No attachments to process for ticket %s", ticket.get("ticket_id"))
        ticket["extracted_attachment_text"] = ""
        return ticket

    extracted_texts: list[str] = []

    for attachment_uri in attachments:
        try:
            text = _extract_text(attachment_uri)
            if text:
                extracted_texts.append(f"[Attachment: {attachment_uri.split('/')[-1]}]\n{text}")
        except Exception as e:
            logger.error("Failed to extract text from %s: %s", attachment_uri, e)
            extracted_texts.append(f"[Attachment: {attachment_uri} — extraction failed]")

    ticket["extracted_attachment_text"] = "\n\n---\n\n".join(extracted_texts)

    logger.info(
        "Extracted text from %d/%d attachments for ticket %s",
        len(extracted_texts),
        len(attachments),
        ticket.get("ticket_id"),
    )

    return ticket


def _extract_text(s3_uri: str) -> str:
    """
    Extract text from an S3 object using Amazon Textract.

    Supports both synchronous (single-page) and asynchronous (multi-page PDF) modes.
    """
    # Skip non-S3 URIs (e.g., whatsapp-media://)
    if not s3_uri.startswith("s3://"):
        logger.warning("Skipping non-S3 attachment: %s", s3_uri)
        return ""

    # Check file extension
    extension = "." + s3_uri.rsplit(".", 1)[-1].lower() if "." in s3_uri else ""
    if extension not in SUPPORTED_EXTENSIONS:
        logger.warning("Unsupported file type: %s", extension)
        return ""

    # Parse S3 URI
    bucket, key = _parse_s3_uri(s3_uri)

    if extension == ".pdf":
        return _extract_pdf_async(bucket, key)
    else:
        return _extract_image_sync(bucket, key)


def _extract_image_sync(bucket: str, key: str) -> str:
    """Synchronous Textract for single-page images."""
    response = textract.detect_document_text(
        Document={"S3Object": {"Bucket": bucket, "Name": key}}
    )

    lines = []
    for block in response.get("Blocks", []):
        if block["BlockType"] == "LINE":
            lines.append(block.get("Text", ""))

    return "\n".join(lines)


def _extract_pdf_async(bucket: str, key: str) -> str:
    """
    Asynchronous Textract for multi-page PDFs.

    Starts an async job and polls for completion.
    """
    # Start async text detection
    response = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}}
    )
    job_id = response["JobId"]
    logger.info("Started Textract job %s for %s/%s", job_id, bucket, key)

    # Poll for completion (with exponential backoff)
    max_wait = 300  # 5 minutes
    wait_time = 0
    interval = 5

    while wait_time < max_wait:
        time.sleep(interval)
        wait_time += interval

        result = textract.get_document_text_detection(JobId=job_id)
        status = result["JobStatus"]

        if status == "SUCCEEDED":
            return _collect_textract_results(job_id, result)
        elif status == "FAILED":
            logger.error("Textract job %s failed: %s", job_id, result.get("StatusMessage"))
            return ""

        # Exponential backoff (cap at 30s)
        interval = min(interval * 1.5, 30)

    logger.error("Textract job %s timed out after %ds", job_id, max_wait)
    return ""


def _collect_textract_results(job_id: str, first_result: dict) -> str:
    """Collect all pages of Textract results, handling pagination."""
    lines: list[str] = []

    result = first_result
    while True:
        for block in result.get("Blocks", []):
            if block["BlockType"] == "LINE":
                lines.append(block.get("Text", ""))

        # Check for more pages
        next_token = result.get("NextToken")
        if not next_token:
            break

        result = textract.get_document_text_detection(
            JobId=job_id, NextToken=next_token
        )

    return "\n".join(lines)


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse s3://bucket/key into (bucket, key)."""
    path = uri.replace("s3://", "")
    parts = path.split("/", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""
