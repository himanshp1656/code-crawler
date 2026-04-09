import ast
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FunctionDefInfo:
    id: str
    name: str
    qualname: str
    file: str
    lineno: int
    col_offset: int
    class_name: Optional[str] = None
    end_lineno: Optional[int] = None
    source: Optional[str] = None


@dataclass
class ClassDefInfo:
    name: str
    qualname: str
    file: str
    lineno: int
    col_offset: int


@dataclass
class ImportInfo:
    type: str  # "import" or "from"
    module: Optional[str]
    name: str
    asname: Optional[str]


@dataclass
class CallInfo:
    caller_id: str
    func_expr: str
    file: str
    lineno: int
    col_offset: int


@dataclass
class FileParseResult:
    module: str
    path: str
    functions: List[FunctionDefInfo] = field(default_factory=list)
    classes: List[ClassDefInfo] = field(default_factory=list)
    imports: List[ImportInfo] = field(default_factory=list)
    calls: List[CallInfo] = field(default_factory=list)
    # module_context: top-level imports (as source strings) + literal globals
    module_context: Dict = field(default_factory=lambda: {"imports": [], "globals": {}})


def _module_name_from_path(root: str, file_path: str) -> str:
    rel = os.path.relpath(file_path, root)
    if rel.endswith(".py"):
        rel = rel[:-3]
    parts: List[str] = []
    for part in rel.split(os.sep):
        if part == "__init__":
            continue
        parts.append(part)
    return ".".join(parts)


class _AstVisitor(ast.NodeVisitor):
    def __init__(self, module: str, path: str, source_text: str = ""):
        self.module = module
        self.path = path
        self._source_lines = source_text.splitlines()
        self.result = FileParseResult(module=module, path=path)
        self._scope_stack: List[str] = []
        self._current_class: Optional[str] = None

    def _current_qualname(self, name: str) -> str:
        if self._scope_stack:
            return ".".join([self.module] + self._scope_stack + [name])
        return ".".join([self.module, name])

    def _current_function_id(self, name: str) -> str:
        if self._scope_stack:
            suffix = ".".join(self._scope_stack + [name])
        else:
            suffix = name
        return f"{self.module}:{suffix}"
    def _get_func_expr(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parts: List[str] = []
            cur: ast.AST = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            parts.reverse()
            return ".".join(parts)
        return ast.unparse(node) if hasattr(ast, "unparse") else type(node).__name__

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.result.imports.append(
                ImportInfo(
                    type="import",
                    module=alias.name,
                    name=alias.name,
                    asname=alias.asname,
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module
        for alias in node.names:
            self.result.imports.append(
                ImportInfo(
                    type="from",
                    module=module,
                    name=alias.name,
                    asname=alias.asname,
                )
            )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = self._current_qualname(node.name)
        self.result.classes.append(
            ClassDefInfo(
                name=node.name,
                qualname=qualname,
                file=self.path,
                lineno=node.lineno,
                col_offset=node.col_offset,
            )
        )
        prev_class = self._current_class
        self._current_class = node.name
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()
        self._current_class = prev_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        qualname = self._current_qualname(node.name)
        func_id = self._current_function_id(node.name)
        end_lineno = getattr(node, "end_lineno", None) or node.lineno
        source = "\n".join(self._source_lines[node.lineno - 1 : end_lineno])
        self.result.functions.append(
            FunctionDefInfo(
                id=func_id,
                name=node.name,
                qualname=qualname,
                file=self.path,
                lineno=node.lineno,
                col_offset=node.col_offset,
                class_name=self._current_class,
                end_lineno=end_lineno,
                source=source,
            )
        )
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        # handle async functions same as normal
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    def visit_Call(self, node: ast.Call) -> None:
        func_expr = self._get_func_expr(node.func)

        if self._scope_stack:
            suffix = ".".join(self._scope_stack)
            caller_id = f"{self.module}:{suffix}"

            self.result.calls.append(
                CallInfo(
                    caller_id=caller_id,
                    func_expr=func_expr,
                    file=self.path,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                )
            )

        self.generic_visit(node)

def parse_python_file(root: str, file_path: str) -> Optional[FileParseResult]:
    """
    Parse a single Python file into our intermediate representation.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as exc:
        logger.warning("Skipping file with syntax error: %s (%s)", file_path, exc)
        return None
    except OSError as exc:
        logger.warning("Skipping unreadable file: %s (%s)", file_path, exc)
        return None

    module = _module_name_from_path(root, file_path)
    visitor = _AstVisitor(module=module, path=os.path.relpath(file_path, root), source_text=source)
    visitor.visit(tree)

    # Extract module-level context: import statements + literal assignments
    mc_imports: List[str] = []
    mc_globals: Dict = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mc_imports.append(ast.unparse(node))
        elif isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    mc_globals[node.targets[0].id] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                try:
                    mc_globals[node.target.id] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
    visitor.result.module_context = {"imports": mc_imports, "globals": mc_globals}
    return visitor.result


def parse_repository(root: str) -> Dict[str, FileParseResult]:
    """
    Recursively parse all .py files under the given root directory,
    skipping virtual environments and hidden folders.
    """
    results: Dict[str, FileParseResult] = {}
    
    # Define directories you want to ignore
    EXCLUDED_DIRS = {
        ".venv", "venv", "env", ".git", "__pycache__", 
        ".pytest_cache", ".idea", ".vscode", "node_modules"
    }

    # dirnames is the second yield value from os.walk
    for dirpath, dirnames, filenames in os.walk(root):
        
        # 1. Modify dirnames IN-PLACE to skip excluded directories
        # This prevents os.walk from even looking inside them
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith('.')]

        for name in filenames:
            if not name.endswith(".py"):
                continue
                
            full_path = os.path.join(dirpath, name)
            
            # 2. Optional: Skip huge generated files or __init__.py if desired
            parsed = parse_python_file(root, full_path)
            if parsed:
                results[parsed.path] = parsed

    logger.info("Parsed %d Python files under %s", len(results), root)
    return results