"""실제 API를 호출하지 않고 시행 버전·정확한 출처·재수집 경계를 검증한다."""

from dataclasses import replace
from datetime import date

import httpx
import pytest

from resource_ai.integrations.law_api import LawOpenApiClient
from resource_ai.rag.ingestion.dataset import validate_documents
from resource_ai.rag.ingestion.legal_expansion import (
    CachedOfficialCollector,
    content_fingerprint,
    parse_administrative_rule,
    select_current_record,
)
from resource_ai.rag.ingestion.official_parsing import parse_law_document

SEARCH = """<LawSearch>
<law><법령명한글>폐기물관리법</법령명한글><현행연혁코드>현행</현행연혁코드>
<법령ID>001771</법령ID><법령일련번호>42</법령일련번호><시행일자>20260918</시행일자></law>
<law><법령명한글>폐기물관리법</법령명한글><현행연혁코드>시행예정</현행연혁코드>
<법령ID>001771</법령ID><법령일련번호>99</법령일련번호><시행일자>20270101</시행일자></law>
<law><법령명한글>폐기물관리법 시행령</법령명한글><현행연혁코드>현행</현행연혁코드>
<시행일자>20260101</시행일자></law></LawSearch>"""
BODY = """<법령><기본정보><법령ID>001771</법령ID><법령명_한글>폐기물관리법</법령명_한글>
<공포일자>20260317</공포일자><시행일자>20260918</시행일자></기본정보><조문>
<조문단위><조문여부>전문</조문여부><조문번호>1</조문번호><조문내용>제1장 총칙</조문내용></조문단위>
<조문단위><조문여부>조문</조문여부><조문번호>1</조문번호><조문내용>제1조 목적</조문내용>
<호><호내용>1. 처리 기준을 준수한다.</호내용></호></조문단위></조문></법령>"""
ADMIN = """<AdmRulService><행정규칙기본정보>
<행정규칙일련번호>123</행정규칙일련번호><행정규칙ID>77</행정규칙ID>
<행정규칙명>폐기물 처리 지침</행정규칙명><행정규칙종류>예규</행정규칙종류>
<발령일자>20260101</발령일자><시행일자>20260102</시행일자><현행여부>Y</현행여부>
</행정규칙기본정보><조문내용>Ⅰ. 허가 검토
업종과 대상을 확인한다.
Ⅱ. 적합성 확인
실제 허가증을 확인한다.</조문내용>
<별표><별표단위><별표번호>1</별표번호><별표구분>별표</별표구분>
<별표내용>대상별 검토 기준</별표내용></별표단위>
<별표단위><별표번호>2</별표번호><별표구분>별표</별표구분>
<별표제목>이미지 전용 표</별표제목></별표단위></별표></AdmRulService>"""


def test_current_selection_rejects_future_and_partial_titles():
    selected = select_current_record(SEARCH, "폐기물 관리법", "law", date(2026, 10, 26))
    assert selected.findtext("법령일련번호") == "42"
    with pytest.raises(ValueError, match="found 0"):
        select_current_record(SEARCH, "폐기물", "law", date(2026, 10, 26))
    with pytest.raises(ValueError, match="found 0"):
        select_current_record(SEARCH, "폐기물관리법", "law", date(2026, 1, 1))


def test_ambiguous_current_version_fails_closed():
    duplicate = SEARCH.replace("<법령일련번호>99", "<법령일련번호>43")
    duplicate = duplicate.replace("시행예정", "현행").replace("20270101", "20260101")
    with pytest.raises(ValueError, match="found 2"):
        select_current_record(duplicate, "폐기물관리법", "law", date(2026, 10, 26))


def test_law_chapter_is_not_article_and_direct_items_are_preserved():
    doc = parse_law_document(BODY)
    assert len(doc.sections) == 1
    assert "총칙" not in doc.sections[0].content
    assert "1. 처리 기준을 준수한다." in doc.sections[0].content


def test_admin_rules_keep_kind_canonical_identity_and_missing_annexes():
    doc = parse_administrative_rule(ADMIN)
    assert doc.document_type == "LAW"
    assert doc.metadata["canonical_source_id"] == "admrul:77"
    assert doc.metadata["instrument_type"] == "예규"
    assert doc.source_url.endswith("admRulSeq=123")
    assert len(doc.sections) == 3
    assert doc.sections[1].heading == "Ⅱ. 적합성 확인"
    assert doc.metadata["annex_text_missing"] == [{"number": "2", "title": "이미지 전용 표"}]
    assert validate_documents([doc]).passed
    assert content_fingerprint(doc) == content_fingerprint(
        replace(doc, metadata={"collected_at": "new"})
    )
    assert content_fingerprint(doc) != content_fingerprint(replace(doc, version="new"))


def test_repeated_admin_article_nodes_do_not_drop_after_first_article():
    xml = ADMIN.replace("</조문내용>", "</조문내용><조문내용>제2조(기준) 중요한 기준</조문내용>")
    doc = parse_administrative_rule(xml)
    assert any(s.content == "제2조(기준) 중요한 기준" for s in doc.sections)
    section = next(s for s in doc.sections if s.metadata.get("article") == "제2조")
    assert section.metadata["article_title"] == "기준"


@pytest.mark.parametrize(
    "old,new", [("<현행여부>Y", "<현행여부>N"), ("<시행일자>20260102", "<시행일자>")]
)
def test_admin_missing_current_or_date_fails(old, new):
    with pytest.raises(ValueError):
        parse_administrative_rule(ADMIN.replace(old, new))


def test_effective_api_parameters_and_cache_match_selected_version(tmp_path):
    calls = []

    def handler(request):
        params = request.url.params
        calls.append(dict(params))
        assert params["target"] == "eflaw"
        if request.url.path.endswith("lawSearch.do"):
            assert params["nw"] == "3"
            return httpx.Response(200, text=SEARCH)
        assert params["MST"] == "42"
        assert params["efYd"] == "20260918"
        return httpx.Response(200, text=BODY)

    with LawOpenApiClient("secret", transport=httpx.MockTransport(handler)) as client:
        collector = CachedOfficialCollector(client, tmp_path, date(2026, 10, 26), interval=0)
        doc = collector.collect("폐기물관리법", "law", "core", "3")
        again = collector.collect("폐기물관리법", "law", "core", "3")
    assert len(calls) == 2
    assert content_fingerprint(doc) == content_fingerprint(again)
    assert doc.source_id == "law:001771:42:20260918"
    assert doc.metadata["supplementary_provisions_indexed"] is False


def test_api_errors_do_not_leak_authentication_value(tmp_path):
    def handler(request):
        return httpx.Response(500, request=request)

    with LawOpenApiClient("my-secret-OC", transport=httpx.MockTransport(handler)) as client:
        collector = CachedOfficialCollector(client, tmp_path, date(2026, 10, 26), interval=0)
        with pytest.raises(ValueError) as exc:
            collector.collect("폐기물관리법", "law", "core", "3")
    assert "my-secret-OC" not in str(exc.value)
    assert not list(tmp_path.glob("*.xml"))


def test_admin_alias_does_not_bind_upper_statute_to_same_numbered_guideline():
    from resource_ai.rag.legal_context import references

    refs, unresolved = references(
        "법 제2조와 「폐기물관리법」 제25조 및 별표 1에 따른다.",
        "가상 처리 지침",
        administrative_rule=True,
    )
    assert ("가상 처리 지침", "제2조") not in refs
    assert ("폐기물관리법", "제25조") in refs
    assert ("가상 처리 지침", "별표1") in refs
    assert any(x.startswith("unresolved_administrative_alias:") for x in unresolved)


def test_cache_sha256_is_the_exact_cached_bytes_across_newline_styles(tmp_path):
    import hashlib

    with LawOpenApiClient(
        "test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text="<LawSearch>\r\n</LawSearch>"),
        ),
    ) as client:
        collector = CachedOfficialCollector(client, tmp_path, date(2026, 10, 26), interval=0)
        _, digest = collector._request("test", client.search_effective_laws, "폐기물")
        cached = next(tmp_path.glob("*.xml"))
        assert digest == hashlib.sha256(cached.read_bytes()).hexdigest()
        _, repeated_digest = collector._request("test", client.search_effective_laws, "폐기물")
        assert digest == repeated_digest
