# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Code analysis utilities for Lambda functions.
Analyzes code chunks and generates structured summaries and test cases.
"""

import re
import json
import os
import boto3
from typing import Dict, List, Any, Optional
from .code_chunker import CodeChunk
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# Amazon Bedrock Guardrail configuration — loaded from environment variables
# Set by CloudFormation stack outputs: BedrockGuardrailId and BedrockGuardrailVersion
GUARDRAIL_ID = os.environ.get('BEDROCK_GUARDRAIL_ID', '')
GUARDRAIL_VERSION = os.environ.get('BEDROCK_GUARDRAIL_VERSION', '')


def _get_guardrail_config() -> dict:
    """Return guardrailConfig dict for Bedrock converse() calls, or empty dict if not configured."""
    if GUARDRAIL_ID and GUARDRAIL_VERSION:
        return {
            "guardrailIdentifier": GUARDRAIL_ID,
            "guardrailVersion": GUARDRAIL_VERSION,
            "trace": "enabled"
        }
    logger.warning("Bedrock Guardrail not configured — BEDROCK_GUARDRAIL_ID / BEDROCK_GUARDRAIL_VERSION env vars missing")
    return {}

class CodeAnalyzer:
    """Analyzes code chunks and generates structured insights and test cases."""
    
    def __init__(self, region: str = 'us-east-1', model_id: str = None):
        """Initialize CodeAnalyzer with optional Bedrock client."""
        self.region = region
        # Use inference profile ARN for Claude 4.6 Sonnet
        self.model_id = model_id or 'us.anthropic.claude-sonnet-4-6'
        self.bedrock_client = None
        self.max_workers = 5  # Max parallel threads for Bedrock calls
        
        # Initialize Bedrock client lazily (only when needed)
        try:
            self.bedrock_client = boto3.client('bedrock-runtime', region_name=self.region)
            logger.info(f"Bedrock client initialized for region: {self.region}")
        except Exception as e:
            logger.warning(f"Could not initialize Bedrock client: {e}")

    def analyze_chunks(self, chunks: List[CodeChunk]) -> Dict[str, Any]:
        """Generate incremental analysis of chunked code (parallel processing)."""
        analysis = {
            'chunk_summaries': [],
            'overall_structure': {},
            'key_components': [],
            'input_output_schema': {},
            'edge_cases': [],
            'dependencies': []
        }

        # Process chunks in parallel
        logger.info(f"Analyzing {len(chunks)} chunks in parallel (max_workers={self.max_workers})")
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all chunk analysis tasks
            future_to_chunk = {
                executor.submit(self._analyze_single_chunk, chunk, f"{chunk.file_name}#{idx}"): (chunk, idx)
                for idx, chunk in enumerate(chunks)
            }
            
            # Collect results as they complete
            chunk_results = []
            for future in as_completed(future_to_chunk):
                chunk, idx = future_to_chunk[future]
                try:
                    chunk_analysis = future.result()
                    chunk_results.append((idx, chunk_analysis))
                    logger.info(f"Completed analysis for chunk {idx}: {chunk.file_name}")
                except Exception as e:
                    logger.error(f"Error analyzing chunk {idx} ({chunk.file_name}): {e}")
                    # Create minimal analysis for failed chunk
                    chunk_results.append((idx, {
                        'chunk_id': f"{chunk.file_name}#{idx}",
                        'file_name': chunk.file_name,
                        'chunk_type': chunk.chunk_type,
                        'components': [],
                        'edge_cases': [],
                        'dependencies': [],
                        'inputs': [],
                        'outputs': []
                    }))
        
        # Sort by original index to maintain order
        chunk_results.sort(key=lambda x: x[0])
        analysis['chunk_summaries'] = [result for _, result in chunk_results]
        
        # Aggregate results
        for chunk_analysis in analysis['chunk_summaries']:
            analysis['key_components'].extend(chunk_analysis.get('components', []))
            analysis['edge_cases'].extend(chunk_analysis.get('edge_cases', []))
            analysis['dependencies'].extend(chunk_analysis.get('dependencies', []))

        analysis['overall_structure'] = self._synthesize_structure(analysis['chunk_summaries'])
        
        logger.info(f"Completed analysis of {len(chunks)} chunks")
        return analysis

    def _analyze_single_chunk(self, chunk: CodeChunk, chunk_id: str) -> Dict[str, Any]:
        """Analyze a single code chunk with detailed pattern extraction."""
        analysis = {
            'chunk_id': chunk_id,
            'file_name': chunk.file_name,
            'chunk_type': chunk.chunk_type,
            'summary': chunk.summary,
            'content': chunk.content,
            'components': [],
            'edge_cases': [],
            'dependencies': [],
            'inputs': [],
            'outputs': []
        }

        lines = chunk.content.split('\n')
        analysis['components'] = self._extract_components(lines)
        analysis['dependencies'] = self._extract_dependencies(lines)
        analysis['edge_cases'] = self._identify_edge_cases(lines)
        analysis['inputs'], analysis['outputs'] = self._extract_io_patterns(lines)

        # Generate better summary using Bedrock
        if self.bedrock_client:
            analysis['summary'] = self._generate_chunk_summary(chunk, analysis)

        return analysis

    # ------------------ Component extraction ------------------
    def _extract_components(self, lines: List[str]) -> List[Dict[str, Any]]:
        components = []

        for line in lines:
            # Python
            py_func = re.match(r'^\s*def\s+(\w+)\((.*?)\)', line)
            if py_func:
                components.append({'type':'function','name':py_func.group(1),'parameters':py_func.group(2),'language':'python'})
            py_class = re.match(r'^\s*class\s+(\w+)', line)
            if py_class:
                components.append({'type':'class','name':py_class.group(1),'language':'python'})

            # JavaScript / TypeScript
            js_patterns = [r'^\s*function\s+(\w+)\s*\(', r'^\s*const\s+(\w+)\s*=\s*\(', r'^\s*export\s+function\s+(\w+)\s*\(']
            for pattern in js_patterns:
                js_match = re.match(pattern, line)
                if js_match:
                    components.append({'type':'function','name':js_match.group(1),'language':'javascript'})
                    break
            js_class = re.match(r'^\s*class\s+(\w+)', line)
            if js_class:
                components.append({'type':'class','name':js_class.group(1),'language':'javascript'})

            # TODO: Extend regex patterns for Java, C#, Ruby functions/classes

        return components

    # ------------------ Dependencies ------------------
    def _extract_dependencies(self, lines: List[str]) -> List[str]:
        deps = []
        for line in lines:
            line = line.strip()
            if re.match(r'^(import|from)\s+', line) or re.match(r'^(using|require)', line):
                deps.append(line)
        return deps

    # ------------------ Edge cases ------------------
    def _identify_edge_cases(self, lines: List[str]) -> List[str]:
        edge_cases = []
        patterns = [
            (r'if.*is None','Null check'),
            (r'if.*== ""','Empty string'),
            (r'if.*len\(','Length check'),
            (r'try:','Exception handling'),
            (r'except','Error catching'),
            (r'raise','Error raising'),
            (r'assert','Assertion'),
            (r'if.*not\s+','Negation check'),
            (r'\.get\(','Safe dictionary access'),
            (r'isinstance\(','Type checking')
        ]
        for line in lines:
            for pattern, desc in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    edge_cases.append(f"{desc}: {line.strip()}")
                    break
        return edge_cases

    # ------------------ IO Patterns ------------------
    def _extract_io_patterns(self, lines: List[str]) -> tuple:
        inputs, outputs = [], []

        input_patterns = [r'event\[', r'event\.get\(', r'request\.', r'body\.', r'headers\[', r'queryStringParameters', r'pathParameters']
        output_patterns = [r'return\s+{', r'response\s*=', r'statusCode', r'\.json\(', r'HttpResponse']

        for line in lines:
            line_s = line.strip()
            if any(re.search(p, line_s) for p in input_patterns):
                inputs.append(line_s)
            if any(re.search(p, line_s) for p in output_patterns):
                outputs.append(line_s)
        return inputs, outputs

    # ------------------ Structure synthesis ------------------
    def _synthesize_structure(self, chunk_summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
        struct = {'total_functions':0,'total_classes':0,'main_components':[],'external_dependencies':set(),
                  'error_handling_patterns':[],'languages_used':set(),'input_patterns':[],'output_patterns':[]}
        for summary in chunk_summaries:
            for comp in summary.get('components',[]):
                if comp['type']=='function':
                    struct['total_functions'] += 1
                    if 'handler' in comp['name'].lower():
                        struct['main_components'].append(comp)
                elif comp['type']=='class':
                    struct['total_classes'] += 1
                struct['languages_used'].add(comp.get('language','unknown'))
            for dep in summary.get('dependencies',[]):
                struct['external_dependencies'].add(dep)
            struct['error_handling_patterns'].extend(summary.get('edge_cases',[]))
            struct['input_patterns'].extend(summary.get('inputs',[]))
            struct['output_patterns'].extend(summary.get('outputs',[]))
        struct['external_dependencies'] = list(struct['external_dependencies'])
        struct['languages_used'] = list(struct['languages_used'])
        return struct
 
    def _generate_chunk_summary(self, chunk: CodeChunk, analysis: Dict[str, Any]) -> str:
        """
        Generate a meaningful summary for a code chunk using Bedrock.
        
        Args:
            chunk: The code chunk
            analysis: Basic analysis results
            
        Returns:
            AI-generated summary of the chunk's purpose
        """
        try:
            # Get function names
            func_names = [c['name'] for c in analysis.get('components', []) if c['type'] == 'function']
            
            # Limit content size
            content = chunk.content
            if len(content) > 4000:
                content = content[:4000] + "\n... (truncated)"
            
            prompt = f"""Analyze this code chunk and provide a concise summary of its purpose and behavior.

File: {chunk.file_name}
Type: {chunk.chunk_type}
Functions: {', '.join(func_names) if func_names else 'none'}

Code:
{content}

Provide a 1-2 sentence summary describing what this code does. Focus on purpose and behavior, not just listing functions.
No markdown, no code blocks."""

            converse_kwargs = {
                "modelId": self.model_id,
                "messages": [{"role": "user", "content": [{"text": "Summarize this code chunk as instructed."}]}],
                "system": [{"text": prompt}],
                "inferenceConfig": {
                    "maxTokens": 150,
                    "temperature": 0.2
                }
            }
            guardrail_cfg = _get_guardrail_config()
            if guardrail_cfg:
                converse_kwargs["guardrailConfig"] = guardrail_cfg

            response = self.bedrock_client.converse(**converse_kwargs)
            
            summary = response['output']['message']['content'][0]['text'].strip()
            summary = summary.replace('```', '').replace('**', '').strip()
            
            logger.info(f"Generated chunk summary for {chunk.file_name}: {summary[:60]}...")
            return summary
            
        except Exception as e:
            logger.warning(f"Failed to generate chunk summary: {e}")
            return chunk.summary or f"Code chunk from {chunk.file_name}"