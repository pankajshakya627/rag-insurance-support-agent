"""
ML Stack — SageMaker endpoints and Bedrock Guardrail configuration.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    aws_bedrock as bedrock,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_sagemaker as sagemaker,
)
from constructs import Construct


class MLStack(cdk.Stack):
    """ML/AI infrastructure: SageMaker endpoints and Bedrock guardrails."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---- SageMaker Execution Role ----
        self.sagemaker_role = iam.Role(
            self,
            "SageMakerExecutionRole",
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSageMakerFullAccess"
                ),
            ],
        )

        # ---- PII NER Model (Placeholder — deployed via SageMaker Training Job) ----
        # In production, the model artifacts are uploaded to S3 after training.
        # This creates the endpoint configuration ready for the model.

        self.pii_endpoint_config = sagemaker.CfnEndpointConfig(
            self,
            "PIINEREndpointConfig",
            endpoint_config_name="insurance-pii-ner-config",
            production_variants=[
                sagemaker.CfnEndpointConfig.ProductionVariantProperty(
                    variant_name="AllTraffic",
                    model_name="insurance-pii-ner-model",  # Created by training job
                    initial_instance_count=1,
                    instance_type="ml.m5.large",
                    initial_variant_weight=1.0,
                ),
            ],
        )

        # ---- Intent Classifier Endpoint (Placeholder) ----
        self.classifier_endpoint_config = sagemaker.CfnEndpointConfig(
            self,
            "ClassifierEndpointConfig",
            endpoint_config_name="insurance-intent-classifier-config",
            production_variants=[
                sagemaker.CfnEndpointConfig.ProductionVariantProperty(
                    variant_name="AllTraffic",
                    model_name="insurance-intent-classifier-model",
                    initial_instance_count=1,
                    instance_type="ml.m5.large",
                    initial_variant_weight=1.0,
                ),
            ],
        )

        # ---- Bedrock Guardrail ----
        self.guardrail = bedrock.CfnGuardrail(
            self,
            "InsuranceGuardrail",
            name="insurance-ai-guardrail",
            description="Guardrail for Insurance Customer Support AI Agent",
            blocked_input_messaging=(
                "I'm unable to process this request. Please contact our "
                "support team directly."
            ),
            blocked_outputs_messaging=(
                "I apologize, but I cannot provide this information. "
                "A team member will follow up with you."
            ),
            # Content filters
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="SEXUAL",
                        input_strength="HIGH",
                        output_strength="HIGH",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="VIOLENCE",
                        input_strength="HIGH",
                        output_strength="HIGH",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="HATE",
                        input_strength="HIGH",
                        output_strength="HIGH",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="INSULTS",
                        input_strength="MEDIUM",
                        output_strength="HIGH",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="MISCONDUCT",
                        input_strength="HIGH",
                        output_strength="HIGH",
                    ),
                ],
            ),
            # Topic policy — block off-topic discussions
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(
                topics_config=[
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="InvestmentAdvice",
                        definition=(
                            "Providing investment advice, stock tips, "
                            "cryptocurrency recommendations, or financial "
                            "planning outside insurance products."
                        ),
                        type="DENY",
                    ),
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="MedicalDiagnosis",
                        definition=(
                            "Providing medical diagnoses, treatment plans, "
                            "or prescription recommendations."
                        ),
                        type="DENY",
                    ),
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="LegalAdvice",
                        definition=(
                            "Providing specific legal advice or legal "
                            "opinions on liability or fault."
                        ),
                        type="DENY",
                    ),
                ],
            ),
            # Word policy — block specific phrases
            word_policy_config=bedrock.CfnGuardrail.WordPolicyConfigProperty(
                words_config=[
                    bedrock.CfnGuardrail.WordConfigProperty(text="guaranteed payout"),
                    bedrock.CfnGuardrail.WordConfigProperty(text="claim approved"),
                    bedrock.CfnGuardrail.WordConfigProperty(text="we will pay you"),
                    bedrock.CfnGuardrail.WordConfigProperty(text="full reimbursement"),
                ],
            ),
            # Sensitive information policy
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(
                        type="US_SOCIAL_SECURITY_NUMBER",
                        action="ANONYMIZE",
                    ),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(
                        type="CREDIT_DEBIT_CARD_NUMBER",
                        action="ANONYMIZE",
                    ),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(
                        type="US_BANK_ACCOUNT_NUMBER",
                        action="ANONYMIZE",
                    ),
                ],
            ),
        )

        # ---- Outputs ----
        cdk.CfnOutput(self, "GuardrailId", value=self.guardrail.attr_guardrail_id)
        cdk.CfnOutput(
            self, "SageMakerRoleArn", value=self.sagemaker_role.role_arn
        )
