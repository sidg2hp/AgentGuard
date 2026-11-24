import os
from dataclasses import dataclass, field
from pathlib import Path
import secrets


def token_path():
    return Path(os.getenv("AGENTGUARD_TOKEN_FILE") or ".agentguard/token")


def init_token():
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(secrets.token_urlsafe(40) + "\n")
    return path


@dataclass(frozen=True)
class Settings:
    database: Path
    token: str = field(repr=False)
    allow_web: bool = False
    allow_model: bool = False
    capacity: int = 100

    @classmethod
    def load(cls):
        token = os.getenv("AGENTGUARD_API_TOKEN")
        if not token:
            try:
                token = token_path().read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                raise ValueError("Run agentguard init or configure AGENTGUARD_API_TOKEN.") from None
        if not token.isascii() or len(token) < 32 or any(c.isspace() for c in token):
            raise ValueError("Use a workspace token of at least 32 ASCII characters without spaces.")
        capacity = int(os.getenv("AGENTGUARD_QUEUE_CAPACITY", "100"))
        if not 1 <= capacity <= 10000:
            raise ValueError("AGENTGUARD_QUEUE_CAPACITY must be between 1 and 10000.")
        web_flag = os.getenv("AGENTGUARD_ALLOW_WEB", "1")
        if web_flag not in {"0", "1"}:
            raise ValueError("AGENTGUARD_ALLOW_WEB must be 0 or 1.")
        return cls(Path(os.getenv("AGENTGUARD_DB") or ".agentguard/workspace.db"), token,
                   web_flag == "1", os.getenv("AGENTGUARD_ALLOW_MODEL") == "1", capacity)

    @property
    def web_unavailable_reason(self):
        if not self.allow_web:
            return "disabled"
        if not (os.getenv("TAVILY_API_KEY") or "").strip():
            return "missing_key"
        return None

    def protected_values(self):
        return (self.token, os.getenv("TAVILY_API_KEY") or "")
