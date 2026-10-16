from fastapi import FastAPI

from resource_ai.api.routes.rag import router as rag_router

app = FastAPI(title="Re:Source Legal RAG", version="0.1.0")
app.include_router(rag_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
