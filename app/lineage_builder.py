import logging
import os
import json
from typing import Dict, List, Any

from .python_ast_parser import CallInfo, FileParseResult, FunctionDefInfo, ImportInfo, ClassDefInfo

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "output"


def _build_function_index(
    files: Dict[str, FileParseResult]
) -> Dict[str, FunctionDefInfo]:
    index: Dict[str, FunctionDefInfo] = {}
    for file_result in files.values():
        for fn in file_result.functions:
            index[fn.id] = fn
    return index


def _build_name_index(
    files: Dict[str, FileParseResult]
) -> Dict[str, List[FunctionDefInfo]]:
    name_index: Dict[str, List[FunctionDefInfo]] = {}
    for file_result in files.values():
        for fn in file_result.functions:
            name_index.setdefault(fn.name, []).append(fn)
    return name_index


def _build_import_maps(
    files: Dict[str, FileParseResult]
) -> Dict[str, Dict[str, str]]:
    """
    For each file, build a mapping alias -> fully-qualified target.
    """
    import_maps: Dict[str, Dict[str, str]] = {}
    for path, file_result in files.items():
        alias_map: Dict[str, str] = {}
        for imp in file_result.imports:
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


def _resolve_callee(
    call: CallInfo,
    file_result: FileParseResult,
    func_index: Dict[str, FunctionDefInfo],
    name_index: Dict[str, List[FunctionDefInfo]],
    import_maps: Dict[str, Dict[str, str]],
) -> str | None:
    """
    Best-effort resolution from call.func_expr to a function id.
    """
    expr = call.func_expr
    imports_for_file = import_maps.get(file_result.path, {})

    def find_by_module_and_name(module: str, name: str) -> str | None:
        candidates = [
            fn for fn in func_index.values()
            if fn.file and fn.qualname.startswith(f"{module}.") and fn.name == name
        ]
        if len(candidates) == 1:
            return candidates[0].id
        return None

    # Case 1: simple name "foo"
    if "." not in expr:
        # imported alias?
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

        # function defined in same file
        same_module_candidates = [
            fn
            for fn in func_index.values()
            if fn.file == file_result.path and fn.name == expr
        ]
        if len(same_module_candidates) == 1:
            return same_module_candidates[0].id

        # unique function name across project
        candidates = name_index.get(expr, [])
        if len(candidates) == 1:
            return candidates[0].id

        return None

    # Case 2: dotted expression "alias.foo" / "pkg.mod.func"
    parts = expr.split(".")
    head, *rest = parts

    # head is import alias?
    if head in imports_for_file:
        target = imports_for_file[head]
        module = target
        name = rest[-1] if rest else head
        resolved = find_by_module_and_name(module, name)
        if resolved:
            return resolved

    # head is module path?
    module_path = head
    if rest:
        candidate_name = rest[-1]
        resolved = find_by_module_and_name(module_path, candidate_name)
        if resolved:
            return resolved

    # fallback: treat last segment as short name
    short = parts[-1]
    candidates = name_index.get(short, [])
    if len(candidates) == 1:
        return candidates[0].id

    return None


def build_lineage(
    files: Dict[str, FileParseResult],
    workflow_id: str,
    run_id: str,
) -> List[dict]:
    """
    Build a function-level lineage graph with each function as a separate JSON object.

    Each function (asset) will have:
        - id: unique ID for the function
        - name: qualified name (workflow_id/run_id/function_name)
        - file: file path
        - lineno: line number
        - upstream_ids: IDs of functions that call this function (callers)
    """
    func_index = _build_function_index(files)
    name_index = _build_name_index(files)
    import_maps = _build_import_maps(files)

    assets: Dict[str, dict] = {}
    upstream_sets: Dict[str, set] = {}

    # Build a file_path → FileParseResult lookup for module_context access
    file_results_by_path = {fr.path: fr for fr in files.values()}

    # Create assets for each function
    for fn in func_index.values():
        asset_id = fn.id
        fr = file_results_by_path.get(fn.file)
        assets[asset_id] = {
            "id": asset_id,
            "name": fn.qualname,
            "file": fn.file,
            "lineno": fn.lineno,
            "end_lineno": fn.end_lineno,
            "source": fn.source,
            "upstream_ids": [],
            "module_context": fr.module_context if fr else {"imports": [], "globals": {}},
        }
        upstream_sets[asset_id] = set()

    # lineage edges
    # NOTE: The current storage model stores caller IDs in upstream_ids.
    # This means: upstream_ids of node B = IDs of nodes that CALL B (its callers).
    # Consequence in the API:
    #   d.upstream   = callers of this function  (who calls it)
    #   d.downstream = callees of this function  (what it calls)
    # Impact analysis (in changes.html) traverses d.upstream to follow callers.
    # TODO: invert to Atlan's model (upstream = dependencies, downstream = consumers)
    #       and re-crawl all repos when doing a schema migration.
    for file_result in files.values():
        for call in file_result.calls:
            callee_id = _resolve_callee(
                call, file_result, func_index, name_index, import_maps
            )
            if not callee_id:
                continue

            caller_asset_id = call.caller_id
            callee_asset_id = callee_id

            # skip self-references and duplicates
            if caller_asset_id == callee_asset_id:
                continue
            if callee_asset_id in upstream_sets and caller_asset_id not in upstream_sets[callee_asset_id]:
                upstream_sets[callee_asset_id].add(caller_asset_id)

    # convert sets to sorted lists
    for asset_id, ups in upstream_sets.items():
        assets[asset_id]["upstream_ids"] = sorted(ups)

    return list(assets.values())



