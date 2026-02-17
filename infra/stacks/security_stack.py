"""
Security Stack — KMS keys, IAM roles, and Cognito user pool.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    aws_cognito as cognito,
    aws_iam as iam,
    aws_kms as kms,
)
from constructs import Construct


class SecurityStack(cdk.Stack):
    """Security infrastructure: encryption, auth, and access control."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---- KMS Key (Customer Managed) ----
        self.kms_key = kms.Key(
            self,
            "InsuranceAI-CMK",
            alias="alias/insurance-ai",
            description="Customer managed key for Insurance AI data encryption",
            enable_key_rotation=True,
            pending_window=cdk.Duration.days(30),
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # ---- Cognito User Pool (HITL Dashboard Auth) ----
        self.user_pool = cognito.UserPool(
            self,
            "ReviewerPool",
            user_pool_name="insurance-ai-reviewers",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(
                username=True,
                email=True,
            ),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
                fullname=cognito.StandardAttribute(required=True, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            mfa=cognito.Mfa.REQUIRED,
            mfa_second_factor=cognito.MfaSecondFactor(
                sms=True,
                otp=True,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # App client for the dashboard
        self.user_pool_client = self.user_pool.add_client(
            "DashboardClient",
            user_pool_client_name="insurance-ai-dashboard",
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
            ),
            prevent_user_existence_errors=True,
        )

        # ---- Reviewer Group ----
        cognito.CfnUserPoolGroup(
            self,
            "ReviewersGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="reviewers",
            description="Insurance support reviewers who can approve AI responses",
        )

        cognito.CfnUserPoolGroup(
            self,
            "AdminsGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="admins",
            description="Administrators with full access",
        )

        # ---- Outputs ----
        cdk.CfnOutput(self, "KmsKeyArn", value=self.kms_key.key_arn)
        cdk.CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        cdk.CfnOutput(
            self,
            "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
        )
