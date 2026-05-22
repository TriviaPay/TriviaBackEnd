from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from routers.trivia.service import get_recent_winners


def test_recent_winners_includes_country_details():
    target_date = date(2026, 4, 8)
    bronze_winner = SimpleNamespace(
        account_id=101,
        position=1,
        money_awarded=10.0,
    )
    silver_winner = SimpleNamespace(
        account_id=202,
        position=2,
        money_awarded=7.5,
    )
    users = [
        SimpleNamespace(
            account_id=101,
            username="bronze_user",
            country="United States",
            country_code="US",
        ),
        SimpleNamespace(
            account_id=202,
            username="silver_user",
            country="India",
            country_code="IN",
        ),
    ]
    profile_map = {
        101: {
            "profile_pic_url": "https://img/bronze.png",
            "badge": {"image_url": "https://img/badge-bronze.png"},
            "avatar_url": "https://img/avatar-bronze.png",
            "subscription_badges": [{"id": "bronze_badge", "name": "Bronze", "price": 5.0}],
            "level": 3,
            "level_progress": "30/100",
        },
        202: {
            "profile_pic_url": "https://img/silver.png",
            "badge": {"image_url": "https://img/badge-silver.png"},
            "avatar_url": "https://img/avatar-silver.png",
            "subscription_badges": [{"id": "silver_badge", "name": "Silver", "price": 10.0}],
            "level": 4,
            "level_progress": "40/100",
        },
    }

    with patch(
        "routers.trivia.service.trivia_repository.get_most_recent_winner_draw_date",
        return_value=target_date,
    ), patch(
        "routers.trivia.service.trivia_repository.get_bronze_winners_for_date",
        return_value=[bronze_winner],
    ), patch(
        "routers.trivia.service.trivia_repository.get_silver_winners_for_date",
        return_value=[silver_winner],
    ), patch(
        "routers.trivia.service.trivia_repository.get_users_by_account_ids",
        return_value=users,
    ), patch(
        "utils.chat_helpers.get_user_chat_profile_data_bulk",
        return_value=profile_map,
    ):
        payload = get_recent_winners(db=object(), current_user=None)

    assert payload == [
        {
            "mode": "bronze",
            "position": 1,
            "username": "bronze_user",
            "user_id": 101,
            "money_awarded": 10.0,
            "country": "United States",
            "country_code": "US",
            "profile_pic": "https://img/bronze.png",
            "badge_image_url": "https://img/badge-bronze.png",
            "avatar_url": "https://img/avatar-bronze.png",
            "subscription_badges": [{"id": "bronze_badge"}],
            "level": 3,
            "level_progress": "30/100",
            "draw_date": "2026-04-08",
        },
        {
            "mode": "silver",
            "position": 2,
            "username": "silver_user",
            "user_id": 202,
            "money_awarded": 7.5,
            "country": "India",
            "country_code": "IN",
            "profile_pic": "https://img/silver.png",
            "badge_image_url": "https://img/badge-silver.png",
            "avatar_url": "https://img/avatar-silver.png",
            "subscription_badges": [{"id": "silver_badge"}],
            "level": 4,
            "level_progress": "40/100",
            "draw_date": "2026-04-08",
        },
    ]
