import asyncio
from dataclasses import dataclass


@dataclass
class SignupCommand:
    username: str
    primary_course: str
    fallback_course: str | None
    sectionpoint: str | None
    planobject: str | None
    offset_seconds: float


class SignupProvider:
    async def run(self, command: SignupCommand, emit_log):
        raise NotImplementedError


class MockSignupProvider(SignupProvider):
    async def run(self, command: SignupCommand, emit_log):
        await emit_log("info", f"Preparing signup for user {command.username}")
        await asyncio.sleep(1)
        await emit_log("info", f"Validating primary course {command.primary_course}")
        await asyncio.sleep(1)
        if command.fallback_course:
            await emit_log("info", f"Fallback course available: {command.fallback_course}")
        await asyncio.sleep(command.offset_seconds)
        await emit_log("info", "Submitting registration request")
        await asyncio.sleep(1)
        result = {
            "registered_course": command.primary_course,
            "fallback_used": False,
            "message": "Registration simulation completed",
        }
        await emit_log("info", "Job completed")
        return result
