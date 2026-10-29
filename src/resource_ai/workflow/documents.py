"""Bounded local text/PDF extraction and opt-in Azure layout OCR, with provenance."""

import base64
import binascii
import hashlib
import json
import re
import subprocess
import sys
import time
from decimal import Decimal
from io import BytesIO
from urllib.parse import urlparse

import httpx

from resource_ai.knowledge_graph.ontology import identity
from resource_ai.workflow.store import WorkflowError


def decode_document(upload):
    try:
        content = base64.b64decode(upload.base64_content, validate=True)
    except (ValueError, binascii.Error):
        raise WorkflowError("invalid-document-encoding", 422) from None
    if not content or len(content) > 6_000_000:
        raise WorkflowError("document-size-limit", 413)
    valid = {
        "application/pdf": content.startswith(b"%PDF-"),
        "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": content.startswith(b"\xff\xd8\xff"),
        "text/plain": b"\x00" not in content,
    }
    if not valid[upload.media_type]:
        raise WorkflowError("document-type-mismatch", 422)
    return content, hashlib.sha256(content).hexdigest()


def local_extraction(content, media_type):
    if media_type == "text/plain":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise WorkflowError("document-must-be-utf8", 422) from None
        return {
            "status": "extracted",
            "provider": "local-text",
            "pages": [{"page": 1, "text": text[:30000], "confidence": None}],
            "truncated": len(text) > 30000,
        }
    if media_type != "application/pdf":
        return {"status": "ocr_not_configured", "provider": "none", "pages": []}
    try:
        result = subprocess.run(
            [sys.executable, "-m", "resource_ai.workflow.pdf_worker"],
            input=content,
            capture_output=True,
            timeout=15,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if len(result.stdout) > 500000:
            raise ValueError("oversized extraction")
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError):
        return {"status": "unreadable_or_timed_out", "provider": "local-pdf", "pages": []}


def _pdf_text(content):
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            return {"status": "encrypted_document", "provider": "local-pdf", "pages": []}
        if len(reader.pages) > 30:
            return {"status": "page_limit", "provider": "local-pdf", "pages": []}
        pages = []
        remaining = 30000
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages.append({"page": index + 1, "text": text[:remaining], "confidence": None})
            remaining -= min(remaining, len(text))
            if remaining == 0:
                break
        return {
            "status": "extracted" if any(p["text"].strip() for p in pages) else "needs_ocr",
            "provider": "local-pdf",
            "pages": pages,
            "truncated": remaining == 0,
        }
    except ImportError:
        return {"status": "pdf_parser_not_installed", "provider": "none", "pages": []}
    except Exception:  # noqa: BLE001 - parser messages can contain document text.
        return {"status": "unreadable_document", "provider": "local-pdf", "pages": []}


class AzureLayoutOCR:
    def __init__(self, config, *, transport=None, sleep=time.sleep, clock=time.monotonic):
        self.config, self.transport, self.sleep, self.clock = config, transport, sleep, clock

    def extract(self, content, media_type):
        c = self.config
        if not c.workflow_ocr_enabled or not c.workflow_ocr_allow_external:
            return {"status": "ocr_external_disabled", "provider": "none", "pages": []}
        endpoint = c.workflow_ocr_endpoint.rstrip("/")
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.path not in ("", "/")
            or not parsed.hostname
            or not parsed.hostname.endswith(
                (".cognitiveservices.azure.com", ".services.ai.azure.com")
            )
            or not c.workflow_ocr_api_key.get_secret_value()
        ):
            return {"status": "ocr_not_configured", "provider": "none", "pages": []}
        headers = {"Ocp-Apim-Subscription-Key": c.workflow_ocr_api_key.get_secret_value()}
        url = (
            endpoint
            + "/documentintelligence/documentModels/prebuilt-layout:analyze?api-version=2024-11-30&pages=1-5"
        )
        start = self.clock()
        try:
            with httpx.Client(
                transport=self.transport, timeout=10, follow_redirects=False, trust_env=False
            ) as client:
                response = client.post(
                    url, headers={**headers, "Content-Type": media_type}, content=content
                )
                if response.status_code != 202:
                    return {"status": "ocr_provider_error", "provider": "azure-layout", "pages": []}
                location = response.headers.get("operation-location", "")
                target = urlparse(location)
                if (
                    target.scheme != "https"
                    or target.netloc != parsed.netloc
                    or not re.fullmatch(
                        r"/documentintelligence/documentModels/prebuilt-layout/analyzeResults/[A-Za-z0-9-]+",
                        target.path,
                    )
                ):
                    return {
                        "status": "ocr_invalid_operation_url",
                        "provider": "azure-layout",
                        "pages": [],
                    }
                for _ in range(15):
                    if self.clock() - start > 35:
                        break
                    response = client.get(location, headers=headers)
                    response.raise_for_status()
                    if len(response.content) > 4_000_000:
                        break
                    data = response.json()
                    if data.get("status") == "failed":
                        return {"status": "ocr_failed", "provider": "azure-layout", "pages": []}
                    if data.get("status") == "succeeded":
                        pages = []
                        for p in data.get("analyzeResult", {}).get("pages", [])[:5]:
                            pages.append(
                                {
                                    "page": p["pageNumber"],
                                    "width": p.get("width"),
                                    "height": p.get("height"),
                                    "unit": p.get("unit"),
                                    "lines": p.get("lines", [])[:1500],
                                    "text": "\n".join(
                                        line.get("content", "") for line in p.get("lines", [])
                                    )[:6000],
                                    "words": [
                                        {
                                            "content": w.get("content", ""),
                                            "confidence": w.get("confidence"),
                                            "polygon": w.get("polygon"),
                                        }
                                        for w in p.get("words", [])[:1500]
                                    ],
                                }
                            )
                        return {
                            "status": "extracted",
                            "provider": "azure-layout",
                            "pages": pages,
                            "tables": data.get("analyzeResult", {}).get("tables", [])[:100],
                            "scope": "pages_1_to_5_unconfirmed_not_field_validation",
                        }
                    self.sleep(1)
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            return {"status": "ocr_unavailable", "provider": "azure-layout", "pages": []}
        return {
            "status": "ocr_pending_no_automatic_resubmit",
            "provider": "azure-layout",
            "pages": [],
        }


def suggest_fields(extraction):
    """Literal candidates only. Multiple matches stay ambiguous; nothing is auto-confirmed."""
    suggestions = []
    pattern = re.compile(
        r"(?P<label>물량|중량|수량|함수율|수분|오염도|오염함량|처리단가|매각단가|단가)\s*[:：]?\s*(?P<value>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>kg|톤|t|%|원/kg|원/톤|KRW/kg|KRW/t)(?![A-Za-z가-힣/])",
        re.IGNORECASE,
    )
    for page in extraction.get("pages", []):
        for match in pattern.finditer(page["text"]):
            suggestions.append(
                {
                    "field": match["label"],
                    "value": match["value"].replace(",", ""),
                    "unit": match["unit"],
                    "page": page["page"],
                    "quote": match[0],
                    "start": match.start(),
                    "end": match.end(),
                    "user_confirmed": False,
                }
            )
    return suggestions[:100]


def reviewable_fields(document_id, extraction):
    """Only literal lot fields; prices, site identity and inferred facts stay manual."""
    rows = []
    from resource_ai.document_processing.reports import classify

    if classify("\n".join(p.get("text", "") for p in extraction.get("pages", []))) in {
        "food_contact_migration",
        "mechanical_material_test",
    }:
        return rows
    for item in suggest_fields(extraction):
        page_text = next(p["text"] for p in extraction["pages"] if p["page"] == item["page"])
        start = max(page_text.rfind("\n", 0, item["start"]) + 1, item["start"] - 160)
        end = page_text.find("\n", item["end"])
        end = min(len(page_text) if end < 0 else end, item["end"] + 160)
        context_quote = page_text[start:end]
        qualified = re.search(
            r"(?:최대|최소|예상|추정|약)\s*$", page_text[max(0, item["start"] - 8) : item["start"]]
        ) or re.match(
            r"\s*(?:[~～–—±-]|이상|이하|미만|초과|내외|정도|까지|또는|혹은|to\b|or\b)",
            page_text[item["end"] : item["end"] + 20],
            re.IGNORECASE,
        )
        unit = item["unit"].lower()
        target, normalized_unit, multiplier = None, None, Decimal(1)
        if item["field"] in {"물량", "중량", "수량"} and unit in {"kg", "톤", "t"}:
            target, normalized_unit = "quantity_kg", "kg"
            multiplier = Decimal(1000) if unit in {"톤", "t"} else Decimal(1)
        elif item["field"] in {"함수율", "수분"} and unit == "%":
            target, normalized_unit = "moisture_pct", "%"
        elif item["field"] in {"오염도", "오염함량"} and unit == "%":
            target, normalized_unit = "contamination_pct", "%"
        # Bound precision before multiplication; do not round a literal into an accepted fact.
        value = Decimal(item["value"]) * multiplier if len(item["value"]) <= 24 else None
        valid = (
            target is not None
            and value is not None
            and (
                Decimal(0) < value <= Decimal("1e12")
                if target == "quantity_kg"
                else Decimal(0) <= value <= Decimal(100)
            )
        )
        valid = bool(valid and not qualified and Decimal(str(float(value))) == value)
        rows.append(
            {
                **item,
                "field_id": identity("document-field", [document_id, item]),
                "target_field": target,
                "normalized_value": str(value) if valid else None,
                "normalized_unit": normalized_unit,
                "can_apply": valid,
                "conversion": "1 t = 1000 kg" if multiplier == 1000 else "literal_same_unit",
                "requires_user_confirmation": True,
                "context_quote": context_quote,
                "context_start": start,
                "context_end": end,
            }
        )
    counts = {}
    for row in rows:
        if row["target_field"]:
            counts[row["target_field"]] = counts.get(row["target_field"], 0) + 1
    for row in rows:
        row["multiple_matches"] = counts.get(row["target_field"], 0) > 1
    return rows


def present_documents(state):
    """Read-time enrichment for earlier saved documents; never changes stored revision."""
    return {
        **state,
        "documents": {
            key: {**doc, "review_fields": reviewable_fields(key, doc["extraction"])}
            for key, doc in state["documents"].items()
        },
    }
