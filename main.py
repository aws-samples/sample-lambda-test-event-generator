# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import os
import json
import logging
import time
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

# Import agents
from agents.analyzer_agent import AnalyzerAgent
from agents.generator_agent import GeneratorAgent
from agents.validator_agent import ValidatorAgent
from integrations.memory_store import DynamoDBMemoryStore

# Import formatters
from utils.formatters import format_setup_instructions

# Input validation
import re
from typing import Optional, List, Union

# Constants for validation
MAX_FUNCTION_NAME_LENGTH = 170
MAX_CUSTOM_INSTRUCTIONS_LENGTH = 2000
MAX_TARGET_FILTER_LENGTH = 200
MAX_IGNORE_PATTERNS = 50
MAX_IGNORE_PATTERN_LENGTH = 200
MIN_TEST_CASES = 1
MAX_TEST_CASES = 50
FUNCTION_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_:/-]+$')
TARGET_FILTER_PATTERN = re.compile(r'^[a-zA-Z0-9_./\-]+$')
IGNORE_PATTERN_PATTERN = re.compile(r'^[a-zA-Z0-9_./\-*]+$')

# Rate limiting constants
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_REQUESTS = 5  # max requests per window per user

# Rate limiting storage (in-memory, per-process)
# For production, use DynamoDB or Redis for distributed rate limiting
_rate_limit_store = defaultdict(list)


class ValidationError(Exception):
    """Custom exception for input validation errors."""
    pass


class RateLimitError(Exception):
    """Custom exception for rate limit violations."""
    pass


def sanitize_error_message(error: Exception, context: str = "") -> str:
    """
    Sanitize error messages to prevent leaking internal details.
    
    SECURITY: Removes file paths, AWS SDK details, and infrastructure information.
    Returns user-friendly error messages only.
    
    Args:
        error: Original exception
        context: Context for the error (e.g., "analysis", "generation")
        
    Returns:
        Sanitized error message safe for external display
    """
    error_str = str(error)
    
    # Map of internal errors to user-friendly messages
    if "ResourceNotFoundException" in error_str or "function not found" in error_str.lower():
        return "Lambda function not found. Please verify the function name and region."
    
    if "AccessDenied" in error_str or "not authorized" in error_str.lower():
        return "Access denied. Please verify IAM permissions for Lambda and Bedrock."
    
    if "ThrottlingException" in error_str or "rate exceeded" in error_str.lower():
        return "AWS API rate limit exceeded. Please try again in a few moments."
    
    if "ValidationException" in error_str:
        return "Invalid request parameters. Please check your input."
    
    if "ServiceException" in error_str or "InternalError" in error_str:
        return "AWS service error. Please try again later."
    
    if "timeout" in error_str.lower() or "timed out" in error_str.lower():
        return "Request timed out. Please try again."
    
    if "bedrock" in error_str.lower() and "model" in error_str.lower():
        return "Bedrock model error. Please verify model access is enabled."
    
    # Generic error for anything else (don't leak details)
    if context:
        return f"Error during {context}. Please try again or contact support."
    return "An error occurred. Please try again or contact support."


def check_rate_limit(user_id: str) -> None:
    """
    Check if user has exceeded rate limit.
    
    SECURITY: Prevents cost abuse from unlimited Bedrock API calls.
    
    Args:
        user_id: User identifier (from Cognito or session)
        
    Raises:
        RateLimitError: If rate limit exceeded
    """
    current_time = time.time()
    
    # Clean up old entries
    _rate_limit_store[user_id] = [
        timestamp for timestamp in _rate_limit_store[user_id]
        if current_time - timestamp < RATE_LIMIT_WINDOW
    ]
    
    # Check if limit exceeded
    if len(_rate_limit_store[user_id]) >= RATE_LIMIT_MAX_REQUESTS:
        oldest_request = _rate_limit_store[user_id][0]
        wait_time = int(RATE_LIMIT_WINDOW - (current_time - oldest_request))
        raise RateLimitError(
            f"Rate limit exceeded. Maximum {RATE_LIMIT_MAX_REQUESTS} requests per {RATE_LIMIT_WINDOW} seconds. "
            f"Please wait {wait_time} seconds before trying again."
        )
    
    # Record this request
    _rate_limit_store[user_id].append(current_time)
    logger.info(f"Rate limit check passed for user {user_id}: {len(_rate_limit_store[user_id])}/{RATE_LIMIT_MAX_REQUESTS} requests")


def validate_function_name(function_name: any) -> str:
    """
    Validate Lambda function name.
    
    Args:
        function_name: Function name to validate
        
    Returns:
        Validated and sanitized function name
        
    Raises:
        ValidationError: If validation fails
    """
    if not function_name:
        raise ValidationError("function_name is required")
    
    if not isinstance(function_name, str):
        raise ValidationError(f"function_name must be a string, got {type(function_name).__name__}")
    
    function_name = function_name.strip()
    
    if len(function_name) > MAX_FUNCTION_NAME_LENGTH:
        raise ValidationError(f"function_name too long (max {MAX_FUNCTION_NAME_LENGTH} characters)")
    
    if not FUNCTION_NAME_PATTERN.match(function_name):
        raise ValidationError("function_name contains invalid characters. Only alphanumeric, _, :, /, - allowed")
    
    return function_name


def validate_num_test_cases(num_test_cases: any) -> int:
    """
    Validate number of test cases.
    
    Args:
        num_test_cases: Number to validate
        
    Returns:
        Validated number of test cases
        
    Raises:
        ValidationError: If validation fails
    """
    if num_test_cases is None:
        return 10  # Default
    
    if not isinstance(num_test_cases, (int, float)):
        raise ValidationError(f"num_test_cases must be a number, got {type(num_test_cases).__name__}")
    
    num_test_cases = int(num_test_cases)
    
    if num_test_cases < MIN_TEST_CASES:
        raise ValidationError(f"num_test_cases must be at least {MIN_TEST_CASES}")
    
    if num_test_cases > MAX_TEST_CASES:
        raise ValidationError(f"num_test_cases cannot exceed {MAX_TEST_CASES}")
    
    return num_test_cases


def validate_custom_instructions(custom_instructions: any) -> str:
    """
    Validate custom instructions.
    
    Args:
        custom_instructions: Instructions to validate
        
    Returns:
        Validated and sanitized instructions
        
    Raises:
        ValidationError: If validation fails
    """
    if not custom_instructions:
        return ""
    
    if not isinstance(custom_instructions, str):
        raise ValidationError(f"custom_instructions must be a string, got {type(custom_instructions).__name__}")
    
    custom_instructions = custom_instructions.strip()
    
    if len(custom_instructions) > MAX_CUSTOM_INSTRUCTIONS_LENGTH:
        raise ValidationError(f"custom_instructions too long (max {MAX_CUSTOM_INSTRUCTIONS_LENGTH} characters)")
    
    return custom_instructions


def validate_target_filter(target_filter: any) -> str:
    """
    Validate target filter.
    
    Args:
        target_filter: Target to validate
        
    Returns:
        Validated and sanitized target filter
        
    Raises:
        ValidationError: If validation fails
    """
    if not target_filter:
        return ""
    
    if not isinstance(target_filter, str):
        raise ValidationError(f"target_filter must be a string, got {type(target_filter).__name__}")
    
    target_filter = target_filter.strip()
    
    if len(target_filter) > MAX_TARGET_FILTER_LENGTH:
        raise ValidationError(f"target_filter too long (max {MAX_TARGET_FILTER_LENGTH} characters)")
    
    if not TARGET_FILTER_PATTERN.match(target_filter):
        raise ValidationError("target_filter contains invalid characters. Only alphanumeric, _, ., /, - allowed")
    
    return target_filter


def validate_ignore_patterns(ignore_patterns: any) -> List[str]:
    """
    Validate ignore patterns.
    
    Args:
        ignore_patterns: Patterns to validate
        
    Returns:
        Validated list of ignore patterns
        
    Raises:
        ValidationError: If validation fails
    """
    if not ignore_patterns:
        return []
    
    if not isinstance(ignore_patterns, list):
        raise ValidationError(f"ignore_patterns must be a list, got {type(ignore_patterns).__name__}")
    
    if len(ignore_patterns) > MAX_IGNORE_PATTERNS:
        raise ValidationError(f"Too many ignore patterns (max {MAX_IGNORE_PATTERNS})")
    
    validated_patterns = []
    for i, pattern in enumerate(ignore_patterns):
        if not isinstance(pattern, str):
            raise ValidationError(f"ignore_patterns[{i}] must be a string, got {type(pattern).__name__}")
        
        pattern = pattern.strip()
        
        if not pattern:
            continue  # Skip empty patterns
        
        if len(pattern) > MAX_IGNORE_PATTERN_LENGTH:
            raise ValidationError(f"ignore_patterns[{i}] too long (max {MAX_IGNORE_PATTERN_LENGTH} characters)")
        
        if not IGNORE_PATTERN_PATTERN.match(pattern):
            raise ValidationError(f"ignore_patterns[{i}] contains invalid characters. Only alphanumeric, _, ., /, -, * allowed")
        
        validated_patterns.append(pattern)
    
    return validated_patterns


def validate_payload(payload: dict, action: str) -> dict:
    """
    Validate and sanitize entire payload based on action.
    
    Args:
        payload: Raw payload from API
        action: Action being performed
        
    Returns:
        Validated and sanitized payload
        
    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(payload, dict):
        raise ValidationError(f"Payload must be a dict, got {type(payload).__name__}")
    
    validated = {}
    
    if action == 'generate_test_cases':
        validated['function_name'] = validate_function_name(payload.get('function_name'))
        validated['num_test_cases'] = validate_num_test_cases(payload.get('num_test_cases', 10))
        validated['custom_instructions'] = validate_custom_instructions(payload.get('custom_instructions', ''))
        validated['target_filter'] = validate_target_filter(payload.get('target_filter', ''))
        validated['ignore_patterns'] = validate_ignore_patterns(payload.get('ignore_patterns', []))
    
    elif action == 'save_feedback':
        validated['function_name'] = validate_function_name(payload.get('function_name'))
        
        # Validate test_cases_with_feedback
        feedback_items = payload.get('test_cases_with_feedback', [])
        if not isinstance(feedback_items, list):
            raise ValidationError(f"test_cases_with_feedback must be a list, got {type(feedback_items).__name__}")
        
        if len(feedback_items) > 100:  # Reasonable limit
            raise ValidationError("Too many feedback items (max 100)")
        
        validated['test_cases_with_feedback'] = feedback_items
        
        # Validate user_id
        user_id = payload.get('user_id', '')
        if not isinstance(user_id, str):
            raise ValidationError(f"user_id must be a string, got {type(user_id).__name__}")
        validated['user_id'] = user_id[:200]  # Limit length
        
        # Validate target_function
        validated['target_function'] = validate_target_filter(payload.get('target_function', ''))
    
    return validated


# Import formatters
from utils.formatters import format_setup_instructions

# AgentCore Runtime
from bedrock_agentcore.runtime import BedrockAgentCoreApp
app = BedrockAgentCoreApp()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

REGION = os.getenv("AWS_REGION", "us-east-1")

# Initialize agents
analyzer_agent = AnalyzerAgent(region=REGION)
generator_agent = GeneratorAgent(region=REGION)
validator_agent = ValidatorAgent(region=REGION)
memory_store = DynamoDBMemoryStore(region=REGION)


def sanitize_analysis_result(analysis_result: "AnalysisResult") -> "AnalysisResult":
    """
    Sanitize analysis result to remove source code before returning to user.
    
    SECURITY: This prevents Lambda source code exposure through the API.
    Only metadata and patterns are kept - no raw source code.
    
    Args:
        analysis_result: Original analysis result with source code
        
    Returns:
        Sanitized analysis result without source code
    """
    from agents.analyzer_agent import AnalysisResult
    
    # Create sanitized version without source code
    sanitized = AnalysisResult(
        function_name=analysis_result.function_name,
        function_info={
            'function_name': analysis_result.function_info.get('function_name', analysis_result.function_name),
            'runtime': analysis_result.function_info.get('runtime'),
            'handler': analysis_result.function_info.get('handler'),
            'timeout': analysis_result.function_info.get('timeout'),
            'memory_size': analysis_result.function_info.get('memory_size'),
            'code_size': analysis_result.function_info.get('code_size'),
            # Remove: code_sha256, last_modified, environment variables, etc.
        },
        code_chunks=[],  # SECURITY: Remove all source code chunks
        chunk_summaries=[
            {
                'chunk_id': cs.get('chunk_id'),
                'file_name': cs.get('file_name'),
                'chunk_type': cs.get('chunk_type'),
                'inputs': cs.get('inputs', []),
                'outputs': cs.get('outputs', []),
                'edge_cases': cs.get('edge_cases', []),
                'metadata': cs.get('metadata', {}),
                # Remove: 'content' field which contains source code
            }
            for cs in analysis_result.chunk_summaries
        ],
        overall_structure=analysis_result.overall_structure,
        dependencies=analysis_result.dependencies,
        error_patterns=analysis_result.error_patterns,
        input_patterns=analysis_result.input_patterns,
        output_patterns=analysis_result.output_patterns,
        analysis_metadata=analysis_result.analysis_metadata
    )
    
    logger.info("Analysis result sanitized - source code removed")
    return sanitized


def generate_comprehensive_test_cases(
    function_name: str,
    num_test_cases: int = 10,
    custom_instructions: str = "",
    target_filter: str = "",
    ignore_patterns: list = None
) -> str:
    """
    Generate comprehensive test cases for a Lambda function based on code analysis.
    This is a single-invoke function that returns all test cases with full details.
    
    Args:
        function_name: The Lambda function name or ARN (validated)
        num_test_cases: Number of test cases to generate (validated, 1-50)
        custom_instructions: Optional custom instructions (validated, max 2000 chars)
        target_filter: Optional function/class/file to focus on (validated)
        ignore_patterns: Optional list of file/folder patterns to ignore (validated)
        
    Returns:
        Comprehensive test cases with full details for review
        
    Note: All inputs should be validated before calling this function.
    """
    try:
        # Defense in depth: Validate inputs even if already validated by handler
        # This protects against direct function calls
        try:
            function_name = validate_function_name(function_name)
            num_test_cases = validate_num_test_cases(num_test_cases)
            custom_instructions = validate_custom_instructions(custom_instructions)
            target_filter = validate_target_filter(target_filter)
            ignore_patterns = validate_ignore_patterns(ignore_patterns)
        except ValidationError as e:
            logger.error(f"Input validation failed: {e}")
            return f"Input validation error: {str(e)}"
        
        logger.info(f"Generating {num_test_cases} test cases for {function_name}")
        if custom_instructions:
            logger.info(f"Custom instructions provided (length: {len(custom_instructions)} chars)")
        if target_filter:
            logger.info(f"Target filter: {target_filter}")
        if ignore_patterns:
            logger.info(f"Ignore patterns: {ignore_patterns}")
        
        # Step 1: Analyze Lambda function
        logger.info("Step 1: Analyzing Lambda function code...")
        try:
            analysis_result = analyzer_agent.analyze_lambda_function(
                function_name=function_name,
                target_filter=target_filter,
                ignore_patterns=ignore_patterns
            )
            
        except ValueError as e:
            error_msg = sanitize_error_message(e, "analysis")
            logger.error(f"Analysis failed: {str(e)}")
            return error_msg
        logger.debug(f"analysis result: {analysis_result}")
        
        # Step 2: Generate test cases
        logger.info("Step 2: Generating test cases based on code analysis...")
        logger.info(f"Chunk summaries count: {len(analysis_result.chunk_summaries)}")
        for i, cs in enumerate(analysis_result.chunk_summaries):
            logger.info(f"Chunk {i}: id={cs.get('chunk_id')}, content_len={len(cs.get('content', ''))}, inputs={len(cs.get('inputs', []))}, outputs={len(cs.get('outputs', []))}")
        try:
            generation_result = generator_agent.generate_test_cases(
                analysis_result=analysis_result,
                num_test_cases=num_test_cases,
                custom_instructions=custom_instructions
            )
        except ValueError as e:
            error_msg = sanitize_error_message(e, "test generation")
            logger.error(f"Test generation failed: {str(e)}")
            raise RuntimeError(f"❌ Test generation failed: {error_msg}\n\nPlease try again or check your Lambda function code.")
        except Exception as e:
            error_msg = sanitize_error_message(e, "test generation")
            logger.error(f"Unexpected error during test generation: {str(e)}")
            raise RuntimeError(f"❌ {error_msg}")
        logger.info(f"Generation result: {generation_result.total_candidates} candidates")
        logger.debug(f"test generation: {generation_result}")
        
        # Step 3: Validate and filter
        logger.info("Step 3: Validating and filtering test cases...")
        validation_result = validator_agent.validate_and_filter(
            generation_result=generation_result,
            quality_threshold=0.5,
            max_test_cases=num_test_cases
        )
        logger.debug(f"validation result : {validation_result}")
        
        # SECURITY: Sanitize analysis result to remove source code before output
        # This ensures source code never reaches the response
        analysis_result = sanitize_analysis_result(analysis_result)
        
        # Format comprehensive output with all test case details
        # SECURITY: Never include source code in output
        output = f"""
{'='*80}
LAMBDA TEST CASE GENERATION COMPLETE
{'='*80}

Function: {analysis_result.function_name}
Runtime: {analysis_result.function_info['runtime']}
Handler: {analysis_result.function_info['handler']}
Generated: {len(validation_result.test_cases)} test cases

SECURITY NOTE: Source code is analyzed internally but never exposed in output.
Only test input patterns and metadata are returned.

"""
        
        if custom_instructions:
            output += f"Custom Instructions Applied: {custom_instructions}\n\n"
        
        output += f"""
{'='*80}
FUNCTION ANALYSIS SUMMARY
{'='*80}

Code Analysis:
  - Total Code Chunks: {len(analysis_result.code_chunks)}
  - Dependencies Found: {len(analysis_result.dependencies)}
  - Input Patterns Detected: {len(analysis_result.input_patterns)}
  - Output Patterns Detected: {len(analysis_result.output_patterns)}
  - Error Handling Patterns: {len(analysis_result.error_patterns)}

Configuration:
  - Timeout: {analysis_result.function_info['timeout']} seconds
  - Memory: {analysis_result.function_info['memory_size']} MB
  - Code Size: {analysis_result.function_info['code_size']} bytes

"""
        
        # Show detected input patterns (first 5)
        if analysis_result.input_patterns:
            output += "Detected Input Patterns (from code):\n"
            for pattern in analysis_result.input_patterns[:5]:
                output += f"  - {pattern}\n"
            if len(analysis_result.input_patterns) > 5:
                output += f"  ... and {len(analysis_result.input_patterns) - 5} more\n"
            output += "\n"
        
        output += f"""
{'='*80}
GENERATION METADATA
{'='*80}

Per-Chunk Generation:
  - Chunks Processed: {generation_result.generation_metadata.get('chunks_processed', 0)}
  - Total Candidates Generated: {generation_result.total_candidates}
  - Generation Strategy: 2x requested (generates double, validator selects best)

Test Distribution:
  - Positive Tests: {generation_result.candidates_by_type.get('positive', 0)}
  - Negative Tests: {generation_result.candidates_by_type.get('negative', 0)}
  - Edge Cases: {generation_result.candidates_by_type.get('edge', 0)}

Intelligence Used:
  - Memory Patterns Used: {len(generation_result.memory_patterns_used)}
  - Rejected Patterns Avoided: {len(generation_result.rejected_patterns_avoided)}

Validation & Selection:
  - Original Candidates: {validation_result.original_count}
  - After Validation: {validation_result.validation_metadata['validation_steps']['after_validation']}
  - After Deduplication: {validation_result.validation_metadata['validation_steps']['after_deduplication']}
  - After Scoring: {validation_result.validation_metadata['validation_steps']['after_scoring']}
  - After Quality Filter: {validation_result.validation_metadata['validation_steps']['after_quality_filter']}
  - Final Selected: {validation_result.validated_count}

"""
        
        # Display all test cases with full details
        output += f"""
{'='*80}
ALL TEST CASES - REVIEW AND PROVIDE FEEDBACK
{'='*80}

"""
        
        # Get diversity metrics
        diversity = validation_result.validation_metadata.get('diversity_metrics', {})
        if diversity:
            output += f"""
COVERAGE SUMMARY:
  - Functions Covered: {diversity.get('unique_functions_covered', 0)}
  - Files Covered: {diversity.get('unique_files_covered', 0)}
  - Chunks Covered: {diversity.get('unique_chunks_covered', 0)}
  - Test Distribution: {diversity.get('test_type_distribution', {})}

"""
        
        for idx, tc in enumerate(validation_result.test_cases, 1):
            test_type_label = {
                'positive': '✅ POSITIVE',
                'negative': '❌ NEGATIVE',
                'edge': '⚠️  EDGE CASE'
            }.get(tc.test_type, 'TEST')
            
            # Extract chunk metadata
            chunk_id = tc.metadata.get('chunk_id', 'N/A')
            file_name = tc.metadata.get('file_name', 'N/A')
            chunk_type = tc.metadata.get('chunk_type', 'N/A')
            
            output += f"""
{'─'*80}
TEST CASE {idx}: {tc.test_id}
{'─'*80}

Type: {test_type_label}
Category: {tc.category}
Confidence: {tc.confidence_score:.0%}

TARGET:
  Chunk: {chunk_id}
  File: {file_name}
  Chunk Type: {chunk_type}

Description:
  {tc.description}

INPUT EVENT:
{json.dumps(tc.input_event, indent=2)}

NOTES:
  {tc.notes}

METADATA:
  Generation Method: {tc.metadata.get('generation_method', 'N/A')}

"""
        
        # Instructions for providing feedback
        output += f"""
{'='*80}
NEXT STEPS
{'='*80}

Review the test cases above. The system has analyzed your Lambda code to generate
realistic INPUT test events based on what your code actually expects.

Run these test events against your Lambda function to see the actual output.

To provide feedback and help the system learn:
1. Review each test case
2. Note which ones are good (accept) and which need improvement (reject)
3. For rejected cases, note the specific reason

The system will learn from your feedback and improve future test generation!

{'='*80}
"""
        
        return output
        
    except Exception as e:
        error_msg = sanitize_error_message(e, "test case generation")
        logger.error(f"Error generating test cases: {str(e)}", exc_info=True)
        return f"Error: {error_msg}"


def get_memory_stats_report() -> str:
    """
    Get statistics about DynamoDB memory store and system integration status.
    
    Returns:
        Memory store statistics and configuration info
    """
    try:
        memory_stats = memory_store.get_memory_stats()
        
        report = f"""
# System Integration Status

## DynamoDB Memory Store
**Status**: {'✅ Available' if memory_stats.get('status') == 'available' else '❌ ' + memory_stats.get('status', 'unavailable')}
**Table Name**: {memory_stats.get('table_name', 'N/A')}
**Region**: {memory_stats.get('region', 'N/A')}
**Schema**: {memory_stats.get('schema', 'N/A')}

### Statistics
- **Total Patterns**: {memory_stats.get('total_patterns', 0)}
- **Accepted Patterns**: {memory_stats.get('accepted_patterns', 0)}
- **Rejected Patterns**: {memory_stats.get('rejected_patterns', 0)}

{memory_stats.get('note', '')}

## Setup Instructions
{format_setup_instructions(memory_stats)}

## Environment Variables
- `AWS_REGION`: {REGION}
- `DYNAMODB_TABLE_NAME`: {memory_stats.get('table_name', 'lambda-testcase-memory')}
"""
        
        return report
    except Exception as e:
        error_msg = sanitize_error_message(e, "memory stats retrieval")
        logger.error(f"Error getting memory stats: {str(e)}")
        return f"Error: {error_msg}"


def main(function_name: str, num_test_cases: int = 10, custom_instructions: str = ""):
    """
    Main entry point for direct invocation (local mode)
    
    Args:
        function_name: Lambda function name
        num_test_cases: Number of test cases to generate
        custom_instructions: Optional custom instructions
        
    Returns:
        Test case generation output
    """
    return generate_comprehensive_test_cases(
        function_name=function_name,
        num_test_cases=num_test_cases,
        custom_instructions=custom_instructions
    )


# AgentCore Runtime entrypoint (single handler - routes by 'action' field)
if app:
    @app.entrypoint
    def handler(payload: dict, context=None) -> dict:
        """
        Single AgentCore Runtime entrypoint. Routes by payload 'action' field.
        
        Actions:
            - generate_test_cases (default): Generate test cases for a Lambda function
            - get_memory_stats: Get DynamoDB memory store statistics
            - save_feedback: Save user feedback to memory store
            - health_check: Health check
        
        All inputs are validated before processing.
        Rate limiting applied to prevent cost abuse.
        """
        action = payload.get('action', 'generate_test_cases')
        session_id = getattr(context, 'session_id', None) if context else None
        
        # Extract user ID for rate limiting (from Cognito claims or session)
        user_id = 'anonymous'
        if context and hasattr(context, 'identity'):
            user_id = getattr(context.identity, 'user_id', 'anonymous')
        elif context and hasattr(context, 'authorizer'):
            # Try to get from JWT claims
            authorizer = getattr(context, 'authorizer', {})
            if isinstance(authorizer, dict):
                user_id = authorizer.get('sub') or authorizer.get('username') or 'anonymous'
        
        # Health check - no validation or rate limiting needed
        if action == 'health_check':
            return {
                'status': 'healthy',
                'service': 'lambda-test-case-generator',
                'memory_store': 'available' if memory_store and memory_store.is_available() else 'unavailable'
            }
        
        # Get memory stats - no validation needed, but apply rate limiting
        if action == 'get_memory_stats':
            try:
                check_rate_limit(user_id)
                return {'success': True, 'report': get_memory_stats_report()}
            except RateLimitError as e:
                logger.warning(f"Rate limit exceeded for user {user_id}: {e}")
                return {'success': False, 'error': str(e)}
            except Exception as e:
                logger.error(f"AgentCore: Error getting memory stats: {e}", exc_info=True)
                error_msg = sanitize_error_message(e, "memory stats retrieval")
                return {'success': False, 'error': error_msg}
        
        # Apply rate limiting for actions that invoke Bedrock
        if action in ['generate_test_cases', 'save_feedback']:
            try:
                check_rate_limit(user_id)
            except RateLimitError as e:
                logger.warning(f"Rate limit exceeded for user {user_id}: {e}")
                return {'success': False, 'error': str(e)}
        
        # Validate payload for actions that require it
        try:
            validated_payload = validate_payload(payload, action)
        except ValidationError as e:
            logger.warning(f"AgentCore: Validation error for action '{action}': {e}")
            return {'success': False, 'error': f"Validation error: {str(e)}"}
        except Exception as e:
            logger.error(f"AgentCore: Unexpected validation error: {e}", exc_info=True)
            return {'success': False, 'error': f"Validation error: {str(e)}"}
        
        # Save feedback
        if action == 'save_feedback':
            try:
                from agents.validator_agent import ValidatorAgent
                validator = ValidatorAgent(region=os.getenv('AWS_REGION', 'us-east-1'))
                
                result = validator.process_user_feedback(
                    function_name=validated_payload['function_name'],
                    test_cases_with_feedback=validated_payload['test_cases_with_feedback'],
                    user_id=validated_payload['user_id'],
                    target_function=validated_payload['target_function'],
                )
                
                return result
            except Exception as e:
                logger.error(f"AgentCore: Error saving feedback: {e}", exc_info=True)
                error_msg = sanitize_error_message(e, "feedback save")
                return {'success': False, 'error': error_msg}
        
        # Default: generate_test_cases
        try:
            logger.info(f"AgentCore: Generating tests for {validated_payload['function_name']} (user: {user_id}, session: {session_id})")
            
            output = generate_comprehensive_test_cases(
                function_name=validated_payload['function_name'],
                num_test_cases=validated_payload['num_test_cases'],
                custom_instructions=validated_payload['custom_instructions'],
                target_filter=validated_payload['target_filter'],
                ignore_patterns=validated_payload['ignore_patterns']
            )
            
            if "Lambda function not found" in output or "not found in Lambda code" in output:
                return {'success': False, 'error': output, 'output': output}
            
            return {'success': True, 'output': output, 'function_name': validated_payload['function_name']}
            
        except Exception as e:
            logger.error(f"AgentCore: Error in test generation: {e}", exc_info=True)
            error_msg = sanitize_error_message(e, "test generation")
            return {'success': False, 'error': error_msg}


if __name__ == "__main__":
    logger.info("Starting AgentCore Runtime server...")
    app.run()
