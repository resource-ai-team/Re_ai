"""Developer-labelled retrieval probes, not expert legal applicability labels."""

import json
from pathlib import Path


def build():
    families = [
        (
            "producer",
            "dev",
            "LEGAL",
            ["폐기물관리법 제17조"],
            [
                "사업장폐기물을 업체에 맡길 때 배출자가 확인할 의무는?",
                "공장에서 나온 폐기물을 위탁하기 전 배출자 의무의 근거를 찾아줘.",
                "처리업체로 넘긴 후에도 발생 사업자가 확인해야 할 법적 의무가 있나?",
            ],
        ),
        (
            "permit",
            "dev",
            "LEGAL",
            ["폐기물관리법 제25조"],
            [
                "폐기물 처리업체의 허가 업종과 처리 범위를 확인하려면 어떤 조문을 봐야 하나?",
                "처리업 허가를 받을 때 업종은 어떤 기준으로 나뉘나요?",
                "다른 회사 폐기물을 처리하는 사업의 허가와 영업 범위를 확인하고 싶다.",
            ],
        ),
        (
            "principles",
            "dev",
            "LEGAL",
            ["폐기물관리법 제13조의2"],
            [
                "폐기물을 재활용할 때 지켜야 할 원칙과 준수사항은 어디서 보나?",
                "폐자원 재활용이 제한되는 경우와 기본 원칙을 정한 조문을 찾아줘.",
            ],
        ),
        (
            "circular",
            "dev",
            "LEGAL",
            ["순환경제사회 전환 촉진법 제21조"],
            [
                "폐기물을 순환자원으로 인정받으려면 어떤 제도를 확인해야 하나요?",
                "순환자원 인정 신청의 법적 근거 조문을 확인하고 싶다.",
            ],
        ),
        (
            "sandbox",
            "dev",
            "LEGAL",
            ["순환경제사회 전환 촉진법 제33조"],
            [
                "새 재활용 기술에 임시허가를 받을 수 있는 제도적 근거는?",
                "신기술 재활용 사업을 위한 임시허가 조문을 찾아줘.",
            ],
        ),
        (
            "order",
            "dev",
            "LEGAL",
            ["폐기물관리법 제48조"],
            [
                "부적정하게 처리한 폐기물의 조치명령 대상과 근거가 궁금하다.",
                "폐기물 처리에 대한 조치명령을 규정한 법령 원문을 확인하고 싶어.",
            ],
        ),
        (
            "auction",
            "dev",
            "LEGAL",
            ["2023두62830"],
            [
                "경매로 사업장을 인수하면 다른 장소에 방치된 폐기물 의무도 승계하는가?",
                "사업장 경매 인수자가 외부에 방치된 폐기물의 의무를 승계하는지 다룬 판례를 찾아줘.",
            ],
        ),
        (
            "installation",
            "test",
            "LEGAL",
            ["폐기물관리법 제29조"],
            [
                "공장 안에 폐기물처리시설을 새로 설치할 때 승인이나 신고를 검토할 조문은?",
                "폐기물처리시설의 설치와 변경승인에 관한 법적 근거를 찾아줘.",
                "연구 목적으로 폐기물처리시설을 설치할 때 적용 예외를 확인할 기본 조문은?",
            ],
        ),
        (
            "notification",
            "test",
            "LEGAL",
            ["폐기물관리법 제46조"],
            [
                "폐지나 고철 재활용 사업에서 폐기물처리 신고 대상인지 확인하려고 한다.",
                "폐기물처리 신고자가 변경 신고해야 하는 사항의 법적 근거는?",
                "일부 폐자원 수집 운반이나 재활용에 적용되는 신고 제도를 찾아줘.",
            ],
        ),
        (
            "environment",
            "test",
            "LEGAL",
            ["폐기물관리법 제13조의3"],
            [
                "정해진 재활용 기준이 없는 폐기물은 환경성평가를 확인해야 하나?",
                "폐기물 재활용 시 환경성평가의 대상과 예외를 정한 조문을 찾아줘.",
                "재활용 방법을 바꿀 때 재활용환경성평가 대상인지 확인할 근거가 필요하다.",
            ],
        ),
        (
            "definitions",
            "test",
            "LEGAL",
            ["폐기물관리법 제2조"],
            [
                "사업장폐기물과 생활폐기물의 기본 정의를 확인하고 싶어.",
                "폐기물과 지정폐기물의 정의를 규정한 조문을 찾아줘.",
                "재활용과 처분이라는 용어의 법률상 정의가 어디에 있나?",
            ],
        ),
        (
            "processor",
            "test",
            "LEGAL",
            ["폐기물관리법 제19조"],
            [
                "폐기물 운반자가 인계번호를 숙지하고 알려줘야 하는 의무가 있나?",
                "수탁업체가 영업정지로 폐기물을 처리할 수 없을 때 배출자에게 통보하는 의무는?",
            ],
        ),
        (
            "pet-design",
            "dev",
            "TECHNICAL",
            ["PET는 병·색·라벨·접착·이전 내용물을 분리"],
            [
                "PET 병 재활용성을 판단할 때 라벨과 접착제를 어떻게 확인하나?",
                "폐PET 병의 색과 이전 내용물을 확인해야 하는 이유가 궁금하다.",
                "PET 용기의 부착 라벨 때문에 재활용성이 달라질 수 있나?",
            ],
        ),
        (
            "al-water",
            "dev",
            "TECHNICAL",
            ["용융금속과 물의 접촉 위험", "입고 후 보관 중 생기는 수분도 재확인"],
            [
                "알루미늄 용융금속에 수분이 들어갈 때 생길 수 있는 위험은?",
                "폐알루미늄을 보관한 후 재용해하기 전에 수분을 다시 확인해야 하나?",
            ],
        ),
        (
            "al-alloy",
            "dev",
            "TECHNICAL",
            ["같은 알루미늄도 합금 분리가 먼저"],
            [
                "알루미늄 스크랩을 재용해하기 전에 합금별로 나눠야 하나?",
                "알루미늄 스크랩이라도 합금이 섞여 있으면 공정 선택이 달라지나?",
            ],
        ),
        (
            "scrap-carbon",
            "dev",
            "TECHNICAL",
            ["공정스크랩을 자동 탄소 0으로 두지 않음"],
            [
                "공정에서 나온 스크랩의 탄소를 자동으로 영으로 계산해도 되나?",
                "재활용 공정 스크랩을 탄소 계산에서 무조건 무부담으로 처리할 수 있나?",
            ],
        ),
        (
            "coated-paper",
            "dev",
            "TECHNICAL",
            ["코팅 종이는 맞는 제지 공정을 찾아야 함"],
            [
                "코팅된 종이 포장재는 어떤 제지 공정에 맞는지 확인해야 하나?",
                "라미네이트 종이를 재활용할 때 일반 종이와 같은 공정으로 보내도 되나?",
            ],
        ),
        (
            "chip-pretreat",
            "test",
            "TECHNICAL",
            ["절삭칩 전처리는 조건부 대안"],
            [
                "절삭유가 남은 알루미늄 칩은 용해 전 전처리를 검토해야 하나?",
                "알루미늄 절삭칩의 재용해 경로에서 사전 처리 조건을 확인하고 싶어.",
            ],
        ),
        (
            "chip-grade",
            "test",
            "TECHNICAL",
            ["단일 합금 칩과 혼합 칩은 다른 거래 등급"],
            [
                "단일 합금 알루미늄 칩과 혼합 칩의 거래 등급을 구분해야 하나?",
                "여러 알루미늄 합금 칩이 섞였을 때 등급 분리의 근거가 필요하다.",
            ],
        ),
        (
            "paper-sample",
            "test",
            "TECHNICAL",
            ["시험법의 대상 공정과 시료 대표성 검토"],
            [
                "폐지 재활용성 실험에서 시료 대표성이 실제 공정과 어떻게 연결되나?",
                "종이 재활용 시험 결과를 실제 제지 공정에 그대로 적용해도 되는가?",
            ],
        ),
        (
            "carbon-boundary",
            "test",
            "TECHNICAL",
            ["제품 탄소와 회사 Scope 1은 다른 범위"],
            [
                "알루미늄 제품 탄소발자국과 회사 스코프1 배출량은 같은 기준인가?",
                "제품 단위 탄소와 사업장 직접 배출량을 비교할 때 경계를 구분해야 하나?",
            ],
        ),
        (
            "supplier-lot",
            "test",
            "TECHNICAL",
            ["일반 허용치와 품목별 금지조항 충돌 검토"],
            ["스크랩 공급자 매뉴얼의 일반 허용치와 품목별 조건이 다르면 무엇을 확인해야 하나?"],
        ),
    ]
    rows = []
    for family, split, group, expected, questions in families:
        for index, question in enumerate(questions, 1):
            rows.append(
                {
                    "id": f"{family}-{index}",
                    "family_id": family,
                    "split": split,
                    "search_group": group,
                    "query": question,
                    "expected_any": expected,
                    "match_scope": "locator" if group == "LEGAL" else "any",
                }
            )
    assert len(rows) == 50
    return {
        "dataset_version": "retrieval-study-v1",
        "label_status": "developer_source_checked_expert_review_pending",
        "split_policy": "one family in one split; test is not used to select configuration; source knowledge is not blinded",
        "top_k": 5,
        "cases": rows,
    }


if __name__ == "__main__":
    path = Path("data/config/retrieval_study_v1.json")
    path.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
