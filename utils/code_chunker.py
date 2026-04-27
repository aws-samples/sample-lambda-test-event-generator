# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Code chunking utilities for multi-language Lambda function analysis.
Handles intelligent splitting of large code files into manageable chunks.
Supports: Python, Java, .NET (C#), Node.js (JavaScript/TypeScript), Ruby
"""

import ast
import re
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class CodeChunk:
    """Represents a chunk of code with metadata."""
    
    def __init__(self, content: str, chunk_type: str, file_name: str, start_line: int = 0, end_line: int = 0, language: str = "unknown"):
        self.content = content
        self.chunk_type = chunk_type  # 'file', 'function', 'class', 'window', 'imports'
        self.file_name = file_name
        self.start_line = start_line
        self.end_line = end_line
        self.language = language
        self.summary = ""
        self.key_elements = []


class MultiLanguageCodeChunker:
    """Intelligent code chunking for multi-language Lambda functions."""
    
    def __init__(self, max_chunk_size: int = 2000, overlap_size: int = 100, max_chunks: int = 50):
        self.max_chunk_size = max_chunk_size
        self.overlap_size = overlap_size
        self.max_chunks = max_chunks

        # Language-specific patterns
        self.language_patterns = {
            'python': {
                'extensions': ['.py'],
                'function_patterns': [r'^\s*def\s+(\w+)', r'^\s*async\s+def\s+(\w+)'],
                'class_patterns': [r'^\s*class\s+(\w+)'],
                'import_patterns': [r'^\s*(import|from)\s+'],
                'comment_patterns': [r'^\s*#']
            },
            'java': {
                'extensions': ['.java'],
                'function_patterns': [r'^\s*(public|private|protected)?\s*(static)?\s*\w+\s+(\w+)\s*\('],
                'class_patterns': [r'^\s*(public|private)?\s*class\s+(\w+)'],
                'import_patterns': [r'^\s*import\s+'],
                'comment_patterns': [r'^\s*//', r'^\s*/\*']
            },
            'csharp': {
                'extensions': ['.cs'],
                'function_patterns': [r'^\s*(public|private|protected|internal)?\s*(static)?\s*\w+\s+(\w+)\s*\('],
                'class_patterns': [r'^\s*(public|private|internal)?\s*class\s+(\w+)'],
                'import_patterns': [r'^\s*using\s+'],
                'comment_patterns': [r'^\s*//', r'^\s*/\*']
            },
            'javascript': {
                'extensions': ['.js', '.ts', '.mjs'],
                'function_patterns': [r'^\s*function\s+(\w+)', r'^\s*const\s+(\w+)\s*=\s*\(', r'^\s*(\w+)\s*:\s*function', r'^\s*async\s+function\s+(\w+)'],
                'class_patterns': [r'^\s*class\s+(\w+)'],
                'import_patterns': [r'^\s*(import|const.*require)', r'^\s*export'],
                'comment_patterns': [r'^\s*//', r'^\s*/\*']
            },
            'ruby': {
                'extensions': ['.rb'],
                'function_patterns': [r'^\s*def\s+(\w+)'],
                'class_patterns': [r'^\s*class\s+(\w+)', r'^\s*module\s+(\w+)'],
                'import_patterns': [r'^\s*(require|load|include)', r'^\s*require_relative'],
                'comment_patterns': [r'^\s*#']
            }
        }
    
    def chunk_code_files(self, source_files: Dict[str, str], handler_file: str) -> List[CodeChunk]:
        """Intelligently chunk code files based on language, size and structure.
        In every file, create chunks-
        small file- one chunk
        large file- Chunk by language (Java, C#, Ruby, Python, Nodejs)
        If language not detected from available list Chunk by sliding window
        """
        chunks = []
        filenames = list(source_files.keys())
        
        # Prioritize handler file first
        if handler_file in filenames:
            filenames.remove(handler_file)
            filenames = [handler_file] + filenames

        chunk_counter = 1
        for filename in filenames:
            language = self._detect_language(filename)
            if language != 'unknown':
                file_chunks = self.chunk_single_file(filename, source_files[filename], filename == handler_file, language)
                
                # Assign chunk numbers
                for chunk in file_chunks:
                    if chunk_counter > self.max_chunks:
                        logger.info(f"Reached max_chunks ({self.max_chunks}), stopping further chunking.")
                        return chunks
                    chunk.chunk_number = chunk_counter
                    chunks.append(chunk)
                    chunk_counter += 1
        
        return chunks
    
    def chunk_single_file(self, filename: str, content: str, is_handler: bool, language: str) -> List[CodeChunk]:
        """Chunk a single file based on its language, structure and size."""
        chunks = []
        lines = content.split('\n')
        
        # Strategy A: If file is small, treat as single chunk
        if len(lines) <= self.max_chunk_size:
            chunk = CodeChunk(content, 'file', filename, 1, len(lines), language)
            chunk.summary = self._generate_file_summary(content, filename, language)
            chunks.append(chunk)
            return chunks
        
        # Strategy B: Try logical chunking based on language
        logical_chunks = self._chunk_by_language(filename, content, language)
        if logical_chunks:
            chunks.extend(logical_chunks)
            return chunks
        
        # Strategy C: Sliding window for large files
        window_chunks = self._chunk_with_sliding_window(filename, content, language)
        chunks.extend(window_chunks)
        
        return chunks
    
    def _detect_language(self, filename: str) -> str:
        """Detect programming language from file extension."""
        for language, config in self.language_patterns.items():
            if any(filename.endswith(ext) for ext in config['extensions']):
                return language
        return 'unknown'
    
    def _chunk_by_language(self, filename: str, content: str, language: str) -> List[CodeChunk]:
        """Chunk code based on language-specific patterns."""
        if language == 'python':
            return self._chunk_python_logically(filename, content)
        elif language in ['java', 'csharp']:
            return self._chunk_java_csharp_logically(filename, content, language)
        elif language == 'javascript':
            return self._chunk_javascript_logically(filename, content)
        elif language == 'ruby':
            return self._chunk_ruby_logically(filename, content)
        else:
            return []
    
    def _chunk_python_logically(self, filename: str, content: str) -> List[CodeChunk]:
        """Chunk Python code using AST parsing. Create function/class chunks"""
        chunks = []
        
        try:
            tree = ast.parse(content)
            lines = content.split('\n')
            
            # Extract imports and module-level code first
            imports_end_line = 0
            function_classes = []
            
            # Find all top-level nodes
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    imports_end_line = max(imports_end_line, node.lineno)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    start_line = node.lineno - 1
                    end_line = node.end_lineno if hasattr(node, 'end_lineno') else start_line + 50
                    function_classes.append((node.name, start_line, end_line, type(node).__name__, node))
            
            # Create imports chunk if there are imports
            if imports_end_line > 0:
                imports_content = '\n'.join(lines[:imports_end_line])
                chunk = CodeChunk(imports_content, 'imports', filename, 1, imports_end_line, 'python')
                chunk.summary = "Python imports and global variables"
                chunks.append(chunk)
            
            # Create function/class chunks
            for name, start_line, end_line, node_type, node in function_classes:
                if end_line - start_line > self.max_chunk_size:
                    func_content = '\n'.join(lines[start_line:end_line])
                    sub_chunks = self._chunk_with_sliding_window(filename, func_content, 'python', start_line)
                    chunks.extend(sub_chunks)
                else:
                    func_content = '\n'.join(lines[start_line:end_line])
                    chunk = CodeChunk(func_content, node_type.lower(), filename, start_line + 1, end_line, 'python')
                    chunk.summary = f"Python {node_type} '{name}' - {self._analyze_function_purpose(func_content, 'python', node)}"
                    chunks.append(chunk)
            
            return chunks
            
        except SyntaxError as e:
            logger.warning(f"Failed to parse Python file {filename}: {e}")
            return []
    
    def _chunk_java_csharp_logically(self, filename: str, content: str, language: str) -> List[CodeChunk]:
        """Chunk Java or C# code using regex patterns."""
        chunks = []
        lines = content.split('\n')
        patterns = self.language_patterns[language]
        
        # Find imports/using statements
        imports_end = 0
        for i, line in enumerate(lines):
            if any(re.match(pattern, line) for pattern in patterns['import_patterns']):
                imports_end = i + 1
        
        # Create imports chunk
        if imports_end > 0:
            imports_content = '\n'.join(lines[:imports_end])
            chunk = CodeChunk(imports_content, 'imports', filename, 1, imports_end, language)
            chunk.summary = f"{language.title()} imports and using statements"
            chunks.append(chunk)
        
        # Find classes and methods
        current_chunk_start = imports_end
        current_chunk_lines = []
        brace_count = 0
        in_class = False
        
        for i, line in enumerate(lines[imports_end:], imports_end):
            current_chunk_lines.append(line)
            
            # Count braces to track scope
            brace_count += line.count('{') - line.count('}')
            
            # Check for class definition
            if any(re.match(pattern, line) for pattern in patterns['class_patterns']):
                in_class = True
            
            # Check for method definition
            elif any(re.match(pattern, line) for pattern in patterns['function_patterns']):
                if len(current_chunk_lines) > 10:  # Only create chunk if substantial
                    chunk_content = '\n'.join(current_chunk_lines[:-1])  # Exclude current line
                    chunk = CodeChunk(chunk_content, 'class' if in_class else 'method', filename, 
                                    current_chunk_start + 1, i, language)
                    chunk.summary = self._analyze_function_purpose(chunk_content, language)
                    chunks.append(chunk)
                
                current_chunk_start = i
                current_chunk_lines = [line]
            
            # End of class/method when braces balance
            elif brace_count == 0 and in_class and len(current_chunk_lines) > 10:
                chunk_content = '\n'.join(current_chunk_lines)
                chunk = CodeChunk(chunk_content, 'class', filename, current_chunk_start + 1, i + 1, language)
                chunk.summary = self._analyze_function_purpose(chunk_content, language)
                chunks.append(chunk)
                
                current_chunk_start = i + 1
                current_chunk_lines = []
                in_class = False
        
        # Add final chunk
        if current_chunk_lines and len(current_chunk_lines) > 5:
            chunk_content = '\n'.join(current_chunk_lines)
            chunk = CodeChunk(chunk_content, 'class' if in_class else 'method', filename, 
                            current_chunk_start + 1, len(lines), language)
            chunk.summary = self._analyze_function_purpose(chunk_content, language)
            chunks.append(chunk)
        
        return chunks if len(chunks) > 1 else []
    
    def _chunk_javascript_logically(self, filename: str, content: str) -> List[CodeChunk]:
        """Chunk JavaScript/TypeScript code using regex patterns."""
        chunks = []
        lines = content.split('\n')
        patterns = self.language_patterns['javascript']
        
        # Find imports
        imports_end = 0
        for i, line in enumerate(lines):
            if any(re.match(pattern, line) for pattern in patterns['import_patterns']):
                imports_end = i + 1
        
        # Create imports chunk
        if imports_end > 0:
            imports_content = '\n'.join(lines[:imports_end])
            chunk = CodeChunk(imports_content, 'imports', filename, 1, imports_end, 'javascript')
            chunk.summary = "JavaScript/TypeScript imports and exports"
            chunks.append(chunk)
        
        # Find functions and classes
        current_chunk_start = imports_end
        current_chunk_lines = []
        
        for i, line in enumerate(lines[imports_end:], imports_end):
            # Check for function or class definition
            if any(re.match(pattern, line) for pattern in patterns['function_patterns'] + patterns['class_patterns']):
                if current_chunk_lines and len(current_chunk_lines) > 10:
                    chunk_content = '\n'.join(current_chunk_lines)
                    chunk = CodeChunk(chunk_content, 'function', filename, current_chunk_start + 1, i, 'javascript')
                    chunk.summary = self._analyze_function_purpose(chunk_content, 'javascript')
                    chunks.append(chunk)
                
                current_chunk_start = i
                current_chunk_lines = [line]
            else:
                current_chunk_lines.append(line)
        
        # Add final chunk
        if current_chunk_lines and len(current_chunk_lines) > 5:
            chunk_content = '\n'.join(current_chunk_lines)
            chunk = CodeChunk(chunk_content, 'function', filename, current_chunk_start + 1, len(lines), 'javascript')
            chunk.summary = self._analyze_function_purpose(chunk_content, 'javascript')
            chunks.append(chunk)
        
        return chunks if len(chunks) > 1 else []
    
    def _chunk_ruby_logically(self, filename: str, content: str) -> List[CodeChunk]:
        """Chunk Ruby code using regex patterns."""
        chunks = []
        lines = content.split('\n')
        patterns = self.language_patterns['ruby']
        
        # Find requires
        imports_end = 0
        for i, line in enumerate(lines):
            if any(re.match(pattern, line) for pattern in patterns['import_patterns']):
                imports_end = i + 1
        
        # Create imports chunk
        if imports_end > 0:
            imports_content = '\n'.join(lines[:imports_end])
            chunk = CodeChunk(imports_content, 'imports', filename, 1, imports_end, 'ruby')
            chunk.summary = "Ruby requires and includes"
            chunks.append(chunk)
        
        # Find classes, modules, and methods
        current_chunk_start = imports_end
        current_chunk_lines = []
        indent_level = 0
        
        for i, line in enumerate(lines[imports_end:], imports_end):
            current_indent = len(line) - len(line.lstrip())
            
            # Check for class, module, or method definition
            if any(re.match(pattern, line) for pattern in patterns['class_patterns'] + patterns['function_patterns']):
                if current_chunk_lines and len(current_chunk_lines) > 10:
                    chunk_content = '\n'.join(current_chunk_lines)
                    chunk = CodeChunk(chunk_content, 'class', filename, current_chunk_start + 1, i, 'ruby')
                    chunk.summary = self._analyze_function_purpose(chunk_content, 'ruby')
                    chunks.append(chunk)
                
                current_chunk_start = i
                current_chunk_lines = [line]
                indent_level = current_indent
            else:
                current_chunk_lines.append(line)
        
        # Add final chunk
        if current_chunk_lines and len(current_chunk_lines) > 5:
            chunk_content = '\n'.join(current_chunk_lines)
            chunk = CodeChunk(chunk_content, 'class', filename, current_chunk_start + 1, len(lines), 'ruby')
            chunk.summary = self._analyze_function_purpose(chunk_content, 'ruby')
            chunks.append(chunk)
        
        return chunks if len(chunks) > 1 else []
    
    def _chunk_with_sliding_window(self, filename: str, content: str, language: str, offset: int = 0) -> List[CodeChunk]:
        """Chunk content using sliding window approach."""
        chunks = []
        lines = content.split('\n')
        
        start = 0
        chunk_num = 1
        
        while start < len(lines):
            end = min(start + self.max_chunk_size, len(lines))
            
            # Adjust end to avoid breaking in the middle of functions/classes
            if end < len(lines):
                end = self._find_good_break_point(lines, start, end, language)
            
            chunk_content = '\n'.join(lines[start:end])
            chunk = CodeChunk(
                chunk_content, 
                'window', 
                filename, 
                start + offset + 1, 
                end + offset,
                language
            )
            chunk.summary = f"Code window {chunk_num} - {self._generate_window_summary(chunk_content, language)}"
            chunks.append(chunk)
            
            # Move start with overlap
            start = max(start + self.max_chunk_size - self.overlap_size, end)
            chunk_num += 1
        
        return chunks
    
    def _find_good_break_point(self, lines: List[str], start: int, proposed_end: int, language: str) -> int:
        """Find a good place to break the chunk based on language patterns."""
        patterns = self.language_patterns.get(language, {})
        function_patterns = patterns.get('function_patterns', [])
        class_patterns = patterns.get('class_patterns', [])
        comment_patterns = patterns.get('comment_patterns', [])
        
        # Look backwards from proposed_end for good break points
        for i in range(proposed_end - 1, start, -1):
            line = lines[i].strip()
            
            # Check for function/class definitions or comments
            if (any(re.match(pattern, line) for pattern in function_patterns + class_patterns + comment_patterns) or
                line == '' or line in ['}', 'end', '}']):
                return i + 1
        
        return proposed_end
    
    def _generate_file_summary(self, content: str, filename: str, language: str) -> str:
        """Generate a summary of what's in the file based on language."""
        lines = content.split('\n')
        patterns = self.language_patterns.get(language, {})
        
        functions = sum(1 for line in lines for pattern in patterns.get('function_patterns', []) if re.match(pattern, line))
        classes = sum(1 for line in lines for pattern in patterns.get('class_patterns', []) if re.match(pattern, line))
        imports = sum(1 for line in lines for pattern in patterns.get('import_patterns', []) if re.match(pattern, line))
        
        summary_parts = []
        if functions > 0:
            summary_parts.append(f"{functions} function(s)")
        if classes > 0:
            summary_parts.append(f"{classes} class(es)")
        if imports > 0:
            summary_parts.append(f"{imports} import(s)")
        
        summary = f"{language.title()} file with {', '.join(summary_parts)} ({len(lines)} lines)"
        return summary
    
    def _analyze_function_purpose(self, func_content: str, language: str, node: Optional[ast.AST] = None) -> str:
        """Analyze function content to determine its purpose based on language."""
        lines = func_content.split('\n')
        
        # For Python, try to extract docstring using AST
        if language == 'python' and node and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(node)
            if docstring:
                first_line = docstring.split('\n')[0].strip()
                return first_line[:100] + "..." if len(first_line) > 100 else first_line
        
        # Look for comments/docstrings in the first few lines
        comment_patterns = {
            'python': [r'^\s*"""', r"^\s*'''", r'^\s*#'],
            'java': [r'^\s*/\*\*', r'^\s*\*', r'^\s*//'],
            'csharp': [r'^\s*///', r'^\s*/\*\*', r'^\s*//'],
            'javascript': [r'^\s*/\*\*', r'^\s*\*', r'^\s*//'],
            'ruby': [r'^\s*#']
        }
        
        patterns = comment_patterns.get(language, [])
        for line in lines[1:4]:
            for pattern in patterns:
                if re.match(pattern, line):
                    comment = re.sub(r'^\s*[#/*\'"]+\s*', '', line).strip()
                    if comment and len(comment) > 5:
                        return comment[:100] + "..." if len(comment) > 100 else comment
        
        # Extract function name and categorize
        first_line = lines[0] if lines else ""
        name = self._extract_function_name(first_line, language)
        if name:
            return self._categorize_function_by_name(name, language)
        
        return f"{language.title()} code block"
    
    def _extract_function_name(self, line: str, language: str) -> Optional[str]:
        """Extract function name from definition line based on language."""
        patterns = {
            'python': [r'def\s+(\w+)', r'async\s+def\s+(\w+)'],
            'java': [r'(?:public|private|protected)?\s*(?:static)?\s*\w+\s+(\w+)\s*\('],
            'csharp': [r'(?:public|private|protected|internal)?\s*(?:static)?\s*\w+\s+(\w+)\s*\('],
            'javascript': [r'function\s+(\w+)', r'const\s+(\w+)\s*=', r'(\w+)\s*:\s*function'],
            'ruby': [r'def\s+(\w+)']
        }
        
        for pattern in patterns.get(language, []):
            match = re.search(pattern, line)
            if match:
                return match.group(1)
        
        return None
    
    def _categorize_function_by_name(self, name: str, language: str) -> str:
        """Categorize function by its name and language conventions."""
        name_lower = name.lower()
        
        # Common patterns across languages
        if any(word in name_lower for word in ['handler', 'lambda_handler', 'main']):
            return f"Main {language} Lambda handler function"
        elif any(word in name_lower for word in ['validate', 'check', 'verify', 'is_valid']):
            return f"{language.title()} validation function"
        elif any(word in name_lower for word in ['process', 'transform', 'convert', 'parse']):
            return f"{language.title()} processing function"
        elif any(word in name_lower for word in ['auth', 'login', 'token', 'authenticate']):
            return f"{language.title()} authentication function"
        elif any(word in name_lower for word in ['get', 'fetch', 'retrieve', 'read', 'find']):
            return f"{language.title()} data retrieval function"
        elif any(word in name_lower for word in ['save', 'store', 'create', 'insert', 'write', 'add']):
            return f"{language.title()} data storage function"
        elif any(word in name_lower for word in ['update', 'modify', 'edit', 'change']):
            return f"{language.title()} data modification function"
        elif any(word in name_lower for word in ['delete', 'remove', 'destroy', 'drop']):
            return f"{language.title()} data deletion function"
        else:
            return f"{language.title()} function: {name}"
    
    def _generate_window_summary(self, content: str, language: str) -> str:
        """Generate summary for a code window based on language."""
        lines = content.split('\n')
        non_empty_lines = [line for line in lines if line.strip()]
        
        if not non_empty_lines:
            return "Empty code block"
        
        patterns = self.language_patterns.get(language, {})
        functions = sum(1 for line in lines for pattern in patterns.get('function_patterns', []) if re.match(pattern, line))
        classes = sum(1 for line in lines for pattern in patterns.get('class_patterns', []) if re.match(pattern, line))
        
        # Language-specific return patterns
        return_patterns = {
            'python': [r'^\s*return\s+', r'^\s*yield\s+'],
            'java': [r'^\s*return\s+'],
            'csharp': [r'^\s*return\s+'],
            'javascript': [r'^\s*return\s+'],
            'ruby': [r'^\s*return\s+', r'^\s*\w+$']  # Ruby implicit returns
        }
        
        returns = sum(1 for line in lines for pattern in return_patterns.get(language, []) if re.match(pattern, line))
        
        summary_parts = []
        if functions > 0:
            summary_parts.append(f"{functions} function(s)")
        if classes > 0:
            summary_parts.append(f"{classes} class(es)")
        if returns > 0:
            summary_parts.append(f"{returns} return statement(s)")
        
        return ', '.join(summary_parts) if summary_parts else f"{len(non_empty_lines)} lines of {language} code"


# Backward compatibility aliases
CodeChunker = MultiLanguageCodeChunker