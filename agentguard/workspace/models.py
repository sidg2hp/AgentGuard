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


class EvidencePack(Contract):
    name: str = Field(min_length=1, max_length=120)
    documents: list[Document] = Field(min_length=1, max_length=5)


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

    @field_validator("profiles")
    @classmethod
    def unique_profiles(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Select each profile once.")
        return values


Submission = Annotated[Research | Evaluation, Field(discriminator="kind")]
