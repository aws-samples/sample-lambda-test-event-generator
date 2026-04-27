# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Lambda Fetcher - Core component for fetching Lambda function code and metadata.

This module handles:
- AWS Lambda API interactions
- Code downloading and extraction
- Remove Extension Files/ Ignored Patterns
- Function metadata retrieval
- Error handling for AWS operations
"""

import boto3
import json
import zipfile
import io
import base64
import requests
import os
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class LambdaFetcher:
    """Core component for fetching Lambda function code and metadata."""
    
    # Security limits for zip bomb protection
    MAX_DOWNLOAD_SIZE = 250 * 1024 * 1024  # 250 MB (Lambda max is 250 MB)
    MAX_ZIP_ENTRIES = 10000  # Maximum number of files in zip
    MAX_UNCOMPRESSED_SIZE = 500 * 1024 * 1024  # 500 MB uncompressed
    MAX_COMPRESSION_RATIO = 100  # Max ratio of uncompressed/compressed size
    
    def __init__(self, region: str = None, custom_ignore_patterns: List[str] = None):
        """
        Initialize the Lambda fetcher.
        
        Args:
            region: AWS region for Lambda client
            custom_ignore_patterns: Additional file/folder patterns to ignore (e.g., ['tests/', '*.test.js'])
        """
        self.region = region or 'us-east-1'
        self.lambda_client = boto3.client('lambda', region_name=self.region)
        self.custom_ignore_patterns = custom_ignore_patterns or []
        logger.info(f"LambdaFetcher initialized for region: {self.region}")
        if self.custom_ignore_patterns:
            logger.info(f"Custom ignore patterns: {self.custom_ignore_patterns}")
    
    def get_function_info(self, function_arn: str) -> Dict[str, Any]:
        """Get basic information about a Lambda function."""
        try:
            function_name = self._extract_function_name(function_arn)
            response = self.lambda_client.get_function(FunctionName=function_name)
            
            return {
                'function_name': response['Configuration']['FunctionName'],
                'runtime': response['Configuration']['Runtime'],
                'handler': response['Configuration']['Handler'],
                'description': response['Configuration'].get('Description', ''),
                'timeout': response['Configuration']['Timeout'],
                'memory_size': response['Configuration']['MemorySize'],
                'last_modified': response['Configuration']['LastModified'],
                'code_size': response['Configuration']['CodeSize'],
                'environment_variables': response['Configuration'].get('Environment', {}).get('Variables', {}),
                'layers': [layer['Arn'] for layer in response['Configuration'].get('Layers', [])],
                'code_location': response['Code'].get('Location', ''),
                'repository_type': response['Code'].get('RepositoryType', '')
            }
        except self.lambda_client.exceptions.ResourceNotFoundException:
            function_name = self._extract_function_name(function_arn)
            logger.error(f"Lambda function not found: {function_name}")
            raise ValueError(f"Lambda function not found: {function_name}")
        except self.lambda_client.exceptions.ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(f"AWS error getting function info: {error_code}")
            if error_code == 'AccessDeniedException':
                raise ValueError("Access denied. Please verify IAM permissions.")
            raise ValueError(f"Error accessing Lambda function. Please check permissions and function name.")
        except Exception as e:
            logger.error(f"Unexpected error getting function info: {type(e).__name__}")
            raise ValueError("Error retrieving Lambda function information. Please try again.")
    
    def get_function_code_cleaned(self, function_arn: str) -> Dict[str, Any]:
        """Fetch and prepare Lambda function code for chunking."""
        try:
            function_name = self._extract_function_name(function_arn)
            response = self.lambda_client.get_function(FunctionName=function_name)
            
            code_location = response['Code'].get('Location')
            if not code_location:
                raise ValueError("Lambda function code location not available")
            
            # Download and extract code
            try:
                code_response = requests.get(code_location, timeout=30, stream=True)
                code_response.raise_for_status()
                
                # Check download size to prevent zip bombs
                content_length = code_response.headers.get('content-length')
                if content_length and int(content_length) > self.MAX_DOWNLOAD_SIZE:
                    logger.error(f"Lambda package too large: {content_length} bytes")
                    raise ValueError(f"Lambda package exceeds maximum size ({self.MAX_DOWNLOAD_SIZE} bytes)")
                
                # Download with size limit
                downloaded_size = 0
                chunks = []
                for chunk in code_response.iter_content(chunk_size=8192):
                    if chunk:
                        downloaded_size += len(chunk)
                        if downloaded_size > self.MAX_DOWNLOAD_SIZE:
                            logger.error(f"Lambda package download exceeded size limit")
                            raise ValueError(f"Lambda package exceeds maximum size ({self.MAX_DOWNLOAD_SIZE} bytes)")
                        chunks.append(chunk)
                
                code_content = b''.join(chunks)
                logger.info(f"Downloaded Lambda package: {downloaded_size} bytes")
                
            except requests.exceptions.Timeout:
                logger.error("Timeout downloading Lambda code")
                raise ValueError("Timeout downloading Lambda function code. Please try again.")
            except requests.exceptions.RequestException as e:
                logger.error(f"Error downloading Lambda code: {type(e).__name__}")
                raise ValueError("Error downloading Lambda function code. Please try again.")
            
            extracted_files = self._extract_zip_contents(code_content)
            function_info = self.get_function_info(function_arn)
            
            # Find handler file
            handler_file = self._find_handler_file(function_info['handler'], extracted_files)
            
            return {
                'function_info': function_info,
                'source_files': extracted_files,
                'total_files': len(extracted_files),
                'handler_file': handler_file,
                'code_chunks': [],  # Will be populated by analyzer
                'total_chunks': 0   # Will be updated by analyzer
            }
            
        except self.lambda_client.exceptions.ResourceNotFoundException:
            function_name = self._extract_function_name(function_arn)
            error_msg = f"Lambda function not found: {function_name}\n\n"
            error_msg += f"Please verify:\n"
            error_msg += f"  1. Function name is correct\n"
            error_msg += f"  2. Function exists in region: {self.region}\n"
            error_msg += f"  3. You have permission to access this function\n\n"
            error_msg += f"Tip: List your functions with:\n"
            error_msg += f"   aws lambda list-functions --region {self.region}"
            logger.error(f"Lambda function not found: {function_name}")
            raise ValueError(error_msg)
        except ValueError:
            # Re-raise ValueError (already sanitized)
            raise
        except self.lambda_client.exceptions.ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(f"AWS error getting function code: {error_code}")
            if error_code == 'AccessDeniedException':
                raise ValueError("Access denied. Please verify IAM permissions for Lambda.")
            raise ValueError("Error accessing Lambda function. Please check permissions and function name.")
        except Exception as e:
            logger.error(f"Unexpected error getting function code: {type(e).__name__}")
            raise ValueError("Error retrieving Lambda function code. Please try again.")
    
    def _extract_function_name(self, function_arn: str) -> str:
        """Extract function name from ARN."""
        # Strip whitespace to handle user input errors
        function_arn = function_arn.strip()
        
        if function_arn.startswith('arn:aws:lambda:'):
            return function_arn.split(':')[-1]
        else:
            return function_arn
    
    def _extract_zip_contents(self, zip_content: bytes) -> Dict[str, str]:
        """Extract and return contents of zip file, excluding non-code files and dependencies.
        
        Includes zip bomb protection:
        - Validates number of entries
        - Checks total uncompressed size
        - Monitors compression ratio
        """
        extracted_files = {}
        total_uncompressed_size = 0
        compressed_size = len(zip_content)
        
        try:
            with zipfile.ZipFile(io.BytesIO(zip_content), 'r') as zip_file:
                # Check number of entries (zip bomb protection)
                num_entries = len(zip_file.filelist)
                if num_entries > self.MAX_ZIP_ENTRIES:
                    logger.error(f"Zip contains too many entries: {num_entries}")
                    raise ValueError(f"Lambda package contains too many files ({num_entries}). Maximum allowed: {self.MAX_ZIP_ENTRIES}")
                
                logger.info(f"Extracting {num_entries} entries from Lambda package")
                
                for file_info in zip_file.filelist:
                    if not file_info.is_dir():
                        # Check uncompressed size (zip bomb protection)
                        total_uncompressed_size += file_info.file_size
                        if total_uncompressed_size > self.MAX_UNCOMPRESSED_SIZE:
                            logger.error(f"Total uncompressed size exceeds limit: {total_uncompressed_size} bytes")
                            raise ValueError(f"Lambda package uncompressed size exceeds maximum ({self.MAX_UNCOMPRESSED_SIZE} bytes)")
                        
                        # Skip non-code files and dependency folders
                        if self._should_skip_file(file_info.filename):
                            logger.debug(f"Skipping non-code file: {file_info.filename}")
                            continue
                        
                        try:
                            file_content = zip_file.read(file_info.filename)
                            try:
                                decoded_content = file_content.decode('utf-8')
                                extracted_files[file_info.filename] = decoded_content
                            except UnicodeDecodeError:
                                # Skip binary files instead of encoding them
                                logger.debug(f"Skipping binary file: {file_info.filename}")
                                continue
                        except Exception:
                            # Skip files that can't be read (don't log details)
                            logger.debug(f"Skipping unreadable file: {file_info.filename}")
                            continue
                
                # Check compression ratio (zip bomb protection)
                if compressed_size > 0:
                    compression_ratio = total_uncompressed_size / compressed_size
                    if compression_ratio > self.MAX_COMPRESSION_RATIO:
                        logger.error(f"Compression ratio too high: {compression_ratio:.1f}x")
                        raise ValueError(f"Lambda package has suspicious compression ratio ({compression_ratio:.1f}x). Maximum allowed: {self.MAX_COMPRESSION_RATIO}x")
                    logger.info(f"Compression ratio: {compression_ratio:.1f}x")
                
        except zipfile.BadZipFile:
            logger.error("Invalid zip file format")
            raise ValueError("Invalid Lambda deployment package format")
        except ValueError:
            # Re-raise ValueError (already sanitized)
            raise
        except Exception:
            logger.error("Error extracting deployment package")
            raise ValueError("Error extracting Lambda deployment package")
        
        logger.info(f"Extracted {len(extracted_files)} code files (filtered out dependencies and non-code files)")
        logger.info(f"Total uncompressed size: {total_uncompressed_size / 1024 / 1024:.2f} MB")
        return extracted_files
    
    def _should_skip_file(self, filepath: str) -> bool:
        """
        Determine if a file should be skipped during extraction.
        
        Args:
            filepath: Path of the file in the zip
            
        Returns:
            True if file should be skipped, False otherwise
        """
        filepath_lower = filepath.lower()
        
        # Check custom ignore patterns first
        if self._matches_custom_ignore_patterns(filepath):
            logger.debug(f"Skipping file matching custom ignore pattern: {filepath}")
            return True
        
        # Check if file is in a dependency/library folder
        if self._is_dependency_folder(filepath):
            return True
        
        # Skip common dependency/package folders (using 'in' to match anywhere in path)
        skip_patterns = [
            'node_modules/',
            'venv/',
            'env/',
            '.venv/',
            'virtualenv/',
            '__pycache__/',
            '.pytest_cache/',
            '.git/',
            '.svn/',
            '.hg/',
            'dist/',
            'build/',
            'target/',
            'bin/',
            'obj/',
            '.idea/',
            '.vscode/',
            '.vs/',
            'packages/',
            'vendor/',
            'bower_components/',
            '.gradle/',
            '.mvn/',
        ]
        
        for pattern in skip_patterns:
            if pattern in filepath_lower:
                return True
        
        # Skip non-code file extensions
        skip_extensions = [
            # Binary/compiled
            '.pyc', '.pyo', '.pyd', '.so', '.dll', '.dylib', '.exe', '.bin',
            '.class', '.jar', '.war', '.ear',
            # Images
            '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg', '.webp',
            # Documents
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
            # Archives
            '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar',
            # Media
            '.mp3', '.mp4', '.avi', '.mov', '.wav', '.flac',
            # Fonts
            '.ttf', '.otf', '.woff', '.woff2', '.eot',
            # Other
            '.lock', '.log', '.cache', '.tmp', '.temp',
            '.min.js', '.min.css',  # Minified files
        ]
        
        for ext in skip_extensions:
            if filepath_lower.endswith(ext):
                return True
        
        # Skip hidden files (except important config files)
        filename = filepath.split('/')[-1]
        if filename.startswith('.') and filename not in ['.env', '.env.example', '.gitignore']:
            return True
        
        return False
    
    def _matches_custom_ignore_patterns(self, filepath: str) -> bool:
        """
        Check if filepath matches any custom ignore patterns.
        Supports wildcards (*) and directory patterns (/).
        
        Args:
            filepath: Path of the file in the zip
            
        Returns:
            True if file matches any custom ignore pattern, False otherwise
        """
        if not self.custom_ignore_patterns:
            return False
        
        import fnmatch
        
        for pattern in self.custom_ignore_patterns:
            # Normalize pattern
            pattern = pattern.strip()
            if not pattern:
                continue
            
            # Handle directory patterns (ending with /)
            if pattern.endswith('/'):
                # Match if filepath starts with this directory
                if filepath.startswith(pattern) or f"/{pattern}" in filepath:
                    return True
            # Handle wildcard patterns
            elif '*' in pattern or '?' in pattern:
                # Use fnmatch for wildcard matching
                if fnmatch.fnmatch(filepath, pattern) or fnmatch.fnmatch(filepath.lower(), pattern.lower()):
                    return True
            # Handle exact matches or substring matches
            else:
                # Check if pattern appears anywhere in the path
                if pattern in filepath or pattern.lower() in filepath.lower():
                    return True
        
        return False
    
    def _is_dependency_folder(self, filepath: str) -> bool:
        """
        Detect if a file is inside a dependency/library folder.
        Uses heuristics to identify common packages across multiple languages.
        
        Args:
            filepath: Path of the file in the zip
            
        Returns:
            True if file is in a dependency folder, False otherwise
        """
        # Common library/package names across languages (case-insensitive)
        common_packages = {
            # Python
            'boto3', 'botocore', 'typing_extensions', 's3transfer', 'urllib3',
            'wrapt', 'jmespath', 'aws_xray_sdk', 'requests', 'certifi',
            'charset_normalizer', 'idna', 'python_dateutil', 'six', 'setuptools',
            'pip', 'wheel', 'pkg_resources', 'numpy', 'pandas', 'scipy',
            'matplotlib', 'pillow', 'cryptography', 'pyyaml', 'click',
            'flask', 'django', 'fastapi', 'sqlalchemy', 'alembic',
            'pytest', 'unittest', 'mock', 'coverage', 'tox',
            'redis', 'celery', 'kombu', 'amqp', 'billiard',
            'psycopg2', 'pymysql', 'pymongo', 'elasticsearch',
            'pydantic', 'marshmallow', 'jsonschema', 'attrs',
            'aiohttp', 'asyncio', 'httpx', 'websockets',
            'jwt', 'passlib', 'bcrypt', 'argon2',
            'dotenv', 'configparser', 'toml', 'yaml', 'dateutil', 'aws_lambda_powertools',
            # Node.js
            'express', 'lodash', 'axios', 'moment', 'uuid', 'chalk',
            'commander', 'debug', 'fs_extra', 'glob', 'minimist',
            'aws_sdk', 'serverless', 'webpack', 'babel', 'eslint',
            'jest', 'mocha', 'chai', 'sinon', 'supertest',
            'react', 'vue', 'angular', 'typescript', 'prettier',
            # Ruby
            'aws_sdk', 'json', 'net_http', 'uri', 'base64', 'digest',
            'openssl', 'time', 'date', 'logger', 'fileutils',
            'rails', 'sinatra', 'rack', 'bundler', 'rspec',
            'minitest', 'capybara', 'factory_bot', 'faker',
            # Java
            'com', 'org', 'net', 'io', 'java', 'javax',
            'springframework', 'hibernate', 'jackson', 'gson',
            'junit', 'mockito', 'slf4j', 'logback', 'log4j',
            'apache', 'google', 'amazonaws',
            # C#
            'system', 'microsoft', 'newtonsoft', 'amazon',
            'nunit', 'xunit', 'moq', 'autofac', 'entityframework',
        }
        
        # Split path into parts
        parts = filepath.split('/')
        
        # Check each directory level (exclude filename)
        for part in parts[:-1]:
            part_lower = part.lower()
            
            # Exact match for known packages (after normalizing separators)
            part_normalized = part_lower.replace('-', '_').replace('.', '_')
            if part_normalized in common_packages:
                return True
            
            # Check for versioned packages (e.g., "boto3-1.2.3" or "boto3.dist-info")
            base_name = part_normalized.split('_')[0]  # Get first part before version/suffix
            if base_name in common_packages:
                return True
            
            # Python-specific checks
            if part_lower.endswith('.dist-info') or part_lower.endswith('.egg-info'):
                return True
            if 'site-packages' in part_lower or 'dist-packages' in part_lower:
                return True
            
            # Java-specific checks
            if part_lower in ['maven', 'gradle', 'm2', 'repository']:
                return True
            if part_lower.startswith('com.') or part_lower.startswith('org.') or part_lower.startswith('net.'):
                return True
            
            # C#-specific checks
            if part_lower in ['packages', 'nuget']:
                return True
            if part_lower.startswith('system.') or part_lower.startswith('microsoft.'):
                return True
            
            # Ruby-specific checks
            if part_lower in ['gems', 'bundle']:
                return True
            
            # Common lib folders
            if part_lower in ['lib', 'lib64', 'libs', 'libraries']:
                return True
        
        return False
    
    
    def _find_handler_file(self, handler: str, source_files: Dict[str, str]) -> str:
        """Find the main handler file based on the handler configuration."""
        if '.' in handler:
            module_path = handler.split('.')[0]
            possible_files = [
                f"{module_path}.py", f"{module_path}.js", f"{module_path}.ts",
                f"{module_path}/index.py", f"{module_path}/index.js", f"{module_path}/index.ts",
                "index.py", "index.js", "index.ts", "lambda_function.py", "app.py", "main.py"
            ]
            
            for file_path in possible_files:
                if file_path in source_files:
                    return file_path
        
        # Fallback: return the first code file found
        for filename in source_files.keys():
            if any(filename.endswith(ext) for ext in ['.py', '.js', '.ts', '.java', '.cs', '.rb']) and not filename.startswith('test'):
                return filename
        
        return list(source_files.keys())[0] if source_files else "unknown"