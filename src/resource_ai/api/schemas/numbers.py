"""Never reinterpret JSON true/false as measured quantities."""

from typing import Annotated

from pydantic import BeforeValidator


def reject_boolean(value):
    if isinstance(value, bool):
        raise ValueError("수량·함량에는 true/false 대신 숫자를 입력하세요.")  # noqa: TRY004 - Pydantic validates ValueError, not TypeError.
    return value


NonBooleanFloat = Annotated[float, BeforeValidator(reject_boolean)]
