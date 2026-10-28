"""Internal tenant-scoped case API. Backend supplies tenant after authenticating user."""

import json
from copy import deepcopy
from pathlib import Path
from secrets import compare_digest
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import ValidationError

from resource_ai.agents.semantic_critic import get_semantic_critic_service
from resource_ai.agents.site_review import SiteReviewService
from resource_ai.api.routes.analysis import (
    get_research_service,
    require_critic_service_auth,
    require_road_service_auth,
)
from resource_ai.api.routes.kg_lci import get_lci_datasets
from resource_ai.core.config import get_settings
from resource_ai.core.dependencies import get_graph_query
from resource_ai.integrations.road_routing import get_road_routing_service
from resource_ai.knowledge_graph.graph_store import GraphStoreError
from resource_ai.rag.retrieval.vector_store import Database
from resource_ai.workflow.critic import WorkflowCritic
from resource_ai.workflow.decisions import what_if
from resource_ai.workflow.documents import AzureLayoutOCR, present_documents
from resource_ai.workflow.models import (
    CaseCreate,
    ConditionsUpdate,
    DocumentFactApply,
    DocumentUpload,
    DraftUpdate,
    EvidenceUpdate,
    ExtractionConfirmation,
    InputUpdate,
    RunOptions,
    WhatIfRequest,
)
from resource_ai.workflow.service import CaseService, recompute
from resource_ai.workflow.store import PostgresCaseStore, WorkflowError

router = APIRouter(prefix="/workflow", tags=["internal-evidence-workflow"])


def get_case_store():
    settings = get_settings()
    return PostgresCaseStore(
        Database(
            settings.database_url, connect_timeout_seconds=settings.database_connect_timeout_seconds
        )
    )


def identity(
    authorization: Annotated[str | None, Header()] = None,
    x_workflow_tenant: Annotated[str | None, Header(max_length=160)] = None,
):
    expected = get_settings().workflow_service_token.get_secret_value()
    if not expected:
        raise HTTPException(503, detail={"code": "workflow-auth-not-configured"})
    if not authorization or not compare_digest(
        authorization.encode(), ("Bearer " + expected).encode()
    ):
        raise HTTPException(401, detail={"code": "workflow-auth-required"})
    if not x_workflow_tenant or not x_workflow_tenant.strip():
        raise HTTPException(400, detail={"code": "backend-tenant-required"})
    return x_workflow_tenant


def get_case_service(store: Annotated[PostgresCaseStore, Depends(get_case_store)]):
    # Lazy graph/RAG/LCI construction keeps read/save/document operations lightweight.
    def engine_reader(request):
        graph = get_graph_query()
        return SiteReviewService(
            graph, get_research_service(graph), get_lci_datasets() if request.include_lci else []
        ).run(request)

    return CaseService(
        store,
        engine_reader,
        road_service=get_road_routing_service(),
        critic=WorkflowCritic(get_semantic_critic_service(), slot_store=store),
        ocr=AzureLayoutOCR(get_settings()),
    )


def guarded(operation):
    try:
        return operation()
    except WorkflowError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from None
    except psycopg.Error:
        raise HTTPException(503, detail={"code": "workflow-store-unavailable"}) from None
    except GraphStoreError:
        raise HTTPException(503, detail={"code": "workflow-graph-unavailable"}) from None
    except ValidationError:
        raise HTTPException(
            422, detail={"code": "workflow-input-or-engine-contract-invalid"}
        ) from None


Tenant = Annotated[str, Depends(identity)]
Service = Annotated[CaseService, Depends(get_case_service)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=160)]


def actor_header(x_workflow_actor: Annotated[str | None, Header(max_length=160)] = None):
    return x_workflow_actor.strip() if x_workflow_actor else "backend"


Actor = Annotated[str, Depends(actor_header)]


@router.get("/reference-factors")
def reference_factors(tenant: Tenant):
    catalog = json.loads(Path("data/config/emission_factor_sources_v1.json").read_text("utf-8"))
    sources = {s["id"]: s for s in catalog["sources"]}
    return [
        {**r, "source": sources[r["source_id"]]} for r in catalog["visually_checked_grid_records"]
    ]


@router.post("/cases")
def create(request: CaseCreate, tenant: Tenant, service: Service):
    return guarded(lambda: service.create(tenant, request))


@router.get("/cases")
def list_cases(tenant: Tenant, store: Annotated[PostgresCaseStore, Depends(get_case_store)]):
    return guarded(lambda: store.list_cases(tenant))


@router.get("/cases/{case_id}")
def read(
    case_id: UUID, tenant: Tenant, store: Annotated[PostgresCaseStore, Depends(get_case_store)]
):
    def present():
        value = deepcopy(store.get(tenant, str(case_id)))
        if (
            value["evaluation"].get("decision_support", {}).get("version")
            != "condition-decision-v2"
        ):
            recompute(value)  # Derived current view only, no write or KG/RAG/provider calls.
        return present_documents(value)

    return guarded(present)


@router.get("/cases/{case_id}/history")
def history(
    case_id: UUID, tenant: Tenant, store: Annotated[PostgresCaseStore, Depends(get_case_store)]
):
    return guarded(lambda: store.history(tenant, str(case_id)))


@router.get("/cases/{case_id}/request-pack")
def request_pack(
    case_id: UUID, tenant: Tenant, store: Annotated[PostgresCaseStore, Depends(get_case_store)]
):
    state = guarded(lambda: store.get(tenant, str(case_id)))
    questions = state["evaluation"]["questions"]
    text = "# 폐자원 처리 검토 요청서\n\n" + state["inputs"]["prompt"] + "\n\n"
    text += "공정 수행·투입 규격·이번 로트 인수 가능 여부, 적용 허가 자료, 견적과 유효기간을 확인 부탁드립니다.\n"
    text += "처리·전처리·운송·상하차·잔재물 비용과 매각 수익의 포함 범위를 구분해 주세요.\n\n"
    text += "\n".join("- " + q["question"] for q in questions)
    return Response(text, media_type="text/markdown", headers={"Cache-Control": "no-store"})


def mutate(service, tenant, case_id, key, action, request, actor="backend"):
    payload = request.model_dump(mode="json", exclude={"expected_revision"})
    if action == "inputs":
        payload = payload["inputs"]
    if action == "document":
        payload["expected_revision"] = request.expected_revision
    return guarded(
        lambda: service.operate(
            tenant, str(case_id), request.expected_revision, key, action, payload, actor=actor
        )
    )


@router.put("/cases/{case_id}/inputs")
def inputs(
    case_id: UUID, request: InputUpdate, tenant: Tenant, service: Service, idempotency_key: Key
):
    return mutate(service, tenant, case_id, idempotency_key, "inputs", request)


@router.put("/cases/{case_id}/evidence")
def evidence(
    case_id: UUID,
    request: EvidenceUpdate,
    tenant: Tenant,
    service: Service,
    idempotency_key: Key,
    actor: Actor,
):
    return mutate(service, tenant, case_id, idempotency_key, "evidence", request, actor)


@router.put("/cases/{case_id}/draft")
def draft(
    case_id: UUID, request: DraftUpdate, tenant: Tenant, service: Service, idempotency_key: Key
):
    return mutate(service, tenant, case_id, idempotency_key, "draft", request)


@router.post("/cases/{case_id}/run")
def run(
    case_id: UUID,
    request: RunOptions,
    tenant: Tenant,
    service: Service,
    idempotency_key: Key,
    x_road_authorization: Annotated[str | None, Header()] = None,
    x_critic_service_token: Annotated[str | None, Header()] = None,
):
    if request.query_roads:
        require_road_service_auth(service.road_service, x_road_authorization)
    if request.run_critic:
        require_critic_service_auth(service.critic.service, x_critic_service_token)
    return mutate(service, tenant, case_id, idempotency_key, "run", request)


@router.post("/cases/{case_id}/documents")
def document(
    case_id: UUID, request: DocumentUpload, tenant: Tenant, service: Service, idempotency_key: Key
):
    return mutate(service, tenant, case_id, idempotency_key, "document", request)


@router.put("/cases/{case_id}/conditions")
def conditions(
    case_id: UUID,
    request: ConditionsUpdate,
    tenant: Tenant,
    service: Service,
    idempotency_key: Key,
    actor: Actor,
):
    return mutate(service, tenant, case_id, idempotency_key, "conditions", request, actor)


@router.post("/cases/{case_id}/what-if")
def preview(
    case_id: UUID,
    request: WhatIfRequest,
    tenant: Tenant,
    store: Annotated[PostgresCaseStore, Depends(get_case_store)],
):
    return guarded(lambda: what_if(store.get(tenant, str(case_id)), request))


@router.post("/cases/{case_id}/documents/confirm")
def confirm_document(
    case_id: UUID,
    request: ExtractionConfirmation,
    tenant: Tenant,
    service: Service,
    idempotency_key: Key,
    actor: Actor,
):
    return mutate(service, tenant, case_id, idempotency_key, "confirm_document", request, actor)


@router.post("/cases/{case_id}/documents/apply-facts")
def apply_document_facts(
    case_id: UUID,
    request: DocumentFactApply,
    tenant: Tenant,
    service: Service,
    idempotency_key: Key,
    actor: Actor,
):
    return mutate(service, tenant, case_id, idempotency_key, "apply_document_facts", request, actor)


@router.get("/cases/{case_id}/documents/{document_id}")
def download_document(
    case_id: UUID,
    document_id: str,
    tenant: Tenant,
    store: Annotated[PostgresCaseStore, Depends(get_case_store)],
):
    content, media_type = guarded(lambda: store.get_document(tenant, str(case_id), document_id))
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": "attachment",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
