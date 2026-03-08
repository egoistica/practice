from __future__ import annotations

import unittest
from datetime import timedelta
from uuid import uuid4

from fastapi import Depends, FastAPI
import httpx

from app.core.dependencies import get_current_user, get_db, require_admin
from app.core.security import create_access_token, settings
from app.models.user import User


class FakeSession:
    def __init__(self, users_by_id: dict[str, User]) -> None:
        self._users_by_id = users_by_id

    async def get(self, model: type[User], key):  # noqa: ANN001
        return self._users_by_id.get(str(key))


def build_user(*, is_admin: bool = False) -> User:
    return User(
        id=uuid4(),
        username=f"user-{uuid4()}",
        email=f"{uuid4()}@example.com",
        hashed_password="hashed",
        is_admin=is_admin,
        is_active=True,
        token_balance=1000,
    )


class AuthDependenciesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_secret_key = settings.JWT_SECRET_KEY
        self.original_secret_legacy = settings.JWT_SECRET
        self.original_algo = settings.JWT_ALGORITHM

        settings.JWT_SECRET_KEY = "unit-test-secret-key"
        settings.JWT_SECRET = "unit-test-secret-key"
        settings.JWT_ALGORITHM = "HS256"

        self.app = FastAPI()

        @self.app.get("/me")
        async def me(user: User = Depends(get_current_user)) -> dict[str, str]:
            return {"user_id": str(user.id)}

        @self.app.get("/admin")
        async def admin(user: User = Depends(require_admin)) -> dict[str, str]:
            return {"user_id": str(user.id), "role": "admin"}

    def tearDown(self) -> None:
        settings.JWT_SECRET_KEY = self.original_secret_key
        settings.JWT_SECRET = self.original_secret_legacy
        settings.JWT_ALGORITHM = self.original_algo
        self.app.dependency_overrides.clear()

    def _override_db(self, users: list[User]) -> None:
        users_by_id = {str(user.id): user for user in users}
        fake_session = FakeSession(users_by_id)

        async def override_get_db():
            yield fake_session

        self.app.dependency_overrides[get_db] = override_get_db

    async def _request(self, method: str, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, headers=headers)

    async def test_unauthorized_request_returns_401(self) -> None:
        self._override_db([])
        response = await self._request("GET", "/me")

        self.assertEqual(response.status_code, 401)

    async def test_invalid_token_returns_401(self) -> None:
        self._override_db([])
        response = await self._request("GET", "/me", headers={"Authorization": "Bearer invalid-token"})

        self.assertEqual(response.status_code, 401)

    async def test_non_admin_user_gets_403(self) -> None:
        user = build_user(is_admin=False)
        self._override_db([user])
        token = create_access_token({"user_id": user.id}, expires_delta=timedelta(minutes=10))
        response = await self._request("GET", "/admin", headers={"Authorization": f"Bearer {token}"})

        self.assertEqual(response.status_code, 403)

    async def test_authorized_request_returns_user(self) -> None:
        user = build_user(is_admin=False)
        self._override_db([user])
        token = create_access_token({"user_id": user.id}, expires_delta=timedelta(minutes=10))
        response = await self._request("GET", "/me", headers={"Authorization": f"Bearer {token}"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], str(user.id))

    async def test_admin_route_allows_admin_user(self) -> None:
        user = build_user(is_admin=True)
        self._override_db([user])
        token = create_access_token({"user_id": user.id}, expires_delta=timedelta(minutes=10))
        response = await self._request("GET", "/admin", headers={"Authorization": f"Bearer {token}"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], "admin")

    async def test_inactive_user_gets_401(self) -> None:
        user = build_user(is_admin=False)
        user.is_active = False
        self._override_db([user])
        token = create_access_token({"user_id": user.id}, expires_delta=timedelta(minutes=10))
        response = await self._request("GET", "/me", headers={"Authorization": f"Bearer {token}"})

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
