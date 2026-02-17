"""
Ingestion Stack — SES, API Gateway, SNS, and Lambda functions.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    aws_apigateway as apigw,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_lambda_python_alpha as lambda_python,
    aws_s3 as s3,
    aws_ses as ses,
    aws_ses_actions as ses_actions,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
)
from constructs import Construct


class IngestionStack(cdk.Stack):
    """Ingestion pipeline: email (SES), webhooks (API Gateway), orchestration (SNS)."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        raw_bucket: s3.IBucket,
        attachments_bucket: s3.IBucket,
        tickets_table: dynamodb.ITable,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---- SNS Topic (orchestration entry point) ----
        self.orchestration_topic = sns.Topic(
            self,
            "OrchestrationTopic",
            topic_name="insurance-ai-orchestration",
            display_name="Insurance AI Pipeline Trigger",
        )

        # ---- Shared Lambda Layer (schemas + config) ----
        shared_env = {
            "S3_RAW_MESSAGES_BUCKET": raw_bucket.bucket_name,
            "S3_ATTACHMENTS_BUCKET": attachments_bucket.bucket_name,
            "SNS_ORCHESTRATION_TOPIC": self.orchestration_topic.topic_arn,
            "DYNAMODB_TICKETS_TABLE": tickets_table.table_name,
        }

        # ---- Email Handler Lambda ----
        self.email_handler = _lambda.Function(
            self,
            "EmailHandler",
            function_name="insurance-ai-email-handler",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="lambdas.ingestion.email_handler.handler",
            code=_lambda.Code.from_asset(".."),
            timeout=cdk.Duration.seconds(60),
            memory_size=512,
            environment=shared_env,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        # Grant permissions
        raw_bucket.grant_read_write(self.email_handler)
        attachments_bucket.grant_read_write(self.email_handler)
        tickets_table.grant_read_write_data(self.email_handler)
        self.orchestration_topic.grant_publish(self.email_handler)

        # ---- Webhook Handler Lambda ----
        self.webhook_handler = _lambda.Function(
            self,
            "WebhookHandler",
            function_name="insurance-ai-webhook-handler",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="lambdas.ingestion.webhook_handler.handler",
            code=_lambda.Code.from_asset(".."),
            timeout=cdk.Duration.seconds(30),
            memory_size=256,
            environment=shared_env,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        raw_bucket.grant_read_write(self.webhook_handler)
        tickets_table.grant_read_write_data(self.webhook_handler)
        self.orchestration_topic.grant_publish(self.webhook_handler)

        # ---- API Gateway (Webhook ingestion) ----
        self.api = apigw.RestApi(
            self,
            "WebhookAPI",
            rest_api_name="insurance-ai-webhooks",
            description="Webhook ingestion for WhatsApp and Chatbot",
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                throttling_rate_limit=100,
                throttling_burst_limit=200,
            ),
        )

        webhook_resource = self.api.root.add_resource("webhook")
        channel_resource = webhook_resource.add_resource("{channel}")
        channel_resource.add_method(
            "POST",
            apigw.LambdaIntegration(self.webhook_handler),
        )

        # ---- SES Receipt Rule (simplified — domain must be verified) ----
        # Note: SES receipt rules require the domain to be verified in SES.
        # This creates the rule set; the actual rule is added after domain verification.

        self.ses_rule_set = ses.ReceiptRuleSet(
            self,
            "IncomingEmailRuleSet",
            receipt_rule_set_name="insurance-ai-incoming",
        )

        # ---- SNS Notification Topic for HITL ----
        self.hitl_notification_topic = sns.Topic(
            self,
            "HITLNotificationTopic",
            topic_name="insurance-ai-hitl-notifications",
            display_name="Insurance AI HITL Review Notifications",
        )

        # ---- Outputs ----
        cdk.CfnOutput(self, "ApiUrl", value=self.api.url)
        cdk.CfnOutput(
            self,
            "OrchestrationTopicArn",
            value=self.orchestration_topic.topic_arn,
        )
