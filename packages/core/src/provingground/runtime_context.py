"""Context variables set by the runner and read by targets/providers during one invocation."""

from __future__ import annotations

from contextvars import ContextVar

#: Retry attempt (0 = first try) of the current target invocation.
current_attempt: ContextVar[int] = ContextVar("pground_attempt", default=0)
#: Case id being evaluated (lets simulators and agents tag spans).
current_case_id: ContextVar[str | None] = ContextVar("pground_case_id", default=None)
