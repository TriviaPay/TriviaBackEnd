"""Trivia/Draws/Rewards schemas."""

from typing import Optional

from pydantic import BaseModel, Field

from core.config import TRIVIA_LIVE_CHAT_MAX_MESSAGE_LENGTH


class SubmitAnswerRequest(BaseModel):
    question_id: int
    answer: str


class TriviaLiveChatSendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=TRIVIA_LIVE_CHAT_MAX_MESSAGE_LENGTH)
    client_message_id: Optional[str] = Field(
        None, description="Client-provided ID for idempotency"
    )
    reply_to_message_id: Optional[int] = Field(
        None, description="ID of message being replied to"
    )


class TriviaReminderRequest(BaseModel):
    heading: str = Field(
        default="Trivia Reminder",
        description="Notification title shown in the push notification",
    )
    message: str = Field(
        default="You still haven't completed today's trivia! Answer now to enter the draw. 🎯",
        description="Notification message body",
    )
    only_incomplete_users: bool = Field(
        default=True,
        description="If true, send only to users who have NOT answered correctly today",
    )
