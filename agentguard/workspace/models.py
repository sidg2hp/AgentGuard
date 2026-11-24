"""Public submission contracts. Provider addresses and credentials belong to the operator."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentguard.evidence import source_url


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class Document(Contract):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=4000)
    url: str = Field(default="", max_length=2048)

    @field_validator("url")
    @classmethod
    def public_reference(cls, value):
        if value and source_url(value, ()) != value:
            raise ValueError("Use an HTTP(S) reference without credentials, query parameters or fragments.")
        return value


class AcceptanceCheck(Contract):
    query: str = Field(min_length=1, max_length=1000)
    status: Literal["answered", "insufficient"] = "answered"
    contains: str = Field(default="", max_length=500)
    document: int | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="after")
    def expectation(self):
        if self.status == "answered" and not (self.contains or self.document):
            raise ValueError("An answer check needs expected text or a document number.")
        if self.status == "insufficient" and (self.contains or self.document):
            raise ValueError("An abstention check cannot require evidence.")
        return self


class EvidencePack(Contract):
    name: str = Field(min_length=1, max_length=120)
    documents: list[Document] = Field(min_length=1, max_length=5)
    checks: list[AcceptanceCheck] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def cited_documents_exist(self):
        if any(c.document and c.document > len(self.documents) for c in self.checks):
            raise ValueError("An acceptance check references a missing document.")
        return self


class Research(Contract):
    kind: Literal["research"]
    query: str = Field(min_length=1, max_length=1000)
    source: Literal["catalog", "pack", "web"] = "catalog"
    pack_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    drafter: Literal["extractive", "ollama"] = "extractive"

    @model_validator(mode="after")
    def check_pack(self):
        if (self.source == "pack") != (self.pack_id is not None):
            raise ValueError("Specify a pack ID exactly when source is pack.")
        return self


Profile = Literal["baseline-replay", "guarded-replay", "guarded-extractive", "guarded-ollama"]


class Evaluation(Contract):
    kind: Literal["evaluation"]
    profiles: list[Profile] = Field(default_factory=lambda: ["baseline-replay", "guarded-replay"],
                                    min_length=1, max_length=4)
    category: str | None = Field(default=None, max_length=64)
    scenario: str | None = Field(default=None, max_length=64)
    pack_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def active_profiles_for_packs(self):
        if self.pack_id and any(p.endswith("-replay") for p in self.profiles):
            raise ValueError("Pack checks require extractive or Ollama drafting.")
        return self

    @field_validator("profiles")
    @classmethod
    def unique_profiles(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Select each profile once.")
        return values


Submission = Annotated[Research | Evaluation, Field(discriminator="kind")]


class Receipt(BaseModel):
    id: str


class SavedPack(EvidencePack):
    id: str


class PackSummary(Receipt):
    name: str
    size: int
    created: float
    checks: int


class RunSummary(Receipt):
    kind: Literal["research", "evaluation"]
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    created: float
    updated: float
    attempt: int
    progress: int
    total: int
    error: str | None
    suite_sha256: str | None


class LifecycleEvent(BaseModel):
    seq: int
    at: float
    state: str
    attempt: int


class RunDetail(RunSummary):
    request: Submission
    result: dict | None
    events: list[LifecycleEvent]
