import json
import logging
import os
import tempfile
from typing import Any, Dict

from temporalio import activity

from .db import get_session_context
from .lineage_builder import build_lineage
from .python_ast_parser import (
    CallInfo,
    ClassDefInfo,
    FileParseResult,
    FunctionDefInfo,
    ImportInfo,
    RelationshipInfo,
    parse_repository,
)
from .repo_crawler import clone_repository
from .repositories.lineage_repo import LineageRepository, normalize_repo_name


logger = logging.getLogger(__name__)
DEFAULT_OUTPUT_DIR = os.path.join(os.getcwd(), "output")


@activity.defn(name="clone_repo_activity")
async def clone_repo_activity(workflow_args: Dict[str, Any]) -> Dict[str, Any]:
    from urllib.parse import urlparse, urlunparse
    from .crypto import decrypt_pat
    from .models.user import User

    repo_url = workflow_args["github_repo_url"]
    branch = workflow_args.get("branch", "main")
    logger.info("clone_repo_activity: cloning repo=%s branch=%s", repo_url, branch)

    # Fetch PAT for the triggering user — inject into URL without logging it
    auth_url = repo_url
    user_id = workflow_args.get("user_id")
    if user_id:
        try:
            async with get_session_context() as session:
                user = await session.get(User, int(user_id))
                if user and user.github_pat_encrypted:
                    pat = decrypt_pat(user.github_pat_encrypted)
                    parsed = urlparse(repo_url)
                    auth_url = urlunparse(parsed._replace(netloc=f"{pat}@{parsed.netloc}"))
                    logger.info("clone_repo_activity: using PAT for user_id=%s", user_id)
        except Exception:
            logger.warning("clone_repo_activity: failed to fetch PAT for user_id=%s, proceeding without", user_id)

    repo_path = clone_repository(repo_url=repo_url, auth_url=auth_url, branch=branch)
    logger.info("clone_repo_activity: cloned to repo_path=%s", repo_path)
    updated = dict(workflow_args)
    updated["repo_path"] = repo_path
    return updated


@activity.defn(name="parse_repo_activity")
async def parse_repo_activity(workflow_args: Dict[str, Any]) -> Dict[str, Any]:
    language = workflow_args.get("language", "python")
    if language != "python":
        raise ValueError(f"Unsupported language: {language} (only 'python' supported)")

    repo_path = workflow_args["repo_path"]
    logger.info("parse_repo_activity: parsing repo_path=%s language=%s", repo_path, language)
    parsed_files = parse_repository(repo_path)

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
                    "class_id": fn.class_id,
                    "end_lineno": fn.end_lineno,
                    "source": fn.source,
                }
                for fn in result.functions
            ],
            "classes": [
                {
                    "id": cls.id,
                    "name": cls.name,
                    "qualname": cls.qualname,
                    "file": cls.file,
                    "lineno": cls.lineno,
                    "col_offset": cls.col_offset,
                    "end_lineno": cls.end_lineno,
                    "source": cls.source,
                    "base_classes": cls.base_classes,
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
            "relationships": [
                {
                    "type": rel.type,
                    "source_id": rel.source_id,
                    "source_type": rel.source_type,
                    "target_name": rel.target_name,
                    "file": rel.file,
                    "lineno": rel.lineno,
                }
                for rel in result.relationships
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
                class_id=fn.get("class_id"),
                end_lineno=fn.get("end_lineno"),
                source=fn.get("source"),
            )
            for fn in data["functions"]
        ]
        file_result.classes = [
            ClassDefInfo(
                id=cls["id"],
                name=cls["name"],
                qualname=cls["qualname"],
                file=cls["file"],
                lineno=cls["lineno"],
                col_offset=cls["col_offset"],
                end_lineno=cls.get("end_lineno"),
                source=cls.get("source"),
                base_classes=cls.get("base_classes") or [],
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
        file_result.relationships = [
            RelationshipInfo(
                type=rel["type"],
                source_id=rel["source_id"],
                source_type=rel["source_type"],
                target_name=rel["target_name"],
                file=rel.get("file", ""),
                lineno=rel.get("lineno", 0),
            )
            for rel in data.get("relationships", [])
        ]
        files[path] = file_result

    logger.info(
        "build_lineage_activity: building lineage for %d parsed files",
        len(files),
    )
    repo_url = workflow_args["github_repo_url"]
    branch = workflow_args.get("branch", "main")
    tenant_id = workflow_args.get("tenant_id", "default")
    safe_repo = normalize_repo_name(repo_url)

    # Load function/class stubs from other already-crawled repos so that
    # cross-repo self.method() / super().method() calls can be resolved.
    cross_repo_stubs = []
    try:
        async with get_session_context() as session:
            lineage_repo = LineageRepository(session)
            cross_repo_stubs = await lineage_repo.fetch_cross_repo_stubs(
                tenant_id, exclude_repo=safe_repo
            )
        logger.info(
            "build_lineage_activity: loaded %d cross-repo stubs for resolution",
            len(cross_repo_stubs),
        )
    except Exception:
        logger.warning("build_lineage_activity: failed to load cross-repo stubs, continuing without")

    lineage_assets = build_lineage(files, workflow_id, run_id, cross_repo_stubs=cross_repo_stubs)

    async with get_session_context() as session:
        lineage_repo = LineageRepository(session)
        inserted = await lineage_repo.replace_for_repo_branch(
            tenant_id=tenant_id,
            repo=safe_repo,
            branch=branch,
            repo_url=repo_url,
            workflow_id=workflow_id,
            run_id=run_id,
            lineage_assets=lineage_assets,
        )
        await session.commit()

    # Run cross-repo base class resolution across all repos in this tenant.
    # Runs after every crawl so newly crawled repos can resolve previously
    # unresolved bases in other repos (and vice versa).
    async with get_session_context() as session:
        lineage_repo = LineageRepository(session)
        resolved = await lineage_repo.resolve_cross_repo_bases(tenant_id)
        logger.info("build_lineage_activity: cross-repo resolved %d base class links", resolved)

    logger.info(
        "build_lineage_activity: stored lineage in Postgres (assets=%s repo=%s branch=%s)",
        inserted,
        safe_repo,
        branch,
    )

    try:
        os.remove(parsed_repo_path)
        logger.info("build_lineage_activity: deleted temp file %s", parsed_repo_path)
    except OSError:
        pass

    updated = dict(workflow_args)
    updated["lineage_stats"] = {"assets": inserted}
    return updated
