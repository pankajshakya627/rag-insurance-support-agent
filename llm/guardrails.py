"""
Guardrails & Validation — pre/post-generation safety checks.

Implements:
  1. Pre-generation: Input toxicity detection
  2. Post-generation: Hallucination check, payout promise detection,
     off-topic filtering
  3. Bedrock Guardrails API integration (where available)
  4. Custom regex/keyword fallbacks
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import boto3
from jinja2 import Template

from config.prompts import HALLUCINATION_CHECK_TEMPLATE
from config.settings import settings

logger = logging.getLogger(__name__)

bedrock_runtime = boto3.client("bedrock-runtime", region_name=settings.aws.region)


@dataclass
class GuardrailResult:
    """Result of all guardrail checks on a response."""

    passed: bool = True
    violations: list[str] = field(default_factory=list)
    toxicity_detected: bool = False
    hallucination_detected: bool = False
    payout_promise_detected: bool = False
    off_topic_detected: bool = False
    severity: str = "none"  # none, low, medium, high, critical

    @property
    def should_block(self) -> bool:
        """Whether the response should be blocked from sending."""
        return self.severity in ("high", "critical") or not self.passed


# ---------------------------------------------------------------------------
# Payout promise patterns — things the AI must NEVER promise
# ---------------------------------------------------------------------------
PAYOUT_PATTERNS = [
    re.compile(r"you\s+will\s+receive\s+\$?\d+", re.IGNORECASE),
    re.compile(r"your\s+claim\s+(?:is|has been)\s+approved", re.IGNORECASE),
    re.compile(r"we\s+(?:will|shall)\s+pay\s+(?:you\s+)?\$?\d+", re.IGNORECASE),
    re.compile(r"guaranteed\s+(?:payout|payment|coverage)", re.IGNORECASE),
    re.compile(r"I\s+(?:can\s+)?confirm\s+(?:your\s+)?(?:claim|payout)", re.IGNORECASE),
    re.compile(r"(?:full|complete|total)\s+reimbursement\s+of", re.IGNORECASE),
    re.compile(r"entitled\s+to\s+\$?\d+", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Off-topic patterns — topics the insurance agent should NOT discuss
# ---------------------------------------------------------------------------
OFF_TOPIC_PATTERNS = [
    re.compile(r"(?:stock|crypto|bitcoin|investment)\s+(?:advice|tips|recommendation)", re.IGNORECASE),
    re.compile(r"(?:political|election|vote)\s+(?:opinion|view)", re.IGNORECASE),
    re.compile(r"(?:medical|health)\s+(?:diagnosis|prescription)", re.IGNORECASE),
    re.compile(r"(?:legal)\s+(?:advice|opinion)", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Toxicity indicators
# ---------------------------------------------------------------------------
TOXICITY_KEYWORDS = [
    "kill", "murder", "attack", "threaten", "bomb", "weapon",
    "hate", "racist", "sexist",
]


class GuardrailsValidator:
    """
    Multi-layered validation for LLM inputs and outputs.

    Layers:
    1. Bedrock Guardrails API (if configured)
    2. Custom regex-based checks
    3. LLM-based hallucination verification
    """

    def validate_input(self, text: str) -> GuardrailResult:
        """
        Pre-generation validation on customer input.

        Checks for toxicity and potential manipulation attempts.
        """
        result = GuardrailResult()

        # Check for toxicity
        text_lower = text.lower()
        found_toxic: list[str] = []
        for keyword in TOXICITY_KEYWORDS:
            if keyword in text_lower:
                found_toxic.append(keyword)

        if found_toxic:
            result.toxicity_detected = True
            result.violations.append(f"Toxic content detected: {', '.join(found_toxic)}")
            result.severity = "medium"

        # Check via Bedrock Guardrails API if configured
        if settings.bedrock.guardrail_id:
            bedrock_result = self._check_bedrock_guardrails(text, "INPUT")
            if bedrock_result:
                result.violations.extend(bedrock_result)
                result.severity = "high"
                result.passed = False

        return result

    def validate_output(
        self,
        response_text: str,
        context_chunks: list[dict[str, Any]] | None = None,
        run_hallucination_check: bool = True,
    ) -> GuardrailResult:
        """
        Post-generation validation on AI response.

        Runs all checks: payout promises, off-topic, hallucination, Bedrock guardrails.
        """
        result = GuardrailResult()

        # Check 1: Payout promises
        payout_violations = self._check_payout_promises(response_text)
        if payout_violations:
            result.payout_promise_detected = True
            result.violations.extend(payout_violations)
            result.severity = "critical"
            result.passed = False

        # Check 2: Off-topic content
        off_topic_violations = self._check_off_topic(response_text)
        if off_topic_violations:
            result.off_topic_detected = True
            result.violations.extend(off_topic_violations)
            result.severity = max(result.severity, "medium", key=_severity_rank)

        # Check 3: Hallucination (LLM-based, optional)
        if run_hallucination_check and context_chunks:
            hallucination_result = self._check_hallucination(
                response_text, context_chunks
            )
            if hallucination_result:
                result.hallucination_detected = True
                result.violations.extend(hallucination_result)
                result.severity = max(result.severity, "high", key=_severity_rank)
                result.passed = False

        # Check 4: Bedrock Guardrails API
        if settings.bedrock.guardrail_id:
            bedrock_violations = self._check_bedrock_guardrails(
                response_text, "OUTPUT"
            )
            if bedrock_violations:
                result.violations.extend(bedrock_violations)
                result.severity = "high"
                result.passed = False

        if result.violations:
            logger.warning(
                "Guardrail violations found (severity=%s): %s",
                result.severity,
                "; ".join(result.violations),
            )

        return result

    def _check_payout_promises(self, text: str) -> list[str]:
        """Detect unauthorized financial promises in the response."""
        violations: list[str] = []

        for pattern in PAYOUT_PATTERNS:
            matches = pattern.findall(text)
            for match in matches:
                violations.append(f"Payout promise detected: '{match}'")

        return violations

    def _check_off_topic(self, text: str) -> list[str]:
        """Detect off-topic content outside insurance support scope."""
        violations: list[str] = []

        for pattern in OFF_TOPIC_PATTERNS:
            if pattern.search(text):
                violations.append(f"Off-topic content: pattern '{pattern.pattern}'")

        return violations

    def _check_hallucination(
        self,
        response_text: str,
        context_chunks: list[dict[str, Any]],
    ) -> list[str]:
        """
        Use a second LLM call to verify response is grounded in context.

        This is the most expensive check — only run when necessary.
        """
        prompt = Template(HALLUCINATION_CHECK_TEMPLATE).render(
            context_chunks=context_chunks,
            response=response_text,
        )

        try:
            response = bedrock_runtime.invoke_model(
                modelId=settings.bedrock.generation_model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 512,
                    "temperature": 0.0,
                    "messages": [{"role": "user", "content": prompt}],
                }),
            )

            result = json.loads(response["body"].read().decode("utf-8"))
            text = result.get("content", [{}])[0].get("text", "{}")

            parsed = _extract_json(text)
            if parsed and not parsed.get("is_grounded", True):
                claims = parsed.get("unsupported_claims", [])
                return [f"Hallucination — unsupported claim: '{c}'" for c in claims]

        except Exception as e:
            logger.error("Hallucination check failed: %s", e)
            return ["Hallucination check could not be completed"]

        return []

    def _check_bedrock_guardrails(self, text: str, source: str) -> list[str]:
        """
        Apply Bedrock Guardrails API for content filtering.

        Returns a list of violations if any content policy is triggered.
        """
        try:
            response = bedrock_runtime.apply_guardrail(
                guardrailIdentifier=settings.bedrock.guardrail_id,
                guardrailVersion=settings.bedrock.guardrail_version,
                source=source,
                content=[{"text": {"text": text}}],
            )

            action = response.get("action", "NONE")
            if action == "GUARDRAIL_INTERVENED":
                outputs = response.get("outputs", [])
                violations = [
                    f"Bedrock Guardrail: {o.get('text', 'blocked')}"
                    for o in outputs
                ]
                return violations

        except Exception as e:
            logger.error("Bedrock Guardrails API call failed: %s", e)

        return []


def _severity_rank(severity: str) -> int:
    """Map severity string to numeric rank for comparison."""
    return {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}.get(
        severity, 0
    )


def _extract_json(text: str) -> dict | None:
    """Extract JSON from text."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None
