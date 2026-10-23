"""Typed, sourced material-state transitions, never automatic W2RKG chain inference."""

import json
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from resource_ai.knowledge_graph import ontology as graph
from resource_ai.knowledge_graph.dataset import read_bundle


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: str = Field(pattern=r"^[a-z][a-z0-9-]{0,100}$")


SupportGroup = Literal["states", "transitions", "organisations", "facilities", "capabilities"]
SupportKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,100}$")]
SupportTargets = Annotated[list[SupportKey], Field(min_length=1)]


class SourceRecord(Record):
    """Curator-declared linkage scope, not an audit of the source's contents."""

    url: str = Field(pattern=r"^https://")
    publisher: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    checked_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    source_date: str | None
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    acquisition: str
    digest_basis: Literal["downloaded-original-bytes", "curator-record-not-original-page"]
    model_transfer_allowed: bool
    export_scope: Literal["team-authored-factual-summary-only; raw-page-and-assets-excluded"]
    supported_records: dict[SupportGroup, SupportTargets] = Field(
        min_length=1,
        description="Records this source was curated to support; not independent source verification",
    )

    @model_validator(mode="after")
    def unique_support_targets(self):
        date.fromisoformat(self.checked_date)
        if self.source_date is not None:
            date.fromisoformat(self.source_date)
        if any(len(keys) != len(set(keys)) for keys in self.supported_records.values()):
            raise ValueError("Duplicate source support target")
        return self


class SourcedRecord(Record):
    label: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)


class MaterialState(SourcedRecord):
    material: str
    input_forms: list[str]
    is_entry: bool
    is_output: bool


class ProcessTransition(SourcedRecord):
    input_state: str
    output_state: str
    operation_steps: list[str] = Field(min_length=1)
    conditions: list[str] = Field(min_length=1)
    risk_checks: list[str] = Field(min_length=1)


class Organisation(SourcedRecord):
    pass


class ReportedMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    value: float = Field(gt=0, allow_inf_nan=False)
    unit: str
    scope: Literal["website-reported-design-capacity-not-current-available"]
    source_id: str


class RegistryIdentityLink(BaseModel):
    """A reviewed snapshot record is not a permanent facility identifier."""

    model_config = ConfigDict(extra="forbid")
    listing_id: str = Field(pattern=r"^facility-listing:[a-f0-9]{32}$")
    registry_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_ids: list[str] = Field(min_length=1)
    checked_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    basis: Literal["official_site_address_and_registry_record_review"]

    @model_validator(mode="after")
    def reviewed_link(self):
        date.fromisoformat(self.checked_date)
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("Duplicate identity source")
        return self


class Facility(SourcedRecord):
    organisation: str
    country: Literal["KR"]
    region: str
    address: str | None
    reported_metrics: list[ReportedMetric]
    # Reference cases cannot set current permit, available capacity, price or acceptance.
    current_permit_scope: None
    current_available_capacity_kg: None
    current_acceptance: None
    current_price_krw_per_kg: None
    latitude: float | None = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(ge=-180, le=180, allow_inf_nan=False)
    registry_identity_links: list[RegistryIdentityLink] = Field(default_factory=list)


class FacilityInputProfile(BaseModel):
    """Public examples are open lists, not exclusive intake specifications."""

    model_config = ConfigDict(extra="forbid")
    evidence_scope: Literal["site_publication", "organisation_only_not_site_verified"]
    material_examples: list[str]
    form_examples: list[str]
    output_description: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    locator: str = Field(min_length=1)
    excludes_designated_waste: Literal[True] | None = None
    exclusion_source_ids: list[str] = Field(default_factory=list)
    exclusion_locator: str | None = None
    verified_intake_limits_available: Literal[False] = False

    @model_validator(mode="after")
    def scope_is_explicit(self):
        excluded = self.excludes_designated_waste is True
        if excluded != bool(self.exclusion_source_ids) or excluded != bool(self.exclusion_locator):
            raise ValueError("Explicit exclusion needs its own source and locator")
        if excluded and self.evidence_scope != "site_publication":
            raise ValueError("Organisation hypothesis cannot assert a site exclusion")
        return self


class Capability(Record):
    facility: str
    transition: str
    basis: Literal["published_site_process", "site_role_hypothesis"]
    source_ids: list[str] = Field(min_length=1)
    conditions: list[str] = Field(min_length=1)
    input_profile: FacilityInputProfile


class PlanningReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["planning-reference-v2"]
    scope: str
    sources: list[SourceRecord]
    states: list[MaterialState]
    transitions: list[ProcessTransition]
    organisations: list[Organisation]
    facilities: list[Facility]
    capabilities: list[Capability]

    @model_validator(mode="after")
    def check_references(self):
        mappings = {}
        for name in GROUPS:
            rows = getattr(self, name)
            mappings[name] = {r.key: r for r in rows}
            if len(mappings[name]) != len(rows):
                raise ValueError("Duplicate planning reference key")
        sources, states = mappings["sources"], mappings["states"]
        for source in self.sources:
            for group, keys in source.supported_records.items():
                if not set(keys) <= mappings[group].keys():
                    raise ValueError("Unlinked source support target")
        for name in GROUPS:
            if name == "sources":
                continue
            for row in getattr(self, name):
                if (
                    len(set(row.source_ids)) != len(row.source_ids)
                    or not set(row.source_ids) <= sources.keys()
                ):
                    raise ValueError("Unlinked planning source")
                if any(
                    row.key not in sources[sid].supported_records.get(name, [])
                    for sid in row.source_ids
                ):
                    raise ValueError("Planning source outside declared support scope")
        for state in self.states:
            if state.material not in graph.ontology()["materials"]:
                raise ValueError("Unknown planning material")
        for transition in self.transitions:
            if transition.input_state not in states or transition.output_state not in states:
                raise ValueError("Unlinked material state")
            if (
                transition.input_state == transition.output_state
                or states[transition.input_state].material
                != states[transition.output_state].material
            ):
                raise ValueError("Invalid or cross-material transition")
        linked_listings = set()
        for facility in self.facilities:
            if facility.organisation not in mappings["organisations"]:
                raise ValueError("Unlinked organisation")
            if any(m.source_id not in facility.source_ids for m in facility.reported_metrics):
                raise ValueError("Unlinked reported metric")
            if (facility.latitude is None) != (facility.longitude is None):
                raise ValueError("Incomplete location point")
            for link in facility.registry_identity_links:
                if facility.address is None or not set(link.source_ids) <= set(facility.source_ids):
                    raise ValueError("Identity link needs sourced site address")
                if link.listing_id in linked_listings:
                    raise ValueError("Duplicate or ambiguous registry identity link")
                linked_listings.add(link.listing_id)
        for capability in self.capabilities:
            if (
                capability.facility not in mappings["facilities"]
                or capability.transition not in mappings["transitions"]
            ):
                raise ValueError("Unlinked facility capability")
            profile = capability.input_profile
            input_state = states[mappings["transitions"][capability.transition].input_state]
            if (
                not set(profile.source_ids + profile.exclusion_source_ids)
                <= set(capability.source_ids)
                or any(m not in graph.ontology()["materials"] for m in profile.material_examples)
                or input_state.material not in profile.material_examples
                or (profile.evidence_scope == "site_publication")
                != (capability.basis == "published_site_process")
            ):
                raise ValueError("Invalid or unlinked facility input profile")
        return self


GROUPS = {
    "sources": "Source",
    "states": "MaterialState",
    "transitions": "ProcessTransition",
    "organisations": "Organisation",
    "facilities": "Facility",
    "capabilities": "FacilityCapability",
}
PLANNING_RELATIONS = {"INPUT_STATE", "OUTPUT_STATE", "OPERATED_BY", "AT_FACILITY", "FOR_TRANSITION"}


def reference():
    return PlanningReference.model_validate(read_bundle("planning-reference.json"))


def node_id(kind, key):
    return graph.identity("planning-" + kind, key)


def compile_reference(value=None):
    """One compiler is shared by import and exact Neo4j edge/source validation."""
    value = value or reference()
    fingerprint = graph.digest(value.model_dump(mode="json"))
    nodes, edges = {}, set()

    def edge(kind, start, end):
        edges.add((kind, start, end))

    for group, kind in GROUPS.items():
        for row in getattr(value, group):
            record = row.model_dump(mode="json")
            identifier = node_id(kind, row.key)
            props = {
                "planning_key": row.key,
                "planning_bundle_sha256": fingerprint,
                "record": record,
                "validation": "source-backed-research-not-operating-approval",
                "dispatch_approved": False,
            }
            label = getattr(row, "label", row.key)
            if group == "sources":
                props.update(
                    source_key="planning:" + row.key,
                    origin="primary-source-factual-summary",
                    metadata=record,
                )
            nodes[identifier] = {"kind": kind, "label": label, "properties": props}
            if group == "sources":
                eid = node_id("Evidence", row.key)
                nodes[eid] = {
                    "kind": "Evidence",
                    "label": row.key,
                    "properties": {
                        **props,
                        "record_key": "planning:" + row.key,
                        "locator": row.locator,
                        "model_transfer_allowed": row.model_transfer_allowed,
                        "source_url": row.url,
                        "summary": row.summary,
                        "checked_date": row.checked_date,
                        "source_date": row.source_date,
                        "scope": value.scope,
                    },
                }
                edge("FROM_SOURCE", eid, identifier)
            else:
                for sid in row.source_ids:
                    edge("SUPPORTED_BY", identifier, node_id("Evidence", sid))
    states = {s.key: s for s in value.states}
    for state in value.states:
        edge("FOR_MATERIAL", node_id("MaterialState", state.key), "material:" + state.material)
    for transition in value.transitions:
        tid = node_id("ProcessTransition", transition.key)
        edge("INPUT_STATE", tid, node_id("MaterialState", transition.input_state))
        edge("OUTPUT_STATE", tid, node_id("MaterialState", transition.output_state))
        edge("FOR_MATERIAL", tid, "material:" + states[transition.input_state].material)
    for facility in value.facilities:
        edge(
            "OPERATED_BY",
            node_id("Facility", facility.key),
            node_id("Organisation", facility.organisation),
        )
    for capability in value.capabilities:
        cid = node_id("FacilityCapability", capability.key)
        edge("AT_FACILITY", cid, node_id("Facility", capability.facility))
        edge("FOR_TRANSITION", cid, node_id("ProcessTransition", capability.transition))
    return value, fingerprint, nodes, edges


def import_reference(builder):
    _, _, nodes, edges = compile_reference()
    for identifier, node in sorted(nodes.items()):
        builder.node(identifier, node["kind"], node["label"], node["properties"])
    for kind, start, end in sorted(edges):
        builder.edge(kind, start, end, basis="source-backed-planning-reference-v2")


def audit_reference(connection):
    """Reject missing, modified, extra or misbound records even on a newly built stage."""
    actual = {
        row[0]: {"kind": row[1], "label": row[2], "properties": json.loads(row[3])}
        for row in connection.execute(
            "SELECT id,kind,label,properties FROM kg_nodes WHERE json_extract(properties,'$.planning_key') IS NOT NULL"
        )
    }
    included = (
        dict(connection.execute("SELECT key,value FROM kg_meta")).get("includes_curated") == "true"
    )
    if not included:
        if actual:
            raise ValueError("Unexpected planning data without curated import")
        return
    _, _, expected, edges = compile_reference()
    if actual != expected:
        raise ValueError("Planning reference record mismatch")
    found = set()
    for kind, start, end, props in connection.execute(
        "SELECT kind,source_id,target_id,properties FROM kg_edges"
    ):
        if start not in actual:
            continue
        if json.loads(props) != {
            "dispatch_approved": False,
            "basis": "source-backed-planning-reference-v2",
        }:
            raise ValueError("Planning reference edge changed")
        found.add((kind, start, end))
    if found != edges:
        raise ValueError("Planning reference edge binding mismatch")
