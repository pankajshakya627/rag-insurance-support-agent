"""
Centralized configuration for the Insurance AI Agent.

All settings are loaded from environment variables with sensible defaults
for local development. In production, these are injected via Lambda env
vars or SSM Parameter Store.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings
from pydantic import Field


class AWSSettings(BaseSettings):
    """Core AWS configuration."""

    region: str = Field(default="us-east-1", alias="AWS_REGION")
    account_id: str = Field(default="", alias="AWS_ACCOUNT_ID")


class S3Settings(BaseSettings):
    """S3 bucket names for different data domains."""

    raw_messages_bucket: str = Field(
        default="insurance-ai-raw-messages",
        alias="S3_RAW_MESSAGES_BUCKET",
    )
    audit_logs_bucket: str = Field(
        default="insurance-ai-audit-logs",
        alias="S3_AUDIT_LOGS_BUCKET",
    )
    attachments_bucket: str = Field(
        default="insurance-ai-attachments",
        alias="S3_ATTACHMENTS_BUCKET",
    )
    finetuning_bucket: str = Field(
        default="insurance-ai-finetuning-data",
        alias="S3_FINETUNING_BUCKET",
    )


class DynamoDBSettings(BaseSettings):
    """DynamoDB table names."""

    tickets_table: str = Field(
        default="InsuranceAI-Tickets",
        alias="DYNAMODB_TICKETS_TABLE",
    )
    conversation_state_table: str = Field(
        default="InsuranceAI-ConversationState",
        alias="DYNAMODB_CONVERSATION_TABLE",
    )
    customer_profiles_table: str = Field(
        default="InsuranceAI-CustomerProfiles",
        alias="DYNAMODB_CUSTOMER_PROFILES_TABLE",
    )


class OpenSearchSettings(BaseSettings):
    """OpenSearch Serverless configuration for vector store."""

    endpoint: str = Field(
        default="",
        alias="OPENSEARCH_ENDPOINT",
    )
    collection_name: str = Field(
        default="insurance-knowledge-base",
        alias="OPENSEARCH_COLLECTION",
    )
    policy_index: str = Field(
        default="policy-documents",
        alias="OPENSEARCH_POLICY_INDEX",
    )
    historical_index: str = Field(
        default="historical-tickets",
        alias="OPENSEARCH_HISTORICAL_INDEX",
    )
    compliance_index: str = Field(
        default="compliance-rules",
        alias="OPENSEARCH_COMPLIANCE_INDEX",
    )
    embedding_dimension: int = Field(default=1024)
    top_k: int = Field(default=5)
    similarity_threshold: float = Field(default=0.7)


class BedrockSettings(BaseSettings):
    """Amazon Bedrock model configuration."""

    generation_model_id: str = Field(
        default="anthropic.claude-3-5-sonnet-20241022-v2:0",
        alias="BEDROCK_GENERATION_MODEL",
    )
    embedding_model_id: str = Field(
        default="amazon.titan-embed-text-v2:0",
        alias="BEDROCK_EMBEDDING_MODEL",
    )
    guardrail_id: str = Field(
        default="",
        alias="BEDROCK_GUARDRAIL_ID",
    )
    guardrail_version: str = Field(
        default="DRAFT",
        alias="BEDROCK_GUARDRAIL_VERSION",
    )
    max_tokens: int = Field(default=2048)
    temperature: float = Field(default=0.2)


class SageMakerSettings(BaseSettings):
    """SageMaker endpoint configuration."""

    pii_endpoint_name: str = Field(
        default="insurance-pii-ner",
        alias="SAGEMAKER_PII_ENDPOINT",
    )
    classifier_endpoint_name: str = Field(
        default="insurance-intent-classifier",
        alias="SAGEMAKER_CLASSIFIER_ENDPOINT",
    )


class HITLSettings(BaseSettings):
    """Human-in-the-loop thresholds and configuration."""

    auto_approve_confidence: float = Field(default=0.90)
    escalation_keywords: list[str] = Field(
        default=[
            "lawyer", "sue", "fraud", "mis-sold", "misselling",
            "mis-selling", "legal", "ombudsman", "regulator",
            "compensation", "negligence",
        ],
    )
    sns_review_topic_arn: str = Field(
        default="",
        alias="SNS_HITL_REVIEW_TOPIC",
    )


class CognitoSettings(BaseSettings):
    """Cognito user pool for HITL dashboard authentication."""

    user_pool_id: str = Field(default="", alias="COGNITO_USER_POOL_ID")
    client_id: str = Field(default="", alias="COGNITO_CLIENT_ID")
    domain: str = Field(default="", alias="COGNITO_DOMAIN")


class Settings(BaseSettings):
    """Root settings container aggregating all sub-configurations."""

    aws: AWSSettings = Field(default_factory=AWSSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    dynamodb: DynamoDBSettings = Field(default_factory=DynamoDBSettings)
    opensearch: OpenSearchSettings = Field(default_factory=OpenSearchSettings)
    bedrock: BedrockSettings = Field(default_factory=BedrockSettings)
    sagemaker: SageMakerSettings = Field(default_factory=SageMakerSettings)
    hitl: HITLSettings = Field(default_factory=HITLSettings)
    cognito: CognitoSettings = Field(default_factory=CognitoSettings)

    # Feature flags
    use_sagemaker_pii: bool = Field(
        default=False,
        description="Use SageMaker NER for PII instead of Comprehend",
    )
    use_sagemaker_classifier: bool = Field(
        default=False,
        description="Use SageMaker for intent classification instead of Bedrock",
    )
    strict_rag_mode: bool = Field(
        default=True,
        description="If no relevant context found, defer to human instead of generating",
    )


# Module-level singleton
settings = Settings()
