# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Streamlit UI for Lambda Test Case Generator
Authenticates via Cognito, invokes AgentCore with bearer token.
"""

import streamlit as st
import json
import os
import re
import time
from datetime import datetime
import boto3
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from utils.cognito_auth import authenticate, refresh_token, is_token_valid, signup, confirm_signup

# AgentCore invocation support
try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    AGENTCORE_SDK_AVAILABLE = True
except ImportError:
    AGENTCORE_SDK_AVAILABLE = False

# Page configuration
st.set_page_config(page_title="Lambda Test Case Generator", layout="wide")

# Custom CSS for reduced spacing
st.markdown("""
<style>
    /* Reduce top padding */
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 2rem !important;
    }
    
    /* Reduce sidebar top padding */
    section[data-testid="stSidebar"] > div {
        padding-top: 0 !important;
    }
    
    section[data-testid="stSidebar"] .block-container {
        padding-top: 0 !important;
    }
    
    /* User info and logout button styling */
    .user-logout-container {
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0.5rem 0;
        margin-bottom: 1rem;
        border-bottom: 1px solid rgba(250, 250, 250, 0.2);
    }
    
    .user-logout-container strong {
        margin-left: 0.3rem;
    }
</style>
""", unsafe_allow_html=True)

# Constants
RATE_LIMIT_SECONDS = 30
MAX_FUNCTION_NAME_LENGTH = 170
FUNCTION_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_:/-]+$')
MAX_CUSTOM_INSTRUCTIONS_LENGTH = 2000

AGENT_NAME = os.getenv("AGENTCORE_AGENT_NAME", "lambda_test_generator")
AGENT_REGION = os.getenv("AWS_REGION", "us-east-1")

# Cognito config - REQUIRED for authentication
# SECURITY: Cognito authentication is mandatory and cannot be disabled
COGNITO_POOL_ID = os.getenv("COGNITO_POOL_ID")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID")

if not COGNITO_POOL_ID or not COGNITO_CLIENT_ID:
    st.error("Cognito authentication is required but not configured.")
    st.error("Please set COGNITO_POOL_ID and COGNITO_CLIENT_ID environment variables.")
    st.info("See README.md for deployment instructions.")
    st.stop()

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
_defaults = {
    "test_cases": [],
    "feedback": {},
    "function_name": "",
    "analysis_summary": {},
    "generation_metadata": {},
    "raw_output": "",
    "ignore_patterns": [],
    "last_generation_time": 0.0,
    # Auth
    "authenticated": False,
    "auth_tokens": {},
    "username": "",
    # Signup flow
    "show_signup": False,
    "show_verify": False,
    "signup_email": "",
    "_auth_initialized": False,
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ---------------------------------------------------------------------------
# Session persistence helpers - SECURE VERSION
# ---------------------------------------------------------------------------
# NOTE: Streamlit's session_state is server-side and secure.
# Tokens are never exposed in URLs, browser history, or Referer headers.
# Session data is stored in memory on the Streamlit server.

def save_auth_to_session():
    """
    Save auth state to server-side session.
    
    SECURITY: This is a no-op because st.session_state is already server-side.
    Tokens are stored in memory on the Streamlit server, not in URLs.
    This prevents:
    - Browser history leakage
    - Referer header leakage
    - Shoulder surfing (URL bar visibility)
    - URL sharing attacks
    """
    # Tokens are already in st.session_state (server-side)
    # No need to persist to URL or cookies
    pass


def restore_auth_from_session():
    """
    Restore auth state from server-side session.
    
    SECURITY: This is a no-op because st.session_state persists automatically
    during the Streamlit session. When the browser tab closes or session expires,
    the user must re-authenticate.
    """
    if st.session_state._auth_initialized:
        return
    
    st.session_state._auth_initialized = True
    
    # Session state is already restored by Streamlit
    # No need to read from URL or cookies


def clear_auth_from_session():
    """
    Clear auth state from server-side session.
    
    SECURITY: Clears tokens from memory. User must re-authenticate.
    """
    # Clear tokens from session state
    st.session_state.authenticated = False
    st.session_state.auth_tokens = {}
    st.session_state.username = ""


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def _ensure_token() -> str:
    """Return a valid access token, refreshing if needed."""
    tokens = st.session_state.auth_tokens
    
    # If no tokens but user is authenticated (shouldn't happen), force re-login
    if not tokens and st.session_state.authenticated:
        st.session_state.authenticated = False
        st.session_state.username = ""
        clear_auth_from_session()
        st.rerun()
    
    if is_token_valid(tokens.get("expires_at", 0)):
        return tokens["access_token"]

    # Try refresh
    result = refresh_token(tokens.get("refresh_token", ""))
    if result["success"]:
        st.session_state.auth_tokens.update(result)
        # Tokens automatically persist in server-side session state
        return result["access_token"]

    # Refresh failed — force re-login
    st.session_state.authenticated = False
    st.session_state.auth_tokens = {}
    st.session_state.username = ""
    clear_auth_from_session()
    st.rerun()


def _login_page():
    """Render login form."""
    # Center the login container
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.title("Lambda Test Case Generator")
        
        # Show signup form
        if st.session_state.show_signup:
            st.markdown("### Create Account")
            with st.form("signup_form"):
                email = st.text_input("Email")
                password = st.text_input("Password", type="password", help="Min 8 chars, uppercase, lowercase, number, special character")
                password_confirm = st.text_input("Confirm Password", type="password")
                col_a, col_b = st.columns(2)
                with col_a:
                    signup_btn = st.form_submit_button("Sign Up", type="primary", use_container_width=True)
                with col_b:
                    back_btn = st.form_submit_button("Back to Sign In", use_container_width=True)
            
            if back_btn:
                st.session_state.show_signup = False
                st.rerun()
            
            if signup_btn:
                if not email or not password:
                    st.error("Please enter both email and password.")
                elif password != password_confirm:
                    st.error("Passwords do not match.")
                else:
                    with st.spinner("Creating account..."):
                        result = signup(email, password)
                    if result["success"]:
                        st.success(result["message"])
                        st.session_state.signup_email = email
                        st.session_state.show_signup = False
                        st.session_state.show_verify = True
                        st.rerun()
                    else:
                        st.error(result["error"])
            return
        
        # Show verification form
        if st.session_state.show_verify:
            st.markdown("### Verify Email")
            st.info(f"A verification code has been sent to **{st.session_state.signup_email}**")
            with st.form("verify_form"):
                code = st.text_input("Verification Code")
                col_a, col_b = st.columns(2)
                with col_a:
                    verify_btn = st.form_submit_button("Verify", type="primary", use_container_width=True)
                with col_b:
                    cancel_btn = st.form_submit_button("Cancel", use_container_width=True)
            
            if cancel_btn:
                st.session_state.show_verify = False
                st.session_state.signup_email = ""
                st.rerun()
            
            if verify_btn:
                if not code:
                    st.error("Please enter the verification code.")
                else:
                    with st.spinner("Verifying..."):
                        result = confirm_signup(st.session_state.signup_email, code)
                    if result["success"]:
                        st.success(result["message"])
                        st.session_state.show_verify = False
                        st.session_state.signup_email = ""
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error(result["error"])
            return
        
        # Show login form
        st.markdown("### Sign In")
        with st.form("login_form"):
            username = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In", type="primary", use_container_width=True)

        if submitted:
            if not username or not password:
                st.error("Please enter both email and password.")
            else:
                with st.spinner("Authenticating..."):
                    result = authenticate(username, password)
                if result["success"]:
                    st.session_state.authenticated = True
                    st.session_state.auth_tokens = result
                    st.session_state.username = result["username"]
                    # Tokens automatically persist in server-side session state
                    st.rerun()
                else:
                    st.error(result["error"])
        
        # Signup link
        st.markdown("---")
        if st.button("Create Account", use_container_width=True):
            st.session_state.show_signup = True
            st.rerun()


def get_current_user_id() -> str:
    """Get user ID from Cognito token."""
    return st.session_state.username


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------
def sanitize_function_name(name: str) -> str:
    name = name.strip()
    if len(name) > MAX_FUNCTION_NAME_LENGTH:
        raise ValueError(f"Function name too long (max {MAX_FUNCTION_NAME_LENGTH} chars)")
    if name and not FUNCTION_NAME_PATTERN.match(name):
        raise ValueError("Function name contains invalid characters. Only alphanumeric, _, :, /, - allowed.")
    return name


def sanitize_custom_instructions(instructions: str) -> str:
    return instructions.strip()[:MAX_CUSTOM_INSTRUCTIONS_LENGTH]


# ---------------------------------------------------------------------------
# AgentCore invocation
# ---------------------------------------------------------------------------
def invoke_agentcore(payload: dict, bearer_token: str = None) -> dict:  # noqa: B107
    """Invoke AgentCore agent with optional bearer token."""
    try:
        from bedrock_agentcore_starter_toolkit.operations.runtime.invoke import invoke_bedrock_agentcore
        from pathlib import Path
        
        config_path = Path(__file__).parent / ".bedrock_agentcore.yaml"
        
        result = invoke_bedrock_agentcore(
            config_path=config_path,
            payload=payload,
            agent_name=AGENT_NAME,
            bearer_token=bearer_token if bearer_token else None,
            local_mode=False,
        )
        
        # InvokeResult has 'response' attribute containing the agent's response
        response = result.response
        
        # The response might be a string that needs parsing
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except json.JSONDecodeError:
                pass
        
        # Check if response indicates success
        if isinstance(response, dict):
            # Check if response has nested 'response' key with JSON string
            if 'response' in response and isinstance(response['response'], str):
                try:
                    nested_response = json.loads(response['response'])
                    return {
                        "success": nested_response.get("success", True),
                        "output": nested_response.get("output", str(nested_response)),
                        "error": nested_response.get("error"),
                    }
                except json.JSONDecodeError:
                    pass
            
            return {
                "success": response.get("success", True),
                "output": response.get("output", str(response)),
                "error": response.get("error"),
            }
        else:
            # Response is a string or other type
            return {
                "success": True,
                "output": str(response),
                "error": None,
            }
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}


def invoke_test_generation(function_name: str, num_test_cases: int, custom_instructions: str = "",
                           target_filter: str = "", ignore_patterns: list = None) -> dict:
    """Invoke test generation via AgentCore with auth."""
    token = _ensure_token()

    payload = {
        "action": "generate_test_cases",
        "function_name": function_name,
        "num_test_cases": num_test_cases,
        "custom_instructions": custom_instructions,
        "target_filter": target_filter,
        "ignore_patterns": ignore_patterns or [],
    }

    result = invoke_agentcore(payload, bearer_token=token)

    if result["success"]:
        output = result.get("output", "")
        if "Lambda function not found" in output or "not found in Lambda code" in output:
            return {"success": False, "output": output, "error": output}
    return result


# ---------------------------------------------------------------------------
# AWS / DynamoDB checks
# ---------------------------------------------------------------------------
def check_aws_credentials():
    try:
        boto3.client("sts").get_caller_identity()
        return True
    except Exception:
        return False


def check_dynamodb_configured():
    try:
        table_name = os.getenv("DYNAMODB_TABLE_NAME", "lambda-testcase-memory")
        region = os.getenv("AWS_REGION", "us-east-1")
        table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        table.load()
        return True, table_name, region
    except Exception as e:
        return False, None, str(e)


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------
def parse_agentcore_output(output: str) -> dict:
    import html
    
    # Output is already extracted by invoke_agentcore(), just unescape HTML
    output = html.unescape(output)
    
    test_cases = []
    analysis_summary = {}
    generation_metadata = {}

    try:
        func_match = re.search(r'Function: ([\w-]+)', output)
        runtime_match = re.search(r'Runtime: ([\w.]+)', output)
        if func_match:
            analysis_summary['function_name'] = func_match.group(1)
        if runtime_match:
            analysis_summary['runtime'] = runtime_match.group(1)

        if "FUNCTION ANALYSIS SUMMARY" in output:
            section = output.split("FUNCTION ANALYSIS SUMMARY")[1].split("=" * 80)[0]
            for label, key in [("Total Code Chunks", "total_chunks"), ("Dependencies Found", "dependencies_count"),
                               ("Input Patterns Detected", "input_patterns_count"), ("Output Patterns Detected", "output_patterns_count")]:
                m = re.search(rf'{label}:\s*(\d+)', section)
                if m:
                    analysis_summary[key] = int(m.group(1))

        if "GENERATION METADATA" in output:
            section = output.split("GENERATION METADATA")[1].split("=" * 80)[0]
            for label, key in [("Positive Tests", "positive_tests"), ("Negative Tests", "negative_tests"),
                               ("Edge Cases", "edge_cases"), ("Memory Patterns Used", "memory_patterns_used"),
                               ("Rejected Patterns Avoided", "rejected_patterns_avoided")]:
                m = re.search(rf'{label}:\s*(\d+)', section)
                if m:
                    generation_metadata[key] = int(m.group(1))

        pattern = r'TEST CASE (\d+): ([\w_]+).*?Type: (.*?)\n.*?Category: (.*?)\n.*?Confidence: ([\d.]+)%.*?Description:\s*(.*?)\n\nINPUT EVENT:\n(.*?)\n\nNOTES:'
        for match in re.finditer(pattern, output, re.DOTALL):
            try:
                test_type_raw = match.group(3).strip()
                if '✅' in test_type_raw or 'POSITIVE' in test_type_raw:
                    test_type = 'positive'
                elif '❌' in test_type_raw or 'NEGATIVE' in test_type_raw:
                    test_type = 'negative'
                elif '⚠️' in test_type_raw or 'EDGE' in test_type_raw:
                    test_type = 'edge'
                else:
                    test_type = 'unknown'

                test_cases.append({
                    'test_id': match.group(2),
                    'test_type': test_type,
                    'category': match.group(4).strip(),
                    'confidence_score': float(match.group(5)) / 100.0,
                    'description': match.group(6).strip(),
                    'input_event': json.loads(match.group(7).strip()),
                })
            except Exception:
                continue
    except Exception as e:
        st.error(f"Error parsing output: {e}")

    return {"test_cases": test_cases, "analysis_summary": analysis_summary, "generation_metadata": generation_metadata}


# ===========================================================================
# MAIN UI
# ===========================================================================

# Restore auth state from session if page was reloaded
restore_auth_from_session()

# Gate on authentication - REQUIRED
if not st.session_state.authenticated:
    _login_page()
    st.stop()

# Title
st.title("Lambda Test Case Generator")
st.markdown("Generate comprehensive test cases for your AWS Lambda functions based on actual code analysis.")

# Show logged-in user + logout
with st.sidebar:
    st.markdown(f'<div class="user-logout-container">Welcome, <strong>{st.session_state.username}</strong></div>', unsafe_allow_html=True)
    if st.button("Logout", use_container_width=True, type="secondary"):
        for k in ["authenticated", "auth_tokens", "username", "test_cases", "feedback",
                   "raw_output", "analysis_summary", "generation_metadata"]:
            st.session_state[k] = _defaults[k]
        clear_auth_from_session()
        st.rerun()

# Sidebar configuration
with st.sidebar:
    st.header("Configuration")

    function_name = st.text_input("Lambda Function Name",
                                  placeholder="my-lambda-function", help="Enter the Lambda function name or ARN")
    test_case_count = 10

    st.divider()
    st.subheader("Target Filter (Optional)")
    target_filter = st.text_input("Function/Class/File to Focus On",
                                  placeholder="e.g., validate_user, UserService, auth.py",
                                  help="Generate test cases focused on a specific function, class, or file.")
    if target_filter:
        st.info(f"Will focus on: **{target_filter}**")

    with st.expander("Target Filter Examples"):
        st.markdown("""
        **Function names:** `validate_user`, `process_payment`
        **Class names:** `UserService`, `PaymentProcessor`
        **File names:** `auth.py`, `validators.js`
        """)

    st.divider()
    st.subheader("Ignore Patterns (Optional)")
    ignore_patterns_text = st.text_area("Files/Folders to Ignore",
                                        value="\n".join(st.session_state.ignore_patterns),
                                        placeholder="tests/\n*.test.js\n*.spec.py", height=100)
    ignore_patterns = [p.strip() for p in ignore_patterns_text.split('\n') if p.strip()]
    st.session_state.ignore_patterns = ignore_patterns
    if ignore_patterns:
        st.info(f"Ignoring {len(ignore_patterns)} pattern(s)")

    custom_prompt = st.text_area("Custom Instructions", placeholder="e.g., Focus on authentication scenarios",
                                 help="These instructions override all other guidance.")

    st.divider()
    st.subheader("System Status")

    lambda_region = os.getenv("AWS_REGION", "us-east-1")
    st.info(f"**Lambda Region:** {lambda_region}")
    st.info(f"**Bedrock Region:** us-east-1")

    if check_aws_credentials():
        st.success("AWS Credentials Configured", icon=":material/check_circle:")
    else:
        st.error("AWS Credentials Not Found", icon=":material/cancel:")

    dynamo_ok, table_name, region_or_err = check_dynamodb_configured()
    if dynamo_ok:
        st.success("DynamoDB Memory Store Configured", icon=":material/check_circle:")
        st.caption(f"Table: {table_name}")
    else:
        st.warning("DynamoDB Memory Store Not Configured", icon=":material/warning:")

    st.success("Cognito Authentication Enabled", icon=":material/check_circle:")
    st.caption(f"Pool: {COGNITO_POOL_ID}")

# Main content
col1, col2 = st.columns([3, 1])
with col1:
    st.header("Generate Test Cases")
with col2:
    generate_button = st.button("Generate Test Cases", type="primary", use_container_width=True, disabled=not function_name)

if generate_button and function_name:
    try:
        function_name = sanitize_function_name(function_name)
    except ValueError as e:
        st.error(f"Invalid function name: {e}")
        st.stop()

    custom_prompt = sanitize_custom_instructions(custom_prompt) if custom_prompt else ""

    elapsed = time.time() - st.session_state.last_generation_time
    if elapsed < RATE_LIMIT_SECONDS:
        st.warning(f"Rate limit: please wait {int(RATE_LIMIT_SECONDS - elapsed)}s before generating again.")
        st.stop()

    if not check_aws_credentials():
        st.error("AWS Credentials not configured! Run `aws configure`.", icon=":material/cancel:")
        st.stop()

    st.session_state.function_name = function_name
    st.session_state.function_name = function_name
    st.session_state.target_filter = target_filter if target_filter else None
    st.session_state.feedback = {}
    st.session_state.last_generation_time = time.time()

    spinner_text = f"Generating test cases for {function_name}"
    if target_filter:
        spinner_text += f" (focusing on: {target_filter})"

    with st.spinner(spinner_text + "..."):
        result = invoke_test_generation(function_name, test_case_count, custom_prompt, target_filter, ignore_patterns)

    if not result['success']:
        st.error(result.get('error', 'Unknown error'))
        if result.get('output'):
            with st.expander("Additional Details"):
                st.text(result['output'])
        st.stop()

    st.session_state.raw_output = result['output']
    parsed = parse_agentcore_output(result['output'])
    st.session_state.test_cases = parsed['test_cases']
    st.session_state.analysis_summary = parsed['analysis_summary']
    st.session_state.generation_metadata = parsed['generation_metadata']

    if not st.session_state.test_cases:
        st.warning("No test cases were parsed from the output.", icon=":material/warning:")
        with st.expander("Raw Output"):
            st.text(result['output'])
        st.stop()

    st.success(f"Generated {len(st.session_state.test_cases)} test cases", icon=":material/check_circle:")

# Display test cases
if st.session_state.test_cases:
    st.divider()
    st.header(f"Test Cases for {st.session_state.function_name}")

    if st.session_state.analysis_summary:
        with st.expander("Function Analysis Summary", icon=":material/analytics:", expanded=False):
            s = st.session_state.analysis_summary
            c1, c2, c3 = st.columns(3)
            c1.metric("Runtime", s.get('runtime', 'N/A'))
            c1.metric("Code Chunks", s.get('total_chunks', 0))
            c2.metric("Dependencies", s.get('dependencies_count', 0))
            c2.metric("Input Patterns", s.get('input_patterns_count', 0))
            c3.metric("Output Patterns", s.get('output_patterns_count', 0))

    if st.session_state.generation_metadata:
        with st.expander("Generation Metadata", icon=":material/bar_chart:", expanded=False):
            m = st.session_state.generation_metadata
            c1, c2, c3 = st.columns(3)
            c1.metric("Positive Tests", m.get('positive_tests', 0))
            c2.metric("Negative Tests", m.get('negative_tests', 0))
            c3.metric("Edge Cases", m.get('edge_cases', 0))

    tab1, tab2, tab3 = st.tabs(["Review Test Cases", "Summary", "Raw Output"])

    with tab1:
        from integrations.memory_store import DynamoDBMemoryStore
        memory_store = DynamoDBMemoryStore()

        for idx, tc in enumerate(st.session_state.test_cases, 1):
            with st.container():
                ch1, ch2, ch3 = st.columns([2, 1, 1])
                ch1.subheader(f"Test Case {idx}: {tc['test_id']}")
                ch2.markdown(f"**Type:** `{tc['test_type'].upper()}`")
                ch3.markdown(f"**Confidence:** {tc['confidence_score']:.0%}")

                st.markdown(f"**Description:** {tc['description']}")
                st.markdown(f"**Category:** {tc['category']}")
                st.markdown("**Input Event:**")
                st.json(tc['input_event'], expanded=True)

                pattern_hash = memory_store._create_pattern_hash(tc)
                uk = f"tc_{pattern_hash[:16]}"

                cb1, cb2 = st.columns(2)
                is_accepted = st.session_state.feedback.get(uk, {}).get('status') == 'accepted'
                is_rejected = st.session_state.feedback.get(uk, {}).get('status') == 'rejected'

                with cb1:
                    if st.button("Accept", key=f"accept_{uk}", use_container_width=True, disabled=is_accepted, icon=":material/check:"):
                        st.session_state.feedback[uk] = {'status': 'accepted', 'test_case': tc,
                                                         'timestamp': datetime.utcnow().isoformat(), 'submitted': True}
                        st.rerun()
                with cb2:
                    if st.button("Reject", key=f"reject_{uk}", use_container_width=True, disabled=is_rejected, icon=":material/close:"):
                        st.session_state.feedback[uk] = {'status': 'rejected', 'test_case': tc,
                                                         'timestamp': datetime.utcnow().isoformat(), 'submitted': False}
                        st.rerun()

                if st.session_state.feedback.get(uk, {}).get('status') == 'rejected':
                    fd = st.session_state.feedback[uk]
                    if not fd.get('submitted'):
                        st.markdown("---")
                        st.markdown("**Please provide rejection details:**")
                        reason = st.selectbox("Rejection Reason", options=[
                            "missing_auth_headers", "wrong_status_code", "unrealistic_data",
                            "missing_required_fields", "incorrect_event_source", "invalid_json_structure",
                            "wrong_assertions", "incorrect_field_values", "missing_edge_cases", "other"
                        ], key=f"reason_{uk}")
                        custom_reason = st.text_area("Additional Details (Optional)", key=f"custom_{uk}",
                                                     placeholder="Explain why this test case was rejected...")
                        if st.button("Submit Rejection", key=f"submit_reject_{uk}", type="primary"):
                            fd['rejection_reason'] = reason
                            fd['custom_reason'] = custom_reason
                            fd['submitted'] = True
                            st.success("Rejection submitted!")
                    else:
                        st.warning(f"Rejected: {fd.get('rejection_reason', 'N/A')}", icon=":material/cancel:")
                        if fd.get('custom_reason'):
                            st.caption(f"Details: {fd['custom_reason']}")
                elif st.session_state.feedback.get(uk, {}).get('status') == 'accepted':
                    st.success("This test case has been accepted", icon=":material/check_circle:")

                st.divider()

    with tab2:
        st.subheader("Feedback Summary")
        total = len(st.session_state.test_cases)
        accepted = sum(1 for f in st.session_state.feedback.values() if f['status'] == 'accepted')
        rejected = sum(1 for f in st.session_state.feedback.values() if f['status'] == 'rejected')
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", total)
        c2.metric("Accepted", accepted)
        c3.metric("Rejected", rejected)
        c4.metric("Pending", total - accepted - rejected)

    with tab3:
        st.subheader("Raw AgentCore Output")
        st.text(st.session_state.raw_output)

    # Save feedback
    st.divider()
    _, col_save, _ = st.columns([2, 1, 2])
    with col_save:
        if st.button("Save All Feedback to Memory", type="primary", use_container_width=True):
            if not st.session_state.feedback:
                st.warning("No feedback provided yet.")
            else:
                unsubmitted = [k for k, f in st.session_state.feedback.items()
                               if f['status'] == 'rejected' and not f.get('submitted')]
                if unsubmitted:
                    st.error(f"Please submit rejection details for {len(unsubmitted)} test case(s) first.")
                else:
                    try:
                        # Prepare feedback items
                        items = []
                        for fd in st.session_state.feedback.values():
                            if fd.get('submitted') and fd.get('test_case'):
                                items.append({
                                    'test_case': fd['test_case'],
                                    'feedback': fd['status'],
                                    'rejection_reason': fd.get('rejection_reason', ''),
                                    'custom_reason': fd.get('custom_reason', ''),
                                })
                        
                        # Call AgentCore API with authentication
                        payload = {
                            'action': 'save_feedback',
                            'function_name': st.session_state.function_name,
                            'test_cases_with_feedback': items,
                            'user_id': get_current_user_id(),
                            'target_function': st.session_state.get('target_filter'),
                        }
                        
                        token = _ensure_token()
                        result = invoke_agentcore(payload, bearer_token=token)

                        if result.get("success"):
                            # Output is already extracted by invoke_agentcore
                            output = result.get("output", "")
                            
                            # Try to parse as JSON if it's a dict/object string
                            try:
                                if isinstance(output, str):
                                    response_data = json.loads(output)
                                else:
                                    response_data = output
                                    
                                st.success(f"✅ Feedback saved! {response_data.get('accepted_count', 0)} accepted, {response_data.get('rejected_count', 0)} rejected")
                            except json.JSONDecodeError:
                                # Output is plain text, not JSON
                                st.success(f"✅ Feedback saved! {output}")
                        else:
                            st.error(f"Failed to save feedback: {result.get('error')}")

                    except Exception as e:
                        st.error(f"Error saving feedback: {e}")

# Footer
st.divider()
st.markdown("<div style='text-align: center; color: gray; padding: 20px;'>Lambda Test Case Generator | Powered by Amazon Bedrock</div>",
            unsafe_allow_html=True)
