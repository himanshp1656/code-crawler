from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, distinct, func, insert, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.models.lineage_node import LineageNode
from app.models.repo_settings import RepoSettings


def normalize_repo_name(repo_url_or_name: str) -> str:
    s = repo_url_or_name.rstrip("/")
    s = s.rsplit("/", 1)[-1]
    return s.replace(".git", "")


class LineageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

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
        await self._s.execute(
            delete(LineageNode).where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.repo == repo,
                LineageNode.branch == branch,
            )
        )

        if lineage_assets:
            await self._s.execute(
                insert(LineageNode),
                [
                    {
                        "asset_id": a["id"],
                        "tenant_id": tenant_id,
                        "repo": repo,
                        "branch": branch,
                        "repo_url": repo_url,
                        "workflow_id": workflow_id,
                        "run_id": run_id,
                        "node_type": a.get("node_type", "function"),
                        "name": a["name"],
                        "file_path": a["file"],
                        "lineno": int(a["lineno"]),
                        "end_lineno": a.get("end_lineno"),
                        "source": a.get("source"),
                        "module_context": a.get("module_context"),
                        "class_id": a.get("class_id"),
                        "base_class_ids": a.get("base_class_ids") or [],
                        "downstream_ids": a.get("downstream_ids") or [],
                        "upstream_ids": a.get("upstream_ids") or [],
                        "relationships": a.get("relationships") or [],
                        "unresolved_bases": a.get("unresolved_bases") or [],
                    }
                    for a in lineage_assets
                ],
            )

        await self._s.flush()
        return len(lineage_assets)

    async def fetch_class_lineage_data(
        self, tenant_id: str, repo: str, branch: str
    ) -> Dict[str, Any]:
        """
        Return class-level lineage: class nodes + class→class call edges.
        Edges are aggregated from function-level downstream_ids.
        """
        # 1. All class nodes
        class_result = await self._s.execute(
            select(
                LineageNode.asset_id,
                LineageNode.name,
                LineageNode.file_path,
                LineageNode.lineno,
                LineageNode.base_class_ids,
            ).where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.repo == repo,
                LineageNode.branch == branch,
                LineageNode.node_type == "class",
            ).order_by(LineageNode.name)
        )
        classes: Dict[str, Dict] = {}
        for r in class_result.all():
            classes[r.asset_id] = {
                "id": r.asset_id,
                "name": r.name,
                "file": r.file_path,
                "lineno": r.lineno,
                "base_class_ids": r.base_class_ids or [],
                "method_count": 0,
            }

        # 2. Method counts per class
        count_result = await self._s.execute(
            select(LineageNode.class_id, func.count(LineageNode.asset_id))
            .where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.repo == repo,
                LineageNode.branch == branch,
                LineageNode.node_type == "function",
                LineageNode.class_id.isnot(None),
            )
            .group_by(LineageNode.class_id)
        )
        for class_id, count in count_result.all():
            if class_id in classes:
                classes[class_id]["method_count"] = count

        # 3. Compute class→class call edges from function downstream_ids
        func_result = await self._s.execute(
            select(LineageNode.asset_id, LineageNode.class_id, LineageNode.downstream_ids)
            .where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.repo == repo,
                LineageNode.branch == branch,
                LineageNode.node_type == "function",
            )
        )
        # function_id → class_id
        func_class_map: Dict[str, Optional[str]] = {}
        all_funcs = func_result.all()
        for r in all_funcs:
            func_class_map[r.asset_id] = r.class_id

        # aggregate: (caller_class, callee_class) → call_count
        edge_counts: Dict[tuple, int] = {}
        for r in all_funcs:
            callee_class = r.class_id
            if not callee_class or callee_class not in classes:
                continue
            for caller_id in (r.downstream_ids or []):
                caller_class = func_class_map.get(caller_id)
                if caller_class and caller_class != callee_class and caller_class in classes:
                    key = (caller_class, callee_class)
                    edge_counts[key] = edge_counts.get(key, 0) + 1

        call_edges = [
            {"source": src, "target": tgt, "call_count": count, "edge_type": "calls"}
            for (src, tgt), count in edge_counts.items()
        ]

        # 4. Add inheritance edges from base_class_ids
        # Also fetch cross-repo parent classes so extends edges are visible
        cross_repo_ids = set()
        for cls in classes.values():
            for parent_id in (cls["base_class_ids"] or []):
                if parent_id not in classes:
                    cross_repo_ids.add(parent_id)

        if cross_repo_ids:
            cross_result = await self._s.execute(
                select(
                    LineageNode.asset_id,
                    LineageNode.name,
                    LineageNode.file_path,
                    LineageNode.lineno,
                    LineageNode.repo,
                    LineageNode.repo_url,
                    LineageNode.branch,
                    LineageNode.base_class_ids,
                ).where(
                    LineageNode.tenant_id == tenant_id,
                    LineageNode.node_type == "class",
                    LineageNode.asset_id.in_(cross_repo_ids),
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

        inherit_edges = []
        for cls in classes.values():
            for parent_id in (cls["base_class_ids"] or []):
                if parent_id in classes:
                    inherit_edges.append({
                        "source": cls["id"],
                        "target": parent_id,
                        "edge_type": "extends",
                    })

        return {"nodes": list(classes.values()), "edges": call_edges + inherit_edges}

    async def list_branches_for_repo(self, tenant_id: str, repo: str) -> List[str]:
        result = await self._s.execute(
            select(distinct(LineageNode.branch))
            .where(LineageNode.tenant_id == tenant_id, LineageNode.repo == repo)
            .order_by(LineageNode.branch)
        )
        return [row[0] for row in result.all()]

    async def list_functions_for_branch(
        self, tenant_id: str, repo: str, branch: str
    ) -> List[Dict[str, Any]]:
        result = await self._s.execute(
            select(LineageNode.asset_id, LineageNode.name, LineageNode.file_path)
            .where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.repo == repo,
                LineageNode.branch == branch,
            )
            .order_by(LineageNode.name)
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
                LineageNode.asset_id,
                LineageNode.name,
                LineageNode.file_path,
                LineageNode.lineno,
                LineageNode.end_lineno,
                LineageNode.source,
            )
            .where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.repo == repo,
                LineageNode.branch == branch,
                LineageNode.name == name,
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
        from sqlalchemy import func as sqlfunc
        # Fetch branches
        result = await self._s.execute(
            select(
                LineageNode.repo,
                LineageNode.branch,
                sqlfunc.max(LineageNode.repo_url).label("repo_url"),
            )
            .where(LineageNode.tenant_id == tenant_id)
            .group_by(LineageNode.repo, LineageNode.branch)
            .order_by(LineageNode.repo, LineageNode.branch)
        )
        rows = result.all()

        # Fetch default branches for all repos (table may not exist yet before migration)
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

    async def resolve_cross_repo_bases(self, tenant_id: str) -> int:
        """
        For every class node with unresolved_bases in this tenant, try to match
        against classes in OTHER repos (at their default branch).

        Matching: qualified_key "application_sdk.templates.SqlMetadataExtractor"
        matches a class "SqlMetadataExtractor" in file "application_sdk/templates/sql_metadata_extractor.py"
        because parent_module("application_sdk/templates/sql_metadata_extractor.py")
                = "application_sdk.templates"
        and "application_sdk.templates.SqlMetadataExtractor" ends with
            "application_sdk.templates.SqlMetadataExtractor" ✅
        """
        # 1. Get default branches per repo
        try:
            settings_result = await self._s.execute(
                select(RepoSettings.repo, RepoSettings.default_branch)
                .where(RepoSettings.tenant_id == tenant_id)
            )
            default_branches: dict = {r: b for r, b in settings_result.all()}
        except Exception:
            default_branches = {}

        # 2. Fetch all class nodes with non-empty unresolved_bases
        # Use jsonb_array_length > 0 — SQLAlchemy != [] doesn't work reliably for JSONB
        unresolved_result = await self._s.execute(
            select(
                LineageNode.asset_id,
                LineageNode.repo,
                LineageNode.branch,
                LineageNode.base_class_ids,
                LineageNode.unresolved_bases,
            ).where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.node_type == "class",
                func.jsonb_array_length(LineageNode.unresolved_bases) > 0,
            )
        )
        nodes_with_unresolved = unresolved_result.all()
        logger.info("resolve_cross_repo_bases: found %d class nodes with unresolved bases", len(nodes_with_unresolved))
        if not nodes_with_unresolved:
            return 0

        # 3. Fetch all class nodes from ALL repos as resolution candidates
        candidates_result = await self._s.execute(
            select(
                LineageNode.asset_id,
                LineageNode.name,
                LineageNode.file_path,
                LineageNode.repo,
                LineageNode.branch,
            ).where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.node_type == "class",
            )
        )
        # Build candidate lookup: list of (asset_id, name, file_path, repo, branch)
        all_candidates = candidates_result.all()

        def _candidate_keys(file_path: str, class_name: str):
            """
            Yield possible qualified keys for a class given its file path.
            e.g. "application_sdk/templates/sql_metadata_extractor.py", "SqlMetadataExtractor"
              → "application_sdk.templates.SqlMetadataExtractor"  (parent dir module)
              → "application_sdk.templates.sql_metadata_extractor.SqlMetadataExtractor" (full file module)
            """
            file_module = file_path.replace("/", ".").replace("\\", ".")
            if file_module.endswith(".py"):
                file_module = file_module[:-3]
            # Strip __init__
            if file_module.endswith(".__init__"):
                file_module = file_module[:-9]
            # Full file module key
            yield f"{file_module}.{class_name}"
            # Parent package key (most common: from pkg.subpkg import Class)
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
                matches = []

                for cand in all_candidates:
                    cand_asset_id, cand_name, cand_file_path, cand_repo, cand_branch = cand
                    # Don't link to self
                    if cand_repo == repo:
                        continue
                    # Only check the default branch of the other repo
                    expected_branch = default_branches.get(cand_repo, "")
                    if expected_branch and cand_branch != expected_branch:
                        continue

                    # asset_id format is "module_path:ClassName" — extract simple class name
                    cand_class_name = cand_asset_id.split(":")[-1]
                    for key in _candidate_keys(cand_file_path, cand_class_name):
                        if qualified_key.endswith(key) or key.endswith(qualified_key):
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

            # Update node with newly resolved base class IDs
            await self._s.execute(
                text("""
                    UPDATE lineage_nodes
                    SET base_class_ids = :base_class_ids,
                        unresolved_bases = :unresolved_bases
                    WHERE asset_id = :asset_id
                      AND tenant_id = :tenant_id
                      AND repo = :repo
                      AND branch = :branch
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

    async def delete_branch(self, tenant_id: str, repo: str, branch: str) -> int:
        result = await self._s.execute(
            delete(LineageNode).where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.repo == repo,
                LineageNode.branch == branch,
            )
        )
        await self._s.flush()
        return result.rowcount

    async def delete_repo(self, tenant_id: str, repo: str) -> int:
        result = await self._s.execute(
            delete(LineageNode).where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.repo == repo,
            )
        )
        await self._s.flush()
        return result.rowcount

    async def set_default_branch(self, tenant_id: str, repo: str, branch: str) -> None:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = pg_insert(RepoSettings).values(
            tenant_id=tenant_id, repo=repo, default_branch=branch
        ).on_conflict_do_update(
            index_elements=["tenant_id", "repo"],
            set_={"default_branch": branch},
        )
        await self._s.execute(stmt)
        await self._s.commit()

    async def fetch_lineage_data(
        self,
        tenant_id: str,
        repo: str,
        branch: str,
        *,
        offset: int = 0,
        limit: int = 100,
        search: str = "",
        filter: str = "connected",
        sort: str = "connections",
    ) -> Dict[str, Any]:
        base_where = [
            LineageNode.tenant_id == tenant_id,
            LineageNode.repo == repo,
            LineageNode.branch == branch,
        ]

        # --- Stats (one lightweight count query) ---
        stats_result = await self._s.execute(
            select(
                func.count().label("total"),
                func.count().filter(
                    or_(
                        func.jsonb_array_length(LineageNode.downstream_ids) > 0,
                        func.jsonb_array_length(LineageNode.upstream_ids) > 0,
                    )
                ).label("connected"),
            ).where(*base_where)
        )
        stats_row = stats_result.one()
        total = stats_row.total
        connected = stats_row.connected
        isolated = total - connected

        # --- Filtered + paginated query ---
        up_len = func.jsonb_array_length(LineageNode.upstream_ids)
        down_len = func.jsonb_array_length(LineageNode.downstream_ids)

        q = select(
            LineageNode.asset_id,
            LineageNode.node_type,
            LineageNode.name,
            LineageNode.file_path,
            LineageNode.lineno,
            LineageNode.class_id,
            down_len.label("downstream_count"),
            up_len.label("upstream_count"),
        ).where(*base_where)

        # Search
        if search:
            pattern = f"%{search}%"
            q = q.where(
                or_(
                    LineageNode.name.ilike(pattern),
                    LineageNode.file_path.ilike(pattern),
                )
            )

        # Filter
        if filter == "connected":
            q = q.where(or_(down_len > 0, up_len > 0))
        elif filter == "upstream":
            q = q.where(up_len > 0)
        elif filter == "downstream":
            q = q.where(down_len > 0)

        # Sort
        if sort == "connections":
            q = q.order_by((down_len + up_len).desc(), LineageNode.name)
        elif sort == "file":
            q = q.order_by(LineageNode.file_path, LineageNode.name)
        else:
            q = q.order_by(LineageNode.name)

        # Count filtered total (before pagination)
        count_q = select(func.count()).select_from(q.subquery())
        filtered_total = (await self._s.execute(count_q)).scalar() or 0

        # Paginate
        q = q.offset(offset).limit(limit)
        result = await self._s.execute(q)

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
            for row in result.all()
        ]

        return {
            "nodes": nodes,
            "total": total,
            "connected": connected,
            "isolated": isolated,
            "filtered_total": filtered_total,
            "offset": offset,
            "limit": limit,
        }

    async def fetch_node_with_neighbors(
        self, tenant_id: str, repo: str, branch: str, asset_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await self._s.execute(
            select(
                LineageNode.asset_id,
                LineageNode.node_type,
                LineageNode.name,
                LineageNode.file_path,
                LineageNode.lineno,
                LineageNode.end_lineno,
                LineageNode.source,
                LineageNode.module_context,
                LineageNode.class_id,
                LineageNode.base_class_ids,
                LineageNode.downstream_ids,
                LineageNode.upstream_ids,
                LineageNode.relationships,
            ).where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.repo == repo,
                LineageNode.branch == branch,
                LineageNode.asset_id == asset_id,
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

        _neighbor_cols = [
            LineageNode.asset_id,
            LineageNode.node_type,
            LineageNode.name,
            LineageNode.file_path,
            LineageNode.lineno,
            LineageNode.end_lineno,
            LineageNode.source,
            LineageNode.class_id,
            LineageNode.downstream_ids,
            LineageNode.upstream_ids,
        ]

        # For function nodes: fetch callers (downstream_ids) and callees (upstream_ids)
        callers: List[Dict[str, Any]] = []
        callees: List[Dict[str, Any]] = []
        if node["node_type"] != "class":
            if node["downstream_ids"]:
                up_result = await self._s.execute(
                    select(*_neighbor_cols).where(
                        LineageNode.tenant_id == tenant_id,
                        LineageNode.repo == repo,
                        LineageNode.branch == branch,
                        LineageNode.asset_id.in_(node["downstream_ids"]),
                    )
                )
                callers = [
                    {
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
                    for r in up_result.all()
                ]

            if node.get("upstream_ids"):
                callee_result = await self._s.execute(
                    select(*_neighbor_cols).where(
                        LineageNode.tenant_id == tenant_id,
                        LineageNode.repo == repo,
                        LineageNode.branch == branch,
                        LineageNode.asset_id.in_(node["upstream_ids"]),
                    )
                )
                callees = [
                    {
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
                    for r in callee_result.all()
                ]

        # For class nodes, fetch methods and use pre-computed class-level edges.
        methods: List[Dict[str, Any]] = []
        if node["node_type"] == "class":
            # Methods query: fetch method list for sidebar display
            methods_result = await self._s.execute(
                select(
                    LineageNode.asset_id,
                    LineageNode.name,
                    LineageNode.file_path,
                    LineageNode.lineno,
                    LineageNode.end_lineno,
                ).where(
                    LineageNode.tenant_id == tenant_id,
                    LineageNode.repo == repo,
                    LineageNode.branch == branch,
                    LineageNode.class_id == asset_id,
                    LineageNode.node_type == "function",
                ).order_by(LineageNode.lineno)
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

            # Class-level call neighbors + inheritance neighbors in one query.
            # upstream_ids/downstream_ids are pre-computed at build time.
            # base_class_ids gives parent classes; children found via contains.
            parent_ids = [cid for cid in (node["base_class_ids"] or []) if cid != asset_id]
            all_neighbor_ids = set(node["upstream_ids"]) | set(node["downstream_ids"]) | set(parent_ids)

            # Combine: fetch call neighbors by ID + inheritance children via contains
            neighbor_where = [
                LineageNode.tenant_id == tenant_id,
                LineageNode.node_type == "class",
                LineageNode.asset_id != asset_id,
            ]
            conditions = [LineageNode.base_class_ids.contains([asset_id])]
            if all_neighbor_ids:
                conditions.append(LineageNode.asset_id.in_(all_neighbor_ids))
            neighbor_where.append(or_(*conditions))

            neighbor_result = await self._s.execute(
                select(
                    LineageNode.asset_id, LineageNode.name, LineageNode.file_path,
                    LineageNode.lineno, LineageNode.end_lineno,
                    LineageNode.repo, LineageNode.repo_url, LineageNode.branch,
                    LineageNode.base_class_ids,
                    LineageNode.upstream_ids, LineageNode.downstream_ids,
                ).where(*neighbor_where)
            )

            upstream_set = set(node["upstream_ids"])
            downstream_set = set(node["downstream_ids"])
            parent_id_set = set(parent_ids)
            seen_callees = set()
            seen_callers = set()

            neighbor_rows = neighbor_result.all()

            # Count children (extended_by) per class from the neighbor rows
            # so downstream_count includes classes that extend this one
            neighbor_ids = [r.asset_id for r in neighbor_rows]
            child_counts: Dict[str, int] = {}
            if neighbor_ids:
                child_count_result = await self._s.execute(
                    text("""
                        SELECT parent_id, COUNT(*) AS cnt
                        FROM lineage_nodes,
                             jsonb_array_elements_text(base_class_ids) AS parent_id
                        WHERE tenant_id = :tenant_id
                          AND node_type = 'class'
                        GROUP BY parent_id
                    """),
                    {"tenant_id": tenant_id},
                )
                child_counts = {row[0]: row[1] for row in child_count_result.all()}

            for r in neighbor_rows:
                cross = r.repo != repo
                # Count includes both call edges and inheritance for accuracy
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
                # Upstream (callees): extends takes priority over calls
                if r.asset_id not in seen_callees:
                    if r.asset_id in parent_id_set:
                        seen_callees.add(r.asset_id)
                        callees.append({**base, "relationship_type": "extends"})
                    elif r.asset_id in upstream_set:
                        seen_callees.add(r.asset_id)
                        callees.append({**base, "relationship_type": "calls"})
                # Downstream (callers): extended_by takes priority over calls
                if r.asset_id not in seen_callers:
                    if asset_id in (r.base_class_ids or []):
                        seen_callers.add(r.asset_id)
                        callers.append({**base, "relationship_type": "extended_by"})
                    elif r.asset_id in downstream_set:
                        seen_callers.add(r.asset_id)
                        callers.append({**base, "relationship_type": "calls"})

        # Atlan model: upstream = dependencies (callees), downstream = consumers (callers).
        # downstream_ids stores callers; API upstream = callees, API downstream = callers.
        return {"node": node, "upstream": callees, "downstream": callers, "methods": methods}
