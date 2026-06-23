"""AST parser — extracts function/class signatures, imports, and call chains.

Uses tree-sitter for multi-language support. Falls back to regex-based
extraction when tree-sitter is not installed or for unsupported languages.
"""
import logging
import re

logger = logging.getLogger(__name__)


class ASTParser:
    """Extracts code structure information from source files.

    Supports Python (tree-sitter), JavaScript/TypeScript, Go, and Java.
    Falls back to regex patterns for languages without tree-sitter grammars.
    """

    def __init__(self):
        self._parser = None
        self._language = None

    def _init_treesitter(self):
        """Lazy-init tree-sitter for Python (most common case)."""
        if self._parser is not None:
            return
        try:
            import tree_sitter_python as tspython
            import tree_sitter
            self._language = tree_sitter.Language(tspython.language())
            self._parser = tree_sitter.Parser(self._language)
            logger.debug("tree-sitter Python parser initialized")
        except ImportError:
            logger.debug("tree-sitter not installed, using regex fallback")
        except Exception as e:
            logger.debug("tree-sitter init failed: %s, using regex fallback", e)

    def extract_context(self, source_code: str, language: str = "python") -> dict:
        """Extract code structure from source code.

        Returns:
            dict with keys: functions, classes, imports, complexity, call_chains
        """
        self._init_treesitter()

        if self._parser and language == "python":
            return self._extract_with_treesitter(source_code)
        else:
            return self._extract_with_regex(source_code, language)

    def _extract_with_treesitter(self, source_code: str) -> dict:
        """Extract code structure using tree-sitter (Python)."""
        try:
            tree = self._parser.parse(bytes(source_code, "utf-8"))
            root = tree.root_node

            functions = []
            classes = []
            imports = []
            call_chains = []

            self._walk_node(root, source_code, functions, classes, imports, call_chains)

            # Calculate rough complexity: count branches and loops
            complexity = self._count_complexity(root)

            return {
                "functions": functions,
                "classes": classes,
                "imports": imports,
                "call_chains": call_chains[:20],  # limit
                "complexity": complexity,
            }
        except Exception as e:
            logger.debug("tree-sitter parsing failed: %s", e)
            return self._extract_with_regex(source_code, "python")

    def _walk_node(self, node, source, functions, classes, imports, call_chains):
        """Walk the AST and collect structure information."""
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = source[name_node.start_byte:name_node.end_byte]
                params_node = node.child_by_field_name("parameters")
                params = source[params_node.start_byte:params_node.end_byte] if params_node else "()"
                functions.append(f"{name}{params}")
        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = source[name_node.start_byte:name_node.end_byte]
                body = node.child_by_field_name("body")
                method_count = sum(
                    1 for c in (body.children if body else [])
                    if c.type == "function_definition"
                )
                classes.append(f"{name} ({method_count} methods)")
        elif node.type in ("import_statement", "import_from_statement"):
            imports.append(source[node.start_byte:node.end_byte].strip())
        elif node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node:
                func_name = source[func_node.start_byte:func_node.end_byte]
                # Get caller context: which function contains this call?
                parent = node.parent
                while parent and parent.type != "function_definition":
                    parent = parent.parent
                caller = None
                if parent:
                    name_node = parent.child_by_field_name("name")
                    if name_node:
                        caller = source[name_node.start_byte:name_node.end_byte]

                call_chains.append({
                    "caller": caller or "(top-level)",
                    "callee": func_name,
                })

        for child in node.children:
            self._walk_node(child, source, functions, classes, imports, call_chains)

    def _count_complexity(self, root) -> int:
        """Count cyclomatic complexity indicators."""
        count = 1
        for node in root.children:
            if node.type in ("if_statement", "elif_clause", "for_statement",
                             "while_statement", "except_clause", "match_case"):
                count += 1
                count += self._count_complexity(node)
            elif node.type in ("boolean_operator",):
                count += 1
                count += self._count_complexity(node)
            else:
                count += self._count_complexity(node)
        return count

    def find_functions_for_lines(
        self, source_code: str, line_numbers: list[int], language: str = "python"
    ) -> str:
        """Extract the full text of functions that contain the given lines.

        Returns a concatenated string of complete function bodies, or empty string
        if no functions found. Used to give reviewers full function context for
        the lines changed in a diff.
        """
        self._init_treesitter()

        if self._parser and language == "python":
            return self._find_functions_treesitter(source_code, line_numbers)
        else:
            return self._find_functions_regex(source_code, line_numbers, language)

    def _find_functions_treesitter(
        self, source_code: str, line_numbers: list[int]
    ) -> str:
        """Use tree-sitter to locate and extract full function bodies."""
        try:
            tree = self._parser.parse(bytes(source_code, "utf-8"))
            root = tree.root_node
            line_set = set(line_numbers)

            bodies = []
            self._collect_function_bodies(root, source_code, line_set, bodies)
            return "\n\n".join(bodies)
        except Exception as e:
            logger.debug("tree-sitter function extraction failed: %s", e)
            return ""

    def _collect_function_bodies(
        self, node, source: str, target_lines: set[int], bodies: list[str]
    ):
        """Walk AST, collecting full text of functions that intersect target_lines."""
        if node.type in ("function_definition", "method_definition"):
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            func_lines = set(range(start_line, end_line + 1))
            if func_lines & target_lines:
                name_node = node.child_by_field_name("name")
                name = source[name_node.start_byte:name_node.end_byte] if name_node else "?"
                body = source[node.start_byte:node.end_byte]
                bodies.append(f"# --- function: {name} (lines {start_line}-{end_line}) ---\n{body}")
                return  # Don't recurse into matched function
        for child in node.children:
            self._collect_function_bodies(child, source, target_lines, bodies)

    def _find_functions_regex(
        self, source_code: str, line_numbers: list[int], language: str
    ) -> str:
        """Regex-based function extraction for non-Python languages."""
        lines = source_code.split("\n")
        target = set(line_numbers)
        bodies = []

        # Detect language-specific function patterns
        func_start_pat = {
            "php": r'^\s*(?:public\s+|private\s+|protected\s+)?function\s+(\w+)',
            "javascript": r'^\s*(?:async\s+)?function\s+(\w+)',
            "typescript": r'^\s*(?:async\s+)?function\s+(\w+)',
            "go": r'^\s*func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)',
            "java": r'^\s*(?:public\s+|private\s+|protected\s+)?\w+\s+(\w+)\s*\([^)]*\)\s*\{',
        }.get(language, r'^\s*(?:def\s+|function\s+)(\w+)')

        # Find function boundaries
        i = 0
        while i < len(lines):
            m = re.match(func_start_pat, lines[i])
            if m:
                name = m.group(1)
                j = i
                # Find matching closing brace (simple brace counting)
                depth = 0
                started = False
                while j < len(lines):
                    depth += lines[j].count("{") - lines[j].count("}")
                    if "{" in lines[j]:
                        started = True
                    if started and depth == 0:
                        break
                    j += 1
                end = min(j + 1, len(lines))
                func_lines = set(range(i + 1, end + 1))
                if func_lines & target:
                    body = "\n".join(lines[i:end])
                    bodies.append(f"# --- function: {name} (lines {i+1}-{end}) ---\n{body}")
                i = end
            else:
                i += 1

        return "\n\n".join(bodies)

    def _extract_with_regex(self, source_code: str, language: str) -> dict:
        """Fallback regex-based extraction for any language."""
        functions = []
        classes = []
        imports = []

        # Python function definitions
        func_matches = re.findall(
            r'^\s*(?:async\s+)?def\s+(\w+)\s*\([^)]*\)', source_code, re.MULTILINE
        )
        functions.extend(func_matches)

        # Python class definitions
        class_matches = re.findall(
            r'^\s*class\s+(\w+)', source_code, re.MULTILINE
        )
        classes.extend(f"{c}" for c in class_matches)

        # Python imports
        import_matches = re.findall(
            r'^(?:import\s+.*|from\s+\S+\s+import\s+.*)', source_code, re.MULTILINE
        )
        imports.extend(import_matches)

        # Function calls (Python)
        call_matches = re.findall(
            r'(\w+)\s*\(', source_code
        )
        call_chains = [{"callee": c, "caller": "?"} for c in call_matches[:30]]

        return {
            "functions": functions,
            "classes": classes,
            "imports": imports,
            "call_chains": call_chains,
            "complexity": len(re.findall(r'\b(if|for|while|except)\b', source_code)) + 1,
        }
