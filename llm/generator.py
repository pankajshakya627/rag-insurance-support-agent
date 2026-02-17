"""
LLM Response Generator — Bedrock Claude integration with prompt engineering.

Constructs prompts from RAG context, invokes Claude via Bedrock,
and parses structured responses.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from jinja2 import Template

from config.prompts import GENERATION_TEMPLATE, SYSTEM_PROMPT
from config.settings import settings
from rag.retriever import RetrievalContext
from schemas.response import DraftResponse

logger = logging.getLogger(__name__)

bedrock_runtime = boto3.client("bedrock-runtime", region_name=settings.aws.region)


class ResponseGenerator:
    """
    Generates draft responses using Amazon Bedrock Claude 3.5 Sonnet.

    The generator follows a strict pattern:
    1. System prompt defines the agent persona and rules
    2. RAG context provides grounding
    3. Customer query (redacted) is the input
    4. Output is a structured DraftResponse with confidence and citations
    """

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or settings.bedrock.generation_model_id

    def generate(
        self,
        ticket_id: str,
        query: str,
        context: RetrievalContext,
        channel: str = "email",
        customer_id: str = "",
        guardrail_id: str | None = None,
    ) -> DraftResponse:
        """
        Generate a draft response for a customer query.

        If context is insufficient (strict_mode), returns a safe
        escalation response instead of hallucinating.
        """
        # Handle insufficient context (strict_mode)
        if not context.has_sufficient_context:
            return self._insufficient_context_response(ticket_id, context)

        # Construct the user prompt from template
        user_prompt = Template(GENERATION_TEMPLATE).render(
            context_chunks=context.chunks,
            channel=channel,
            customer_id=customer_id,
            query=query,
        )

        # Call Bedrock
        try:
            raw_response = self._invoke_bedrock(
                user_prompt=user_prompt,
                guardrail_id=guardrail_id,
            )

            # Parse structured response
            return self._parse_response(ticket_id, raw_response, context)

        except Exception as e:
            logger.error("Generation failed for ticket %s: %s", ticket_id, e)
            return DraftResponse(
                ticket_id=ticket_id,
                draft_text=(
                    "I apologize for the inconvenience. I'm unable to process "
                    "your request at this time. A team member will follow up "
                    "with you shortly."
                ),
                confidence=0.0,
                requires_escalation=True,
                escalation_reason=f"Generation failure: {e}",
                is_grounded=False,
            )

    def _invoke_bedrock(
        self,
        user_prompt: str,
        guardrail_id: str | None = None,
    ) -> str:
        """Invoke Bedrock Claude with the constructed prompt."""
        request_body: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": settings.bedrock.max_tokens,
            "temperature": settings.bedrock.temperature,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": user_prompt},
            ],
        }

        invoke_kwargs: dict[str, Any] = {
            "modelId": self.model_id,
            "contentType": "application/json",
            "accept": "application/json",
            "body": json.dumps(request_body),
        }

        # Apply Bedrock Guardrails if configured
        g_id = guardrail_id or settings.bedrock.guardrail_id
        if g_id:
            invoke_kwargs["guardrailIdentifier"] = g_id
            invoke_kwargs["guardrailVersion"] = settings.bedrock.guardrail_version

        response = bedrock_runtime.invoke_model(**invoke_kwargs)
        result = json.loads(response["body"].read().decode("utf-8"))

        # Extract text from Claude's response
        content_blocks = result.get("content", [])
        text_parts = [
            block.get("text", "")
            for block in content_blocks
            if block.get("type") == "text"
        ]

        return "\n".join(text_parts)

    def _parse_response(
        self,
        ticket_id: str,
        raw_response: str,
        context: RetrievalContext,
    ) -> DraftResponse:
        """Parse the structured JSON response from Claude."""
        parsed = self._extract_json(raw_response)

        if parsed:
            return DraftResponse(
                ticket_id=ticket_id,
                draft_text=parsed.get("draft_response", raw_response),
                cited_sections=parsed.get("cited_sections", []),
                confidence=float(parsed.get("confidence", 0.5)),
                requires_escalation=parsed.get("requires_escalation", False),
                escalation_reason=parsed.get("escalation_reason"),
                context_chunks_used=len(context.chunks),
                is_grounded=True,
            )

        # Fallback: treat raw response as the draft
        logger.warning("Could not parse JSON from generation, using raw response")
        return DraftResponse(
            ticket_id=ticket_id,
            draft_text=raw_response,
            confidence=0.5,
            context_chunks_used=len(context.chunks),
            is_grounded=True,
        )

    def _insufficient_context_response(
        self,
        ticket_id: str,
        context: RetrievalContext,
    ) -> DraftResponse:
        """Generate a safe response when RAG context is insufficient."""
        return DraftResponse(
            ticket_id=ticket_id,
            draft_text=(
                "Thank you for reaching out. I want to make sure I give you "
                "accurate information regarding your query. Let me connect you "
                "with a specialist who can help with this specific question. "
                "A team member will be in touch shortly."
            ),
            confidence=0.0,
            requires_escalation=True,
            escalation_reason=(
                f"Insufficient RAG context (max_score={context.max_similarity_score:.3f})"
            ),
            context_chunks_used=0,
            is_grounded=True,  # Safe response is by definition grounded
        )

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """Extract JSON from LLM response, handling markdown code blocks."""
        import re

        # Try direct parse
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass

        # Try markdown code block
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding JSON object
        match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return None
