# Improvement Action Items

## 1. Move `source` out of `lineage_nodes` (High Priority)

**Problem:** Every row in `lineage_nodes` stores full function source code as TEXT. At scale (10k+ functions), this makes rows fat — fewer fit in Postgres 8KB pages, meaning more I/O per query. `fetch_node_with_neighbors` is the most affected: it pulls source for the focal node and all its neighbors in one shot.

**Fix:** Create a separate `function_source` table and join only when source is actually needed (asset detail page).

```sql
CREATE TABLE function_source (
    asset_id    TEXT        NOT NULL,
    tenant_id   TEXT        NOT NULL,
    repo        TEXT        NOT NULL,
    branch      TEXT        NOT NULL,
    source      TEXT,
    PRIMARY KEY (asset_id, tenant_id, repo, branch)
);
```

Remove the `source` column from `lineage_nodes`. Update `build_lineage_activity` to insert source separately. Update `fetch_node_with_neighbors` to JOIN on demand.

**Impact:** Main list query and neighbor-lookup stay narrow and fast regardless of function body size.

---

## 2. Fix `resolve_cross_repo_bases` — add repo/branch scoping (Medium Priority)

**Problem:** This method fetches all class nodes for the entire tenant with no repo filter:
```python
# lineage_repo.py — candidates query
.where(
    LineageNode.tenant_id == tenant_id,
    LineageNode.node_type == "class",   # no repo/branch filter
)
```
As more repos are added, the scan size grows proportionally. 10 repos = 10x cost.

**Fix:** Limit candidates to repos that have a default branch set (already fetched in `default_branches`). Filter `LineageNode.repo.in_(default_branches.keys())` and `LineageNode.branch.in_(default_branches.values())`.

---

## 3. Add trigram index for ILIKE search (Low Priority)

**Problem:** Search queries use `ILIKE '%q%'` which forces a sequential scan even with the `ix_ln_name` index. The index only helps prefix/exact matches and ORDER BY.

**Fix:** Add a GIN trigram index using `pg_trgm` extension.

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX ix_ln_name_trgm ON lineage_nodes USING gin (name gin_trgm_ops);
```

**Impact:** `ILIKE '%query%'` becomes index-assisted. Meaningful once repos have 5k+ functions.

---

## 4. Incremental crawl (git diff fetch) (Medium Priority)

**Problem:** Re-crawling a branch re-parses every file even when only a few changed.

**Plan:**
1. Store `last_crawled_commit VARCHAR` in `repo_settings` (migration).
2. New endpoint `GET /api/repos/diff-preview?repo=&branch=` — runs `git diff <stored_sha>..HEAD --name-only -- '*.py'` and returns changed files.
3. Workflow accepts optional `changed_files: list[str]`; `parse_repo_activity` skips unchanged files; `build_lineage_activity` deletes+reinserts only nodes from changed files.
4. After crawl completes, persist current HEAD SHA to `repo_settings`.
5. Frontend: when re-crawling an already-crawled branch, show diff preview modal with "Incremental" vs "Full re-crawl" choice.

**Caveat:** Cross-file call edges may go stale if a caller changes but the callee file is not in the diff set. Full re-crawl is always safe; incremental covers ~80% of real-world cases.

---

## 5. Consider JSONB array size limits (Low Priority)

**Problem:** Functions called from many places (e.g. a shared utility) accumulate large `upstream_ids` / `downstream_ids` arrays. GIN index update cost grows with array size on every write.

**Fix:** No immediate action needed. If any function exceeds ~500 entries in these arrays, consider capping at write time or moving call edges to a separate `call_edges` table for better query flexibility.
