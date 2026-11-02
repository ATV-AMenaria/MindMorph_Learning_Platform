"""Authenticated HTTP client for pre-JWT learning-flow regression fixtures.

Each fixture already names its owner in the route or creation body. Sign that
identity so tests exercise the real middleware; explicit headers always win.
Security tests use FastAPI's unmodified TestClient.
"""
from urllib.parse import unquote

from fastapi.testclient import TestClient

from services.security import create_access_token


class AuthenticatedClient(TestClient):
    def build_request(self, method, url, **kwargs):
        request = super().build_request(method, url, **kwargs)
        if "authorization" not in request.headers:
            parts = request.url.path.strip("/").split("/")
            user = (kwargs.get("json") or {}).get("user_id", "test-operator")
            if len(parts) > 1 and parts[0] in {"sessions", "exercises", "attempts", "users"} and parts[1] not in {"stream", "followup"}:
                user = unquote(parts[1])
            token = create_access_token(user, "fixture@example.com")
            request.headers["Authorization"] = f"Bearer {token}"
        return request
