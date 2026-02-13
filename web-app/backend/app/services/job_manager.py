import asyncio
import json
from collections import defaultdict
from uuid import uuid4

from app.schemas import SignupJobRequest
from app.services.signup_provider import MockSignupProvider, SignupCommand, SignupProvider
from app.services.store import JobStore


class JobManager:
    def __init__(self, store: JobStore, provider: SignupProvider | None = None):
        self.store = store
        self.provider = provider or MockSignupProvider()
        self.subscribers: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)

    async def create_job(self, payload: SignupJobRequest) -> dict:
        job_id = str(uuid4())
        job = self.store.create_job(job_id, payload.model_dump())
        asyncio.create_task(self._run_job(job_id, payload))
        return job

    async def _run_job(self, job_id: str, payload: SignupJobRequest) -> None:
        self.store.update_job(job_id, status="running")

        async def emit_log(level: str, message: str):
            log_line = self.store.append_log(job_id, level, message)
            await self._publish(job_id, {"type": "log", "payload": self._serialize(log_line)})

        try:
            cmd = SignupCommand(
                username=payload.username,
                primary_course=payload.primary_course,
                fallback_course=payload.fallback_course,
                sectionpoint=payload.sectionpoint,
                planobject=payload.planobject,
                offset_seconds=payload.offset_seconds,
            )
            result = await self.provider.run(cmd, emit_log)
            self.store.update_job(job_id, status="completed", result=result)
            await self._publish(job_id, {"type": "status", "payload": {"status": "completed"}})
        except Exception as exc:
            self.store.append_log(job_id, "error", f"Job failed: {exc}")
            self.store.update_job(job_id, status="failed", result={"error": str(exc)})
            await self._publish(job_id, {"type": "status", "payload": {"status": "failed"}})

    async def _publish(self, job_id: str, event: dict) -> None:
        raw = json.dumps(event)
        for queue in list(self.subscribers[job_id]):
            await queue.put(raw)

    def subscribe(self, job_id: str) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self.subscribers[job_id].add(q)
        return q

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[str]) -> None:
        self.subscribers[job_id].discard(queue)

    @staticmethod
    def _serialize(data: dict) -> dict:
        out = dict(data)
        if "created_at" in out:
            out["created_at"] = out["created_at"].isoformat()
        return out
