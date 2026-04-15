import ast
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FunctionDefInfo:
    id: str
    name: str
    qualname: str
    file: str
    lineno: int
    col_offset: int
    class_name: Optional[str] = None
    class_id: Optional[str] = None        # ID of the enclosing class
    end_lineno: Optional[int] = None
    source: Optional[str] = None
    decorators: List[str] = field(default_factory=list)
    is_async: bool = False
    is_static: bool = False
    is_classmethod: bool = False
    is_property: bool = False
    is_abstract: bool = False
    return_annotation: Optional[str] = None


@dataclass
class ClassDefInfo:
    id: str                                # module:ClassName (or module:Outer.Inner)
    name: str
    qualname: str
    file: str
    lineno: int
    col_offset: int
    end_lineno: Optional[int] = None
    source: Optional[str] = None
    base_classes: List[str] = field(default_factory=list)   # raw names as written
    decorators: List[str] = field(default_factory=list)
    is_abstract: bool = False              # inherits ABC / has abstractmethod
    is_protocol: bool = False              # inherits Protocol  (Python's interface)
    is_dataclass: bool = False             # decorated with @dataclass


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
class RelationshipInfo:
    """
    Structural relationship between two entities (not a call edge).

    Types:
      member_of    — function/method belongs to a class
                     source_id=func_id, target_id=class_id
      inherits_from — class inherits from another class
                     source_id=class_id, target_name=raw base name (resolved later)
      instantiates — function creates an instance of a class via Foo(...)
                     source_id=func_id, target_name=raw class name (resolved later)
    """
    type: str          # "member_of" | "inherits_from" | "instantiates"
    source_id: str
    source_type: str   # "function" | "class"
    target_name: str   # raw name as written in source (for later resolution)
    target_id: Optional[str] = None  # filled in by lineage builder after resolution
    file: str = ""
    lineno: int = 0


@dataclass
class FileParseResult:
    module: str
    path: str
    functions: List[FunctionDefInfo] = field(default_factory=list)
    classes: List[ClassDefInfo] = field(default_factory=list)
    imports: List[ImportInfo] = field(default_factory=list)
    calls: List[CallInfo] = field(default_factory=list)
    relationships: List[RelationshipInfo] = field(default_factory=list)
    # module_context: top-level imports (as source strings) + literal globals
    module_context: Dict = field(default_factory=lambda: {"imports": [], "globals": {}})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _annotation_str(ann) -> Optional[str]:
    """Return a string representation of an AST annotation node, or None."""
    if ann is None:
        return None
    try:
        return ast.unparse(ann)
    except Exception:
        return None


def _decorator_names(decorator_list: list) -> List[str]:
    """Extract decorator names/expressions from a decorator list."""
    names: List[str] = []
    for dec in decorator_list:
        try:
            names.append(ast.unparse(dec))
        except Exception:
            if isinstance(dec, ast.Name):
                names.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                names.append(dec.attr)
    return names


def _base_class_names(node: ast.ClassDef) -> List[str]:
    """Return the raw base class names as written in source (excluding 'object')."""
    names: List[str] = []
    for base in node.bases:
        try:
            name = ast.unparse(base)
        except Exception:
            name = base.id if isinstance(base, ast.Name) else ""
        if name and name != "object":
            names.append(name)
    return names


def _classify_function(node: ast.FunctionDef):
    """Return (is_static, is_classmethod, is_property, is_abstract) from decorators."""
    is_static = is_classmethod = is_property = is_abstract = False
    for dec in node.decorator_list:
        name = None
        if isinstance(dec, ast.Name):
            name = dec.id
        elif isinstance(dec, ast.Attribute):
            name = dec.attr
        elif isinstance(dec, ast.Call):
            f = dec.func
            name = f.id if isinstance(f, ast.Name) else (
                f.attr if isinstance(f, ast.Attribute) else None
            )
        if name == "staticmethod":
            is_static = True
        elif name == "classmethod":
            is_classmethod = True
        elif name == "property":
            is_property = True
        elif name == "abstractmethod":
            is_abstract = True
    return is_static, is_classmethod, is_property, is_abstract


def _classify_class(node: ast.ClassDef):
    """Return (is_abstract, is_protocol, is_dataclass) for a class definition."""
    is_abstract = is_protocol = is_dataclass = False

    for base in node.bases:
        try:
            name = ast.unparse(base)
        except Exception:
            name = base.id if isinstance(base, ast.Name) else ""
        if name in ("ABC", "abc.ABC"):
            is_abstract = True
        if name in ("Protocol", "typing.Protocol", "typing_extensions.Protocol"):
            is_protocol = True

    for kw in node.keywords:
        if kw.arg == "metaclass":
            try:
                val = ast.unparse(kw.value)
            except Exception:
                val = ""
            if "ABCMeta" in val:
                is_abstract = True

    for dec in node.decorator_list:
        try:
            name = ast.unparse(dec)
        except Exception:
            name = dec.id if isinstance(dec, ast.Name) else (
                dec.attr if isinstance(dec, ast.Attribute) else ""
            )
        # strip call args: "dataclass(frozen=True)" → "dataclass"
        bare = name.split("(")[0].rsplit(".", 1)[-1]
        if bare == "dataclass":
            is_dataclass = True

    return is_abstract, is_protocol, is_dataclass


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------

class _AstVisitor(ast.NodeVisitor):
    def __init__(self, module: str, path: str, source_text: str = ""):
        self.module = module
        self.path = path
        self._source_lines = source_text.splitlines()
        self.result = FileParseResult(module=module, path=path)

        # Tracks both class and function names for qualname/ID building.
        self._scope_stack: List[str] = []

        # Tracks only class names (not functions) for class ID building.
        # Needed so nested-class IDs don't include intermediate method names.
        self._class_scope_stack: List[str] = []

        # Full computed ID of each enclosing function (pushed/popped by visit_FunctionDef).
        self._func_id_stack: List[str] = []

        # Current innermost class name (for class_name field on FunctionDefInfo).
        self._current_class: Optional[str] = None
        # Current innermost class ID.
        self._current_class_id: Optional[str] = None

    # -- Qualname / ID builders ------------------------------------------

    def _current_qualname(self, name: str) -> str:
        if self._scope_stack:
            return ".".join([self.module] + self._scope_stack + [name])
        return ".".join([self.module, name])

    def _make_class_id(self, extra: str = "") -> str:
        """Build class ID from current class scope stack + optional extra name."""
        parts = self._class_scope_stack + ([extra] if extra else [])
        return f"{self.module}:{'.'.join(parts)}"

    @staticmethod
    def _param_sig(args: ast.arguments) -> str:
        """
        Compact parameter signature (bare names only, no types/defaults).
        Excludes self/cls. Includes *args/**kwargs with their * prefix.
        Examples:
          def hey(x, y)            -> "(x,y)"
          def process(self, data)  -> "(data)"
          def run(*args, **kwargs) -> "(*args,**kwargs)"
        """
        params: List[str] = []
        for arg in args.args:
            if arg.arg not in ("self", "cls"):
                params.append(arg.arg)
        if args.vararg:
            params.append(f"*{args.vararg.arg}")
        for arg in args.kwonlyargs:
            params.append(arg.arg)
        if args.kwarg:
            params.append(f"**{args.kwarg.arg}")
        return f"({','.join(params)})"

    def _current_function_id(self, name: str, node: ast.FunctionDef) -> str:
        """
        Stable, unique ID for a function definition.
        Format: module:QualName(param1,param2)
        Examples:
          analytics.pipeline:DataProcessor.summarize(data,window)
          analytics.pipeline:process(x,y)
          utils:_helper(*args,**kwargs)
        """
        sig = self._param_sig(node.args)
        if self._scope_stack:
            suffix = ".".join(self._scope_stack + [name])
        else:
            suffix = name
        return f"{self.module}:{suffix}{sig}"

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
            elif isinstance(cur, ast.Call):
                # Handle super().method() — emit "super().attr" so the resolver
                # can look up the method in parent classes.
                func = cur.func
                if isinstance(func, ast.Name) and func.id == "super":
                    parts.append("super()")
            parts.reverse()
            return ".".join(parts)
        return ast.unparse(node) if hasattr(ast, "unparse") else type(node).__name__

    # -- Overload detection -----------------------------------------------

    @staticmethod
    def _is_overload_stub(node: ast.FunctionDef) -> bool:
        """Return True if the function is a @typing.overload stub."""
        for dec in node.decorator_list:
            name = dec.id if isinstance(dec, ast.Name) else (
                dec.attr if isinstance(dec, ast.Attribute) else None
            )
            if name == "overload":
                return True
        return False

    # -- Visitors ---------------------------------------------------------

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
        end_lineno = getattr(node, "end_lineno", None) or node.lineno

        base_classes = _base_class_names(node)
        decorators = _decorator_names(node.decorator_list)
        is_abstract, is_protocol, is_dataclass = _classify_class(node)

        # Build class ID from class-only scope (excludes enclosing function names)
        self._class_scope_stack.append(node.name)
        class_id = self._make_class_id()

        source = "\n".join(self._source_lines[node.lineno - 1 : end_lineno])

        cls_info = ClassDefInfo(
            id=class_id,
            name=node.name,
            qualname=qualname,
            file=self.path,
            lineno=node.lineno,
            col_offset=node.col_offset,
            end_lineno=end_lineno,
            source=source,
            base_classes=base_classes,
            decorators=decorators,
            is_abstract=is_abstract,
            is_protocol=is_protocol,
            is_dataclass=is_dataclass,
        )
        self.result.classes.append(cls_info)

        # inherits_from relationships (one per base class)
        for base_name in base_classes:
            # Skip stdlib/typing bases that are never in our repo
            if base_name in ("ABC", "abc.ABC", "Protocol", "typing.Protocol",
                             "typing_extensions.Protocol", "Enum", "IntEnum",
                             "str", "int", "float", "dict", "list", "tuple"):
                continue
            self.result.relationships.append(
                RelationshipInfo(
                    type="inherits_from",
                    source_id=class_id,
                    source_type="class",
                    target_name=base_name,
                    file=self.path,
                    lineno=node.lineno,
                )
            )

        # Recurse into the class body with updated state
        prev_class = self._current_class
        prev_class_id = self._current_class_id
        self._current_class = node.name
        self._current_class_id = class_id
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()
        self._class_scope_stack.pop()
        self._current_class = prev_class
        self._current_class_id = prev_class_id

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Skip @overload stubs — type-hint declarations, not real implementations
        if self._is_overload_stub(node):
            return

        qualname = self._current_qualname(node.name)
        func_id = self._current_function_id(node.name, node)
        end_lineno = getattr(node, "end_lineno", None) or node.lineno
        source = "\n".join(self._source_lines[node.lineno - 1 : end_lineno])

        decorators = _decorator_names(node.decorator_list)
        is_static, is_classmethod, is_property, is_abstract = _classify_function(node)
        is_async = isinstance(node, ast.AsyncFunctionDef)
        return_annotation = _annotation_str(getattr(node, "returns", None))

        self.result.functions.append(
            FunctionDefInfo(
                id=func_id,
                name=node.name,
                qualname=qualname,
                file=self.path,
                lineno=node.lineno,
                col_offset=node.col_offset,
                class_name=self._current_class,
                class_id=self._current_class_id,
                end_lineno=end_lineno,
                source=source,
                decorators=decorators,
                is_async=is_async,
                is_static=is_static,
                is_classmethod=is_classmethod,
                is_property=is_property,
                is_abstract=is_abstract,
                return_annotation=return_annotation,
            )
        )

        # member_of relationship — function belongs to a class
        if self._current_class_id:
            self.result.relationships.append(
                RelationshipInfo(
                    type="member_of",
                    source_id=func_id,
                    source_type="function",
                    target_name=self._current_class or "",
                    target_id=self._current_class_id,
                    file=self.path,
                    lineno=node.lineno,
                )
            )

        self._scope_stack.append(node.name)
        self._func_id_stack.append(func_id)
        self.generic_visit(node)
        self._scope_stack.pop()
        self._func_id_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    def visit_Call(self, node: ast.Call) -> None:
        func_expr = self._get_func_expr(node.func)

        if self._func_id_stack:
            caller_id = self._func_id_stack[-1]

            self.result.calls.append(
                CallInfo(
                    caller_id=caller_id,
                    func_expr=func_expr,
                    file=self.path,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                )
            )

            # Detect class instantiation: Foo(...) where Foo starts with uppercase
            # and is not a builtin. Emitted as an "instantiates" relationship.
            # The lineage builder resolves the class name to a real class ID.
            bare = func_expr.rsplit(".", 1)[-1]
            if bare and bare[0].isupper() and bare not in (
                "True", "False", "None", "Exception", "ValueError",
                "TypeError", "KeyError", "RuntimeError", "StopIteration",
            ):
                self.result.relationships.append(
                    RelationshipInfo(
                        type="instantiates",
                        source_id=caller_id,
                        source_type="function",
                        target_name=func_expr,
                        file=self.path,
                        lineno=node.lineno,
                    )
                )

        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_python_file(root: str, file_path: str) -> Optional[FileParseResult]:
    """Parse a single Python file into our intermediate representation."""
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

    EXCLUDED_DIRS = {
        ".venv", "venv", "env", ".git", "__pycache__",
        ".pytest_cache", ".idea", ".vscode", "node_modules"
    }

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".")]

        for name in filenames:
            if not name.endswith(".py"):
                continue
            full_path = os.path.join(dirpath, name)
            parsed = parse_python_file(root, full_path)
            if parsed:
                results[parsed.path] = parsed

    logger.info(
        "Parsed %d Python files under %s — %d functions, %d classes, %d relationships",
        len(results),
        root,
        sum(len(r.functions) for r in results.values()),
        sum(len(r.classes) for r in results.values()),
        sum(len(r.relationships) for r in results.values()),
    )
    return results
