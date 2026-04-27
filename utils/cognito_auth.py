# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Cognito authentication helper for Streamlit UI."""

import os
import time
import logging
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

COGNITO_POOL_ID = os.getenv("COGNITO_POOL_ID", "")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")
COGNITO_REGION = os.getenv("COGNITO_REGION", os.getenv("AWS_REGION", "us-east-1"))


def authenticate(username: str, password: str) -> dict:
    """
    Authenticate user with Cognito and return tokens.
    
    Returns:
        dict with 'success', 'id_token', 'access_token', 'refresh_token', 'expires_at', 'error'
    """
    if not COGNITO_POOL_ID or not COGNITO_CLIENT_ID:
        return {"success": False, "error": "Cognito not configured. Set COGNITO_POOL_ID and COGNITO_CLIENT_ID."}

    try:
        client = boto3.client("cognito-idp", region_name=COGNITO_REGION)
        resp = client.initiate_auth(
            ClientId=COGNITO_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )

        # Handle NEW_PASSWORD_REQUIRED challenge
        if resp.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
            return {"success": False, "error": "Password change required. Please contact your administrator."}

        auth = resp["AuthenticationResult"]
        return {
            "success": True,
            "id_token": auth["IdToken"],
            "access_token": auth["AccessToken"],
            "refresh_token": auth.get("RefreshToken", ""),
            "expires_at": time.time() + auth["ExpiresIn"],
            "username": username,
        }
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "NotAuthorizedException":
            return {"success": False, "error": "Invalid email or password."}
        if code == "UserNotFoundException":
            return {"success": False, "error": "User not found."}
        if code == "UserNotConfirmedException":
            return {"success": False, "error": "Email not verified. Please check your email for the verification code."}
        return {"success": False, "error": f"Authentication failed: {e.response['Error']['Message']}"}
    except Exception as e:
        logger.error(f"Cognito auth error: {e}")
        return {"success": False, "error": f"Authentication error: {str(e)}"}


def signup(email: str, password: str) -> dict:
    """Sign up a new user."""
    if not COGNITO_POOL_ID or not COGNITO_CLIENT_ID:
        return {"success": False, "error": "Cognito not configured."}

    try:
        client = boto3.client("cognito-idp", region_name=COGNITO_REGION)
        client.sign_up(
            ClientId=COGNITO_CLIENT_ID,
            Username=email,
            Password=password,
            UserAttributes=[{"Name": "email", "Value": email}],
        )
        return {"success": True, "message": "Signup successful! Please check your email for a verification code."}
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "UsernameExistsException":
            return {"success": False, "error": "An account with this email already exists."}
        if code == "InvalidPasswordException":
            return {"success": False, "error": "Password does not meet requirements (min 8 chars, uppercase, lowercase, number)."}
        if code == "InvalidParameterException":
            return {"success": False, "error": "Invalid email or password format."}
        return {"success": False, "error": f"Signup failed: {e.response['Error']['Message']}"}
    except Exception as e:
        logger.error(f"Signup error: {e}")
        return {"success": False, "error": f"Signup error: {str(e)}"}


def confirm_signup(email: str, code: str) -> dict:
    """Confirm user signup with verification code."""
    if not COGNITO_POOL_ID or not COGNITO_CLIENT_ID:
        return {"success": False, "error": "Cognito not configured."}

    try:
        client = boto3.client("cognito-idp", region_name=COGNITO_REGION)
        client.confirm_sign_up(
            ClientId=COGNITO_CLIENT_ID,
            Username=email,
            ConfirmationCode=code,
        )
        return {"success": True, "message": "Email verified! You can now sign in."}
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "CodeMismatchException":
            return {"success": False, "error": "Invalid verification code."}
        if code == "ExpiredCodeException":
            return {"success": False, "error": "Verification code expired. Please request a new one."}
        return {"success": False, "error": f"Verification failed: {e.response['Error']['Message']}"}
    except Exception as e:
        logger.error(f"Confirmation error: {e}")
        return {"success": False, "error": f"Verification error: {str(e)}"}


def refresh_token(refresh_tok: str) -> dict:
    """Refresh an expired token."""
    try:
        client = boto3.client("cognito-idp", region_name=COGNITO_REGION)
        resp = client.initiate_auth(
            ClientId=COGNITO_CLIENT_ID,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": refresh_tok},
        )
        auth = resp["AuthenticationResult"]
        return {
            "success": True,
            "id_token": auth["IdToken"],
            "access_token": auth["AccessToken"],
            "expires_at": time.time() + auth["ExpiresIn"],
        }
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        return {"success": False, "error": str(e)}


def is_token_valid(expires_at: float, buffer_seconds: int = 60) -> bool:
    """Check if token is still valid with a buffer."""
    return time.time() < (expires_at - buffer_seconds)
