import pytest
from pydantic import ValidationError

from forge.schemas import AnalysisOutput, DesignOutput, DeveloperOutput, Edit, ReviewOutput
from forge.util import extract_json


def _analysis(**over):
    base = {
        "summary": "s",
        "acceptance_criteria": [{"id": "AC-1", "given": "g", "when": "w", "then": "t"}],
        "tasks": [{"id": "T-1", "title": "t", "description": "d", "criteria": ["AC-1"]}],
    }
    base.update(over)
    return base


def test_analysis_valid():
    assert AnalysisOutput.model_validate(_analysis()).tasks[0].criteria == ["AC-1"]


def test_analysis_rejects_unknown_criterion_reference():
    bad = _analysis(tasks=[{"id": "T-1", "title": "t", "description": "d", "criteria": ["AC-9"]}])
    with pytest.raises(ValidationError, match="unknown criteria"):
        AnalysisOutput.model_validate(bad)


def test_contracts_forbid_extra_fields():
    with pytest.raises(ValidationError):
        AnalysisOutput.model_validate(_analysis(run_shell="rm -rf /"))


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.py", "a/../../b.py", "C:/x.py", ""])
def test_edit_paths_must_be_repo_relative(path):
    with pytest.raises(ValidationError):
        Edit(path=path, search="", replace="x")


def test_edit_path_normalised():
    assert Edit(path="./refunds/service.py", search="a", replace="b").path == "refunds/service.py"


def test_developer_must_do_something():
    with pytest.raises(ValidationError):
        DeveloperOutput(rationale="nothing", edits=[], scope_requests=[])


def test_design_change_scope_is_sorted_unique():
    d = DesignOutput.model_validate(
        {
            "summary": "s",
            "approach": "a",
            "impacted_components": [
                {"path": "refunds/service.py", "change_type": "modify", "reason": "r"},
                {"path": "api/handlers.py", "change_type": "modify", "reason": "r"},
                {"path": "refunds/service.py", "change_type": "modify", "reason": "again"},
            ],
        }
    )
    assert d.change_scope == ["api/handlers.py", "refunds/service.py"]


def test_rubric_score_bounds():
    with pytest.raises(ValidationError):
        ReviewOutput.model_validate(
            {
                "summary": "s",
                "rubric": [{"criterion": "correctness", "score": 7, "justification": "j"}],
                "recommendation": "approve",
            }
        )


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        'Here you go:\n```json\n{"a": 1}\n```\nThanks',
        'Sure! {"a": 1} hope that helps',
        "```\n{\"a\": 1}\n```",
    ],
)
def test_extract_json_variants(text):
    assert extract_json(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2, 3]", '{"a": 1'])
def test_extract_json_rejects_garbage(text):
    with pytest.raises(ValueError):
        extract_json(text)


def test_extract_json_with_trailing_object():
    assert extract_json('{"a": 1}\n{"b": 2}') == {"a": 1}
