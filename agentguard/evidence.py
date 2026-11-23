"""Bound untrusted evidence and release only attributed source excerpts."""
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from agentguard.policy import protected


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: str = Field(pattern=r"^S[1-5]$")
    quote: str = Field(min_length=1, max_length=1000)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    claims: list[Claim] = Field(max_length=8)


def source_url(value, secrets):
    if not isinstance(value, str) or len(value) > 2048 or protected(value, secrets):
        return ""
    try:
        url = urlsplit(value)
        if url.scheme in {"https", "http"} and url.hostname and not url.username and not url.password:
            return url._replace(query="", fragment="").geturl()
    except ValueError:
        pass
    return ""


def excerpts(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|[\r\n]+", text) if 0 < len(s.strip()) <= 1000]


def collect(rows, secrets=()):
    documents = {}
    for row in rows[:5] if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("content"), str):
            continue
        lines = [s for s in excerpts(row["content"][:4000]) if not protected(s, secrets)]
        if lines:
            documents[f"S{len(documents) + 1}"] = {
                "excerpts": lines, "url": source_url(row.get("url"), secrets),
            }
    return documents


NO_EVIDENCE = "The available sources provide insufficient evidence to answer this question."
BLOCKED = "The draft failed output validation."
STOPPED = "The research request could not be completed."


def release(candidate, documents, secrets=()):
    draft = Draft.model_validate(candidate)
    if not draft.claims:
        return NO_EVIDENCE, "insufficient", {}
    sources = {}
    lines = []
    for claim in draft.claims:
        doc = documents.get(claim.source)
        if protected(claim.quote, secrets) or not doc or claim.quote not in doc["excerpts"]:
            return BLOCKED, "blocked", {}
        sources[claim.source] = doc["url"]
        lines.append(f"{claim.quote} [{claim.source}]")
    return "\n".join(lines), "answered", sources
