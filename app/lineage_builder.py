import logging
import os
import json
from typing import Dict, List, Any, Optional

from .python_ast_parser import CallInfo, ClassDefInfo, FileParseResult, FunctionDefInfo, ImportInfo

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "output"


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------

def _build_function_index(files: Dict[str, FileParseResult]) -> Dict[str, FunctionDefInfo]:
    index: Dict[str, FunctionDefInfo] = {}
    for fr in files.values():
        for fn in fr.functions:
            index[fn.id] = fn
    return index


def _build_name_index(files: Dict[str, FileParseResult]) -> Dict[str, List[FunctionDefInfo]]:
    name_index: Dict[str, List[FunctionDefInfo]] = {}
    for fr in files.values():
        for fn in fr.functions:
            name_index.setdefault(fn.name, []).append(fn)
    return name_index


def _build_class_index(files: Dict[str, FileParseResult]) -> Dict[str, ClassDefInfo]:
    """id → ClassDefInfo"""
    index: Dict[str, ClassDefInfo] = {}
    for fr in files.values():
        for cls in fr.classes:
            index[cls.id] = cls
    return index


def _build_class_name_index(files: Dict[str, FileParseResult]) -> Dict[str, List[ClassDefInfo]]:
    """short name → [ClassDefInfo]"""
    name_index: Dict[str, List[ClassDefInfo]] = {}
    for fr in files.values():
        for cls in fr.classes:
            name_index.setdefault(cls.name, []).append(cls)
    return name_index


def _build_import_maps(files: Dict[str, FileParseResult]) -> Dict[str, Dict[str, str]]:
    """For each file, build alias → fully-qualified target."""
    import_maps: Dict[str, Dict[str, str]] = {}
    for path, fr in files.items():
        alias_map: Dict[str, str] = {}
        for imp in fr.imports:
            if imp.type == "import":
                alias = imp.asname or imp.name.split(".")[-1]
                alias_map[alias] = imp.module or imp.name
            elif imp.type == "from":
                alias = imp.asname or imp.name
                if imp.module:
                    alias_map[alias] = f"{imp.module}.{imp.name}"
                else:
                    alias_map[alias] = imp.name
        import_maps[path] = alias_map
    return import_maps


def _build_star_imports(files: Dict[str, FileParseResult]) -> Dict[str, List[str]]:
    """Returns {file_path: [module, ...]} for files that have 'from module import *'."""
    result: Dict[str, List[str]] = {}
    for path, fr in files.items():
        for imp in fr.imports:
            if imp.name == "*" and imp.module:
                result.setdefault(path, []).append(imp.module)
    return result


def _build_method_index(func_index: Dict[str, FunctionDefInfo]) -> Dict[str, Dict[str, List[str]]]:
    """Returns {class_id: {method_name: [func_id, ...]}} for O(1) method lookup."""
    idx: Dict[str, Dict[str, List[str]]] = {}
    for fn in func_index.values():
        if fn.class_id:
            idx.setdefault(fn.class_id, {}).setdefault(fn.name, []).append(fn.id)
    return idx


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------

def _resolve_callee(
    call: CallInfo,
    file_result: FileParseResult,
    func_index: Dict[str, FunctionDefInfo],
    name_index: Dict[str, List[FunctionDefInfo]],
    import_maps: Dict[str, Dict[str, str]],
    class_index: Dict[str, "ClassDefInfo"],
    class_name_index: Dict[str, List["ClassDefInfo"]],
    star_imports: Dict[str, List[str]],
    method_index: Dict[str, Dict[str, List[str]]],
    class_method_lookup: Dict[str, List[str]],
) -> Optional[str]:
    """Best-effort resolution from call.func_expr to a function ID."""
    expr = call.func_expr
    imports_for_file = import_maps.get(file_result.path, {})

    def find_by_module_and_name(module: str, name: str) -> Optional[str]:
        candidates = [
            fn for fn in func_index.values()
            if fn.file and fn.qualname.startswith(f"{module}.") and fn.name == name
        ]
        if len(candidates) == 1:
            return candidates[0].id
        return None

    def _simple_class_name(raw: str) -> str:
        """Extract the bare class name from either a raw name or a class ID (module:Name)."""
        if ":" in raw:
            raw = raw.split(":", 1)[1]   # "module:ClassName" → "ClassName"
        return raw.rsplit(".", 1)[-1]    # "Outer.Inner" → "Inner"

    def collect_ancestor_class_ids(start_class_id: str) -> List[str]:
        """BFS through class_index returning all reachable ancestor class IDs in order."""
        result: List[str] = []
        queue = [start_class_id]
        visited: set = set()
        while queue:
            cid = queue.pop(0)
            if cid in visited:
                continue
            visited.add(cid)
            result.append(cid)
            cls_info = class_index.get(cid)
            if not cls_info:
                continue
            cls_file = cls_info.file or file_result.path
            for base_name in cls_info.base_classes:
                parent_id = _resolve_class(
                    base_name, cls_file, import_maps, class_index, class_name_index,
                )
                if parent_id and parent_id not in visited:
                    queue.append(parent_id)
        return result

    def resolve_via_hierarchy(start_class_id: str, method_name: str, skip_self: bool = False) -> Optional[str]:
        """Walk the full class hierarchy and look up ClassName.method_name in the
        flat class_method_lookup for each ancestor in BFS order.
        Falls back to method_index (same-repo BFS) and then unique global name.
        """
        ancestor_ids = collect_ancestor_class_ids(start_class_id)
        if skip_self and ancestor_ids:
            ancestor_ids = ancestor_ids[1:]  # super() — skip the class itself

        # 1. Try class_method_lookup in hierarchy order (covers cross-repo)
        for cid in ancestor_ids:
            cls_info = class_index.get(cid)
            if not cls_info:
                continue
            key = f"{cls_info.name}.{method_name}"
            ids = class_method_lookup.get(key, [])
            if len(ids) == 1:
                return ids[0]

        # 2. Also try with raw ancestor names from base_classes strings (handles
        #    unresolved cross-repo parents whose class_index entry is missing)
        seen_names: set = set()
        for cid in collect_ancestor_class_ids(start_class_id):
            cls_info = class_index.get(cid)
            if not cls_info:
                continue
            for base_raw in cls_info.base_classes:
                simple = _simple_class_name(base_raw)
                if simple in seen_names:
                    continue
                seen_names.add(simple)
                ids = class_method_lookup.get(f"{simple}.{method_name}", [])
                if len(ids) == 1:
                    return ids[0]

        # 3. Unique global name fallback
        candidates = name_index.get(method_name, [])
        if len(candidates) == 1:
            return candidates[0].id
        return None

    parts = expr.split(".")

    # ── Case 0: self.method() / cls.method() ─────────────────────────────────
    if parts[0] in ("self", "cls") and len(parts) == 2:
        method_name = parts[1]
        caller = func_index.get(call.caller_id)
        if caller and caller.class_id:
            return resolve_via_hierarchy(caller.class_id, method_name)
        candidates = name_index.get(method_name, [])
        if len(candidates) == 1:
            return candidates[0].id
        return None

    # ── Case 0b: self.attr.method() — chained access, resolve last segment ──
    if parts[0] in ("self", "cls") and len(parts) >= 3:
        short = parts[-1]
        candidates = name_index.get(short, [])
        if len(candidates) == 1:
            return candidates[0].id
        return None

    # ── Case 0c: super().method() — skip self class, start from parents ──────
    if parts[0] == "super()" and len(parts) == 2:
        method_name = parts[1]
        caller = func_index.get(call.caller_id)
        if caller and caller.class_id:
            return resolve_via_hierarchy(caller.class_id, method_name, skip_self=True)
        return None

    # ── Case 1: simple name "foo" ────────────────────────────────────────────
    if "." not in expr:
        # Imported alias?
        if expr in imports_for_file:
            target = imports_for_file[expr]
            if "." in target:
                module, name = target.rsplit(".", 1)
                resolved = find_by_module_and_name(module, name)
                if resolved:
                    return resolved
            else:
                resolved = find_by_module_and_name(target, expr)
                if resolved:
                    return resolved

        # Same file first
        same_file = [
            fn for fn in func_index.values()
            if fn.file == file_result.path and fn.name == expr
        ]
        if len(same_file) == 1:
            return same_file[0].id

        # Unique across project
        candidates = name_index.get(expr, [])
        if len(candidates) == 1:
            return candidates[0].id

        # Disambiguation: prefer same module
        if candidates:
            same_module = [
                fn for fn in candidates
                if fn.qualname.startswith(file_result.module + ".")
            ]
            if len(same_module) == 1:
                return same_module[0].id

        # Star import fallback
        for star_module in star_imports.get(file_result.path, []):
            star_candidates = [
                fn for fn in func_index.values()
                if fn.qualname.startswith(star_module + ".") and fn.name == expr
            ]
            if len(star_candidates) == 1:
                return star_candidates[0].id

        return None

    # ── Case 2: dotted expression "alias.foo" / "pkg.mod.func" ──────────────
    head, *rest = parts

    # head is an import alias?
    if head in imports_for_file:
        target = imports_for_file[head]
        module = target
        name = rest[-1] if rest else head
        resolved = find_by_module_and_name(module, name)
        if resolved:
            return resolved

    # head is a module path?
    module_path = head
    if rest:
        candidate_name = rest[-1]
        resolved = find_by_module_and_name(module_path, candidate_name)
        if resolved:
            return resolved

    # Fallback: treat last segment as short name with same-file preference
    short = parts[-1]
    candidates = name_index.get(short, [])
    if len(candidates) == 1:
        return candidates[0].id
    if candidates:
        same_file = [fn for fn in candidates if fn.file == file_result.path]
        if len(same_file) == 1:
            return same_file[0].id

    return None


def _resolve_class(
    target_name: str,
    file_path: str,
    import_maps: Dict[str, Dict[str, str]],
    class_index: Dict[str, ClassDefInfo],
    class_name_index: Dict[str, List[ClassDefInfo]],
) -> Optional[str]:
    """Resolve a raw class name (as written in source) to a class ID."""
    # Already a known class ID?
    if target_name in class_index:
        return target_name

    imports_for_file = import_maps.get(file_path, {})

    if "." not in target_name:
        # Check import alias
        if target_name in imports_for_file:
            target = imports_for_file[target_name]
            if "." in target:
                module, name = target.rsplit(".", 1)
                candidate_id = f"{module}:{name}"
                if candidate_id in class_index:
                    return candidate_id
        # Short name lookup
        candidates = class_name_index.get(target_name, [])
        if len(candidates) == 1:
            return candidates[0].id
        return None

    # Dotted name e.g. "module.ClassName"
    parts = target_name.rsplit(".", 1)
    if len(parts) == 2:
        module, name = parts
        # Module might be an import alias
        if module in imports_for_file:
            real_module = imports_for_file[module]
            candidate_id = f"{real_module}:{name}"
            if candidate_id in class_index:
                return candidate_id
        # Direct module:Name
        candidate_id = f"{module}:{name}"
        if candidate_id in class_index:
            return candidate_id
        # Short name fallback
        candidates = class_name_index.get(name, [])
        if len(candidates) == 1:
            return candidates[0].id

    return None


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_lineage(
    files: Dict[str, FileParseResult],
    workflow_id: str,
    run_id: str,
    cross_repo_stubs: Optional[List[dict]] = None,
) -> List[dict]:
    """
    Build the full lineage graph — functions AND classes — as a flat list of assets.

    Function assets have:
        node_type="function", class_id (enclosing class or null),
        downstream_ids (callers), relationships (instantiates etc.)

    Class assets have:
        node_type="class", base_class_ids (resolved parent class IDs),
        downstream_ids=[], relationships=[]
    """
    func_index = _build_function_index(files)
    name_index = _build_name_index(files)
    class_index = _build_class_index(files)
    class_name_index = _build_class_name_index(files)
    import_maps = _build_import_maps(files)
    star_imports = _build_star_imports(files)
    method_index = _build_method_index(func_index)

    # --- Augment indexes with cross-repo stubs (for self/super resolution) ---
    # Stubs are added to indexes only — they never appear in the returned assets.
    cross_repo_ids: set = set()
    if cross_repo_stubs:
        for a in cross_repo_stubs:
            cross_repo_ids.add(a["id"])
            if a["node_type"] == "function":
                short_name = a["name"].rsplit(".", 1)[-1]
                cid = a.get("class_id")
                cname = cid.split(":")[-1] if cid and ":" in cid else cid
                fn = FunctionDefInfo(
                    id=a["id"],
                    name=short_name,
                    qualname=a["name"],
                    file=a.get("file") or "",
                    lineno=0,
                    col_offset=0,
                    class_id=cid,
                    class_name=cname,
                )
                func_index[fn.id] = fn
                name_index.setdefault(fn.name, []).append(fn)
                if cid:
                    method_index.setdefault(cid, {}).setdefault(fn.name, []).append(fn.id)
            elif a["node_type"] == "class":
                short_name = a["name"].rsplit(".", 1)[-1]
                # Use resolved base_class_ids as base_classes — _resolve_class finds them
                # directly via class_index because they ARE the class IDs.
                cls = ClassDefInfo(
                    id=a["id"],
                    name=short_name,
                    qualname=a["name"],
                    file=a.get("file") or "",
                    lineno=0,
                    col_offset=0,
                    base_classes=a.get("base_class_ids") or [],
                )
                class_index[cls.id] = cls
                class_name_index.setdefault(cls.name, []).append(cls)
        logger.info("build_lineage: augmented indexes with %d cross-repo stubs", len(cross_repo_stubs))

    # Build flat ClassName.method_name → [func_id] lookup across ALL functions
    # (current repo + cross-repo). Used in resolve_via_hierarchy for self/super calls.
    class_method_lookup: Dict[str, List[str]] = {}
    for fn in func_index.values():
        if fn.class_name:
            key = f"{fn.class_name}.{fn.name}"
            class_method_lookup.setdefault(key, []).append(fn.id)

    assets: Dict[str, dict] = {}
    downstream_sets: Dict[str, set] = {}
    upstream_sets: Dict[str, set] = {}
    file_results_by_path = {fr.path: fr for fr in files.values()}

    # --- Class assets ---
    for fr in files.values():
        for cls in fr.classes:
            imports_for_file = import_maps.get(fr.path, {})
            base_class_ids: List[str] = []
            unresolved_bases: List[dict] = []

            for base_name in cls.base_classes:
                resolved = _resolve_class(
                    base_name, fr.path, import_maps, class_index, class_name_index
                )
                if resolved:
                    base_class_ids.append(resolved)
                else:
                    # Build qualified key using import map for cross-repo resolution
                    # e.g. "SqlMetadataExtractor" → "application_sdk.templates.SqlMetadataExtractor"
                    qualified_key = imports_for_file.get(base_name, base_name)
                    unresolved_bases.append({
                        "name": base_name,
                        "qualified_key": qualified_key,
                    })

            assets[cls.id] = {
                "id": cls.id,
                "node_type": "class",
                "name": cls.qualname,
                "file": cls.file,
                "lineno": cls.lineno,
                "end_lineno": cls.end_lineno,
                "source": cls.source,
                "class_id": None,
                "base_class_ids": sorted(set(base_class_ids)),
                "unresolved_bases": unresolved_bases,
                "downstream_ids": [],
                "upstream_ids": [],
                "relationships": [],
                "module_context": fr.module_context,
            }

    # --- Function assets (current repo only — skip cross-repo stubs) ---
    for fn in func_index.values():
        if fn.id in cross_repo_ids:
            continue
        fr = file_results_by_path.get(fn.file)
        assets[fn.id] = {
            "id": fn.id,
            "node_type": "function",
            "name": fn.qualname,
            "file": fn.file,
            "lineno": fn.lineno,
            "end_lineno": fn.end_lineno,
            "source": fn.source,
            "class_id": fn.class_id,
            "base_class_ids": [],
            "downstream_ids": [],
            "upstream_ids": [],
            "relationships": [],
            "module_context": fr.module_context if fr else {"imports": [], "globals": {}},
        }
        downstream_sets[fn.id] = set()
        upstream_sets[fn.id] = set()

    # --- Call edges (function → function) ---
    for fr in files.values():
        for call in fr.calls:
            callee_id = _resolve_callee(
                call, fr, func_index, name_index, import_maps,
                class_index, class_name_index, star_imports, method_index,
                class_method_lookup,
            )
            if not callee_id:
                continue
            caller_id = call.caller_id
            if caller_id == callee_id:
                continue
            if callee_id in downstream_sets and caller_id not in downstream_sets[callee_id]:
                downstream_sets[callee_id].add(caller_id)
            if caller_id in upstream_sets and callee_id not in upstream_sets[caller_id]:
                upstream_sets[caller_id].add(callee_id)

    for asset_id, ds in downstream_sets.items():
        assets[asset_id]["downstream_ids"] = sorted(ds)
    for asset_id, us in upstream_sets.items():
        assets[asset_id]["upstream_ids"] = sorted(us)

    # --- Class-level call edges (aggregated from method edges) ---
    # For each class, upstream_ids = classes whose methods are called by this class's methods
    # For each class, downstream_ids = classes whose methods call this class's methods
    # Build func_id → class_id map
    func_to_class: Dict[str, str] = {}
    for a in assets.values():
        if a["node_type"] == "function" and a["class_id"]:
            func_to_class[a["id"]] = a["class_id"]

    class_upstream: Dict[str, set] = {}
    class_downstream: Dict[str, set] = {}
    for a in assets.values():
        if a["node_type"] != "function" or not a["class_id"]:
            continue
        my_class = a["class_id"]
        # This function calls these (upstream_ids) — map to their classes
        for callee_id in a["upstream_ids"]:
            callee_class = func_to_class.get(callee_id)
            if callee_class and callee_class != my_class:
                class_upstream.setdefault(my_class, set()).add(callee_class)
        # These call this function (downstream_ids) — map to their classes
        for caller_id in a["downstream_ids"]:
            caller_class = func_to_class.get(caller_id)
            if caller_class and caller_class != my_class:
                class_downstream.setdefault(my_class, set()).add(caller_class)

    for class_id, us in class_upstream.items():
        if class_id in assets:
            assets[class_id]["upstream_ids"] = sorted(us)
    for class_id, ds in class_downstream.items():
        if class_id in assets:
            assets[class_id]["downstream_ids"] = sorted(ds)

    # --- Instantiates relationships ---
    for fr in files.values():
        for rel in fr.relationships:
            if rel.type != "instantiates":
                continue
            if rel.source_id not in assets:
                continue
            target_id = _resolve_class(
                rel.target_name, fr.path, import_maps, class_index, class_name_index
            )
            if not target_id:
                continue
            entry = {"type": "instantiates", "target_id": target_id}
            existing = assets[rel.source_id]["relationships"]
            if entry not in existing:
                existing.append(entry)

    logger.info(
        "build_lineage: %d function assets, %d class assets",
        sum(1 for a in assets.values() if a["node_type"] == "function"),
        sum(1 for a in assets.values() if a["node_type"] == "class"),
    )
    return list(assets.values())
