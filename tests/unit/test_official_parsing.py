from resource_ai.rag.ingestion.official_parsing import (
    article_code,
    parse_law_document,
    parse_law_search,
    parse_precedent_document,
    parse_precedent_search,
)

LAW_SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<LawSearch>
  <law>
    <법령일련번호>284397</법령일련번호>
    <법령명한글>폐기물관리법</법령명한글>
    <법령ID>001771</법령ID>
    <공포일자>20260317</공포일자>
    <소관부처명>기후에너지환경부</소관부처명>
    <시행일자>20260918</시행일자>
  </law>
</LawSearch>
"""


LAW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<법령>
  <기본정보>
    <법령ID>001771</법령ID>
    <공포일자>20260317</공포일자>
    <공포번호>21467</공포번호>
    <법령명_한글>폐기물관리법</법령명_한글>
    <소관부처>기후에너지환경부</소관부처>
    <시행일자>20260918</시행일자>
    <제개정구분>타법개정</제개정구분>
  </기본정보>
  <조문>
    <조문단위>
      <조문번호>17</조문번호>
      <조문제목>사업장폐기물배출자의 의무 등</조문제목>
      <조문시행일자>20260918</조문시행일자>
      <조문내용>제17조(사업장폐기물배출자의 의무 등)</조문내용>
      <항>
        <항번호>①</항번호>
        <항내용>① 사업자는 다음 사항을 지켜야 한다.</항내용>
        <호><호번호>1.</호번호><호내용>1. 적정하게 처리해야 한다.</호내용></호>
        <호>
          <호번호>2.</호번호><호내용>2. 계획서를 제출한다.</호내용>
          <목><목번호>가.</목번호><목내용>가. 사업장 소재지</목내용></목>
        </호>
      </항>
    </조문단위>
  </조문>
  <별표>
    <별표단위>
      <별표번호>4</별표번호>
      <별표가지번호>2</별표가지번호>
      <별표구분>별표</별표구분>
      <별표제목>재활용 유형별 세부분류</별표제목>
      <별표내용>R-1 원형 그대로 재사용</별표내용>
    </별표단위>
  </별표>
</법령>
"""


PRECEDENT_SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PrecSearch>
  <prec>
    <판례일련번호>615977</판례일련번호>
    <사건명>조치명령</사건명>
    <사건번호>2023두62830</사건번호>
    <선고일자>2026.01.15</선고일자>
    <법원명>대법원</법원명>
  </prec>
</PrecSearch>
"""


PRECEDENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PrecService>
  <판례정보일련번호>615977</판례정보일련번호>
  <사건명>조치명령</사건명>
  <사건번호>2023두62830</사건번호>
  <선고일자>20260115</선고일자>
  <법원명>대법원</법원명>
  <판결유형>판결</판결유형>
  <판시사항><![CDATA[<br/>사업장 인수자의 의무 승계 범위]]></판시사항>
  <판결요지><![CDATA[원칙적으로 의무가 승계된다.<br/>예외 사정은 별도 판단한다.]]></판결요지>
  <참조조문>폐기물관리법 제17조</참조조문>
</PrecService>
"""


def test_article_code() -> None:
    assert article_code("17") == "001700"
    assert article_code("17의2") == "001702"


def test_parse_law_search() -> None:
    result = parse_law_search(LAW_SEARCH_XML)[0]
    assert result.title == "폐기물관리법"
    assert result.law_id == "001771"


def test_parse_law_document_preserves_hierarchy() -> None:
    document = parse_law_document(LAW_XML, selected_annexes={"4의2"})
    assert document.source_id == "law:001771:20260317"
    assert document.effective_from.isoformat() == "2026-09-18"
    assert [section.heading for section in document.sections] == [
        "제17조(사업장폐기물배출자의 의무 등) ①항",
        "별표 4의2 재활용 유형별 세부분류",
    ]
    assert "사업장 소재지" in document.sections[0].content
    assert document.sections[0].metadata["hierarchy_path"] == ["폐기물관리법", "제17조", "①"]


def test_parse_law_document_can_collect_only_padded_annex_numbers() -> None:
    padded = LAW_XML.replace("<별표번호>4</별표번호>", "<별표번호>0004</별표번호>").replace(
        "<별표가지번호>2</별표가지번호>",
        "<별표가지번호>02</별표가지번호>",
    )
    document = parse_law_document(
        padded,
        selected_annexes={"4의2"},
        include_articles=False,
    )
    assert len(document.sections) == 1
    assert document.sections[0].metadata["annex"] == "4의2"


def test_parse_precedent_search_and_document() -> None:
    search_result = parse_precedent_search(PRECEDENT_SEARCH_XML)[0]
    assert search_result.case_number == "2023두62830"

    document = parse_precedent_document(PRECEDENT_XML)
    assert document.source_id == "precedent:615977"
    assert document.document_no == "2023두62830"
    assert [section.heading for section in document.sections] == [
        "판시사항",
        "판결요지",
        "참조조문",
    ]
    assert document.sections[1].content == "원칙적으로 의무가 승계된다.\n예외 사정은 별도 판단한다."
