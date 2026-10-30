"""Portable graph records; no user cases or provider credentials are persisted."""

import hashlib
import json
import math
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Layer = Literal["fact", "ontology", "rule"]
Kind = Literal[
    "Case",
    "Article",
    "Issue",
    "Feature",
    "Community",
    "Interpretation",
    "ChecklistItem",
    "Reference",
]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identity(kind, value):
    return f"lg:{kind}:" + hashlib.sha256(canonical(value).encode()).hexdigest()[:32]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceSpan(Strict):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    id: str
    source_id: str
    source_url: str
    title: str
    section_key: str
    text: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    version: str | None = None
    effective_from: date | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def verify_digest(self):
        if hashlib.sha256(self.text.encode()).hexdigest() != self.sha256:
            raise ValueError("Legal evidence text digest mismatch")
        return self


class LegalNode(Strict):
    id: str
    kind: Kind
    layers: list[Layer] = Field(min_length=1)
    label: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    vector: list[float] = Field(default_factory=list)


class LegalEdge(Strict):
    source: str
    target: str
    relation: str = Field(pattern=r"^[A-Z][A-Z_]{1,63}$")
    evidence_ids: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class GraphSnapshot(Strict):
    schema_version: Literal["legal-hierargraph-v1"] = "legal-hierargraph-v1"
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    nodes: list[LegalNode]
    edges: list[LegalEdge]
    evidence: list[EvidenceSpan]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def linked(self):
        ids = {n.id for n in self.nodes}
        eids = {e.id for e in self.evidence}
        if len(ids) != len(self.nodes) or len(eids) != len(self.evidence):
            raise ValueError("Duplicate legal graph identity")
        links = {(e.source, e.relation, e.target) for e in self.edges}
        if len(links) != len(self.edges):
            raise ValueError("Duplicate legal graph edge")
        for n in self.nodes:
            if not set(n.evidence_ids) <= eids:
                raise ValueError("Legal node evidence does not resolve")
            if any(not math.isfinite(v) for v in n.vector):
                raise ValueError("Legal vector contains nonfinite values")
        kinds = {n.id: n.kind for n in self.nodes}
        relation_kinds = {
            "CITES": ({"Case"}, {"Article"}),
            "RESOLVES_CASE": ({"Reference"}, {"Case"}),
            "HAS_CHECKLIST": ({"Article"}, {"ChecklistItem"}),
            "HAS_INTERPRETATION": ({"Article"}, {"Interpretation"}),
            "HAS_ISSUE": ({"Case"}, {"Issue"}),
            "RELATED_ISSUE": ({"Article"}, {"Issue"}),
            "HAS_FEATURE": ({"Case"}, {"Feature"}),
            "BELONGS_TO": ({"Case"}, {"Community"}),
            "SIMILAR_TO": ({"Case"}, {"Case"}),
        }
        for e in self.edges:
            if e.source not in ids or e.target not in ids or not set(e.evidence_ids) <= eids:
                raise ValueError("Legal graph edge does not resolve")
            if e.relation in relation_kinds:
                source_kinds, target_kinds = relation_kinds[e.relation]
                if kinds[e.source] not in source_kinds or kinds[e.target] not in target_kinds:
                    raise ValueError("Legal edge endpoint kinds do not match")
        return self

    def fingerprint(self):
        return hashlib.sha256(canonical(self.model_dump(mode="json")).encode()).hexdigest()

    def node_map(self):
        return {n.id: n for n in self.nodes}

    def evidence_map(self):
        return {e.id: e for e in self.evidence}


class BuildConfig(Strict):
    knn_k: int = Field(default=3, ge=1, le=20)
    seed: int = 42
    max_checklist_items: int = Field(default=12, ge=1, le=30)
    max_model_calls: int = Field(default=100, ge=1, le=1000)


class LegalGraphError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)
