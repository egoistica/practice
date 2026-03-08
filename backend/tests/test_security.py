from __future__ import annotations

import unittest
from datetime import timedelta
from uuid import uuid4

from app.core import security


class SecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_jwt_secret_key = security.settings.JWT_SECRET_KEY
        self.original_jwt_secret = security.settings.JWT_SECRET
        self.original_jwt_algorithm = security.settings.JWT_ALGORITHM
        self.original_access_expire = security.settings.ACCESS_TOKEN_EXPIRE_MINUTES

        security.settings.JWT_SECRET_KEY = "unit-test-secret-key"
        security.settings.JWT_SECRET = "unit-test-secret-key"
        security.settings.JWT_ALGORITHM = "HS256"
        security.settings.ACCESS_TOKEN_EXPIRE_MINUTES = 15

    def tearDown(self) -> None:
        security.settings.JWT_SECRET_KEY = self.original_jwt_secret_key
        security.settings.JWT_SECRET = self.original_jwt_secret
        security.settings.JWT_ALGORITHM = self.original_jwt_algorithm
        security.settings.ACCESS_TOKEN_EXPIRE_MINUTES = self.original_access_expire

    def test_hash_and_verify_password(self) -> None:
        password = "StrongPassword123!"
        hashed = security.hash_password(password)

        self.assertNotEqual(password, hashed)
        self.assertTrue(security.verify_password(password, hashed))
        self.assertFalse(security.verify_password("wrong-password", hashed))

    def test_create_access_token_contains_exp_and_user_id_claims(self) -> None:
        user_id = uuid4()
        token = security.create_access_token(
            {"user_id": user_id, "scope": "user:read"},
            expires_delta=timedelta(minutes=5),
        )

        payload = security.decode_token(token)
        self.assertEqual(payload["user_id"], str(user_id))
        self.assertIn("exp", payload)
        self.assertEqual(payload["type"], "access")

    def test_create_access_token_requires_user_id(self) -> None:
        with self.assertRaises(ValueError):
            security.create_access_token({"scope": "missing-user-id"}, expires_delta=timedelta(minutes=5))

    def test_create_refresh_token_contains_exp_and_user_id_claims(self) -> None:
        user_id = uuid4()
        token = security.create_refresh_token(user_id)

        payload = security.decode_token(token)
        self.assertEqual(payload["user_id"], str(user_id))
        self.assertIn("exp", payload)
        self.assertEqual(payload["type"], "refresh")

    def test_decode_token_rejects_invalid_token(self) -> None:
        with self.assertRaises(ValueError):
            security.decode_token("not-a-jwt")


if __name__ == "__main__":
    unittest.main()
