import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.schemas import JobDetails, JobLogLine, SignupJobRequest, SignupJobResponse
from app.services.job_manager import JobManager
from app.services.store import JobStore

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
store = JobStore()
manager = JobManager(store)


def get_manager() -> JobManager:
    return manager


def get_store() -> JobStore:
    return store


@router.post("", response_model=SignupJobResponse)
async def create_job(payload: SignupJobRequest, jm: JobManager = Depends(get_manager)):
    job = await jm.create_job(payload)
    return SignupJobResponse(job_id=job["job_id"], status=job["status"], created_at=job["created_at"])


@router.get("/{job_id}", response_model=JobDetails)
def get_job(job_id: str, st: JobStore = Depends(get_store)):
    job = st.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobDetails(**job)


@router.get("/{job_id}/logs", response_model=list[JobLogLine])
def get_job_logs(job_id: str, st: JobStore = Depends(get_store)):
    if not st.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return [JobLogLine(**line) for line in st.get_logs(job_id)]


@router.get("/{job_id}/logs/stream")
async def stream_job_logs(job_id: str, jm: JobManager = Depends(get_manager), st: JobStore = Depends(get_store)):
    if not st.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        history = st.get_logs(job_id)
        for line in history:
            payload = {
                "type": "log",
                "payload": {
                    "id": line["id"],
                    "job_id": line["job_id"],
                    "level": line["level"],
                    "message": line["message"],
                    "created_at": line["created_at"].isoformat(),
                },
            }
            yield {"event": "message", "data": json.dumps(payload)}

        queue = jm.subscribe(job_id)
        try:
            while True:
                event = await queue.get()
                yield {"event": "message", "data": event}
        except asyncio.CancelledError:
            pass
        finally:
            jm.unsubscribe(job_id, queue)

    return EventSourceResponse(event_generator())
