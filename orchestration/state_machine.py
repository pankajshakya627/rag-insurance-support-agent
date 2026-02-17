"""
Step Functions State Machine Definition — ASL (Amazon States Language).

Defines the complete orchestration workflow:
  Ingest → PII Redact → Classify → [HITL Branch] → RAG Retrieve →
  Generate Response → Validate → [Auto/HITL Approval] → Send → Audit
"""

from __future__ import annotations

import json


def build_state_machine_definition(
    pii_lambda_arn: str,
    classifier_lambda_arn: str,
    attachment_lambda_arn: str,
    rag_lambda_arn: str,
    generator_lambda_arn: str,
    validator_lambda_arn: str,
    response_sender_lambda_arn: str,
    feedback_lambda_arn: str,
    hitl_queue_url: str,
    dlq_arn: str,
) -> dict:
    """
    Build the AWS Step Functions state machine definition (ASL).

    This is a standard JSON ASL definition that can be deployed via CDK.
    """
    return {
        "Comment": "Insurance AI Customer Support — Orchestration Pipeline",
        "StartAt": "ProcessAttachments",
        "States": {
            # ---- Phase 1: Attachment Processing ----
            "ProcessAttachments": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": attachment_lambda_arn,
                    "Payload": {"ticket.$": "$"},
                },
                "ResultSelector": {"ticket.$": "$.Payload"},
                "ResultPath": "$",
                "Retry": _standard_retry(),
                "Catch": [_catch_all("AttachmentProcessingFailed")],
                "Next": "RedactPII",
            },
            # ---- Phase 2: PII Redaction ----
            "RedactPII": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": pii_lambda_arn,
                    "Payload": {"ticket.$": "$.ticket"},
                },
                "ResultSelector": {"ticket.$": "$.Payload"},
                "ResultPath": "$",
                "Retry": _standard_retry(),
                "Catch": [_catch_all("PIIRedactionFailed")],
                "Next": "ClassifyIntent",
            },
            # ---- Phase 3: Intent Classification ----
            "ClassifyIntent": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": classifier_lambda_arn,
                    "Payload": {"ticket.$": "$.ticket"},
                },
                "ResultSelector": {"ticket.$": "$.Payload"},
                "ResultPath": "$",
                "Retry": _standard_retry(),
                "Catch": [_catch_all("ClassificationFailed")],
                "Next": "CheckEscalation",
            },
            # ---- Phase 3b: Escalation Check ----
            "CheckEscalation": {
                "Type": "Choice",
                "Choices": [
                    {
                        "Variable": "$.ticket.classification.force_hitl",
                        "BooleanEquals": True,
                        "Next": "ImmediateHITLReview",
                    },
                ],
                "Default": "RetrieveContext",
            },
            # ---- Immediate HITL for high-risk tickets ----
            "ImmediateHITLReview": {
                "Type": "Task",
                "Resource": "arn:aws:states:::sqs:sendMessage.waitForTaskToken",
                "Parameters": {
                    "QueueUrl": hitl_queue_url,
                    "MessageBody": {
                        "ticket.$": "$.ticket",
                        "review_type": "immediate_escalation",
                        "task_token.$": "$$.Task.Token",
                    },
                },
                "TimeoutSeconds": 86400,  # 24 hour timeout
                "Catch": [_catch_all("HITLReviewTimeout")],
                "Next": "SendResponse",
            },
            # ---- Phase 4: RAG Retrieval ----
            "RetrieveContext": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": rag_lambda_arn,
                    "Payload": {"ticket.$": "$.ticket"},
                },
                "ResultSelector": {
                    "ticket.$": "$.Payload.ticket",
                    "context.$": "$.Payload.context",
                },
                "ResultPath": "$",
                "Retry": _standard_retry(),
                "Catch": [_catch_all("RetrievalFailed")],
                "Next": "GenerateResponse",
            },
            # ---- Phase 5: Response Generation ----
            "GenerateResponse": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": generator_lambda_arn,
                    "Payload": {
                        "ticket.$": "$.ticket",
                        "context.$": "$.context",
                    },
                },
                "ResultSelector": {
                    "ticket.$": "$.Payload.ticket",
                    "context.$": "$.Payload.context",
                    "draft.$": "$.Payload.draft",
                },
                "ResultPath": "$",
                "Retry": _standard_retry(),
                "Catch": [_catch_all("GenerationFailed")],
                "Next": "ValidateResponse",
            },
            # ---- Phase 6: Validation ----
            "ValidateResponse": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": validator_lambda_arn,
                    "Payload": {
                        "draft.$": "$.draft",
                        "context.$": "$.context",
                    },
                },
                "ResultSelector": {
                    "ticket.$": "$.Payload.ticket",
                    "draft.$": "$.Payload.draft",
                    "validation.$": "$.Payload.validation",
                },
                "ResultPath": "$",
                "Retry": _standard_retry(),
                "Catch": [_catch_all("ValidationFailed")],
                "Next": "ApprovalDecision",
            },
            # ---- Phase 7: Auto-approve or HITL ----
            "ApprovalDecision": {
                "Type": "Choice",
                "Choices": [
                    # Block if guardrails failed
                    {
                        "Variable": "$.validation.should_block",
                        "BooleanEquals": True,
                        "Next": "HITLReview",
                    },
                    # Block if draft requires escalation
                    {
                        "Variable": "$.draft.requires_escalation",
                        "BooleanEquals": True,
                        "Next": "HITLReview",
                    },
                    # Auto-approve if classification allows it
                    {
                        "And": [
                            {
                                "Variable": "$.ticket.classification.intent",
                                "StringEquals": "general_inquiry",
                            },
                            {
                                "Variable": "$.draft.confidence",
                                "NumericGreaterThanEquals": 0.9,
                            },
                        ],
                        "Next": "AutoApprove",
                    },
                ],
                "Default": "HITLReview",
            },
            # ---- Auto-approval path ----
            "AutoApprove": {
                "Type": "Pass",
                "Parameters": {
                    "ticket.$": "$.ticket",
                    "draft.$": "$.draft",
                    "approved_by": "auto",
                    "review_decision": "approved",
                },
                "Next": "SendResponse",
            },
            # ---- HITL Review (callback pattern) ----
            "HITLReview": {
                "Type": "Task",
                "Resource": "arn:aws:states:::sqs:sendMessage.waitForTaskToken",
                "Parameters": {
                    "QueueUrl": hitl_queue_url,
                    "MessageBody": {
                        "ticket.$": "$.ticket",
                        "draft.$": "$.draft",
                        "validation.$": "$.validation",
                        "review_type": "draft_review",
                        "task_token.$": "$$.Task.Token",
                    },
                },
                "TimeoutSeconds": 86400,
                "Catch": [_catch_all("HITLReviewTimeout")],
                "Next": "SendResponse",
            },
            # ---- Phase 8: Send Response ----
            "SendResponse": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": response_sender_lambda_arn,
                    "Payload.$": "$",
                },
                "ResultSelector": {"result.$": "$.Payload"},
                "Retry": _standard_retry(),
                "Catch": [_catch_all("SendFailed")],
                "Next": "TicketResolved",
            },
            # ---- Success state ----
            "TicketResolved": {
                "Type": "Succeed",
            },
            # ---- Error states ----
            "AttachmentProcessingFailed": _error_state("Attachment processing failed"),
            "PIIRedactionFailed": _error_state("PII redaction failed"),
            "ClassificationFailed": _error_state("Intent classification failed"),
            "RetrievalFailed": _error_state("RAG retrieval failed"),
            "GenerationFailed": _error_state("Response generation failed"),
            "ValidationFailed": _error_state("Response validation failed"),
            "HITLReviewTimeout": _error_state("HITL review timed out (24h)"),
            "SendFailed": _error_state("Response sending failed"),
        },
    }


def _standard_retry() -> list[dict]:
    """Standard retry configuration for Lambda invocations."""
    return [
        {
            "ErrorEquals": [
                "Lambda.ServiceException",
                "Lambda.AWSLambdaException",
                "Lambda.SdkClientException",
                "States.TaskFailed",
            ],
            "IntervalSeconds": 5,
            "MaxAttempts": 3,
            "BackoffRate": 2.0,
        },
    ]


def _catch_all(error_state: str) -> dict:
    """Catch-all error handler routing to the given error state."""
    return {
        "ErrorEquals": ["States.ALL"],
        "ResultPath": "$.error_info",
        "Next": error_state,
    }


def _error_state(description: str) -> dict:
    """Terminal error state that logs the failure."""
    return {
        "Type": "Fail",
        "Error": description,
        "Cause": description,
    }


def export_asl_json(
    output_path: str = "state_machine_definition.json",
    **lambda_arns: str,
) -> None:
    """Export the state machine definition to a JSON file for CDK."""
    definition = build_state_machine_definition(**lambda_arns)
    with open(output_path, "w") as f:
        json.dump(definition, f, indent=2)
