"""Synthetic business cases shared by the live HTTP stress runner and regression tests."""

from copy import deepcopy


def claim(field, value, **changes):
    numeric = field in {"quantity_kg", "contamination", "moisture"}
    return {
        "evidence_id": "synthetic-" + field,
        "field": field,
        "source_type": "operator_reported",
        "raw_value": str(value),
        "text_value": None if numeric else str(value),
        "normalized_value": str(value) if numeric else None,
        "unit": "kg" if field == "quantity_kg" else "wt%" if numeric else None,
        "basis": "wet_mass",
        "meaning": "lot_property",
        "sample_id": "synthetic-sample",
        "same_lot_confirmed": True,
        "synthetic": True,
        **changes,
    }


def claims(contamination_value="20", **changes):
    result = [
        claim("material", "PP"),
        claim("material_form", "rigid_scrap"),
        claim("quantity_kg", 1000),
    ]
    if contamination_value is not None:
        result.append(claim("contamination", contamination_value))
    for row in result:
        row.update(changes.get(row["field"], {}))
    return result


def target(**changes):
    return {
        "specification_id": "synthetic-target",
        "name": "합성 검증 목표 · 실제 국내 규격 아님",
        "version": "1",
        "jurisdiction": "KR",
        "forms": ["rigid_scrap", "rigid_part", "flake", "pellet", "bale"],
        "applies_to": "input_waste",
        "requirement_type": "scenario_target",
        "source": "synthetic stress fixture, not a buyer specification",
        "explicitly_selected": True,
        "applicability_confirmed": True,
        "limits": [{"field": "contamination", "max_value": "5"}],
        **changes,
    }


def stage(**changes):
    return {
        "stage_id": "synthetic-sort",
        "process": "sorting",
        "contaminant_removal_pct": "90",
        "pp_loss_pct": "2",
        "evidence_id": "synthetic-performance",
        **changes,
    }


def scenario(**changes):
    return {"target": target(), **changes}


def economic_scenario(**changes):
    return scenario(
        stages=[stage()],
        explicit_assumptions=["모든 성능·가격은 합성 입력"],
        economics={
            "preprocessing_krw": "100000",
            "transport_krw": "10000",
            "residue_krw_per_kg": "100",
            "sale_price_krw_per_kg": "400",
            "evidence_ids": ["synthetic-economics"],
        },
        **changes,
    )


def catalog():
    rows = []

    def add(
        identifier,
        title,
        *,
        readings=None,
        plan=None,
        level=2,
        calc=True,
        dry=False,
        money=False,
        **extra,
    ):
        rows.append(
            {
                "id": identifier,
                "title": title,
                "claims": claims() if readings is None else readings,
                "scenario": scenario() if plan is None else plan,
                "expected": {
                    "level": level,
                    "mass_balance": calc,
                    "drying_balance": dry,
                    "economics": money,
                },
                **extra,
            }
        )

    add("manual-standard", "사출 공장의 PP 스크랩 기본 입력")
    add("no-input", "아무 정보도 없는 문의", readings=[], level=0, calc=False)
    add(
        "no-contamination", "성적서 없이 PP·물량만 입력", readings=claims(None), level=1, calc=False
    )
    add(
        "no-mass",
        "로트 물량 미확인",
        readings=[r for r in claims() if r["field"] != "quantity_kg"],
        level=1,
        calc=False,
    )
    add(
        "unknown-material",
        "소재 미확인",
        readings=[r for r in claims() if r["field"] != "material"],
        level=0,
        calc=False,
    )
    add(
        "wrong-material",
        "PP 대신 PET 성적서",
        readings=claims(material={"text_value": "PET"}),
        level=0,
        calc=False,
    )
    add(
        "film-outside",
        "경질이 아닌 필름 입력",
        readings=claims(material_form={"text_value": "film"}),
        level=0,
        calc=False,
    )
    add("no-target", "자료는 있지만 목표 미선택", omit_target=True, level=1, calc=False)
    for form in ("rigid_part", "flake", "pellet", "bale"):
        add(
            "form-" + form,
            "PP 형태별 입력: " + form,
            readings=claims(material_form={"text_value": form}),
        )
    for field in ("contamination", "quantity_kg", "material"):
        add(
            "other-sample-" + field,
            "다른 시료의 " + field,
            readings=claims(**{field: {"sample_id": "different-sample"}}),
            level=0 if field == "material" else 1,
            calc=False,
        )
    add(
        "unbound-lot",
        "동일 로트 확인 안 됨",
        readings=claims(contamination={"same_lot_confirmed": False}),
        level=1,
        calc=False,
    )
    for unit in ("mg/L", "%", "ppm", "g/kg"):
        add(
            "unit-" + unit.replace("/", "-"),
            "질량 오염률로 쓸 수 없는 단위: " + unit,
            readings=claims(contamination={"unit": unit}),
            level=1,
            calc=False,
        )
    for basis in ("dry_mass", "volume", "unknown"):
        add(
            "basis-" + basis,
            "호환되지 않은 분모: " + basis,
            readings=claims(contamination={"basis": basis}),
            level=1,
            calc=False,
        )
    for qualifier in ("lt", "gt", "not_detected", "unknown"):
        add(
            "qualifier-" + qualifier,
            "점값이 아닌 측정: " + qualifier,
            readings=claims(contamination={"qualifier": qualifier}),
            level=1,
            calc=False,
        )
    add(
        "future-measurement",
        "평가일보다 미래인 측정일",
        readings=claims(contamination={"measurement_date": "2099-01-01"}),
        level=1,
        calc=False,
    )
    add(
        "metadata-only",
        "전문 미확보 메타데이터를 수치 목표로 사용",
        plan=scenario(target=target(status="metadata_only")),
        level=1,
        calc=False,
    )
    add(
        "future-target",
        "아직 발효되지 않은 목표",
        plan=scenario(target=target(effective_on="2099-01-01")),
        level=1,
        calc=False,
    )
    add(
        "unconfirmed-target",
        "목표 적용 대상 미확인",
        plan=scenario(target=target(applicability_confirmed=False)),
        level=1,
        calc=False,
    )
    add(
        "output-spec-on-input",
        "성형재료 산출물 규격을 원료에 적용",
        plan=scenario(target=target(applies_to="recycled_moulding_material")),
        level=1,
        calc=False,
    )
    add(
        "form-mismatch",
        "스크랩에 펠렛 규격 적용",
        plan=scenario(target=target(forms=["pellet"])),
        level=1,
        calc=False,
    )
    for value in ("0", "0.01", "5", "5.0001", "99.99"):
        add("contamination-" + value, "총 비PP 경계값 " + value, readings=claims(value))
    for value in ("0.001", "1", "1000000", "1000000000000"):
        add(
            "mass-" + value,
            "로트 물량 경계값 " + value,
            readings=claims(quantity_kg={"normalized_value": value}),
        )
    for loss in ("0", "5", "99.99"):
        add(
            "pp-loss-" + loss,
            "PP 자체 손실 민감도 " + loss,
            plan=scenario(overall_pp_loss_pct=loss, explicit_assumptions=["합성 PP 손실 가정"]),
        )
    add(
        "full-pp-loss",
        "전체 PP 손실 100%",
        plan=scenario(overall_pp_loss_pct="100", explicit_assumptions=["합성 PP 완전 손실"]),
        level=1,
        calc=False,
    )
    add(
        "cost-complete",
        "성능·운송·잔재·매각가 모두 가상 입력",
        plan=economic_scenario(),
        level=3,
        money=True,
    )
    for label, rate, loss in (
        ("no-treatment", "0", "0"),
        ("all-contaminant-removal", "100", "0"),
        ("all-pp-lost", "0", "100"),
        ("all-output-lost", "100", "100"),
    ):
        value = economic_scenario()
        value["stages"] = [stage(contaminant_removal_pct=rate, pp_loss_pct=loss)]
        add(
            label,
            "공정 성능 경계: " + label,
            plan=value,
            level=2 if loss == "100" else 3,
            money=loss != "100",
        )
    value = economic_scenario()
    value["economics"]["baseline_net_cost_krw"] = "250000"
    add(
        "baseline-present",
        "기존 처리비가 제공된 경우에만 차이 계산",
        plan=value,
        level=3,
        money=True,
    )
    value = economic_scenario()
    value["stages"] = [
        stage(),
        stage(
            stage_id="synthetic-wash",
            process="washing",
            contaminant_removal_pct="50",
            pp_loss_pct="1",
        ),
    ]
    add("two-stage", "선별→세척의 단계별 손실 계산", plan=value, level=3, money=True)
    value = economic_scenario()
    value["economics"]["sale_price_krw_per_kg"] = "0"
    add("zero-sale-price", "매각가 0도 명시적 가정으로 처리", plan=value, level=3, money=True)
    for initial, limit in (("12", "1"), ("1", "1"), ("1", "5"), ("0", "5")):
        readings = claims(None) + [claim("moisture", initial)]
        add(
            "drying-" + initial + "-" + limit,
            "수분 상한 대조 " + initial + " → " + limit,
            readings=readings,
            plan=scenario(target=target(limits=[{"field": "moisture", "max_value": limit}])),
            level=2,
            calc=False,
            dry=True,
        )
    add(
        "water-exceeds-nonpp",
        "수분이 총 비PP보다 큰 모순",
        readings=claims() + [claim("moisture", 30)],
        level=1,
        calc=False,
    )
    add(
        "two-contradictory-readings",
        "담당자 20%와 10% 충돌",
        readings=claims() + [claim("contamination", 10, evidence_id="second-reading")],
        level=1,
        calc=False,
        conflict=True,
    )
    add(
        "same-number-different-format",
        "20과 20.0을 충돌로 오인하지 않음",
        readings=claims() + [claim("contamination", "20.0", evidence_id="same-value")],
    )
    add(
        "photo-quantitative",
        "현장 사진을 근거로 오염률 생성 금지",
        readings=claims(contamination={"source_type": "photo_observed"}),
        input_http=422,
    )
    add(
        "forged-document-status",
        "직접 입력으로 OCR 확인값 사칭",
        readings=claims(contamination={"source_type": "document_user_confirmed"}),
        input_http=422,
    )
    add(
        "negative-contamination",
        "음수 오염률 거부",
        readings=claims(contamination={"normalized_value": "-1"}),
        input_http=422,
    )
    add(
        "over-100-contamination",
        "100 초과 오염률을 계산에 사용하지 않음",
        readings=claims("101"),
        level=1,
        calc=False,
    )
    add(
        "zero-mass",
        "물량 0은 계산 보류",
        readings=claims(quantity_kg={"normalized_value": "0"}),
        level=1,
        calc=False,
    )
    value = economic_scenario()
    value["overall_pp_loss_pct"] = "15"
    add("double-loss-counting", "총 손실과 단계 손실 중복 입력 거부", plan=value, scenario_http=422)
    value = economic_scenario()
    value["stages"][0]["pp_loss_pct"] = "101"
    add("over-100-stage-rate", "공정 손실률 100 초과 거부", plan=value, scenario_http=422)
    value = economic_scenario()
    value["economics"]["transport_krw"] = "-100"
    add("negative-cost", "음수 운송비 거부", plan=value, scenario_http=422)
    add(
        "future-reported-date",
        "미래 일자를 실제 진술 모드에서도 보류",
        readings=[
            {**r, "synthetic": False}
            for r in claims(contamination={"measurement_date": "2099-01-01"})
        ],
        mode="research",
        level=1,
        calc=False,
    )
    add(
        "hypothetical-target-mode",
        "가상 목표가 실제 자료로 승격되지 않음",
        readings=[{**r, "synthetic": False} for r in claims()],
        mode="research",
        expect_mode="simulation",
    )
    return deepcopy(rows)
