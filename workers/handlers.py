from __future__ import annotations

from typing import Any, Dict, Optional


def handle_task(name: str, payload: Dict[str, Any]) -> None:
    if name == "noop":
        return
    if name == "push.trivia_live_chat":
        from routers.trivia.service import send_push_for_trivia_live_chat_sync

        send_push_for_trivia_live_chat_sync(
            message_id=int(payload["message_id"]),
            sender_id=int(payload["sender_id"]),
            sender_username=str(payload.get("sender_username", "")),
            message=str(payload.get("message", "")),
            draw_date=str(payload.get("draw_date")),
            created_at=str(payload.get("created_at")),
        )
        return
    if name == "pusher.trivia_live_chat":
        from routers.trivia.service import publish_to_pusher_trivia_live

        reply_to: Optional[dict] = payload.get("reply_to")
        publish_to_pusher_trivia_live(
            payload.get("message_id"),
            payload.get("user_id"),
            payload.get("username"),
            payload.get("profile_pic"),
            payload.get("avatar_url"),
            payload.get("frame_url"),
            payload.get("badge"),
            payload.get("message"),
            payload.get("created_at"),
            payload.get("draw_date"),
            reply_to,
        )
        return
    raise ValueError(f"Unknown task: {name}")
