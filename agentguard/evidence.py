"""Bound untrusted evidence and release only attributed source excerpts."""
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from agentguard.policy import protected
from agentguard.catalog import catalog_text


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


_BOUNDARY = re.compile(r"(?<=[.!?])\s+|[\r\n]+")


def excerpts(text, *, limit=None):
    if limit is not None and len(text) > limit:
        # Keep complete excerpts. A truncated sentence could lose a qualification.
        window = text[:limit + 1]
        last = 0
        for match in _BOUNDARY.finditer(window):
            last = match.start()
        text = window[:last]
    return [s.strip() for s in _BOUNDARY.split(text) if 0 < len(s.strip()) <= 1000]


def collect(rows, secrets=()):
    documents = {}
    for index, row in enumerate(rows[:5] if isinstance(rows, list) else [], start=1):
        if not isinstance(row, dict) or not isinstance(row.get("content"), str):
            continue
        lines = [s for s in excerpts(row["content"], limit=4000) if not protected(s, secrets)]
        if lines:
            uri = row.get("url")
            catalog_source = catalog_text(uri) == row["content"] and not protected(uri, secrets)
            documents[f"S{index}"] = {
                "excerpts": lines, "url": uri if catalog_source else source_url(uri, secrets),
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
