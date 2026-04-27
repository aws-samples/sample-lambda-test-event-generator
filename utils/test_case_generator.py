# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Test Case Generator - Pure data synthesizer for test values.

This module handles:
- Filling concrete values into test schemas
- Applying realistic data based on field names
- Edge value mutation for boundary testing
- NO schema invention - only fills provided structures
"""

import json
import logging
import random  # nosec B311 - Used for test data generation, not cryptographic purposes
import string
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class TestCaseGenerator:
    """Pure test data synthesizer - fills values into schemas, does NOT invent structure."""
    
    def __init__(self, seed: Optional[int] = None):
        """
        Initialize the test case generator.
        
        Args:
            seed: Optional seed for reproducible random values
        """
        if seed is not None:
            random.seed(seed)  # nosec B311
        
        logger.info("TestCaseGenerator initialized as data synthesizer")
    
    def fill_test_values(
        self,
        input_schema: Dict[str, Any],
        test_type: str = 'positive'
    ) -> Dict[str, Any]:
        """
        Fill concrete values into a test schema.
        Does NOT invent fields - only fills provided schema.
        
        Args:
            input_schema: Schema with placeholder values like "<email>", "<id>"
            test_type: 'positive', 'negative', or 'edge'
            
        Returns:
            Input event with concrete values filled in
        """
        if not input_schema:
            logger.warning("Empty input schema provided, returning empty dict")
            return {}
        
        filled = self._fill_values_recursive(input_schema, test_type)
        
        return filled
    
    def _fill_values_recursive(self, data: Any, test_type: str) -> Any:
        """Recursively fill placeholder values in data structure."""
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                if isinstance(value, str) and value.startswith('<') and value.endswith('>'):
                    # Placeholder value - fill it
                    result[key] = self._fill_placeholder(value, test_type)
                elif isinstance(value, (dict, list)):
                    result[key] = self._fill_values_recursive(value, test_type)
                else:
                    result[key] = value
            return result
        elif isinstance(data, list):
            return [self._fill_values_recursive(item, test_type) for item in data]
        else:
            return data
    
    def _fill_placeholder(self, placeholder: str, test_type: str) -> Any:
        """
        Fill a single placeholder with appropriate value based on test type.
        
        Args:
            placeholder: String like "<email>", "<id>", "<value>"
            test_type: 'positive', 'negative', or 'edge'
            
        Returns:
            Concrete value
        """
        field_name = placeholder.strip('<>').lower()
        
        if test_type == 'positive':
            return self._get_positive_value(field_name)
        elif test_type == 'negative':
            return self._get_negative_value(field_name)
        else:  # edge
            return self._get_edge_value(field_name)
    
    def _get_positive_value(self, field_name: str) -> Any:
        """Get realistic positive value for a field."""
        if 'email' in field_name:
            return 'user@example.com'
        elif 'id' in field_name:
            return f'{field_name}-{self._generate_id()}'
        elif 'name' in field_name:
            return 'Test Name'
        elif 'url' in field_name:
            return 'https://example.com'
        elif 'count' in field_name or 'size' in field_name or 'number' in field_name:
            return 10
        elif 'flag' in field_name or 'enabled' in field_name or 'active' in field_name:
            return True
        elif 'date' in field_name or 'time' in field_name or 'timestamp' in field_name:
            return datetime.utcnow().isoformat() + 'Z'
        elif 'token' in field_name or 'key' in field_name:
            return f'test-{field_name}-{self._generate_random_string(16)}'
        else:
            return f'test-{field_name}'
    
    def _get_negative_value(self, field_name: str) -> Any:
        """Get invalid value for a field (for negative tests)."""
        if 'email' in field_name:
            return 'invalid-email'
        elif 'id' in field_name:
            return ''  # Empty ID
        elif 'name' in field_name:
            return ''  # Empty name
        elif 'url' in field_name:
            return 'not-a-url'
        elif 'count' in field_name or 'size' in field_name or 'number' in field_name:
            return -1  # Negative number
        elif 'flag' in field_name or 'enabled' in field_name:
            return 'not-a-boolean'
        elif 'date' in field_name or 'time' in field_name:
            return 'invalid-date'
        else:
            return None  # Null value
    
    def _get_edge_value(self, field_name: str) -> Any:
        """Get boundary/edge value for a field."""
        if 'email' in field_name:
            return random.choice(['', 'a@b.c', 'x' * 100 + '@example.com'])  # nosec B311
        elif 'id' in field_name:
            return random.choice(['', 'x' * 500, '0'])  # nosec B311
        elif 'name' in field_name:
            return random.choice(['', 'x' * 1000, '!@#$%', '世界🌍'])  # nosec B311
        elif 'url' in field_name:
            return random.choice(['', 'x' * 2000, 'file:///etc/passwd'])  # nosec B311
        elif 'count' in field_name or 'size' in field_name or 'number' in field_name:
            return random.choice([0, -1, 999999999, 2147483647])  # nosec B311
        elif 'flag' in field_name or 'enabled' in field_name:
            return random.choice([None, 'true', 1, 0])  # nosec B311
        elif 'date' in field_name or 'time' in field_name:
            return random.choice(['', '0000-00-00', '9999-12-31T23:59:59Z'])  # nosec B311
        else:
            return random.choice(['', None, 'x' * 1000])  # nosec B311
    
    # -------------------- Helper Methods --------------------
    def _generate_request_id(self) -> str:
        """Generate a realistic request ID."""
        return f"req-{''.join(random.choices(string.ascii_lowercase + string.digits, k=16))}"  # nosec B311
    
    def _generate_id(self) -> str:
        """Generate a short ID."""
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))  # nosec B311
    
    def _generate_random_string(self, length: int = 10) -> str:
        """Generate a random string."""
        return ''.join(random.choices(string.ascii_letters + string.digits, k=length))  # nosec B311
