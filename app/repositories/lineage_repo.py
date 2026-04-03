from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, distinct, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lineage_node import LineageNode


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
                        "workflow_id": workflow_id,
                        "run_id": run_id,
                        "name": a["name"],
                        "file_path": a["file"],
                        "lineno": int(a["lineno"]),
                        "end_lineno": a.get("end_lineno"),
                        "source": a.get("source"),
                        "upstream_ids": a.get("upstream_ids") or [],
                    }
                    for a in lineage_assets
                ],
            )

        await self._s.flush()
        return len(lineage_assets)

    async def list_repo_branches(self, tenant_id: str) -> List[Dict[str, str]]:
        result = await self._s.execute(
            select(
                distinct(LineageNode.repo), LineageNode.branch
            )
            .where(LineageNode.tenant_id == tenant_id)
            .order_by(LineageNode.repo, LineageNode.branch)
        )
        return [{"repo": r, "branch": b} for r, b in result.all()]

    async def fetch_lineage_data(
        self, tenant_id: str, repo: str, branch: str
    ) -> Dict[str, Any]:
        result = await self._s.execute(
            select(
                LineageNode.asset_id,
                LineageNode.name,
                LineageNode.file_path,
                LineageNode.lineno,
                LineageNode.upstream_ids,
            )
            .where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.repo == repo,
                LineageNode.branch == branch,
            )
            .order_by(LineageNode.name)
        )

        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, str]] = []

        for row in result.all():
            upstream_ids = row.upstream_ids or []
            node = {
                "id": row.asset_id,
                "name": row.name,
                "file": row.file_path,
                "lineno": row.lineno,
                "upstream_ids": upstream_ids,
            }
            nodes.append(node)
            for uid in upstream_ids:
                edges.append({"source": uid, "target": row.asset_id})

        return {"nodes": nodes, "edges": edges}

    async def fetch_node_with_neighbors(
        self, tenant_id: str, repo: str, branch: str, asset_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await self._s.execute(
            select(
                LineageNode.asset_id,
                LineageNode.name,
                LineageNode.file_path,
                LineageNode.lineno,
                LineageNode.end_lineno,
                LineageNode.source,
                LineageNode.upstream_ids,
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
            "name": row.name,
            "file": row.file_path,
            "lineno": row.lineno,
            "end_lineno": row.end_lineno,
            "source": row.source,
            "upstream_ids": row.upstream_ids or [],
        }

        _neighbor_cols = [
            LineageNode.asset_id,
            LineageNode.name,
            LineageNode.file_path,
            LineageNode.lineno,
            LineageNode.end_lineno,
            LineageNode.source,
            LineageNode.upstream_ids,
        ]

        upstream: List[Dict[str, Any]] = []
        if node["upstream_ids"]:
            up_result = await self._s.execute(
                select(*_neighbor_cols).where(
                    LineageNode.tenant_id == tenant_id,
                    LineageNode.repo == repo,
                    LineageNode.branch == branch,
                    LineageNode.asset_id.in_(node["upstream_ids"]),
                )
            )
            upstream = [
                {
                    "id": r.asset_id,
                    "name": r.name,
                    "file": r.file_path,
                    "lineno": r.lineno,
                    "end_lineno": r.end_lineno,
                    "source": r.source,
                    "upstream_ids": r.upstream_ids or [],
                }
                for r in up_result.all()
            ]

        down_result = await self._s.execute(
            select(*_neighbor_cols).where(
                LineageNode.tenant_id == tenant_id,
                LineageNode.repo == repo,
                LineageNode.branch == branch,
                LineageNode.upstream_ids.contains([asset_id]),
                LineageNode.asset_id != asset_id,
            )
        )
        downstream = [
            {
                "id": r.asset_id,
                "name": r.name,
                "file": r.file_path,
                "lineno": r.lineno,
                "end_lineno": r.end_lineno,
                "source": r.source,
                "upstream_ids": r.upstream_ids or [],
            }
            for r in down_result.all()
        ]

        # Compute downstream_count for every neighbor in one query
        all_neighbor_ids = list(
            {n["id"] for n in upstream} | {n["id"] for n in downstream}
        )
        down_counts: Dict[str, int] = {}
        if all_neighbor_ids:
            dc_result = await self._s.execute(
                text(
                    "SELECT elem, COUNT(DISTINCT asset_id) "
                    "FROM lineage_nodes, "
                    "     jsonb_array_elements_text(upstream_ids) AS elem "
                    "WHERE tenant_id = :tid AND repo = :repo "
                    "  AND branch = :branch AND elem = ANY(:ids) "
                    "  AND asset_id != elem "
                    "GROUP BY elem"
                ),
                {
                    "tid": tenant_id,
                    "repo": repo,
                    "branch": branch,
                    "ids": all_neighbor_ids,
                },
            )
            down_counts = {row[0]: row[1] for row in dc_result.all()}

        for n in upstream:
            n["downstream_count"] = down_counts.get(n["id"], 0)
        for n in downstream:
            n["downstream_count"] = down_counts.get(n["id"], 0)

        # Atlan model: upstream = dependencies (callees), downstream = consumers (callers).
        # DB stores callers in upstream_ids, so swap the labels here.
        return {"node": node, "upstream": downstream, "downstream": upstream}
