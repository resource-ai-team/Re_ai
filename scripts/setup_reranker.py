"""Download one pinned optional reranker, not every weight variant. No model calls."""

import argparse
import hashlib
import os
import shutil
from pathlib import Path

from resource_ai.rag.retrieval.model_catalog import MODEL_CATALOG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=list(MODEL_CATALOG))
    args = parser.parse_args()
    spec = MODEL_CATALOG[args.model]
    directory = Path(spec["directory"])
    directory.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(directory).free < spec["bytes"] + 1024**3:
        raise SystemExit("Not enough disk space to retain 1 GiB reserve")
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_XET_CHUNK_CACHE_SIZE_BYTES"] = "0"
    from huggingface_hub import HfApi, hf_hub_download

    files = HfApi().list_repo_files(spec["repository"], revision=spec["revision"])
    required = [spec["weight"], "config.json", "tokenizer.json", "tokenizer_config.json"]
    required += [
        f for f in ["special_tokens_map.json", "sentencepiece.bpe.model", "LICENSE"] if f in files
    ]
    for name in required:
        hf_hub_download(
            spec["repository"], filename=name, revision=spec["revision"], local_dir=directory
        )
    path = directory / spec["weight"]
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if path.stat().st_size != spec["bytes"] or checksum != spec["sha256"]:
        raise SystemExit("Downloaded weight checksum does not match pinned artifact")
    (directory / "revision.txt").write_text(spec["revision"] + "\n", encoding="utf8")
    print(f"Prepared {args.model}; verified pinned weight. Text stays local at inference.")


if __name__ == "__main__":
    main()
