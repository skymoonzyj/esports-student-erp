from app.evaluations.schemas import EvaluationCenterContextResponse


def test_evaluation_context_can_advertise_open_camp_surveys() -> None:
    response = EvaluationCenterContextResponse.model_validate(
        {
            "embedded_start_url": "https://example.test/embedded",
            "standalone_start_url": "https://example.test/standalone",
            "upload_available": False,
            "video_available": False,
            "reports_available": False,
            "surveys_available": True,
            "survey_sections": {
                "respondent": True,
                "staff_feedback": False,
                "administration": False,
            },
            "game_accounts_available": False,
            "admin_available": False,
            "admin_sections": {
                "cohort_staff": False,
                "student_matching": False,
                "analysis_profiles": False,
                "system_tasks": False,
                "adapter_certifications": False,
            },
        }
    )

    assert response.surveys_available is True
