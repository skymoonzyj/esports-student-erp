from __future__ import annotations

import pytest

from app.evaluations.errors import EvaluationResultInvalid
from app.evaluations.result_validation import validate_result_content


@pytest.mark.parametrize(
    "field_key",
    [
        "id_card",
        "id_number",
        "mobile",
        "nickname",
        "player_id",
        "player_name",
    ],
)
def test_shared_result_validator_rejects_identity_field_keys(
    field_key: str,
) -> None:
    with pytest.raises(EvaluationResultInvalid):
        validate_result_content(
            {"required_fields": [field_key]},
            fields={field_key: "masked"},
            confidences={field_key: 1.0},
            missing_fields=[],
            warnings=[],
            components=[
                {
                    "key": "combat",
                    "score": 80,
                    "weight": 1.0,
                    "summary": "作战数据稳定",
                }
            ],
            score=80,
            grade="A",
            advice="继续保持训练节奏",
            require_complete=True,
        )
