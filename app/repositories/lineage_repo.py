from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, distinct, func, insert, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.models.function_branch import FunctionBranch
from app.models.function_def import FunctionDef
from app.models.repo_settings import RepoSettings


def normalize_repo_name(repo_url_or_name: str) -> str:
    s = repo_url_or_name.rstrip("/")
    s = s.rsplit("/", 1)[-1]
    return s.replace(".git", "")


def compute_def_id(tenant_id: str, repo: str, asset_id: str, source: str | None) -> str:
    """Deterministic content-addressed ID for a function version."""
    content = f"{tenant_id}:{repo}:{asset_id}:{source or ''}"
    return hashlib.md5(content.encode()).hexdigest()


class LineageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── Write ──────────────────────────────────────────────────────────────

    async def replace_for_repo_branch(
        self,
        *,
        tenant_id: str,
        repo: str,
        branch: str,
        repo_url: Optional[str] = None,
        workflow_id: str,
        run_id: str,
        lineage_assets: Sequence[Dict[str, Any]],
    ) -> int:
        # 1. Delete existing branch edges
        await self._s.execute(
            delete(FunctionBranch).where(
                FunctionBranch.tenant_id == tenant_id,
                FunctionBranch.repo == repo,
                FunctionBranch.branch == branch,
            )
        )

        if lineage_assets:
            # 2. Upsert function_defs — one row per unique content version.
            #    ON CONFLICT DO NOTHING: if same (tenant,repo,asset_id,source_hash)
            #    already exists from another branch, reuse it.
            def_rows = [
                {
                    "id": compute_def_id(tenant_id, repo, a["id"], a.get("source")),
                    "tenant_id": tenant_id,
                    "repo": repo,
                    "asset_id": a["id"],
                    "node_type": a.get("node_type", "function"),
                    "name": a["name"],
                    "file_path": a["file"],
                    "lineno": int(a["lineno"]),
                    "end_lineno": a.get("end_lineno"),
                    "source_hash": hashlib.md5((a.get("source") or "").encode()).hexdigest(),
                    "source": a.get("source"),
                    "module_context": a.get("module_context"),
                    "class_id": a.get("class_id"),
                }
                for a in lineage_assets
            ]
            await self._s.execute(
                pg_insert(FunctionDef).on_conflict_do_nothing(index_elements=["id"]),
                def_rows,
            )

            # 3. Insert branch edges
            branch_rows = [
                {
                    "tenant_id": tenant_id,
                    "repo": repo,
                    "branch": branch,
                    "asset_id": a["id"],
                    "def_id": compute_def_id(tenant_id, repo, a["id"], a.get("source")),
                    "repo_url": repo_url,
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "upstream_ids": a.get("upstream_ids") or [],
                    "downstream_ids": a.get("downstream_ids") or [],
                    "base_class_ids": a.get("base_class_ids") or [],
                    "relationships": a.get("relationships") or [],
                    "unresolved_bases": a.get("unresolved_bases") or [],
                }
                for a in lineage_assets
            ]
            await self._s.execute(insert(FunctionBranch), branch_rows)

        # 4. Orphan cleanup: remove function_defs no longer referenced by any
        #    branch in this repo (happens when a function is deleted or renamed).
        await self._s.execute(
            delete(FunctionDef).where(
                FunctionDef.tenant_id == tenant_id,
                FunctionDef.repo == repo,
                ~FunctionDef.id.in_(
                    select(FunctionBranch.def_id).where(
                        FunctionBranch.tenant_id == tenant_id,
                        FunctionBranch.repo == repo,
                    )
                ),
            )
        )

        await self._s.flush()
        return len(lineage_assets)

    # ── Class lineage ──────────────────────────────────────────────────────

    async def fetch_class_lineage_data(
        self,
        tenant_id: str,
        repo: str,
        branch: str,
        *,
        offset: int = 0,
        limit: int = 100,
        search: str = "",
    ) -> Dict[str, Any]:
        _join = FunctionBranch.def_id == FunctionDef.id
        base_where = [
            FunctionBranch.tenant_id == tenant_id,
            FunctionBranch.repo == repo,
            FunctionBranch.branch == branch,
            FunctionDef.node_type == "class",
        ]

        # 1. Paginated class nodes with window function for total
        q = (
            select(
                FunctionBranch.asset_id,
                FunctionDef.name,
                FunctionDef.file_path,
                FunctionDef.lineno,
                FunctionBranch.base_class_ids,
                func.count().over().label("_filtered_total"),
            )
            .join(FunctionDef, _join)
            .where(*base_where)
            .order_by(FunctionDef.name)
        )
        if search:
            pattern = f"%{search}%"
            q = q.where(or_(FunctionDef.name.ilike(pattern), FunctionDef.file_path.ilike(pattern)))
        q = q.offset(offset).limit(limit)

        class_result = await self._s.execute(q)
        rows = class_result.all()
        filtered_total = rows[0]._filtered_total if rows else 0

        classes: Dict[str, Dict] = {}
        for r in rows:
            classes[r.asset_id] = {
                "id": r.asset_id,
                "name": r.name,
                "file": r.file_path,
                "lineno": r.lineno,
                "base_class_ids": r.base_class_ids or [],
                "method_count": 0,
            }

        if not classes:
            return {"nodes": [], "edges": [], "filtered_total": 0, "offset": offset, "limit": limit}

        # 2. Method counts for the returned classes only
        count_result = await self._s.execute(
            select(FunctionDef.class_id, func.count(FunctionBranch.asset_id))
            .join(FunctionDef, _join)
            .where(
                FunctionBranch.tenant_id == tenant_id,
                FunctionBranch.repo == repo,
                FunctionBranch.branch == branch,
                FunctionDef.node_type == "function",
                FunctionDef.class_id.in_(list(classes.keys())),
            )
            .group_by(FunctionDef.class_id)
        )
        for class_id, count in count_result.all():
            if class_id in classes:
                classes[class_id]["method_count"] = count

        # 3. Cross-repo parent classes
        cross_repo_ids = set()
        for cls in classes.values():
            for parent_id in (cls["base_class_ids"] or []):
                if parent_id not in classes:
                    cross_repo_ids.add(parent_id)

        if cross_repo_ids:
            cross_result = await self._s.execute(
                select(
                    FunctionBranch.asset_id,
                    FunctionDef.name,
                    FunctionDef.file_path,
                    FunctionDef.lineno,
                    FunctionBranch.repo,
                    FunctionBranch.repo_url,
                    FunctionBranch.branch,
                    FunctionBranch.base_class_ids,
                )
                .join(FunctionDef, _join)
                .where(
                    FunctionBranch.tenant_id == tenant_id,
                    FunctionDef.node_type == "class",
                    FunctionBranch.asset_id.in_(cross_repo_ids),
                )
            )
            for r in cross_result.all():
                classes[r.asset_id] = {
                    "id": r.asset_id,
                    "name": r.name,
                    "file": r.file_path,
                    "lineno": r.lineno,
                    "base_class_ids": r.base_class_ids or [],
                    "method_count": 0,
                    "is_cross_repo": True,
                    "repo": r.repo,
                    "repo_url": r.repo_url,
                    "branch": r.branch,
                }

        # 4. Inheritance edges between returned nodes (including cross-repo parents)
        inherit_edges = []
        for cls in list(classes.values()):
            for parent_id in (cls["base_class_ids"] or []):
                if parent_id in classes:
                    inherit_edges.append({"source": cls["id"], "target": parent_id, "edge_type": "extends"})

        return {
            "nodes": list(classes.values()),
            "edges": inherit_edges,
            "filtered_total": filtered_total,
            "offset": offset,
            "limit": limit,
        }

    # ── Simple lookups ─────────────────────────────────────────────────────

    async def list_branches_for_repo(self, tenant_id: str, repo: str) -> List[str]:
        result = await self._s.execute(
            select(distinct(FunctionBranch.branch))
            .where(FunctionBranch.tenant_id == tenant_id, FunctionBranch.repo == repo)
            .order_by(FunctionBranch.branch)
        )
        return [row[0] for row in result.all()]

    async def list_functions_for_branch(
        self, tenant_id: str, repo: str, branch: str
    ) -> List[Dict[str, Any]]:
        result = await self._s.execute(
            select(FunctionBranch.asset_id, FunctionDef.name, FunctionDef.file_path)
            .join(FunctionDef, FunctionBranch.def_id == FunctionDef.id)
            .where(
                FunctionBranch.tenant_id == tenant_id,
                FunctionBranch.repo == repo,
                FunctionBranch.branch == branch,
            )
            .order_by(FunctionDef.name)
        )
        return [
            {"id": row.asset_id, "name": row.name, "file": row.file_path}
            for row in result.all()
        ]

    async def fetch_node_by_name(
        self, tenant_id: str, repo: str, branch: str, name: str
    ) -> Optional[Dict[str, Any]]:
        result = await self._s.execute(
            select(
                FunctionBranch.asset_id,
                FunctionDef.name,
                FunctionDef.file_path,
                FunctionDef.lineno,
                FunctionDef.end_lineno,
                FunctionDef.source,
            )
            .join(FunctionDef, FunctionBranch.def_id == FunctionDef.id)
            .where(
                FunctionBranch.tenant_id == tenant_id,
                FunctionBranch.repo == repo,
                FunctionBranch.branch == branch,
                FunctionDef.name == name,
            )
            .limit(1)
        )
        row = result.one_or_none()
        if not row:
            return None
        return {
            "id": row.asset_id,
            "name": row.name,
            "file": row.file_path,
            "lineno": row.lineno,
            "end_lineno": row.end_lineno,
            "source": row.source,
        }

    async def list_repo_branches(self, tenant_id: str) -> List[Dict[str, str]]:
        result = await self._s.execute(
            select(
                FunctionBranch.repo,
                FunctionBranch.branch,
                func.max(FunctionBranch.repo_url).label("repo_url"),
            )
            .where(FunctionBranch.tenant_id == tenant_id)
            .group_by(FunctionBranch.repo, FunctionBranch.branch)
            .order_by(FunctionBranch.repo, FunctionBranch.branch)
        )
        rows = result.all()

        try:
            settings_result = await self._s.execute(
                select(RepoSettings.repo, RepoSettings.default_branch)
                .where(RepoSettings.tenant_id == tenant_id)
            )
            default_branches = {r: b for r, b in settings_result.all()}
        except Exception:
            default_branches = {}

        return [
            {"repo": r, "branch": b, "repo_url": u, "default_branch": default_branches.get(r, "")}
            for r, b, u in rows
        ]

    async def set_default_branch(self, tenant_id: str, repo: str, branch: str) -> None:
        stmt = pg_insert(RepoSettings).values(
            tenant_id=tenant_id, repo=repo, default_branch=branch
        ).on_conflict_do_update(
            index_elements=["tenant_id", "repo"],
            set_={"default_branch": branch},
        )
        await self._s.execute(stmt)
        await self._s.commit()

    # ── Cross-repo base class resolution ───────────────────────────────────

    async def resolve_cross_repo_bases(self, tenant_id: str) -> int:
        _join = FunctionBranch.def_id == FunctionDef.id

        # Default branches per repo
        try:
            settings_result = await self._s.execute(
                select(RepoSettings.repo, RepoSettings.default_branch)
                .where(RepoSettings.tenant_id == tenant_id)
            )
            default_branches: dict = {r: b for r, b in settings_result.all()}
        except Exception:
            default_branches = {}

        # Class nodes with unresolved bases
        unresolved_result = await self._s.execute(
            select(
                FunctionBranch.asset_id,
                FunctionBranch.repo,
                FunctionBranch.branch,
                FunctionBranch.base_class_ids,
                FunctionBranch.unresolved_bases,
            )
            .join(FunctionDef, _join)
            .where(
                FunctionBranch.tenant_id == tenant_id,
                FunctionDef.node_type == "class",
                func.jsonb_array_length(FunctionBranch.unresolved_bases) > 0,
            )
        )
        nodes_with_unresolved = unresolved_result.all()
        logger.info(
            "resolve_cross_repo_bases: found %d class nodes with unresolved bases",
            len(nodes_with_unresolved),
        )
        if not nodes_with_unresolved:
            return 0

        # Candidate classes — only from repos with a known default branch
        candidate_where = [
            FunctionBranch.tenant_id == tenant_id,
            FunctionDef.node_type == "class",
        ]
        if default_branches:
            candidate_where.append(FunctionBranch.repo.in_(list(default_branches.keys())))
            candidate_where.append(FunctionBranch.branch.in_(list(default_branches.values())))

        candidates_result = await self._s.execute(
            select(
                FunctionBranch.asset_id,
                FunctionDef.name,
                FunctionDef.file_path,
                FunctionBranch.repo,
                FunctionBranch.branch,
            )
            .join(FunctionDef, _join)
            .where(*candidate_where)
        )
        all_candidates = candidates_result.all()

        def _candidate_keys(file_path: str, class_name: str):
            file_module = file_path.replace("/", ".").replace("\\", ".")
            if file_module.endswith(".py"):
                file_module = file_module[:-3]
            if file_module.endswith(".__init__"):
                file_module = file_module[:-9]
            yield f"{file_module}.{class_name}"
            parts = file_module.split(".")
            if len(parts) > 1:
                parent = ".".join(parts[:-1])
                yield f"{parent}.{class_name}"

        resolved_count = 0
        for node in nodes_with_unresolved:
            asset_id, repo, branch, base_class_ids, unresolved_bases = node
            if not unresolved_bases:
                continue

            new_base_ids = list(base_class_ids or [])
            remaining_unresolved = []

            for unresolved in unresolved_bases:
                qualified_key = unresolved.get("qualified_key", unresolved.get("name", ""))

                # Bare names (no module path) are ambiguous — "Config", "Base", "Model"
                # could refer to anything. Skip cross-repo resolution for them.
                if "." not in qualified_key:
                    remaining_unresolved.append(unresolved)
                    continue

                matches = []

                for cand in all_candidates:
                    cand_asset_id, cand_name, cand_file_path, cand_repo, cand_branch = cand
                    if cand_repo == repo:
                        continue
                    expected_branch = default_branches.get(cand_repo, "")
                    if expected_branch and cand_branch != expected_branch:
                        continue
                    cand_class_name = cand_asset_id.split(":")[-1]
                    for key in _candidate_keys(cand_file_path, cand_class_name):
                        # Require match on a full dotted segment boundary so that
                        # "Config" doesn't match "DatabaseConfig.Config" via suffix.
                        exact = qualified_key == key
                        suffix = (
                            key.endswith("." + qualified_key) or
                            qualified_key.endswith("." + key)
                        )
                        if exact or suffix:
                            matches.append({
                                "asset_id": cand_asset_id,
                                "repo": cand_repo,
                                "branch": cand_branch,
                                "confidence": "exact" if qualified_key == key else "suffix",
                            })
                            break

                if matches:
                    for m in matches:
                        if m["asset_id"] not in new_base_ids:
                            new_base_ids.append(m["asset_id"])
                    resolved_count += 1
                else:
                    remaining_unresolved.append(unresolved)

            await self._s.execute(
                text("""
                    UPDATE function_branches
                    SET base_class_ids  = :base_class_ids,
                        unresolved_bases = :unresolved_bases
                    WHERE asset_id  = :asset_id
                      AND tenant_id = :tenant_id
                      AND repo      = :repo
                      AND branch    = :branch
                """),
                {
                    "base_class_ids": json.dumps(new_base_ids),
                    "unresolved_bases": json.dumps(remaining_unresolved),
                    "asset_id": asset_id,
                    "tenant_id": tenant_id,
                    "repo": repo,
                    "branch": branch,
                },
            )

        await self._s.commit()
        return resolved_count

    # ── Delete ─────────────────────────────────────────────────────────────

    async def delete_branch(self, tenant_id: str, repo: str, branch: str) -> int:
        result = await self._s.execute(
            delete(FunctionBranch).where(
                FunctionBranch.tenant_id == tenant_id,
                FunctionBranch.repo == repo,
                FunctionBranch.branch == branch,
            )
        )
        # Remove orphaned defs (no branch in this repo references them anymore)
        await self._s.execute(
            delete(FunctionDef).where(
                FunctionDef.tenant_id == tenant_id,
                FunctionDef.repo == repo,
                ~FunctionDef.id.in_(
                    select(FunctionBranch.def_id).where(
                        FunctionBranch.tenant_id == tenant_id,
                        FunctionBranch.repo == repo,
                    )
                ),
            )
        )
        await self._s.flush()
        return result.rowcount

    async def delete_repo(self, tenant_id: str, repo: str) -> int:
        result = await self._s.execute(
            delete(FunctionBranch).where(
                FunctionBranch.tenant_id == tenant_id,
                FunctionBranch.repo == repo,
            )
        )
        await self._s.execute(
            delete(FunctionDef).where(
                FunctionDef.tenant_id == tenant_id,
                FunctionDef.repo == repo,
            )
        )
        await self._s.flush()
        return result.rowcount

    # ── Lineage page (list + filter + paginate) ────────────────────────────

    async def fetch_lineage_stats(
        self, tenant_id: str, repo: str, branch: str
    ) -> Dict[str, Any]:
        up_len = func.jsonb_array_length(FunctionBranch.upstream_ids)
        down_len = func.jsonb_array_length(FunctionBranch.downstream_ids)
        is_connected = or_(down_len > 0, up_len > 0)
        result = await self._s.execute(
            select(
                func.count().label("total"),
                func.count().filter(is_connected).label("connected"),
            )
            .select_from(FunctionBranch)
            .where(
                FunctionBranch.tenant_id == tenant_id,
                FunctionBranch.repo == repo,
                FunctionBranch.branch == branch,
            )
        )
        row = result.one()
        return {"total": row.total, "connected": row.connected, "isolated": row.total - row.connected}

    async def fetch_lineage_data(
        self,
        tenant_id: str,
        repo: str,
        branch: str,
        *,
        offset: int = 0,
        limit: int = 100,
        search: str = "",
        sort: str = "connections",
    ) -> Dict[str, Any]:
        _join = FunctionBranch.def_id == FunctionDef.id
        base_where = [
            FunctionBranch.tenant_id == tenant_id,
            FunctionBranch.repo == repo,
            FunctionBranch.branch == branch,
        ]

        up_len = func.jsonb_array_length(FunctionBranch.upstream_ids)
        down_len = func.jsonb_array_length(FunctionBranch.downstream_ids)

        q = (
            select(
                FunctionBranch.asset_id,
                FunctionDef.node_type,
                FunctionDef.name,
                FunctionDef.file_path,
                FunctionDef.lineno,
                FunctionDef.class_id,
                up_len.label("upstream_count"),
                down_len.label("downstream_count"),
                func.count().over().label("_filtered_total"),
            )
            .join(FunctionDef, _join)
            .where(*base_where)
        )

        if search:
            pattern = f"%{search}%"
            q = q.where(
                or_(
                    FunctionDef.name.ilike(pattern),
                    FunctionDef.file_path.ilike(pattern),
                )
            )

        if sort == "connections":
            q = q.order_by((down_len + up_len).desc(), FunctionDef.name)
        elif sort == "file":
            q = q.order_by(FunctionDef.file_path, FunctionDef.name)
        else:
            q = q.order_by(FunctionDef.name)
        q = q.offset(offset).limit(limit)
        result = await self._s.execute(q)
        rows = result.all()
        filtered_total = rows[0]._filtered_total if rows else 0

        nodes = [
            {
                "id": row.asset_id,
                "node_type": row.node_type,
                "name": row.name,
                "file": row.file_path,
                "lineno": row.lineno,
                "class_id": row.class_id,
                "upstream_count": row.upstream_count,
                "downstream_count": row.downstream_count,
            }
            for row in rows
        ]

        return {
            "nodes": nodes,
            "filtered_total": filtered_total,
            "offset": offset,
            "limit": limit,
        }

    # ── Asset page (single node + neighbors) ──────────────────────────────

    async def fetch_node_with_neighbors(
        self, tenant_id: str, repo: str, branch: str, asset_id: str
    ) -> Optional[Dict[str, Any]]:
        _join = FunctionBranch.def_id == FunctionDef.id

        result = await self._s.execute(
            select(
                FunctionBranch.asset_id,
                FunctionDef.node_type,
                FunctionDef.name,
                FunctionDef.file_path,
                FunctionDef.lineno,
                FunctionDef.end_lineno,
                FunctionDef.source,
                FunctionDef.module_context,
                FunctionDef.class_id,
                FunctionBranch.base_class_ids,
                FunctionBranch.downstream_ids,
                FunctionBranch.upstream_ids,
                FunctionBranch.relationships,
            )
            .join(FunctionDef, _join)
            .where(
                FunctionBranch.tenant_id == tenant_id,
                FunctionBranch.repo == repo,
                FunctionBranch.branch == branch,
                FunctionBranch.asset_id == asset_id,
            )
        )
        row = result.one_or_none()
        if not row:
            return None

        node = {
            "id": row.asset_id,
            "node_type": row.node_type,
            "name": row.name,
            "file": row.file_path,
            "lineno": row.lineno,
            "end_lineno": row.end_lineno,
            "source": row.source,
            "module_context": row.module_context,
            "class_id": row.class_id,
            "base_class_ids": row.base_class_ids or [],
            "downstream_ids": row.downstream_ids or [],
            "upstream_ids": row.upstream_ids or [],
            "relationships": row.relationships or [],
        }

        callers: List[Dict[str, Any]] = []
        callees: List[Dict[str, Any]] = []

        if node["node_type"] != "class":
            # Function node: fetch callers + callees in one query
            all_fn_neighbor_ids = set(node["downstream_ids"]) | set(node["upstream_ids"])
            if all_fn_neighbor_ids:
                fn_result = await self._s.execute(
                    select(
                        FunctionBranch.asset_id,
                        FunctionDef.node_type,
                        FunctionDef.name,
                        FunctionDef.file_path,
                        FunctionDef.lineno,
                        FunctionDef.end_lineno,
                        FunctionDef.source,
                        FunctionDef.class_id,
                        FunctionBranch.downstream_ids,
                        FunctionBranch.upstream_ids,
                    )
                    .join(FunctionDef, _join)
                    .where(
                        FunctionBranch.tenant_id == tenant_id,
                        FunctionBranch.repo == repo,
                        FunctionBranch.branch == branch,
                        FunctionBranch.asset_id.in_(all_fn_neighbor_ids),
                    )
                )
                fn_map = {
                    r.asset_id: {
                        "id": r.asset_id,
                        "node_type": r.node_type,
                        "name": r.name,
                        "file": r.file_path,
                        "lineno": r.lineno,
                        "end_lineno": r.end_lineno,
                        "source": r.source,
                        "class_id": r.class_id,
                        "downstream_count": len(r.downstream_ids or []),
                        "upstream_count": len(r.upstream_ids or []),
                    }
                    for r in fn_result.all()
                }
                callers = [fn_map[i] for i in node["downstream_ids"] if i in fn_map]
                callees = [fn_map[i] for i in node["upstream_ids"] if i in fn_map]

        methods: List[Dict[str, Any]] = []
        if node["node_type"] == "class":
            # Methods list
            methods_result = await self._s.execute(
                select(
                    FunctionBranch.asset_id,
                    FunctionDef.name,
                    FunctionDef.file_path,
                    FunctionDef.lineno,
                    FunctionDef.end_lineno,
                )
                .join(FunctionDef, _join)
                .where(
                    FunctionBranch.tenant_id == tenant_id,
                    FunctionBranch.repo == repo,
                    FunctionBranch.branch == branch,
                    FunctionDef.class_id == asset_id,
                    FunctionDef.node_type == "function",
                )
                .order_by(FunctionDef.lineno)
            )
            methods = [
                {
                    "id": r.asset_id,
                    "name": r.name,
                    "file": r.file_path,
                    "lineno": r.lineno,
                    "end_lineno": r.end_lineno,
                }
                for r in methods_result.all()
            ]

            # Class neighbors (call edges + inheritance) in one query
            parent_ids = [cid for cid in (node["base_class_ids"] or []) if cid != asset_id]
            all_neighbor_ids = set(node["upstream_ids"]) | set(node["downstream_ids"]) | set(parent_ids)

            neighbor_where = [
                FunctionBranch.tenant_id == tenant_id,
                FunctionDef.node_type == "class",
                FunctionBranch.asset_id != asset_id,
            ]
            conditions = [FunctionBranch.base_class_ids.contains([asset_id])]
            if all_neighbor_ids:
                conditions.append(FunctionBranch.asset_id.in_(all_neighbor_ids))
            neighbor_where.append(or_(*conditions))

            neighbor_result = await self._s.execute(
                select(
                    FunctionBranch.asset_id,
                    FunctionDef.name,
                    FunctionDef.file_path,
                    FunctionDef.lineno,
                    FunctionDef.end_lineno,
                    FunctionBranch.repo,
                    FunctionBranch.repo_url,
                    FunctionBranch.branch,
                    FunctionBranch.base_class_ids,
                    FunctionBranch.upstream_ids,
                    FunctionBranch.downstream_ids,
                )
                .join(FunctionDef, _join)
                .where(*neighbor_where)
            )

            upstream_set = set(node["upstream_ids"])
            downstream_set = set(node["downstream_ids"])
            parent_id_set = set(parent_ids)
            seen_callees: set = set()
            seen_callers: set = set()
            neighbor_rows = neighbor_result.all()

            # Child counts (classes that inherit from each neighbor)
            neighbor_ids = [r.asset_id for r in neighbor_rows]
            child_counts: Dict[str, int] = {}
            if neighbor_ids:
                child_count_result = await self._s.execute(
                    text("""
                        SELECT parent_id, COUNT(*) AS cnt
                        FROM function_branches fb
                        JOIN function_defs fd ON fd.id = fb.def_id,
                             jsonb_array_elements_text(fb.base_class_ids) AS parent_id
                        WHERE fb.tenant_id = :tenant_id
                          AND fd.node_type = 'class'
                        GROUP BY parent_id
                    """),
                    {"tenant_id": tenant_id},
                )
                child_counts = {row[0]: row[1] for row in child_count_result.all()}

            for r in neighbor_rows:
                cross = r.repo != repo
                r_upstream = set(r.upstream_ids or []) | set(r.base_class_ids or [])
                r_downstream_count = len(r.downstream_ids or []) + child_counts.get(r.asset_id, 0)
                base = {
                    "id": r.asset_id,
                    "node_type": "class",
                    "name": r.name,
                    "file": r.file_path,
                    "lineno": r.lineno,
                    "end_lineno": r.end_lineno,
                    "source": None,
                    "class_id": None,
                    "downstream_count": r_downstream_count,
                    "upstream_count": len(r_upstream),
                    "is_cross_repo": cross,
                    "repo": r.repo,
                    "repo_url": r.repo_url,
                    "branch": r.branch,
                }
                if r.asset_id not in seen_callees:
                    if r.asset_id in parent_id_set:
                        seen_callees.add(r.asset_id)
                        callees.append({**base, "relationship_type": "extends"})
                    elif r.asset_id in upstream_set:
                        seen_callees.add(r.asset_id)
                        callees.append({**base, "relationship_type": "calls"})
                if r.asset_id not in seen_callers:
                    if asset_id in (r.base_class_ids or []):
                        seen_callers.add(r.asset_id)
                        callers.append({**base, "relationship_type": "extended_by"})
                    elif r.asset_id in downstream_set:
                        seen_callers.add(r.asset_id)
                        callers.append({**base, "relationship_type": "calls"})

        return {"node": node, "upstream": callees, "downstream": callers, "methods": methods}
