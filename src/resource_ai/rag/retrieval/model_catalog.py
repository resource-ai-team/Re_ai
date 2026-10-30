"""Pinned optional local rerankers; no automatic runtime downloading."""

MODEL_CATALOG = {
    "onnx-bge": {
        "repository": "onnx-community/bge-reranker-v2-m3-ONNX",
        "revision": "6f5ff65298512715a1e669753bc754d2bc8f367b",
        "directory": "models/bge-reranker-onnx-int8",
        "weight": "onnx/model_int8.onnx",
        "bytes": 570727094,
        "sha256": "912fc1215c2dbff6499700534bd8d31253af01573861abbfc43afd1fab6cce5d",
    },
    "onnx-minilm": {
        "repository": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        "revision": "1427fd652930e4ba29e8149678df786c240d8825",
        "directory": "models/mmarco-reranker-onnx-int8",
        "weight": "onnx/model_quint8_avx2.onnx",
        "bytes": 118620016,
        "sha256": "6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b",
    },
}
