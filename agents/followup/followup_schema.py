from typing import Optional
from pydantic import BaseModel

class FollowUpQuestion(BaseModel):
    id: str
    question: str
    options: list[str]
    default: int = 0

class FollowUpQuestions(BaseModel):
    questions: list[FollowUpQuestion]
    detected_domain: str
    detected_language: Optional[str] = None        # kept for backward compat (programming paths)
    detected_specialization: Optional[str] = None   # generic: "guitar", "financial modeling", "python", etc.
    reasoning: str
