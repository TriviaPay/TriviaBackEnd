from fastapi import APIRouter

# Trivia router - all endpoints have been removed
# Daily login endpoints have been moved to rewards.py
router = APIRouter(prefix="/trivia", tags=["Trivia"])
