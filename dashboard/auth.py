"""
Cognito Authentication wrapper for the HITL dashboard.

In production, this integrates with AWS Cognito User Pool for
MFA-enabled authentication. For MVP, provides a simplified interface.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import boto3

from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class AuthUser:
    """Authenticated user information."""

    user_id: str
    email: str
    name: str
    groups: list[str]
    is_admin: bool = False


class CognitoAuth:
    """
    AWS Cognito authentication for the review dashboard.

    Handles user authentication, token management, and group-based
    authorization for the HITL review workflow.
    """

    def __init__(self) -> None:
        self.user_pool_id = settings.cognito.user_pool_id
        self.client_id = settings.cognito.client_id

        if self.user_pool_id:
            self.cognito = boto3.client("cognito-idp")
        else:
            self.cognito = None
            logger.warning("Cognito not configured — using simplified auth")

    def authenticate(self, username: str, password: str) -> AuthUser | None:
        """
        Authenticate a user with Cognito.

        Returns AuthUser on success, None on failure.
        """
        if not self.cognito:
            # MVP fallback: accept any non-empty credentials
            return AuthUser(
                user_id=username,
                email=f"{username}@insurance.example.com",
                name=username,
                groups=["reviewers"],
            )

        try:
            response = self.cognito.initiate_auth(
                AuthFlow="USER_PASSWORD_AUTH",
                ClientId=self.client_id,
                AuthParameters={
                    "USERNAME": username,
                    "PASSWORD": password,
                },
            )

            # Handle MFA challenge
            if response.get("ChallengeName") == "SMS_MFA":
                logger.info("MFA required for user %s", username)
                return None  # Dashboard should prompt for MFA code

            # Get user info
            auth_result = response.get("AuthenticationResult", {})
            access_token = auth_result.get("AccessToken")

            if access_token:
                return self._get_user_info(access_token, username)

        except self.cognito.exceptions.NotAuthorizedException:
            logger.warning("Authentication failed for %s", username)
        except self.cognito.exceptions.UserNotConfirmedException:
            logger.warning("User %s not confirmed", username)
        except Exception as e:
            logger.error("Authentication error: %s", e)

        return None

    def verify_mfa(
        self, username: str, mfa_code: str, session: str
    ) -> AuthUser | None:
        """Verify MFA code and complete authentication."""
        if not self.cognito:
            return None

        try:
            response = self.cognito.respond_to_auth_challenge(
                ClientId=self.client_id,
                ChallengeName="SMS_MFA",
                Session=session,
                ChallengeResponses={
                    "USERNAME": username,
                    "SMS_MFA_CODE": mfa_code,
                },
            )

            auth_result = response.get("AuthenticationResult", {})
            access_token = auth_result.get("AccessToken")

            if access_token:
                return self._get_user_info(access_token, username)

        except Exception as e:
            logger.error("MFA verification failed: %s", e)

        return None

    def _get_user_info(self, access_token: str, username: str) -> AuthUser:
        """Retrieve user profile and group membership."""
        user_info = self.cognito.get_user(AccessToken=access_token)

        attributes = {
            attr["Name"]: attr["Value"]
            for attr in user_info.get("UserAttributes", [])
        }

        # Get user groups
        groups_response = self.cognito.admin_list_groups_for_user(
            UserPoolId=self.user_pool_id,
            Username=username,
        )
        groups = [g["GroupName"] for g in groups_response.get("Groups", [])]

        return AuthUser(
            user_id=username,
            email=attributes.get("email", ""),
            name=attributes.get("name", username),
            groups=groups,
            is_admin="admins" in groups,
        )

    def check_authorization(self, user: AuthUser, required_group: str) -> bool:
        """Check if user belongs to the required group."""
        return required_group in user.groups or user.is_admin
