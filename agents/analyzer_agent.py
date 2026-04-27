# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Analyzer Agent - Lambda code analysis + automatic JSON test case generator.

Features:
- Fetch Lambda function code
- Multi-language support: Python, Java, C#, JavaScript, Ruby
- Intelligent code chunking
- Regex + LLM (Bedrock) enhanced analysis
- Automatic structured JSON test case generation per function
- Includes short description, inputs, outputs, edge cases
"""

import logging
import json
import boto3
from typing import Dict, Any, List
from dataclasses import dataclass

from utils.lambda_fetcher import LambdaFetcher
from utils.code_chunker import MultiLanguageCodeChunker, CodeChunk
from utils.code_analyzer import CodeAnalyzer

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Result from Lambda function analysis."""
    function_name: str
    function_info: Dict[str, Any]
    code_chunks: List[CodeChunk]
    chunk_summaries: List[Dict[str, Any]]
    overall_structure: Dict[str, Any]
    dependencies: List[str]
    error_patterns: List[str]
    input_patterns: List[str]
    output_patterns: List[str]
    analysis_metadata: Dict[str, Any]


class AnalyzerAgent:
    """Lambda code analysis and JSON test case generation agent.
    
    Note: Always uses us-east-1 for Bedrock API calls (where Claude 3.7 is available),
    but can analyze Lambda functions from any region.
    """
    
    def __init__(self, region: str = None, model_id: str = None):
        self.region = region or 'us-east-1'
        
        # Always use US region for Bedrock (where Claude 3.7 is available)
        self.bedrock_region = 'us-east-1'
        
        # Use the latest Claude model from US region
        self.model_id = model_id or 'us.anthropic.claude-sonnet-4-6'
        
        # Bedrock client uses US region, Lambda client uses specified region
        self.bedrock_client = boto3.client('bedrock-runtime', region_name=self.bedrock_region)
        self.fetcher = None  # Will be initialized with ignore patterns in analyze_lambda_function
        self.chunker = MultiLanguageCodeChunker()
        self.analyzer = CodeAnalyzer()
        logger.info(f"AnalyzerAgent initialized (Lambda region={self.region}, Bedrock region={self.bedrock_region}, model={self.model_id})")

    # -------------------- Lambda Analysis --------------------
    def analyze_lambda_function(self, function_name: str, target_filter: str = "", ignore_patterns: list = None) -> AnalysisResult:
        """
        Perform full Lambda code analysis with optional target filtering and ignore patterns.
        
        Args:
            function_name: Lambda function ARN or name
            target_filter: Optional single function/class/file to focus on
            ignore_patterns: Optional list of file/folder patterns to ignore (e.g., ['tests/', '*.test.js'])
            
        Returns:
            AnalysisResult with filtered chunks if target_filter is provided
        """
        # Initialize fetcher with ignore patterns
        self.fetcher = LambdaFetcher(region=self.region, custom_ignore_patterns=ignore_patterns or [])
        
        function_name = self.fetcher._extract_function_name(function_name)
        code_data = self.fetcher.get_function_code_cleaned(function_name)
        chunks = self.chunker.chunk_code_files(code_data['source_files'], code_data['handler_file'])
        
        # Apply target filter if provided
        if target_filter:
            logger.info(f"Applying target filter: {target_filter}")
            chunks = self._filter_chunks_by_target(chunks, target_filter, code_data['source_files'])
            logger.info(f"Filtered to {len(chunks)} relevant chunks")
        
        basic_analysis = self.analyzer.analyze_chunks(chunks)

        result = AnalysisResult(
            function_name=function_name,
            function_info=code_data['function_info'],
            code_chunks=chunks,
            chunk_summaries=basic_analysis['chunk_summaries'],
            overall_structure=basic_analysis['overall_structure'],
            dependencies=basic_analysis['overall_structure']['external_dependencies'],
            error_patterns=basic_analysis['overall_structure']['error_handling_patterns'],
            input_patterns=basic_analysis['overall_structure']['input_patterns'],
            output_patterns=basic_analysis['overall_structure']['output_patterns'],
            analysis_metadata={
                'total_files': code_data['total_files'],
                'total_chunks': len(chunks),
                'analysis_timestamp': self._get_timestamp(),
                'runtime': code_data['function_info']['runtime'],
                'handler': code_data['function_info']['handler'],
                'bedrock_enhanced': True,
                'model_used': self.model_id,
                'target_filter': target_filter if target_filter else None
            }
        )
        return result
    
    def _filter_chunks_by_target(self, chunks: List[CodeChunk], target_filter: str, source_files: List[Dict]) -> List[CodeChunk]:
        """
        Filter code chunks to only include those relevant to the target function/class/file.
        
        Args:
            chunks: All code chunks
            target_filter: Function/class/file name to focus on
            source_files: Original source files for reference
            
        Returns:
            Filtered list of relevant chunks
            
        Raises:
            ValueError: If target is not found in the code
        """
        relevant_chunks = []
        target_lower = target_filter.lower()
        
        # Step 1: Find chunks that directly contain the target
        direct_matches = []
        for chunk in chunks:
            content = chunk.content.lower()
            file_name = chunk.file_name.lower()
            
            # Check if target is in filename
            if target_lower in file_name:
                direct_matches.append(chunk)
                continue
            
            # Check if target is a function definition
            if f"def {target_lower}" in content or f"function {target_lower}" in content:
                direct_matches.append(chunk)
                continue
            
            # Check if target is a class definition
            if f"class {target_lower}" in content:
                direct_matches.append(chunk)
                continue
        
        if not direct_matches:
            # Build helpful error message with available functions/classes
            available_items = self._extract_available_targets(chunks)
            error_msg = f"Target '{target_filter}' not found in Lambda code.\n\n"
            
            if available_items['functions']:
                error_msg += f"Available functions:\n"
                for func in available_items['functions'][:10]:  # Show first 10
                    error_msg += f"  - {func}\n"
                if len(available_items['functions']) > 10:
                    error_msg += f"  ... and {len(available_items['functions']) - 10} more\n"
            
            if available_items['classes']:
                error_msg += f"\nAvailable classes:\n"
                for cls in available_items['classes'][:10]:
                    error_msg += f"  - {cls}\n"
                if len(available_items['classes']) > 10:
                    error_msg += f"  ... and {len(available_items['classes']) - 10} more\n"
            
            if available_items['files']:
                error_msg += f"\nAvailable files:\n"
                for file in available_items['files'][:10]:
                    error_msg += f"  - {file}\n"
                if len(available_items['files']) > 10:
                    error_msg += f"  ... and {len(available_items['files']) - 10} more\n"
            
            error_msg += "\nTip: Try one of the items listed above or leave the filter empty to analyze the entire Lambda."
            
            logger.error(f"Target '{target_filter}' not found in code")
            raise ValueError(error_msg)
        
        logger.info(f"Found {len(direct_matches)} chunks directly containing '{target_filter}'")
        relevant_chunks.extend(direct_matches)
        
        # Step 2: Find chunks that reference the target (imports, calls, etc.)
        for chunk in chunks:
            if chunk in relevant_chunks:
                continue
            
            content = chunk.content
            
            # Check for imports
            if f"import {target_filter}" in content or f"from {target_filter}" in content:
                relevant_chunks.append(chunk)
                continue
            
            # Check for function calls
            if f"{target_filter}(" in content:
                relevant_chunks.append(chunk)
                continue
            
            # Check for class instantiation
            if f"{target_filter}()" in content or f"new {target_filter}" in content:
                relevant_chunks.append(chunk)
                continue
        
        logger.info(f"Total relevant chunks after filtering: {len(relevant_chunks)}")
        return relevant_chunks
    
    def _extract_available_targets(self, chunks: List[CodeChunk]) -> Dict[str, List[str]]:
        """
        Extract available functions, classes, and files from chunks.
        
        Args:
            chunks: Code chunks to analyze
            
        Returns:
            Dictionary with lists of functions, classes, and files
        """
        import re
        
        functions = set()
        classes = set()
        files = set()
        
        for chunk in chunks:
            content = chunk.content
            file_name = chunk.file_name
            
            # Add filename
            files.add(file_name)
            
            # Extract function definitions (Python, JavaScript, etc.)
            func_patterns = [
                r'def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(',  # Python
                r'function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(',  # JavaScript
                r'public\s+\w+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(',  # Java/C#
            ]
            for pattern in func_patterns:
                matches = re.findall(pattern, content)
                functions.update(matches)
            
            # Extract class definitions
            class_patterns = [
                r'class\s+([a-zA-Z_][a-zA-Z0-9_]*)',  # Python, Java, C#, JavaScript
            ]
            for pattern in class_patterns:
                matches = re.findall(pattern, content)
                classes.update(matches)
        
        return {
            'functions': sorted(list(functions)),
            'classes': sorted(list(classes)),
            'files': sorted(list(files))
        }

    # -------------------- Utilities --------------------
    def _get_timestamp(self) -> str:
        """Get current timestamp."""
        from datetime import datetime
        return datetime.utcnow().isoformat() + 'Z'
