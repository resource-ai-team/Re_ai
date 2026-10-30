from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from resource_ai.api.routes.analysis import router as analysis_router
from resource_ai.api.routes.graph import router as graph_router
from resource_ai.api.routes.kg_lci import router as kg_lci_router
from resource_ai.api.routes.legal_graph import router as legal_graph_router
from resource_ai.api.routes.rag import router as rag_router
from resource_ai.api.routes.workflow import router as workflow_router

app = FastAPI(
    title="Re:Source AI",
    version="0.1.0",
    description="폐기물 처리 의사결정을 위한 AI·지식 기반 서비스",
)
app.include_router(rag_router)
app.include_router(graph_router)
app.include_router(analysis_router)
app.include_router(legal_graph_router)
app.include_router(kg_lci_router)
app.include_router(workflow_router)


@app.middleware("http")
async def private_workflow_responses(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/workflow"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(_request, exc):
    # Raw input may contain company data or non-finite floats that break JSONResponse.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in exc.errors()
            ]
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
