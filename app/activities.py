import json
import logging
import os
import tempfile
from typing import Any, Dict, Optional

from temporalio import activity

from .db import normalize_repo_name, replace_lineage_for_repo_branch
from .lineage_builder import build_lineage
from .python_ast_parser import (
    CallInfo,
    ClassDefInfo,
    FileParseResult,
    FunctionDefInfo,
    ImportInfo,
    parse_repository,
)
from .repo_crawler import clone_repository


logger = logging.getLogger(__name__)
DEFAULT_OUTPUT_DIR = os.path.join(os.getcwd(), "output")


@activity.defn(name="clone_repo_activity")
async def clone_repo_activity(workflow_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clone or refresh the Git repository.

    Expected keys in workflow_args:
        github_repo_url: str
        branch: str (optional, default 'main')
    """
    repo_url = workflow_args["github_repo_url"]
    branch = workflow_args.get("branch", "main")
    logger.info("clone_repo_activity: cloning repo=%s branch=%s", repo_url, branch)
    repo_path = clone_repository(repo_url=repo_url, branch=branch)
    logger.info("clone_repo_activity: cloned to repo_path=%s", repo_path)
    updated = dict(workflow_args)
    updated["repo_path"] = repo_path
    return updated


@activity.defn(name="parse_repo_activity")
async def parse_repo_activity(workflow_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse the cloned repository using Python AST.

    Adds:
        parsed_repo_path: path to JSON-serialized parse result on disk.
    """
    language = workflow_args.get("language", "python")
    if language != "python":
        raise ValueError(f"Unsupported language: {language} (only 'python' supported)")

    repo_path = workflow_args["repo_path"]
    logger.info("parse_repo_activity: parsing repo_path=%s language=%s", repo_path, language)
    parsed_files = parse_repository(repo_path)

    # Build a JSON-serializable structure but store it on disk to avoid
    # exceeding Temporal's gRPC message size limits.
    serializable: Dict[str, Any] = {}
    for path, result in parsed_files.items():
        serializable[path] = {
            "module": result.module,
            "path": result.path,
            "functions": [
                {
                    "id": fn.id,
                    "name": fn.name,
                    "qualname": fn.qualname,
                    "file": fn.file,
                    "lineno": fn.lineno,
                    "col_offset": fn.col_offset,
                    "class_name": fn.class_name,
                }
                for fn in result.functions
            ],
            "classes": [
                {
                    "name": cls.name,
                    "qualname": cls.qualname,
                    "file": cls.file,
                    "lineno": cls.lineno,
                    "col_offset": cls.col_offset,
                }
                for cls in result.classes
            ],
            "imports": [
                {
                    "type": imp.type,
                    "module": imp.module,
                    "name": imp.name,
                    "asname": imp.asname,
                }
                for imp in result.imports
            ],
            "calls": [
                {
                    "caller_id": call.caller_id,
                    "func_expr": call.func_expr,
                    "file": call.file,
                    "lineno": call.lineno,
                    "col_offset": call.col_offset,
                }
                for call in result.calls
            ],
        }

    os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".json", prefix="parsed_repo_", dir=DEFAULT_OUTPUT_DIR
    )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(serializable, f)

    logger.info(
        "parse_repo_activity: wrote parsed repo for %d files to %s",
        len(serializable),
        tmp_path,
    )

    updated = dict(workflow_args)
    updated["parsed_repo_path"] = tmp_path
    return updated


@activity.defn(name="build_lineage_activity")
async def build_lineage_activity(workflow_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a function-level lineage graph from the parsed repository.

    Stores each function as a node in Postgres.
    """
    parsed_repo_path = workflow_args["parsed_repo_path"]
    workflow_id = workflow_args["workflow_id"]
    run_id = workflow_args["run_id"]

    logger.info("build_lineage_activity: loading parsed repo from %s", parsed_repo_path)
    with open(parsed_repo_path, "r", encoding="utf-8") as f:
        parsed_repo: Dict[str, Any] = json.load(f)

    files: Dict[str, FileParseResult] = {}
    for path, data in parsed_repo.items():
        file_result = FileParseResult(
            module=data["module"],
            path=data["path"],
        )
        file_result.functions = [
            FunctionDefInfo(
                id=fn["id"],
                name=fn["name"],
                qualname=fn["qualname"],
                file=fn["file"],
                lineno=fn["lineno"],
                col_offset=fn["col_offset"],
                class_name=fn.get("class_name"),
            )
            for fn in data["functions"]
        ]
        file_result.classes = [
            ClassDefInfo(
                name=cls["name"],
                qualname=cls["qualname"],
                file=cls["file"],
                lineno=cls["lineno"],
                col_offset=cls["col_offset"],
            )
            for cls in data["classes"]
        ]
        file_result.imports = [
            ImportInfo(
                type=imp["type"],
                module=imp["module"],
                name=imp["name"],
                asname=imp["asname"],
            )
            for imp in data["imports"]
        ]
        file_result.calls = [
            CallInfo(
                caller_id=call["caller_id"],
                func_expr=call["func_expr"],
                file=call["file"],
                lineno=call["lineno"],
                col_offset=call["col_offset"],
            )
            for call in data["calls"]
        ]
        files[path] = file_result

    logger.info(
        "build_lineage_activity: building lineage for %d parsed files",
        len(files),
    )
    lineage_assets = build_lineage(files, workflow_id, run_id)
    repo_url = workflow_args["github_repo_url"]
    branch = workflow_args.get("branch", "main")
    tenant_id = workflow_args.get("tenant_id", "default")
    safe_repo = normalize_repo_name(repo_url)

    inserted = await replace_lineage_for_repo_branch(
        tenant_id=tenant_id,
        repo=safe_repo,
        branch=branch,
        workflow_id=workflow_id,
        run_id=run_id,
        lineage_assets=lineage_assets,
    )

    logger.info(
    "build_lineage_activity: stored lineage in Postgres (assets=%s repo=%s branch=%s)",
    inserted,
    safe_repo,
    branch,
    )

    updated = dict(workflow_args)
    updated["lineage_stats"] = {"assets": inserted}
    return updated


@activity.defn(name="store_lineage_activity")
async def store_lineage_activity(workflow_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Move lineage JSON to its final location on disk.
    """
    tmp_path = workflow_args.get("lineage_tmp_path")
    if not tmp_path:
        # New flow stores lineage directly in Postgres during build_lineage_activity.
        # Keep this activity as a no-op for backward compatibility.
        return workflow_args
    output_path: Optional[str] = workflow_args.get("output_path")

    if not output_path:
        repo_url = workflow_args["github_repo_url"]
        branch = workflow_args.get("branch", "main")
        safe_repo = repo_url.rstrip("/").rsplit("/", 1)[-1].replace(".git", "")
        file_name = f"{safe_repo}-{branch}-lineage.json"
        output_path = os.path.join(DEFAULT_OUTPUT_DIR, file_name)

    dir_name = os.path.dirname(output_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    logger.info(
        "store_lineage_activity: moving lineage tmp file %s to %s",
        tmp_path,
        output_path,
    )
    # Atomic move from temp location to final path
    os.replace(tmp_path, output_path)

    updated = dict(workflow_args)
    updated["lineage_path"] = output_path
    return updated

