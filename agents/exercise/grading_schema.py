from typing import List, Optional, Literal

from pydantic import BaseModel, Field, model_validator

from agents.exercise.exercise_schema import CodingLanguageType, ExerciseFormatType


class RubricCriterion(BaseModel):
    """One scored dimension of a case-study / analytical exercise."""

    criterion: str = Field(description="What this criterion evaluates, e.g. 'Correctness of approach'")
    criterion_id: str | None = None
    weight: int = Field(ge=1, le=100, description="Maximum points; all criterion weights sum to exactly 100")


class IOCheck(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    stdin: str = Field(default="", max_length=65536)
    expected_stdout: str = Field(max_length=65536)
    max_points: float = Field(gt=0, le=100, allow_inf_nan=False)
    comparison_mode: Literal["text", "json", "numeric"] = "text"
    absolute_tolerance: float = Field(default=0, ge=0, le=1e6, allow_inf_nan=False)
    relative_tolerance: float = Field(default=0, ge=0, le=0.01, allow_inf_nan=False)


class ExecutionArtifactDraft(BaseModel):
    """Small provider schema for the negotiated execution contract only."""
    format: Literal["coding_challenge"] = "coding_challenge"
    instructions: str
    io_checks: List[IOCheck] = Field(min_length=2, max_length=4)
    reference_files: dict[str, str]
    alternate_correct_files: List[dict[str, str]] = Field(min_length=1, max_length=1)
    negative_control_files: List[dict[str, str]] = Field(min_length=2, max_length=2)
    fixtures: dict[str, str] = Field(default_factory=dict)


class RubricArtifactDraft(BaseModel):
    """Text/code-review schema, without irrelevant executable harness fields."""
    format: ExerciseFormatType
    instructions: str
    rubric: List[RubricCriterion] = Field(min_length=1, max_length=8)


class GradingArtifact(BaseModel):
    """Grading Setup output (architecture §6.4): the auto-grading harness for an exercise.

    For a coding_challenge the grader populates `unit_tests`; for a case_study it populates
    `rubric`. Exactly one is normally filled, matching `format`.
    """

    format: ExerciseFormatType = Field(description="Which exercise format this artifact grades")
    language: Optional[str] = Field(
        default=None,
        description=(
            "The coding language for a coding_challenge: 'python', 'javascript', or 'sql'. "
            "Determines which execution harness runs the tests."
        ),
    )
    unit_tests: List[str] = Field(
        default_factory=list,
        description=(
            "Test snippets that import the user's solution module. "
            "For Python: pytest-style (import from `solution`). "
            "For JavaScript: node:test + node:assert (require('./solution')). "
            "Populated for coding_challenge (python/javascript); empty for case_study and sql."
        ),
    )
    sql_assertions: List[str] = Field(
        default_factory=list,
        description=(
            "JSON-encoded test assertions for SQL exercises. Each element is a JSON object string "
            "with keys: query (SELECT to run), expected (list of expected row tuples), label (test name). "
            "Populated for coding_challenge (sql); empty otherwise."
        ),
    )
    rubric: List[RubricCriterion] = Field(
        default_factory=list,
        description="Weighted scoring criteria. Populated for case_study; empty for coding_challenge.",
    )
    instructions: str = Field(
        description="Brief instructions to the learner: what to submit and how it will be graded"
    )
    method: Literal["execution", "rubric", "structured"] = "execution"
    execution_profile: str | None = None
    profile_revision: str | None = None
    entrypoint: str | None = None
    io_checks: List[IOCheck] = Field(default_factory=list, max_length=12)
    reference_files: dict[str, str] = Field(default_factory=dict)
    alternate_correct_files: List[dict[str, str]] = Field(default_factory=list, max_length=2)
    negative_control_files: List[dict[str, str]] = Field(default_factory=list, max_length=3)
    fixtures: dict[str, str] = Field(default_factory=dict)


    @model_validator(mode="after")
    def rubric_bounds(self):
        if self.format == "case_study" or self.method == "rubric":
            self.method = "rubric"
            if not self.rubric or sum(c.weight for c in self.rubric) != 100:
                raise ValueError("Rubric maxima must sum to exactly 100")
            for index, criterion in enumerate(self.rubric):
                criterion.criterion_id = criterion.criterion_id or f"c{index + 1}"
            if len({c.criterion_id for c in self.rubric}) != len(self.rubric):
                raise ValueError("Rubric criterion IDs must be unique")
        return self


class CriterionVerdict(BaseModel):
    criterion_id: str
    earned_points: float = Field(ge=0, le=100, allow_inf_nan=False)
    evidence: List[str] = Field(min_length=1, max_length=10, description="Literal excerpts from the submission, or a concrete missing-requirement explanation for zero points")


class RubricScore(BaseModel):
    """LLM rubric verdict for a case-study submission (Phase B grading)."""

    score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False, description="Optional claimed total, checked against server-computed points")
    criteria: List[CriterionVerdict] = Field(default_factory=list)
    per_criterion: List[str] = Field(default_factory=list,
        description="One short '<criterion>: <points/feedback>' line per rubric criterion"
    )
    feedback: str = Field(description="Concise overall feedback: strengths and what to improve")
