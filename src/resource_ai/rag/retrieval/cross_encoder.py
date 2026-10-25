"""Bounded local ONNX cross-encoder. Never downloads or sends text externally."""

import hashlib
from dataclasses import replace
from pathlib import Path
from threading import Lock

from resource_ai.evidence.models import corpus_types_for_search_group


class OnnxCrossEncoder:
    def __init__(
        self,
        directory: Path,
        weight: str,
        *,
        model_id: str,
        max_length: int = 512,
        batch_size: int = 4,
        threads: int = 4,
        expected_sha256: str | None = None,
    ):
        if not 64 <= max_length <= 512 or not 1 <= batch_size <= 8:
            raise ValueError("Unbounded reranker input configuration")
        if not 1 <= threads <= 8:
            raise ValueError("threads must be between 1 and 8")
        directory = directory.resolve(strict=True)
        path = (directory / weight).resolve(strict=True)
        if not path.is_relative_to(directory) or path.suffix != ".onnx":
            raise ValueError("Weight must be a local ONNX file within the model directory")
        if expected_sha256:
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha256:
                    raise ValueError("Reranker weight checksum mismatch")
        import onnxruntime as ort
        from transformers import AutoTokenizer

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        self.session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(directory), local_files_only=True, trust_remote_code=False
        )
        self.input_names = {item.name for item in self.session.get_inputs()}
        self.model_id, self.max_length, self.batch_size = model_id, max_length, batch_size
        self.lock = Lock()

    def scores(self, query, results):
        if len(results) > 100 or len(query) > 8000:
            raise ValueError("Reranker candidate/query budget exceeded")
        import numpy as np

        scores = []
        # Limit concurrent tokenization/inference memory on shared CPU servers.
        with self.lock:
            for start in range(0, len(results), self.batch_size):
                batch = results[start : start + self.batch_size]
                texts = [
                    f"{h.title}\n{h.heading}\n{h.citation or ''}\n{h.content}"[:12000]
                    for h in batch
                ]
                inputs = self.tokenizer(
                    [query] * len(batch),
                    texts,
                    padding=True,
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors="np",
                )
                logits = np.asarray(
                    self.session.run(None, {name: inputs[name] for name in self.input_names})[0]
                )
                if logits.shape != (len(batch), 1) or not np.isfinite(logits).all():
                    raise RuntimeError("Reranker returned invalid logits")
                scores.extend(float(value) for value in logits[:, 0])
        return scores

    def rerank(self, query, results, *, limit, search_group=None):
        if limit <= 0:
            raise ValueError("limit must be positive")
        allowed = corpus_types_for_search_group(search_group) if search_group else None
        eligible = [h for h in results if allowed is None or h.document_type in allowed]
        values = self.scores(query, eligible)
        ranked = sorted(
            zip(eligible, values, strict=True),
            key=lambda pair: (-pair[1], -pair[0].score, pair[0].chunk_id),
        )
        return [
            replace(
                hit,
                reranker_score=value,
                reranker_model=self.model_id,
                retrieval_method=f"{hit.retrieval_method}+cross_encoder",
            )
            for hit, value in ranked[:limit]
        ]
