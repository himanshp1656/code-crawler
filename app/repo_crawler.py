import logging
import os
import tempfile
from typing import Optional

from git import Repo

logger = logging.getLogger(__name__)


def _safe_repo_name(repo_url: str) -> str:
    name = repo_url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repository"


def clone_repository(
    repo_url: str,
    branch: str = "main",
    base_dir: Optional[str] = None,
    auth_url: Optional[str] = None,
) -> str:
    """
    Clone (or refresh) a Git repository and return the local path.

    Written to be retry-safe:
    - If the repo already exists, we fetch + checkout the branch and pull.
    - If clone fails, we clean our temp directory so the next retry starts clean.
    """
    if base_dir is None:
        base_dir = os.path.join(os.getcwd(), "output", "repos")

    os.makedirs(base_dir, exist_ok=True)

    repo_name = _safe_repo_name(repo_url)
    target_dir = os.path.join(base_dir, f"{repo_name}-{branch}")

    # auth_url has PAT injected — used for git ops, never logged
    clone_url = auth_url or repo_url

    try:
        if os.path.isdir(os.path.join(target_dir, ".git")):
            logger.info("Reusing existing clone at %s", target_dir)
            repo = Repo(target_dir)
            # Update remote URL in case PAT changed
            repo.remotes.origin.set_url(clone_url)
            repo.remotes.origin.fetch()
            repo.git.checkout(branch)
            repo.git.pull()
        else:
            logger.info("Cloning %s (branch=%s) into %s", repo_url, branch, target_dir)
            Repo.clone_from(clone_url, target_dir, branch=branch)

        return target_dir
    except Exception:
        logger.exception("Failed to clone or refresh repository %s", repo_url)
        try:
            if os.path.isdir(target_dir) and os.path.commonpath(
                [base_dir, target_dir]
            ) == base_dir:
                # best-effort cleanup
                for root, dirs, files in os.walk(target_dir, topdown=False):
                    for f in files:
                        os.remove(os.path.join(root, f))
                    for d in dirs:
                        os.rmdir(os.path.join(root, d))
                os.rmdir(target_dir)
        except Exception:
            logger.warning("Failed to clean up directory after clone failure: %s", target_dir)
        raise

