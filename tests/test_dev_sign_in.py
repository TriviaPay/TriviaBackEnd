import pytest
from fastapi import HTTPException

import routers.auth.service as auth_service


class _FakePasswordClient:
    def sign_in(self, email, password):
        raise Exception(
            "{'status_code': 401, 'error_type': 'server error', "
            '\'error_message\': \'{"errorCode":"E062903","errorDescription":"Password signin failed"}\'}'
        )


class _FakeDescopeClient:
    def __init__(self, *args, **kwargs):
        self.password = _FakePasswordClient()


def test_dev_sign_in_maps_descope_password_failure_to_401(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DESCOPE_PROJECT_ID", "test-project")
    monkeypatch.setattr(auth_service, "DescopeClient", _FakeDescopeClient)

    with pytest.raises(HTTPException) as exc_info:
        auth_service.dev_sign_in("user@example.com", "bad-password", db=None)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid email or password"
