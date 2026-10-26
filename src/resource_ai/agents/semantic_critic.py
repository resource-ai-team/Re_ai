"""One bounded GPT review, exact citation checks, no model-controlled approval."""

import json
import re

from pydantic import ValidationError

from resource_ai.agents.semantic_packet import PacketError, build_packet
from resource_ai.core.config import get_settings
from resource_ai.evidence.critic_models import SemanticCriticOutput, SemanticCriticRun
from resource_ai.knowledge_graph.ontology import identity
from resource_ai.legal_graph.models import LegalGraphError


class SemanticCriticService:
    def __init__(self, *, config=None, model_factory=None):
        self.config = config or get_settings()
        self.model_factory = model_factory

    def unavailable_status(self):
        if not self.config.semantic_critic_enabled:
            return "disabled"
        if not self.config.semantic_critic_allow_external:
            return "external_transfer_disabled"
        if self.model_factory is None and (
            not self.config.azure_openai_api_key.get_secret_value()
            or not self.config.azure_openai_endpoint
            or self.config.azure_openai_deployment_model != "gpt-6-astra"
        ):
            return "not_configured"
        return None

    def _model(self):
        if self.model_factory is not None:
            return self.model_factory()
        from resource_ai.integrations.azure_model import AzureResponsesModel

        c = self.config
        return AzureResponsesModel(
            c.azure_openai_api_key.get_secret_value(),
            endpoint=c.azure_openai_endpoint,
            deployment=c.azure_openai_deployment,
            deployment_model=c.azure_openai_deployment_model,
            reasoning_effort="high",
            max_output_tokens=c.semantic_critic_max_output_tokens,
            timeout=c.semantic_critic_timeout_seconds,
        )

    def run(self, research, numeric_review, numeric, issues, options, *, extra=None):
        result = SemanticCriticRun()
        if options is None:
            return result
        unavailable = self.unavailable_status()
        if unavailable:
            return result.model_copy(update={"status": unavailable})
        if not research.candidates:
            return result.model_copy(update={"status": "no_candidates"})
        try:
            payload, records, targets, coverage, checksum = build_packet(
                research,
                numeric_review,
                numeric,
                issues,
                max_findings=options.max_findings,
                extra=extra,
                max_input_chars=self.config.semantic_critic_max_input_chars,
            )
        except PacketError as exc:
            return result.model_copy(update={"status": str(exc)})
        result.coverage, result.input_sha256 = coverage, checksum
        if (
            len(json.dumps(payload, ensure_ascii=False))
            > self.config.semantic_critic_max_input_chars
        ):
            result.status = "context_budget_exceeded"
            return result
        try:
            model = self._model()
        except Exception:  # noqa: BLE001 - no configuration values in public failures.
            result.status = "not_configured"
            return result
        result.model_calls = 1
        try:
            response = model.complete("semantic_critic", payload)
            parsed = SemanticCriticOutput.model_validate(response)
        except ValidationError:
            result.status = "invalid_output"
        except LegalGraphError as exc:
            result.status = "invalid_output" if exc.code == "model-output" else "unavailable"
        except Exception:  # noqa: BLE001 - keep provider bodies, keys and enterprise facts out of errors.
            result.status = "unavailable"
        metadata = getattr(model, "last_call", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        result.model_identity_verified = metadata.get("verified_model") == "gpt-6-astra"
        usage = metadata.get("usage", {})
        result.usage = {
            k: v
            for k, v in (usage.items() if isinstance(usage, dict) else [])
            if k in {"input_tokens", "output_tokens", "reasoning_tokens"}
            and type(v) is int
            and 0 <= v <= 10000000
        }
        if result.status in {"invalid_output", "unavailable"}:
            return result
        if len(parsed.concerns) > options.max_findings:
            result.status = "invalid_output"
            return result
        result.coverage["findings_limit_reached"] = len(parsed.concerns) >= options.max_findings
        seen = set()
        for index, concern in enumerate(parsed.concerns):
            reason = self._check(concern, records, targets)
            if reason:
                result.rejected_findings.append({"index": index, "reason": reason})
                continue
            row = concern.model_dump(mode="json")
            # All retained findings remain model concerns, even with matching quotes.
            row["citations"] = [
                {
                    **c.model_dump(),
                    "evidence_id": records[c.ref_id]["evidence_id"],
                    "quote_check": "matches_supplied_text",
                }
                for c in concern.citations
            ]
            row["finding_id"] = identity("semantic-finding", row)
            row["semantic_correctness_verified"] = False
            if row["finding_id"] not in seen:
                result.findings.append(row)
                seen.add(row["finding_id"])
        result.status = (
            ("partial" if result.findings else "invalid_output")
            if result.rejected_findings
            else "completed"
        )
        return result

    @staticmethod
    def _check(concern, records, targets):
        if re.search(
            r"<[^>]+>|https?://|\[[^\]]*\]\(", concern.message + " " + concern.next_action
        ):
            return "unexpected_markup_or_url"
        if concern.target_type == "global":
            if concern.candidate_id is not None or concern.target_id is not None:
                return "invalid_global_scope"
        elif concern.candidate_id not in targets or concern.target_id not in targets.get(
            concern.candidate_id, {}
        ).get(concern.target_type, set()):
            return "unknown_or_mismatched_target"
        for citation in concern.citations:
            source = records.get(citation.ref_id)
            if source is None:
                return "unknown_evidence"
            if (
                source["candidate_id"] is not None
                and source["candidate_id"] != concern.candidate_id
            ):
                return "cross_candidate_evidence"
            quote = " ".join(citation.quote.split())
            if quote not in " ".join(source["text"].split()):
                return "quote_mismatch"
        return None


def get_semantic_critic_service():
    return SemanticCriticService()
