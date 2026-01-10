"""Trivia/Draws/Rewards schemas."""

from pydantic import BaseModel


class SubmitAnswerRequest(BaseModel):
    question_id: int
    answer: str
