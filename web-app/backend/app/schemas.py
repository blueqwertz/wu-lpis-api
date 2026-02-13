from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "running", "completed", "failed"]


class SignupJobRequest(BaseModel):
    username: str = Field(..., description="User identifier")
    sectionpoint: str | None = None
    planobject: str | None = None
    primary_course: str
    fallback_course: str | None = None
    offset_seconds: float = 0.7


class SignupJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime


class JobDetails(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    request: dict[str, Any]
    result: dict[str, Any] | None = None


class JobLogLine(BaseModel):
    id: int
    job_id: str
    level: str
    message: str
    created_at: datetime
