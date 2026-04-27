# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
DynamoDB Memory Store - Optimized with composite keys for target-specific patterns.

This module handles:
- Storing accepted/rejected test case patterns
- Learning from user feedback
- Pattern matching for similar functions
- Memory-based test case generation improvements
- Target-specific pattern storage (function-level or GLOBAL)

Schema (Optimized - No Scans, No GSIs):
- Primary Key: function_target (PK) + pattern_sk (SK)
- PK Format: {function_name}#{target_function} or {function_name}#GLOBAL
- Sort Key Format: FEEDBACK#{accepted|rejected}#PATTERN#{pattern_id}
- TTL enabled for auto-cleanup
- begins_with() queries eliminate need for FilterExpression

Examples:
- my-lambda#validate_user - Patterns specific to validate_user function
- my-lambda#GLOBAL - Patterns for entire Lambda (no specific target)
"""

import os
import boto3
import json
import logging
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from decimal import Decimal
from boto3.dynamodb.conditions import Key
import hashlib

logger = logging.getLogger(__name__)


VALID_FEEDBACK_VALUES = {'accepted', 'rejected'}
VALID_REJECTION_REASONS = {
    'missing_auth_headers', 'wrong_status_code', 'unrealistic_data',
    'missing_required_fields', 'incorrect_event_source', 'invalid_json_structure',
    'wrong_assertions', 'incorrect_field_values', 'missing_edge_cases', 'other'
}
MAX_CUSTOM_REASON_LENGTH = 500
MAX_INPUT_PATTERN_SIZE = 50000  # 50KB max for input pattern JSON


class DynamoDBMemoryStore:
    """Optimized DynamoDB memory store with composite sort key for fast queries."""
    
    def __init__(self, region: str = None, table_name: str = None):
        """Initialize the DynamoDB memory store. Only supports new schema with function_target PK."""
        self.region = region or os.getenv('AWS_REGION', 'us-east-1')
        self.table_name = table_name or os.getenv('DYNAMODB_TABLE_NAME', 'lambda-testcase-memory')
        
        try:
            self.dynamodb = boto3.resource('dynamodb', region_name=self.region)
            self.table = self.dynamodb.Table(self.table_name)
            
            # Verify table has correct schema (function_target as PK)
            table_info = self.table.meta.client.describe_table(TableName=self.table_name)
            key_schema = table_info['Table']['KeySchema']
            
            partition_key = next((k['AttributeName'] for k in key_schema if k['KeyType'] == 'HASH'), None)
            
            if partition_key != 'function_target':
                logger.error(f"❌ DynamoDB table has incorrect schema!")
                logger.error(f"   Expected partition key: 'function_target'")
                logger.error(f"   Found partition key: '{partition_key}'")
                logger.error(f"")
                logger.error(f"   Please create a new table with the correct schema:")
                logger.error(f"   See docs/DYNAMODB_SETUP.md for instructions")
                self.available = False
                return
            
            self.available = True
            logger.info(f"✅ DynamoDB memory store initialized: {self.table_name}")
            
        except Exception as e:
            logger.warning(f"DynamoDB memory store not available: {str(e)}")
            self.available = False
    
    def is_available(self) -> bool:
        """Check if DynamoDB memory store is available."""
        return self.available
    
    def store_test_case_feedback(
        self, 
        function_name: str, 
        test_case: Dict[str, Any], 
        feedback: str,
        rejection_reason: str = None,
        custom_reason: str = None,
        user_id: str = "system",
        target_function: str = None
    ) -> bool:
        """
        Store user feedback for a test case.
        
        Args:
            function_name: Name of the Lambda function
            test_case: The test case data
            feedback: 'accepted' or 'rejected'
            rejection_reason: Predefined rejection reason (if rejected)
            custom_reason: Custom rejection reason text (if rejected)
            user_id: ID of the user providing feedback
            target_function: Specific function/class/file this pattern is for (None = GLOBAL)
            
        Returns:
            True if stored successfully, False otherwise
        """
        if not self.available:
            return False
        
        # Validate feedback inputs (Threat Model: Tampering / Memory Store Poisoning)
        if feedback not in VALID_FEEDBACK_VALUES:
            logger.warning(f"Invalid feedback value rejected: {feedback}")
            return False
        if rejection_reason and rejection_reason not in VALID_REJECTION_REASONS:
            logger.warning(f"Invalid rejection reason rejected: {rejection_reason}")
            return False
        if custom_reason:
            custom_reason = custom_reason[:MAX_CUSTOM_REASON_LENGTH]
        
        input_json = json.dumps(test_case.get('input_event', {}), sort_keys=True)
        if len(input_json) > MAX_INPUT_PATTERN_SIZE:
            logger.warning(f"Input pattern too large ({len(input_json)} bytes), rejecting")
            return False
        
        try:
            timestamp = datetime.utcnow().isoformat() + 'Z'
            pattern_hash = self._create_pattern_hash(test_case)
            success_rate = Decimal('1.0') if feedback == 'accepted' else Decimal('0.0')
            
            # Create composite partition key with target function
            target = target_function if target_function else "GLOBAL"
            function_target = f"{function_name}#{target}"
            
            # Create composite sort key: FEEDBACK#{feedback}#PATTERN#{pattern_id}
            pattern_sk = f"FEEDBACK#{feedback}#PATTERN#{pattern_hash}"
            
            # Calculate TTL (90 days from now)
            ttl = int(time.time()) + (90 * 24 * 60 * 60)
            
            item = {
                'function_target': function_target,  # Partition Key (function_name#target)
                'pattern_sk': pattern_sk,  # Sort Key (composite)
                'function_name': function_name,  # Keep for reference
                'target_function': target,  # Keep for reference
                'pattern_hash': pattern_hash,  # Keep for reference
                'test_type': test_case.get('test_type', 'unknown'),
                'category': test_case.get('category', 'unknown'),
                'input_pattern': json.dumps(test_case.get('input_event', {}), sort_keys=True),
                'feedback': feedback,
                'user_id': user_id,
                'timestamp': timestamp,
                'usage_count': 1,
                'success_rate': success_rate,
                'ttl': ttl,
                'metadata': {
                    'description': test_case.get('description', ''),
                    'confidence_score': Decimal(str(test_case.get('confidence_score', 0.5)))
                }
            }
            
            # Add optional fields if present
            if test_case.get('expected_output'):
                item['expected_output_pattern'] = json.dumps(test_case.get('expected_output', {}), sort_keys=True)
            
            if test_case.get('assertions'):
                item['metadata']['assertions'] = test_case.get('assertions', [])
            
            # Add rejection details if rejected
            if feedback == 'rejected':
                if rejection_reason:
                    item['rejection_reason'] = rejection_reason
                if custom_reason:
                    item['custom_reason'] = custom_reason
            
            self.table.put_item(Item=item)
            
            logger.info(f"Stored {feedback} feedback for {function_target}, pattern {pattern_hash[:8]}...")
            return True
            
        except Exception as e:
            logger.error(f"Error storing test case feedback: {str(e)}")
            return False
    
    def store_batch_feedback(
        self,
        function_name: str,
        test_cases_with_feedback: List[Dict[str, Any]],
        user_id: str = "system",
        target_function: str = None
    ) -> int:
        """
        Store multiple test case feedbacks in batch for better performance.
        
        Args:
            function_name: Name of the Lambda function
            test_cases_with_feedback: List of dicts with 'test_case', 'feedback', 'rejection_reason', 'custom_reason'
            user_id: ID of the user providing feedback
            target_function: Specific function/class/file this pattern is for (None = GLOBAL)
            
        Returns:
            Number of items successfully stored
        """
        if not self.available:
            return 0
        
        try:
            stored_count = 0
            target = target_function if target_function else "GLOBAL"
            function_target = f"{function_name}#{target}"
            
            with self.table.batch_writer() as batch:
                for item_data in test_cases_with_feedback:
                    test_case = item_data['test_case']
                    feedback = item_data['feedback']
                    rejection_reason = item_data.get('rejection_reason')
                    custom_reason = item_data.get('custom_reason')
                    
                    if feedback not in VALID_FEEDBACK_VALUES:
                        logger.warning(f"Skipping invalid feedback value: {feedback}")
                        continue
                    if rejection_reason and rejection_reason not in VALID_REJECTION_REASONS:
                        logger.warning(f"Skipping invalid rejection reason: {rejection_reason}")
                        continue
                    if custom_reason:
                        custom_reason = custom_reason[:MAX_CUSTOM_REASON_LENGTH]
                    
                    input_json = json.dumps(test_case.get('input_event', {}), sort_keys=True)
                    if len(input_json) > MAX_INPUT_PATTERN_SIZE:
                        logger.warning(f"Skipping oversized input pattern ({len(input_json)} bytes)")
                        continue
                    
                    timestamp = datetime.utcnow().isoformat() + 'Z'
                    pattern_hash = self._create_pattern_hash(test_case)
                    success_rate = Decimal('1.0') if feedback == 'accepted' else Decimal('0.0')
                    ttl = int(time.time()) + (90 * 24 * 60 * 60)
                    
                    # Composite sort key
                    pattern_sk = f"FEEDBACK#{feedback}#PATTERN#{pattern_hash}"
                    
                    item = {
                        'function_target': function_target,
                        'pattern_sk': pattern_sk,
                        'function_name': function_name,
                        'target_function': target,
                        'pattern_hash': pattern_hash,
                        'test_type': test_case.get('test_type', 'unknown'),
                        'category': test_case.get('category', 'unknown'),
                        'input_pattern': json.dumps(test_case.get('input_event', {}), sort_keys=True),
                        'feedback': feedback,
                        'user_id': user_id,
                        'timestamp': timestamp,
                        'usage_count': 1,
                        'success_rate': success_rate,
                        'ttl': ttl,
                        'metadata': {
                            'description': test_case.get('description', ''),
                            'confidence_score': Decimal(str(test_case.get('confidence_score', 0.5)))
                        }
                    }
                    
                    # Add optional fields if present
                    if test_case.get('expected_output'):
                        item['expected_output_pattern'] = json.dumps(test_case.get('expected_output', {}), sort_keys=True)
                    
                    if test_case.get('assertions'):
                        item['metadata']['assertions'] = test_case.get('assertions', [])
                    
                    # Add rejection details if rejected
                    if feedback == 'rejected':
                        if rejection_reason:
                            item['rejection_reason'] = rejection_reason
                        if custom_reason:
                            item['custom_reason'] = custom_reason
                    
                    batch.put_item(Item=item)
                    stored_count += 1
            
            logger.info(f"Batch stored {stored_count} patterns for {function_target}")
            return stored_count
            
        except Exception as e:
            logger.error(f"Error in batch store: {str(e)}")
            return stored_count
    
    def get_accepted_patterns(
        self, 
        runtime: str = None, 
        dependencies: List[str] = None,
        function_name: str = None,
        target_function: str = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Retrieve accepted test case patterns using pure query (no scans, no filters).
        Retrieves target-specific patterns first, then falls back to GLOBAL patterns.
        
        Args:
            runtime: Lambda runtime (not used, kept for compatibility)
            dependencies: List of dependencies (not used, kept for compatibility)
            function_name: Specific function name to get patterns for
            target_function: Specific target function/class/file (None = GLOBAL only)
            limit: Maximum number of patterns to return
            
        Returns:
            List of accepted test case patterns sorted by success_rate
        """
        if not self.available:
            return []
        
        try:
            if not function_name:
                logger.warning("No function_name specified for get_accepted_patterns")
                return []
            
            all_patterns = []
            
            # Query 1: Get target-specific patterns if target_function is provided
            if target_function:
                function_target = f"{function_name}#{target_function}"
                response = self.table.query(
                    KeyConditionExpression=Key('function_target').eq(function_target) & 
                                          Key('pattern_sk').begins_with('FEEDBACK#accepted'),
                    Limit=limit
                )
                all_patterns.extend(response.get('Items', []))
                logger.debug(f"Retrieved {len(response.get('Items', []))} target-specific patterns for {target_function}")
            
            # Query 2: Get GLOBAL patterns (always query these as fallback)
            if len(all_patterns) < limit:
                function_target_global = f"{function_name}#GLOBAL"
                remaining_limit = limit - len(all_patterns)
                response = self.table.query(
                    KeyConditionExpression=Key('function_target').eq(function_target_global) & 
                                          Key('pattern_sk').begins_with('FEEDBACK#accepted'),
                    Limit=remaining_limit
                )
                all_patterns.extend(response.get('Items', []))
                logger.debug(f"Retrieved {len(response.get('Items', []))} GLOBAL patterns")
            
            # Convert Decimal to float
            all_patterns = self._convert_decimals(all_patterns)
            
            # Sort by success_rate and usage_count
            all_patterns.sort(key=lambda x: (x.get('success_rate', 0), x.get('usage_count', 0)), reverse=True)
            
            # Limit to requested count
            all_patterns = all_patterns[:limit]
            
            logger.debug(f"Retrieved {len(all_patterns)} total accepted patterns")
            return all_patterns
            
        except Exception as e:
            logger.error(f"Error retrieving accepted patterns: {str(e)}")
            return []
    
    def get_rejected_patterns(
        self, 
        function_name: str = None,
        target_function: str = None,
        rejection_reason: str = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Retrieve rejected test case patterns using pure query (no scans, no filters).
        Retrieves target-specific patterns first, then falls back to GLOBAL patterns.
        
        Args:
            function_name: Specific function name to get patterns for
            target_function: Specific target function/class/file (None = GLOBAL only)
            rejection_reason: Filter by specific rejection reason (applied after query)
            limit: Maximum number of patterns to return
            
        Returns:
            List of rejected test case patterns
        """
        if not self.available:
            return []
        
        try:
            if not function_name:
                logger.warning("No function_name specified for get_rejected_patterns")
                return []
            
            all_patterns = []
            
            # Query 1: Get target-specific patterns if target_function is provided
            if target_function:
                function_target = f"{function_name}#{target_function}"
                response = self.table.query(
                    KeyConditionExpression=Key('function_target').eq(function_target) & 
                                          Key('pattern_sk').begins_with('FEEDBACK#rejected'),
                    Limit=limit * 2 if rejection_reason else limit
                )
                all_patterns.extend(response.get('Items', []))
            
            # Query 2: Get GLOBAL patterns
            if len(all_patterns) < limit * 2:
                function_target_global = f"{function_name}#GLOBAL"
                remaining_limit = (limit * 2 if rejection_reason else limit) - len(all_patterns)
                response = self.table.query(
                    KeyConditionExpression=Key('function_target').eq(function_target_global) & 
                                          Key('pattern_sk').begins_with('FEEDBACK#rejected'),
                    Limit=remaining_limit
                )
                all_patterns.extend(response.get('Items', []))
            
            all_patterns = self._convert_decimals(all_patterns)
            
            # Filter by rejection_reason if specified (in-memory, but small dataset)
            if rejection_reason:
                all_patterns = [p for p in all_patterns if p.get('rejection_reason') == rejection_reason]
            
            # Sort by timestamp (most recent first)
            all_patterns.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            all_patterns = all_patterns[:limit]
            
            logger.debug(f"Retrieved {len(all_patterns)} rejected patterns")
            return all_patterns
            
        except Exception as e:
            logger.error(f"Error retrieving rejected patterns: {str(e)}")
            return []
    
    def get_rejection_patterns(
        self,
        function_name: str,
        target_function: str = None,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        Analyze rejection patterns for a function using pure query.
        Combines target-specific and GLOBAL patterns.
        
        Args:
            function_name: Function name to analyze
            target_function: Specific target function/class/file (None = GLOBAL only)
            limit: Maximum number of rejections to analyze
            
        Returns:
            Dictionary with rejection statistics and examples
        """
        if not self.available:
            return {}
        
        try:
            all_rejections = []
            
            # Query 1: Get target-specific rejections if target_function is provided
            if target_function:
                function_target = f"{function_name}#{target_function}"
                response = self.table.query(
                    KeyConditionExpression=Key('function_target').eq(function_target) & 
                                          Key('pattern_sk').begins_with('FEEDBACK#rejected'),
                    Limit=limit
                )
                all_rejections.extend(response.get('Items', []))
            
            # Query 2: Get GLOBAL rejections
            if len(all_rejections) < limit:
                function_target_global = f"{function_name}#GLOBAL"
                remaining_limit = limit - len(all_rejections)
                response = self.table.query(
                    KeyConditionExpression=Key('function_target').eq(function_target_global) & 
                                          Key('pattern_sk').begins_with('FEEDBACK#rejected'),
                    Limit=remaining_limit
                )
                all_rejections.extend(response.get('Items', []))
            
            all_rejections = self._convert_decimals(all_rejections)
            
            # Analyze rejection reasons
            reason_counts = {}
            reason_examples = {}
            
            for rejection in all_rejections:
                reason = rejection.get('rejection_reason', 'unspecified')
                
                # Count occurrences
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
                
                # Store examples (up to 3 per reason)
                if reason not in reason_examples:
                    reason_examples[reason] = []
                if len(reason_examples[reason]) < 3:
                    reason_examples[reason].append({
                        'pattern_hash': rejection.get('pattern_hash', '')[:8],
                        'custom_reason': rejection.get('custom_reason', ''),
                        'timestamp': rejection.get('timestamp', ''),
                        'target_function': rejection.get('target_function', 'GLOBAL')
                    })
            
            # Sort by count
            sorted_reasons = sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)
            
            return {
                'function_name': function_name,
                'target_function': target_function if target_function else 'GLOBAL',
                'total_rejections': len(all_rejections),
                'reason_counts': dict(sorted_reasons),
                'top_reasons': [r[0] for r in sorted_reasons[:5]],
                'reason_examples': reason_examples
            }
            
        except Exception as e:
            logger.error(f"Error analyzing rejection patterns: {str(e)}")
            return {}
    
    def update_pattern_usage(
        self, 
        function_name: str,
        pattern_hash: str,
        feedback: str,
        success: bool,
        target_function: str = None
    ) -> bool:
        """
        Update usage statistics for a pattern.
        
        Args:
            function_name: Function name
            pattern_hash: Pattern hash
            feedback: Feedback type ('accepted' or 'rejected')
            success: Whether the pattern was successful
            target_function: Specific target function/class/file (None = GLOBAL)
            
        Returns:
            True if updated successfully, False otherwise
        """
        if not self.available:
            return False
        
        try:
            # Construct composite keys
            target = target_function if target_function else "GLOBAL"
            function_target = f"{function_name}#{target}"
            pattern_sk = f"FEEDBACK#{feedback}#PATTERN#{pattern_hash}"
            
            # Get current item
            response = self.table.get_item(
                Key={
                    'function_target': function_target,
                    'pattern_sk': pattern_sk
                }
            )
            
            if 'Item' not in response:
                logger.warning(f"Pattern not found: {function_target}/{pattern_hash[:8]}...")
                return False
            
            item = response['Item']
            current_usage = int(item.get('usage_count', 0))
            current_success_rate = float(item.get('success_rate', 0.0))
            
            # Calculate new success rate
            new_usage = current_usage + 1
            if success:
                new_success_rate = ((current_success_rate * current_usage) + 1) / new_usage
            else:
                new_success_rate = (current_success_rate * current_usage) / new_usage
            
            # Update item
            self.table.update_item(
                Key={
                    'function_target': function_target,
                    'pattern_sk': pattern_sk
                },
                UpdateExpression='SET usage_count = :usage, success_rate = :rate, last_used = :timestamp',
                ExpressionAttributeValues={
                    ':usage': new_usage,
                    ':rate': Decimal(str(new_success_rate)),
                    ':timestamp': datetime.utcnow().isoformat() + 'Z'
                }
            )
            
            logger.debug(f"Updated pattern usage: {new_usage}, success_rate: {new_success_rate:.2f}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating pattern usage: {str(e)}")
            return False
    
    def cleanup_old_patterns(self, days_old: int = 90) -> int:
        """
        Clean up old patterns manually (TTL handles most cleanup automatically).
        This is a fallback for patterns that need immediate removal.
        
        Args:
            days_old: Number of days after which to consider patterns old
            
        Returns:
            Number of patterns cleaned up
        """
        if not self.available:
            return 0
        
        try:
            cutoff_date = (datetime.utcnow() - timedelta(days=days_old)).isoformat() + 'Z'
            deleted_count = 0
            
            # Note: This uses scan, but it's a rare admin operation
            # TTL handles most cleanup automatically
            response = self.table.scan(
                FilterExpression='#ts < :cutoff AND success_rate < :min_rate AND usage_count < :min_usage',
                ExpressionAttributeNames={
                    '#ts': 'timestamp'
                },
                ExpressionAttributeValues={
                    ':cutoff': cutoff_date,
                    ':min_rate': Decimal('0.3'),
                    ':min_usage': 5
                }
            )
            
            old_patterns = response.get('Items', [])
            
            # Batch delete old patterns
            if old_patterns:
                with self.table.batch_writer() as batch:
                    for pattern in old_patterns:
                        batch.delete_item(
                            Key={
                                'function_target': pattern['function_target'],
                                'pattern_sk': pattern['pattern_sk']
                            }
                        )
                        deleted_count += 1
            
            logger.info(f"Manually cleaned up {deleted_count} old patterns (TTL handles most cleanup)")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error cleaning up old patterns: {str(e)}")
            return 0
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the memory store.
        
        Returns:
            Dictionary with memory store statistics
        """
        if not self.available:
            return {'status': 'unavailable'}
        
        try:
            # Get table description for item count
            table_desc = self.table.meta.client.describe_table(TableName=self.table_name)
            item_count = table_desc['Table'].get('ItemCount', 0)
            
            # Note: These use scan for stats, but it's a rare operation
            accepted_response = self.table.scan(
                FilterExpression='feedback = :feedback',
                ExpressionAttributeValues={':feedback': 'accepted'},
                Select='COUNT'
            )
            
            rejected_response = self.table.scan(
                FilterExpression='feedback = :feedback',
                ExpressionAttributeValues={':feedback': 'rejected'},
                Select='COUNT'
            )
            
            return {
                'status': 'available',
                'total_patterns': item_count,
                'accepted_patterns': accepted_response.get('Count', 0),
                'rejected_patterns': rejected_response.get('Count', 0),
                'table_name': self.table_name,
                'region': self.region,
                'schema': 'optimized (composite PK with target, composite SK, no GSIs, no hot-path scans)',
                'note': 'TTL enabled for auto-cleanup after 90 days. PK format: {function_name}#{target_function|GLOBAL}'
            }
            
        except Exception as e:
            logger.error(f"Error getting memory stats: {str(e)}")
            return {'status': 'error', 'error': str(e)}
    
    def _convert_floats_to_decimal(self, obj: Any) -> Any:
        """Convert float values to Decimal for DynamoDB compatibility."""
        if isinstance(obj, float):
            return Decimal(str(obj))
        elif isinstance(obj, dict):
            return {k: self._convert_floats_to_decimal(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_floats_to_decimal(item) for item in obj]
        else:
            return obj
    
    def _convert_decimals(self, obj: Any) -> Any:
        """Convert Decimal objects to float for JSON serialization."""
        if isinstance(obj, list):
            return [self._convert_decimals(item) for item in obj]
        elif isinstance(obj, dict):
            return {key: self._convert_decimals(value) for key, value in obj.items()}
        elif isinstance(obj, Decimal):
            return float(obj)
        else:
            return obj
    
    def _create_pattern_hash(self, test_case: Dict[str, Any]) -> str:
        """Create a unique hash for a test case (includes actual values, not just structure)."""
        # Create a unique representation of the test case with actual values
        pattern_data = {
            'test_type': test_case.get('test_type', ''),
            'category': test_case.get('category', ''),
            'description': test_case.get('description', ''),
            'input_event': test_case.get('input_event', {}),
            'confidence_score': test_case.get('confidence_score', 0.0)
        }
        
        pattern_str = json.dumps(pattern_data, sort_keys=True)
        return hashlib.sha256(pattern_str.encode()).hexdigest()

