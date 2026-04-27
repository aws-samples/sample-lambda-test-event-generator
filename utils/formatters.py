# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Formatters - Helper functions for formatting output in main.py
"""

import json
from typing import List, Dict, Any
from agents.analyzer_agent import AnalysisResult
from agents.generator_agent import GenerationResult, TestCaseCandidate
from agents.validator_agent import ValidationResult


def format_generation_report(analysis_result: AnalysisResult, generation_result: GenerationResult) -> str:
    """Format comprehensive generation report."""
    
    memory_status = f"{len(generation_result.memory_patterns_used)} patterns" if generation_result.memory_patterns_used else "No patterns"
    rejection_status = f"Avoided {len(generation_result.rejected_patterns_avoided)} patterns" if generation_result.rejected_patterns_avoided else "No rejections to avoid"
    
    report = f"""
# Test Case Generation Report

## Function: {generation_result.function_name}

## Generation Summary
- **Total Test Cases**: {generation_result.total_candidates}
- **Positive Tests**: {generation_result.candidates_by_type.get('positive', 0)}
- **Negative Tests**: {generation_result.candidates_by_type.get('negative', 0)}
- **Edge Cases**: {generation_result.candidates_by_type.get('edge', 0)}

## Intelligence Used
- **Memory Patterns**: {memory_status}
- **Rejection Avoidance**: {rejection_status}

{format_rejection_avoidance(generation_result.rejected_patterns_avoided)}

## Generated Test Cases

{format_test_cases(generation_result.test_cases)}

## Metadata
- **Generation Time**: {generation_result.generation_metadata.get('generation_timestamp', 'N/A')}
- **Accepted Patterns Used**: {generation_result.generation_metadata.get('accepted_patterns_count', 0)}
- **Rejections Analyzed**: {generation_result.generation_metadata.get('rejection_patterns_analyzed', 0)}
"""
    
    return report


def format_rejection_avoidance(rejected_patterns: List[str]) -> str:
    """Format rejection avoidance section."""
    if not rejected_patterns:
        return ""
    
    patterns_text = "\n".join([f"  - {pattern}" for pattern in rejected_patterns[:5]])
    return f"""
### Rejection Patterns Avoided
{patterns_text}
"""


def format_test_cases(test_cases: List[TestCaseCandidate]) -> str:
    """Format test cases for display."""
    if not test_cases:
        return "No test cases generated."
    
    formatted = []
    for i, tc in enumerate(test_cases, 1):
        formatted.append(f"""
### Test Case {i}: {tc.test_id}
**Type**: {tc.test_type.upper()}
**Category**: {tc.category}
**Description**: {tc.description}
**Confidence**: {tc.confidence_score:.2f}

**Input Event**:
```json
{json.dumps(tc.input_event, indent=2)}
```

**Expected Output**:
```json
{json.dumps(tc.expected_output, indent=2)}
```

**Assertions**:
{chr(10).join([f'- {assertion}' for assertion in tc.assertions[:5]])}

**Notes**: {tc.notes}
""")
    
    return "\n".join(formatted)


def format_test_cases_for_feedback(test_cases: List[TestCaseCandidate]) -> str:
    """Format test cases for feedback workflow."""
    if not test_cases:
        return "No test cases generated."
    
    formatted = []
    for i, tc in enumerate(test_cases, 1):
        # Truncate long JSON for readability
        input_json = json.dumps(tc.input_event, indent=2)
        if len(input_json) > 500:
            input_json = input_json[:500] + "\n  ...\n}"
        
        output_json = json.dumps(tc.expected_output, indent=2)
        if len(output_json) > 300:
            output_json = output_json[:300] + "\n  ...\n}"
        
        formatted.append(f"""
### Test Case {i}: {tc.test_id}
**Type**: {tc.test_type.upper()} | **Category**: {tc.category} | **Confidence**: {tc.confidence_score:.2f}
**Description**: {tc.description}

**Input**:
```json
{input_json}
```

**Expected Output**:
```json
{output_json}
```

**Your Feedback**: [ ] ACCEPT  [ ] REJECT - reason - "details"
---
""")
    
    return "\n".join(formatted)


def format_memory_store_status(memory_stats: Dict[str, Any]) -> str:
    """Get memory store status for feedback workflow."""
    if memory_stats.get('status') == 'available':
        return f"Memory Store Active - {memory_stats.get('total_patterns', 0)} patterns stored"
    else:
        return "Memory Store Not Available - Feedback will not be stored"


def format_setup_instructions(memory_stats: Dict[str, Any]) -> str:
    """Generate setup instructions based on current status."""
    instructions = []
    
    if memory_stats.get('status') != 'available':
        instructions.append("""
### DynamoDB Memory Store Setup
See `docs/DYNAMODB_SETUP.md` for details
""")
    
    if not instructions:
        return "All integrations are properly configured!"
    
    return "\n".join(instructions)
