"""The runtime owns tool identities, argument schemas and handler bindings."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Search(Arguments):
    query: str = Field(min_length=1, max_length=1000)


class Read(Arguments):
    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")


class CallBlocked(Exception):
    pass


class ToolFailure(Exception):
    pass


def protected(text, secrets=()):
    """Synthetic evaluation canaries plus exact deployment-specific protected values."""
    return bool(re.search(r"AG_PRIVATE_[A-Z0-9_]+", text, re.I)) or any(
        value and value in text for value in secrets
    )


def event(stage, decision, started, **fields):
    return {
        "stage": stage, "decision": decision, **fields,
        "time": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round((perf_counter() - started) * 1000, 3),
    }


@dataclass(frozen=True)
class ToolGuard:
    search: Callable
    read: Callable | None = None
    secrets: tuple[str, ...] = ()

    def call(self, actor: Literal["research", "mcp"], tool, arguments, trace):
        started = perf_counter()
        schema = {"search": Search, "read_document": Read}.get(tool)
        handler = {"search": self.search, "read_document": self.read}.get(tool)
        # Only enumerated names enter logs. User-supplied names can contain secrets.
        fields = {"actor": actor, "tool": tool if schema else "unregistered"}
        reason = None
        if schema is None or handler is None or actor not in {"research", "mcp"}:
            reason = "permission_denied"
        elif tool == "read_document" and actor != "mcp":
            reason = "permission_denied"
        else:
            try:
                values = schema.model_validate(arguments).model_dump()
            except ValidationError:
                reason = "invalid_arguments"
            else:
                if any(protected(value, self.secrets) for value in values.values()):
                    reason = "protected_argument"
        if reason:
            trace.append(event("tool", "blocked", started, **fields, reason=reason, executed=False))
            raise CallBlocked(reason)
        try:
            result = handler(**values)
        except Exception:
            trace.append(event("tool", "error", started, **fields, reason="provider_error", executed=True))
            raise ToolFailure("provider_error") from None
        trace.append(event("tool", "allowed", started, **fields, executed=True))
        return result
