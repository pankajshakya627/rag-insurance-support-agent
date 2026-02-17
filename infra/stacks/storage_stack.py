"""
Storage Stack — S3 buckets and DynamoDB tables with KMS encryption.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    aws_dynamodb as dynamodb,
    aws_kms as kms,
    aws_s3 as s3,
)
from constructs import Construct


class StorageStack(cdk.Stack):
    """Data storage infrastructure: S3 data lake + DynamoDB operational store."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        kms_key: kms.IKey,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---- S3 Buckets ----

        self.raw_bucket = s3.Bucket(
            self,
            "RawMessagesBucket",
            bucket_name=f"insurance-ai-raw-messages-{cdk.Aws.ACCOUNT_ID}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=kms_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ArchiveOldMessages",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=cdk.Duration.days(90),
                        ),
                    ],
                ),
            ],
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        self.attachments_bucket = s3.Bucket(
            self,
            "AttachmentsBucket",
            bucket_name=f"insurance-ai-attachments-{cdk.Aws.ACCOUNT_ID}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=kms_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        self.audit_bucket = s3.Bucket(
            self,
            "AuditLogsBucket",
            bucket_name=f"insurance-ai-audit-logs-{cdk.Aws.ACCOUNT_ID}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=kms_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
            enforce_ssl=True,
            object_lock_enabled=True,  # Compliance: immutable audit logs
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        self.finetuning_bucket = s3.Bucket(
            self,
            "FinetuningBucket",
            bucket_name=f"insurance-ai-finetuning-{cdk.Aws.ACCOUNT_ID}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=kms_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # ---- DynamoDB Tables ----

        self.tickets_table = dynamodb.Table(
            self,
            "TicketsTable",
            table_name="InsuranceAI-Tickets",
            partition_key=dynamodb.Attribute(
                name="ticket_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=kms_key,
            point_in_time_recovery=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            time_to_live_attribute="ttl",
        )

        # GSI for querying by status (HITL review queue)
        self.tickets_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI for querying by customer
        self.tickets_table.add_global_secondary_index(
            index_name="customer-index",
            partition_key=dynamodb.Attribute(
                name="customer_id",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        self.conversation_table = dynamodb.Table(
            self,
            "ConversationStateTable",
            table_name="InsuranceAI-ConversationState",
            partition_key=dynamodb.Attribute(
                name="ticket_id",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="turn_number",
                type=dynamodb.AttributeType.NUMBER,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=kms_key,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        self.customer_profiles_table = dynamodb.Table(
            self,
            "CustomerProfilesTable",
            table_name="InsuranceAI-CustomerProfiles",
            partition_key=dynamodb.Attribute(
                name="customer_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=kms_key,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Email lookup index
        self.customer_profiles_table.add_global_secondary_index(
            index_name="email-index",
            partition_key=dynamodb.Attribute(
                name="customer_email",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.KEYS_ONLY,
        )

        # ---- Outputs ----
        cdk.CfnOutput(self, "RawBucketName", value=self.raw_bucket.bucket_name)
        cdk.CfnOutput(self, "TicketsTableName", value=self.tickets_table.table_name)
