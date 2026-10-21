"""Loopback-only developer viewer; does not add routes to the product API."""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from resource_ai.knowledge_graph import graph_store as store

HERE = Path(__file__).resolve().parent


def create_app(data_dir=None):
    data_dir = Path(data_dir) if data_dir is not None else HERE / ".data"
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    application.add_middleware(GZipMiddleware, minimum_size=1000)

    def snapshot():
        try:
            return json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(503, "그래프 내보내기를 먼저 실행해 주세요.") from exc

    @application.get("/api/health")
    def health():
        return {"status": "ok", "app": "resource-kg-explorer", **snapshot()}

    @application.get("/data/graph")
    def graph():
        snapshot()
        if not (data_dir / "graph.json.gz").is_file():
            raise HTTPException(503, "그래프 내보내기를 먼저 실행해 주세요.")
        return FileResponse(
            data_dir / "graph.json.gz",
            media_type="application/json",
            headers={"Content-Encoding": "gzip", "Cache-Control": "no-cache"},
        )

    @application.get("/graph.json")
    def download():
        snapshot()
        if not (data_dir / "graph.json").is_file():
            raise HTTPException(503, "그래프 내보내기를 먼저 실행해 주세요.")
        return FileResponse(
            data_dir / "graph.json",
            media_type="application/json",
            filename="resource-knowledge-graph.json",
        )

    @application.get("/api/node")
    def node(
        id: str = Query(min_length=1, max_length=300),
        generation: str | None = Query(default=None, min_length=1, max_length=64),
    ):
        meta = snapshot()
        if generation is not None and generation != meta["generation"]:
            raise HTTPException(
                409, "표시 중인 그래프가 갱신되었습니다. 페이지를 새로고침해 주세요."
            )

        def read(tx):
            row = tx.run(
                "MATCH (n:ReSourceNode {space:$space,generation:$generation,id:$id}) "
                "RETURN n.id AS id,n.kind AS kind,n.label AS label,"
                "n.properties_json AS properties",
                id=id,
                space=meta["namespace"],
                generation=meta["generation"],
            ).single()
            if row is None:
                return None
            return {
                **dict(row),
                "properties": json.loads(row["properties"]),
                "generation": meta["generation"],
                "read_only": True,
            }

        try:
            result = store._read(store.settings(), read)
        except (store.GraphStoreError, ValueError) as exc:
            raise HTTPException(
                503, "Neo4j 원문 조회 실패. 그래프 스냅샷은 계속 탐색할 수 있습니다."
            ) from exc
        if result is None:
            raise HTTPException(404, "이 스냅샷에 해당 노드가 없습니다.")
        return result

    application.mount("/", StaticFiles(directory=HERE / "static", html=True), name="explorer")
    return application


app = create_app()
