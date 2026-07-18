from src.ai.models import AIProposalCriticResult, LocalPackageAnalysis, SemanticReviewFinding


def test_ai_proposal_critic_result_requires_findings():
    try:
        AIProposalCriticResult.model_validate(
            {
                "semantic_status": "pass",
                "confidence": "High",
                "reason": "ok",
                "repair_suggestion": None,
            }
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "Field required" in str(exc)


def test_semantic_review_finding_requires_all_ref_fields_and_nullable_repair_suggestion():
    try:
        SemanticReviewFinding.model_validate(
            {
                "status": "blocked",
                "issue_code": "missing_refs",
                "file_refs": [],
                "evidence_refs": [],
                "reason": "missing target refs",
                "repair_suggestion": None,
            }
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "Field required" in str(exc)


def test_ai_proposal_critic_result_preserves_top_level_status_and_findings():
    result = AIProposalCriticResult.model_validate(
        {
            "semantic_status": "pass",
            "confidence": "High",
            "reason": "ok",
            "repair_suggestion": None,
            "findings": [
                {
                    "status": "blocked",
                    "issue_code": "diagnostic",
                    "file_refs": [],
                    "target_refs": [],
                    "evidence_refs": [],
                    "reason": "diagnostic only",
                    "repair_suggestion": None,
                }
            ],
        }
    )
    assert result.semantic_status == "pass"
    assert result.findings[0].status == "blocked"


def test_local_package_analysis_schema_exposes_projection_fields():
    schema = LocalPackageAnalysis.model_json_schema()
    props = schema["properties"]
    assert "input_sufficiency" in props
    assert "evidence_gaps" in props
    assert "sample_refs_used" in props
    assert "title_cue_confidence_reason" in props
    assert schema["additionalProperties"] is False
