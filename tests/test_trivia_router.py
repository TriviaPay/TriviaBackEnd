from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.trivia import trivia as trivia_router


def test_trivia_router_has_no_endpoints():
    app = FastAPI()
    app.include_router(trivia_router.router)

    with TestClient(app) as client:
        response = client.get("/trivia")
        assert response.status_code == 404
