"""
Jinja2 prompt templates for the LLM module.

Provides template rendering utilities and pre-built template objects.
"""

from __future__ import annotations

from jinja2 import Template

from config.prompts import (
    CLARIFICATION_TEMPLATE,
    DE_ESCALATION_TEMPLATE,
    GENERATION_TEMPLATE,
    HALLUCINATION_CHECK_TEMPLATE,
    INTENT_CLASSIFICATION_TEMPLATE,
    LANGUAGE_DETECTION_TEMPLATE,
)

# Pre-compiled templates for performance
generation_template = Template(GENERATION_TEMPLATE)
classification_template = Template(INTENT_CLASSIFICATION_TEMPLATE)
clarification_template = Template(CLARIFICATION_TEMPLATE)
de_escalation_template = Template(DE_ESCALATION_TEMPLATE)
language_detection_template = Template(LANGUAGE_DETECTION_TEMPLATE)
hallucination_check_template = Template(HALLUCINATION_CHECK_TEMPLATE)


def render_generation_prompt(
    context_chunks: list[dict],
    channel: str,
    customer_id: str,
    query: str,
) -> str:
    """Render the response generation prompt."""
    return generation_template.render(
        context_chunks=context_chunks,
        channel=channel,
        customer_id=customer_id,
        query=query,
    )


def render_clarification_prompt(
    query: str,
    missing_fields: list[str],
) -> str:
    """Render a clarification request prompt."""
    return clarification_template.render(
        query=query,
        missing_fields=missing_fields,
    )


def render_de_escalation_prompt(
    message: str,
    sentiment: str,
) -> str:
    """Render a de-escalation response prompt."""
    return de_escalation_template.render(
        message=message,
        sentiment=sentiment,
    )
