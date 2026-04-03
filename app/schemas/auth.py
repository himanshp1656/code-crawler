from typing import Literal, Optional

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    handle: str = Field(
        ...,
        min_length=3,
        max_length=39,
        pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
    )
    display_name: str = Field(..., min_length=1, max_length=100)
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8)
    account_type: Literal["personal", "organization"] = "personal"


class IngestionRequest(BaseModel):
    github_repo_url: str = Field(..., description="GitHub repository URL")
    branch: str = Field("main", description="Git branch name")
    language: str = Field("python", description="Source language (python only)")
    tenant_id: str = Field(
        "default",
        description="Tenant id/slug (used to isolate data in Postgres)",
    )
    output_path: Optional[str] = Field(
        None, description="Optional custom path for lineage JSON"
    )


class IngestionResponse(BaseModel):
    workflow_id: str
    run_id: str
