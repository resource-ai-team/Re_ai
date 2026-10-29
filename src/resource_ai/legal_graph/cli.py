"""Explicit structural import or opt-in model enrichment; never silently uses mocks."""

import argparse
import hashlib
import json
from pathlib import Path

from resource_ai.core.config import get_settings
from resource_ai.knowledge_graph.graph_store import GraphStoreError
from resource_ai.legal_graph.builder import build_snapshot, build_structural_snapshot
from resource_ai.legal_graph.llm import configured_model
from resource_ai.legal_graph.models import BuildConfig, LegalGraphError
from resource_ai.legal_graph.store import LegalGraphStore
from resource_ai.rag.ingestion.dataset import validate_documents
from resource_ai.rag.ingestion.loader import load_jsonl
from resource_ai.rag.retrieval.embeddings import create_embedding_provider


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["import", "status", "analyze"])
    parser.add_argument("--dataset", type=Path, default=Path("data/official/legal_documents.jsonl"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--structure-only", action="store_true")
    modes.add_argument("--enrich", action="store_true")
    parser.add_argument("--max-model-calls", type=int, default=100)
    parser.add_argument(
        "--request", type=Path, help="LegalGraphRequest JSON; never logged or stored"
    )
    args = parser.parse_args(argv)
    config = get_settings()
    try:
        store = LegalGraphStore(namespace=config.legal_graph_namespace)
        if args.command == "import":
            if not args.structure_only and not args.enrich:
                parser.error("import에는 --structure-only 또는 --enrich를 명시하세요.")
            documents = load_jsonl(args.dataset)
            validation = validate_documents(documents)
            if not validation.passed:
                raise LegalGraphError("invalid-dataset", "공식 법률 자료 검증에 실패했습니다.")
            digest = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
            if args.enrich:
                model = configured_model()
                embeddings = create_embedding_provider(
                    config.embedding_provider, config.embedding_model, config.embedding_dimension
                )
                snapshot = build_snapshot(
                    documents,
                    embeddings,
                    model,
                    dataset_sha256=digest,
                    config=BuildConfig(max_model_calls=args.max_model_calls),
                )
            else:
                snapshot = build_structural_snapshot(documents, dataset_sha256=digest)
            result = {"imported": store.publish(snapshot), "status": store.status()}
        elif args.command == "status":
            result = store.status()
        else:
            from resource_ai.api.schemas.legal_graph import LegalGraphRequest
            from resource_ai.legal_graph.service import configured_service

            if not args.request:
                parser.error("analyze에는 --request JSON 파일이 필요합니다.")
            request = LegalGraphRequest.model_validate_json(
                args.request.read_text(encoding="utf-8")
            )
            result = configured_service().run(**request.model_dump())
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (LegalGraphError, GraphStoreError) as exc:
        print(json.dumps({"code": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 1
    except (ValueError, OSError):
        # Pydantic/JSON exceptions can echo the entire private request. Do not print them.
        print(
            json.dumps(
                {"code": "invalid-input", "message": "입력 파일·JSON 형식·필드 한도를 확인하세요."},
                ensure_ascii=False,
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
