# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Validator Agent - Responsible for filtering, deduplicating, and validating test cases.

This agent handles:
- Test case validation and filtering
- Deduplication of similar test cases
- Quality scoring and ranking
- Chat-based feedback processing 
"""

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import json
import hashlib
from datetime import datetime

from agents.generator_agent import TestCaseCandidate, GenerationResult
from integrations.memory_store import DynamoDBMemoryStore

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result from test case validation."""
    function_name: str
    original_count: int
    validated_count: int
    duplicates_removed: int
    invalid_removed: int
    test_cases: List[TestCaseCandidate]
    validation_metadata: Dict[str, Any]


class ValidatorAgent:
    """Agent responsible for validating and filtering test case candidates."""
    
    def __init__(self, region: str = None):
        """Initialize the Validator Agent."""
        self.region = region or 'us-east-1'
        
        # Initialize memory store with graceful fallback
        try:
            self.memory_store = DynamoDBMemoryStore(region=self.region)
        except Exception as e:
            logger.warning(f"DynamoDB memory store not available: {str(e)}")
            self.memory_store = None
        
        logger.info(f"ValidatorAgent initialized for region: {self.region}")
        if self.memory_store and self.memory_store.is_available():
            logger.info("DynamoDB memory store enabled for pattern learning")
    
    def validate_and_filter(
        self, 
        generation_result: GenerationResult,
        quality_threshold: float = 0.5,
        max_test_cases: int = 20
    ) -> ValidationResult:
        """
        Validate, score, and select diverse test cases covering different functions/chunks.
        
        Args:
            generation_result: Result from GeneratorAgent with per-function test cases
            quality_threshold: Minimum quality score for test cases
            max_test_cases: Maximum number of test cases to select
            
        Returns:
            ValidationResult with top N diverse test cases
        """
        try:
            function_name = generation_result.function_name
            candidates = generation_result.test_cases
            
            logger.info(f"Validating {len(candidates)} test case candidates for {function_name}")
            
            # Step 1: Basic validation
            valid_candidates = self._validate_test_cases(candidates)
            invalid_count = len(candidates) - len(valid_candidates)
            
            # Step 2: Deduplication
            deduplicated_candidates = self._deduplicate_test_cases(valid_candidates)
            duplicate_count = len(valid_candidates) - len(deduplicated_candidates)
            
            # Step 3: Score all test cases
            scored_candidates = self._score_test_cases(deduplicated_candidates)
            
            # Step 4: Quality filtering
            quality_filtered = [c for c in scored_candidates if c.confidence_score >= quality_threshold]
            
            # Step 5: Select diverse test cases covering different functions/chunks
            final_candidates = self._select_diverse_test_cases(quality_filtered, max_test_cases)
            
            # Step 6: Create result
            result = ValidationResult(
                function_name=function_name,
                original_count=len(candidates),
                validated_count=len(final_candidates),
                duplicates_removed=duplicate_count,
                invalid_removed=invalid_count,
                test_cases=final_candidates,
                validation_metadata={
                    'validation_timestamp': self._get_timestamp(),
                    'quality_threshold': quality_threshold,
                    'max_test_cases': max_test_cases,
                    'validation_steps': {
                        'initial_count': len(candidates),
                        'after_validation': len(valid_candidates),
                        'after_deduplication': len(deduplicated_candidates),
                        'after_scoring': len(scored_candidates),
                        'after_quality_filter': len(quality_filtered),
                        'final_count': len(final_candidates)
                    },
                    'diversity_metrics': self._calculate_diversity_metrics(final_candidates)
                }
            )
            
            logger.info(f"Validation completed for {function_name}: {len(final_candidates)} diverse test cases selected")
            logger.info(f"Coverage: {result.validation_metadata['diversity_metrics']}")
            return result
            
        except Exception as e:
            logger.error(f"Test case validation failed for {generation_result.function_name}: {str(e)}")
            raise
    
    def process_user_feedback(
        self, 
        function_name: str,
        test_cases_with_feedback: List[Dict[str, Any]],
        user_id: str = "chat-user",
        target_function: str = None
    ) -> Dict[str, Any]:
        """
        Process user feedback from chat interface.
        
        Args:
            function_name: Name of the Lambda function
            test_cases_with_feedback: List of test cases with feedback
                Format: [
                    {
                        'test_case': {...},
                        'feedback': 'accepted' or 'rejected',
                        'rejection_reason': 'missing_auth_headers' (if rejected),
                        'custom_reason': 'Our API requires X-API-Key' (optional)
                    }
                ]
            user_id: ID of the user providing feedback
            target_function: Optional target function/class/file (None = GLOBAL)
            
        Returns:
            Processing result with statistics
        """
        try:
            if not self.memory_store or not self.memory_store.is_available():
                logger.warning("Memory store not available, feedback will not be stored")
                return {
                    'status': 'error',
                    'message': 'Memory store not available',
                    'accepted_count': 0,
                    'rejected_count': 0
                }
            
            # Use batch storage for efficiency
            stored_count = self.memory_store.store_batch_feedback(
                function_name=function_name,
                test_cases_with_feedback=test_cases_with_feedback,
                user_id=user_id,
                target_function=target_function
            )
            
            # Count accepted vs rejected
            accepted_count = sum(1 for item in test_cases_with_feedback if item['feedback'] == 'accepted')
            rejected_count = sum(1 for item in test_cases_with_feedback if item['feedback'] == 'rejected')
            
            result = {
                'status': 'success',
                'function_name': function_name,
                'accepted_count': accepted_count,
                'rejected_count': rejected_count,
                'total_processed': stored_count,
                'user_id': user_id,
                'processed_timestamp': self._get_timestamp()
            }
            
            logger.info(f"Processed feedback for {function_name}: {accepted_count} accepted, {rejected_count} rejected")
            return result
            
        except Exception as e:
            logger.error(f"Error processing user feedback: {str(e)}")
            return {
                'status': 'error',
                'message': str(e),
                'accepted_count': 0,
                'rejected_count': 0
            }
    
    def _validate_test_cases(self, candidates: List[TestCaseCandidate]) -> List[TestCaseCandidate]:
        """Validate test case candidates for basic correctness."""
        valid_candidates = []
        
        for candidate in candidates:
            if self._is_valid_test_case(candidate):
                valid_candidates.append(candidate)
            else:
                logger.debug(f"Invalid test case removed: {candidate.test_id}")
        
        return valid_candidates
    
    def _is_valid_test_case(self, candidate: TestCaseCandidate) -> bool:
        """Check if a test case candidate is valid."""
        # Check required fields
        if not all([
            candidate.test_id,
            candidate.test_type,
            candidate.category,
            candidate.description,
            candidate.input_event is not None,
            candidate.expected_output is not None
        ]):
            return False
        
        # Check test type is valid
        if candidate.test_type not in ['positive', 'negative', 'edge']:
            return False
        
        # Check input event is valid JSON-serializable
        try:
            json.dumps(candidate.input_event)
            json.dumps(candidate.expected_output)
        except (TypeError, ValueError):
            return False
        
        # Check confidence score is reasonable
        if not (0.0 <= candidate.confidence_score <= 1.0):
            return False
        
        return True
    
    def _deduplicate_test_cases(self, candidates: List[TestCaseCandidate]) -> List[TestCaseCandidate]:
        """Remove duplicate test cases based on input patterns."""
        seen_hashes = set()
        deduplicated = []
        
        for candidate in candidates:
            test_hash = self._create_test_hash(candidate)
            
            if test_hash not in seen_hashes:
                seen_hashes.add(test_hash)
                deduplicated.append(candidate)
            else:
                logger.debug(f"Duplicate test case removed: {candidate.test_id}")
        
        return deduplicated
    
    def _create_test_hash(self, candidate: TestCaseCandidate) -> str:
        """Create a unique hash for test case deduplication (uses actual values)."""
        # Create hash based on actual values for true deduplication
        hash_data = {
            'test_type': candidate.test_type,
            'category': candidate.category,
            'description': candidate.description,
            'input_event': candidate.input_event,
            'confidence_score': round(candidate.confidence_score, 4)  # Round to avoid float precision issues
        }
        
        hash_str = json.dumps(hash_data, sort_keys=True)
        return hashlib.sha256(hash_str.encode()).hexdigest()
    
    def _filter_by_quality(self, candidates: List[TestCaseCandidate], threshold: float) -> List[TestCaseCandidate]:
        """Filter test cases by quality threshold."""
        return [c for c in candidates if c.confidence_score >= threshold]
    
    def _score_test_cases(self, candidates: List[TestCaseCandidate]) -> List[TestCaseCandidate]:
        """
        Score test cases based on multiple factors:
        - Base confidence score
        - Function coverage (handler functions get higher score)
        - Error handling coverage
        - Input pattern complexity
        """
        for candidate in candidates:
            score = candidate.confidence_score
            
            # Boost score for handler functions (more important)
            func_name = candidate.metadata.get('function_name', '').lower()
            if 'handler' in func_name or 'lambda' in func_name:
                score += 0.1
            
            # Boost score for error handling tests
            if candidate.test_type == 'negative' and candidate.category == 'error_handling':
                score += 0.05
            
            # Boost score for edge cases
            if candidate.test_type == 'edge':
                score += 0.05
            
            # Boost score if from memory pattern
            if candidate.metadata.get('generation_method') == 'memory_pattern':
                score += 0.1
            
            # Cap at 1.0
            candidate.confidence_score = min(1.0, score)
        
        return candidates
    
    def _select_diverse_test_cases(
        self, 
        candidates: List[TestCaseCandidate], 
        max_count: int
    ) -> List[TestCaseCandidate]:
        """
        Select diverse test cases that cover different functions, chunks, and test types.
        Uses a greedy algorithm to maximize coverage diversity.
        Ensures we return at least max_count test cases (the requested amount).
        
        Args:
            candidates: Scored and filtered test cases
            max_count: Target number to select (minimum to return)
            
        Returns:
            Diverse subset of test cases (at least max_count if available)
        """
        if len(candidates) <= max_count:
            # Not enough candidates, return all sorted by score
            logger.warning(f"Only {len(candidates)} candidates available, requested {max_count}")
            return sorted(candidates, key=lambda c: c.confidence_score, reverse=True)
        
        selected = []
        remaining = candidates.copy()
        
        # Track coverage
        covered_functions = set()
        covered_files = set()
        covered_chunks = set()
        type_counts = {'positive': 0, 'negative': 0, 'edge': 0}
        
        # Sort by score initially
        remaining.sort(key=lambda c: c.confidence_score, reverse=True)
        
        # Selection algorithm: balance between score and diversity
        while len(selected) < max_count and remaining:
            best_candidate = None
            best_score = -1
            
            for candidate in remaining:
                # Calculate diversity score
                diversity_score = 0
                
                func_name = candidate.metadata.get('function_name', '')
                file_name = candidate.metadata.get('file_name', '')
                chunk_type = candidate.metadata.get('chunk_type', '')
                test_type = candidate.test_type
                
                # Reward covering new functions
                if func_name and func_name not in covered_functions:
                    diversity_score += 3.0
                
                # Reward covering new files
                if file_name and file_name not in covered_files:
                    diversity_score += 2.0
                
                # Reward covering new chunks
                if chunk_type and chunk_type not in covered_chunks:
                    diversity_score += 1.0
                
                # Reward balancing test types
                min_type_count = min(type_counts.values())
                if type_counts[test_type] == min_type_count:
                    diversity_score += 1.5
                
                # Combined score: 70% diversity, 30% quality
                combined_score = (diversity_score * 0.7) + (candidate.confidence_score * 0.3)
                
                if combined_score > best_score:
                    best_score = combined_score
                    best_candidate = candidate
            
            if best_candidate:
                selected.append(best_candidate)
                remaining.remove(best_candidate)
                
                # Update coverage tracking
                func_name = best_candidate.metadata.get('function_name', '')
                file_name = best_candidate.metadata.get('file_name', '')
                chunk_type = best_candidate.metadata.get('chunk_type', '')
                
                if func_name:
                    covered_functions.add(func_name)
                if file_name:
                    covered_files.add(file_name)
                if chunk_type:
                    covered_chunks.add(chunk_type)
                
                type_counts[best_candidate.test_type] += 1
            else:
                break
        
        # Ensure we have at least max_count test cases
        if len(selected) < max_count and remaining:
            logger.info(f"Adding {max_count - len(selected)} more test cases to reach requested count")
            # Add remaining candidates sorted by score until we reach max_count
            remaining.sort(key=lambda c: c.confidence_score, reverse=True)
            needed = max_count - len(selected)
            selected.extend(remaining[:needed])
        
        logger.info(f"Selected {len(selected)} diverse test cases (requested: {max_count})")
        logger.info(f"Coverage: {len(covered_functions)} functions, {len(covered_files)} files, {len(covered_chunks)} chunks")
        logger.info(f"Type distribution: {type_counts}")
        
        return selected
    
    def _calculate_diversity_metrics(self, test_cases: List[TestCaseCandidate]) -> Dict[str, Any]:
        """Calculate diversity metrics for selected test cases."""
        functions = set()
        files = set()
        chunks = set()
        type_counts = {'positive': 0, 'negative': 0, 'edge': 0}
        
        for tc in test_cases:
            func_name = tc.metadata.get('function_name', '')
            file_name = tc.metadata.get('file_name', '')
            chunk_type = tc.metadata.get('chunk_type', '')
            
            if func_name:
                functions.add(func_name)
            if file_name:
                files.add(file_name)
            if chunk_type:
                chunks.add(chunk_type)
            
            type_counts[tc.test_type] += 1
        
        return {
            'unique_functions_covered': len(functions),
            'unique_files_covered': len(files),
            'unique_chunks_covered': len(chunks),
            'test_type_distribution': type_counts,
            'functions': list(functions),
            'files': list(files)
        }
    
    def _rank_and_limit(self, candidates: List[TestCaseCandidate], max_count: int) -> List[TestCaseCandidate]:
        """Rank test cases by quality and limit to max count."""
        # Sort by confidence score (descending) and test type priority
        type_priority = {'positive': 3, 'negative': 2, 'edge': 1}
        
        sorted_candidates = sorted(
            candidates,
            key=lambda c: (c.confidence_score, type_priority.get(c.test_type, 0)),
            reverse=True
        )
        
        return sorted_candidates[:max_count]
    
    def get_validation_summary(self, validation_result: ValidationResult) -> str:
        """
        Generate a human-readable summary of validation results.
        
        Args:
            validation_result: The validation result to summarize
            
        Returns:
            Formatted validation summary string
        """
        metadata = validation_result.validation_metadata
        steps = metadata['validation_steps']
        
        summary = f"""
# Test Case Validation Summary: {validation_result.function_name}

## Validation Results
- **Original Candidates**: {validation_result.original_count}
- **Final Test Cases**: {validation_result.validated_count}
- **Duplicates Removed**: {validation_result.duplicates_removed}
- **Invalid Removed**: {validation_result.invalid_removed}
- **Quality Threshold**: {metadata['quality_threshold']}

## Validation Pipeline
1. **Initial Count**: {steps['initial_count']} candidates
2. **After Validation**: {steps['after_validation']} valid candidates
3. **After Deduplication**: {steps['after_deduplication']} unique candidates
4. **After Quality Filter**: {steps['after_quality_filter']} high-quality candidates
5. **Final Count**: {steps['final_count']} test cases

## Test Case Breakdown
"""
        
        # Group test cases by type
        by_type = {}
        for tc in validation_result.test_cases:
            if tc.test_type not in by_type:
                by_type[tc.test_type] = []
            by_type[tc.test_type].append(tc)
        
        for test_type, cases in by_type.items():
            summary += f"\n### {test_type.title()} Test Cases ({len(cases)})\n"
            for i, tc in enumerate(cases[:3], 1):  # Show first 3 of each type
                summary += f"{i}. **{tc.description}** (Score: {tc.confidence_score:.2f})\n"
            
            if len(cases) > 3:
                summary += f"   ... and {len(cases) - 3} more\n"
        
        return summary
    
    def _get_timestamp(self) -> str:
        """Get current timestamp."""
        return datetime.utcnow().isoformat() + 'Z'
