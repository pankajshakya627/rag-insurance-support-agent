"""
Orchestration Stack — Step Functions state machine, SQS DLQ, and CloudWatch.
"""

from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
    aws_sqs as sqs,
    aws_stepfunctions as sfn,
)
from constructs import Construct


class OrchestrationStack(cdk.Stack):
    """Step Functions pipeline, processing Lambdas, DLQ, and monitoring."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        tickets_table: dynamodb.ITable,
        audit_bucket: s3.IBucket,
        finetuning_bucket: s3.IBucket,
        ingestion_topic: sns.ITopic,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---- Dead Letter Queue ----
        self.dlq = sqs.Queue(
            self,
            "DeadLetterQueue",
            queue_name="insurance-ai-dlq",
            retention_period=cdk.Duration.days(14),
            encryption=sqs.QueueEncryption.KMS_MANAGED,
        )

        # ---- HITL Review Queue (with callback pattern) ----
        self.hitl_queue = sqs.Queue(
            self,
            "HITLQueue",
            queue_name="insurance-ai-hitl-review",
            visibility_timeout=cdk.Duration.hours(24),
            retention_period=cdk.Duration.days(7),
            encryption=sqs.QueueEncryption.KMS_MANAGED,
            dead_letter_queue=sqs.DeadLetterQueue(
                queue=self.dlq,
                max_receive_count=3,
            ),
        )

        # ---- Shared environment for processing Lambdas ----
        shared_env = {
            "DYNAMODB_TICKETS_TABLE": tickets_table.table_name,
            "S3_AUDIT_LOGS_BUCKET": audit_bucket.bucket_name,
            "S3_FINETUNING_BUCKET": finetuning_bucket.bucket_name,
            "HITL_QUEUE_URL": self.hitl_queue.queue_url,
        }

        lambda_defaults = {
            "runtime": _lambda.Runtime.PYTHON_3_11,
            "code": _lambda.Code.from_asset(".."),
            "timeout": cdk.Duration.seconds(300),
            "memory_size": 1024,
            "environment": shared_env,
            "vpc": vpc,
            "vpc_subnets": ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        }

        # ---- Processing Lambdas ----
        self.attachment_processor = _lambda.Function(
            self, "AttachmentProcessor",
            function_name="insurance-ai-attachment-processor",
            handler="lambdas.ingestion.attachment_processor.handler",
            **lambda_defaults,
        )

        self.pii_redactor = _lambda.Function(
            self, "PIIRedactor",
            function_name="insurance-ai-pii-redactor",
            handler="lambdas.preprocessing.pii_redactor.handler",
            **lambda_defaults,
        )

        self.intent_classifier = _lambda.Function(
            self, "IntentClassifier",
            function_name="insurance-ai-intent-classifier",
            handler="lambdas.preprocessing.intent_classifier.handler",
            **lambda_defaults,
        )

        self.response_sender = _lambda.Function(
            self, "ResponseSender",
            function_name="insurance-ai-response-sender",
            handler="lambdas.orchestration.response_sender.handler",
            **lambda_defaults,
        )

        self.feedback_handler = _lambda.Function(
            self, "FeedbackHandler",
            function_name="insurance-ai-feedback-handler",
            handler="lambdas.orchestration.feedback_handler.handler",
            **lambda_defaults,
        )

        # ---- HITL Callback Lambda ----
        self.hitl_callback = _lambda.Function(
            self, "HITLCallback",
            function_name="insurance-ai-hitl-callback",
            handler="lambdas.orchestration.hitl_callback.handler",
            **lambda_defaults,
        )

        # ---- Grant permissions to all Lambdas ----
        for fn in [
            self.attachment_processor,
            self.pii_redactor,
            self.intent_classifier,
            self.response_sender,
            self.feedback_handler,
            self.hitl_callback,
        ]:
            tickets_table.grant_read_write_data(fn)
            audit_bucket.grant_read_write(fn)

        finetuning_bucket.grant_read_write(self.feedback_handler)
        self.hitl_queue.grant_send_messages(self.response_sender)

        # Bedrock access for classifier and generator
        bedrock_policy = iam.PolicyStatement(
            actions=[
                "bedrock:InvokeModel",
                "bedrock:ApplyGuardrail",
            ],
            resources=["*"],
        )
        self.pii_redactor.add_to_role_policy(bedrock_policy)
        self.intent_classifier.add_to_role_policy(bedrock_policy)

        # Comprehend access for PII redactor
        self.pii_redactor.add_to_role_policy(
            iam.PolicyStatement(
                actions=["comprehend:DetectPiiEntities"],
                resources=["*"],
            )
        )

        # SageMaker access
        sagemaker_policy = iam.PolicyStatement(
            actions=["sagemaker:InvokeEndpoint"],
            resources=["*"],
        )
        self.pii_redactor.add_to_role_policy(sagemaker_policy)
        self.intent_classifier.add_to_role_policy(sagemaker_policy)

        # Textract access for attachment processor
        self.attachment_processor.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "textract:DetectDocumentText",
                    "textract:StartDocumentTextDetection",
                    "textract:GetDocumentTextDetection",
                ],
                resources=["*"],
            )
        )

        # SES access for response sender
        self.response_sender.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ses:SendEmail", "ses:SendRawEmail"],
                resources=["*"],
            )
        )

        # Step Functions callback access for HITL
        self.hitl_callback.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "states:SendTaskSuccess",
                    "states:SendTaskFailure",
                ],
                resources=["*"],
            )
        )

        # ---- Step Functions State Machine ----
        # Import the ASL definition
        from orchestration.state_machine import build_state_machine_definition

        definition = build_state_machine_definition(
            pii_lambda_arn=self.pii_redactor.function_arn,
            classifier_lambda_arn=self.intent_classifier.function_arn,
            attachment_lambda_arn=self.attachment_processor.function_arn,
            rag_lambda_arn=self.intent_classifier.function_arn,  # Placeholder
            generator_lambda_arn=self.intent_classifier.function_arn,  # Placeholder
            validator_lambda_arn=self.intent_classifier.function_arn,  # Placeholder
            response_sender_lambda_arn=self.response_sender.function_arn,
            feedback_lambda_arn=self.feedback_handler.function_arn,
            hitl_queue_url=self.hitl_queue.queue_url,
            dlq_arn=self.dlq.queue_arn,
        )

        self.state_machine = sfn.CfnStateMachine(
            self,
            "PipelineStateMachine",
            state_machine_name="insurance-ai-pipeline",
            definition_string=json.dumps(definition),
            role_arn=self._create_sfn_role().role_arn,
            state_machine_type="STANDARD",
            logging_configuration=sfn.CfnStateMachine.LoggingConfigurationProperty(
                level="ALL",
                include_execution_data=True,
            ),
        )

        # ---- CloudWatch Alarms ----
        alarm_topic = sns.Topic(
            self, "AlarmTopic",
            topic_name="insurance-ai-alarms",
        )

        # DLQ alarm — messages landing in DLQ means something is failing
        dlq_alarm = cloudwatch.Alarm(
            self,
            "DLQAlarm",
            alarm_name="insurance-ai-dlq-messages",
            metric=self.dlq.metric_approximate_number_of_messages_visible(),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        dlq_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        # ---- Outputs ----
        cdk.CfnOutput(
            self,
            "StateMachineArn",
            value=self.state_machine.attr_arn,
        )
        cdk.CfnOutput(
            self,
            "HITLQueueUrl",
            value=self.hitl_queue.queue_url,
        )
        cdk.CfnOutput(
            self,
            "DLQUrl",
            value=self.dlq.queue_url,
        )

    def _create_sfn_role(self) -> iam.Role:
        """Create IAM role for Step Functions execution."""
        role = iam.Role(
            self,
            "StateMachineRole",
            assumed_by=iam.ServicePrincipal("states.amazonaws.com"),
        )

        # Lambda invoke permissions
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    self.attachment_processor.function_arn,
                    self.pii_redactor.function_arn,
                    self.intent_classifier.function_arn,
                    self.response_sender.function_arn,
                    self.feedback_handler.function_arn,
                ],
            )
        )

        # SQS permissions for HITL callback
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["sqs:SendMessage"],
                resources=[self.hitl_queue.queue_arn],
            )
        )

        return role
