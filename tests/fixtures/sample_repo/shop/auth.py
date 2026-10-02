"""Password hashing and login (session token issuance)."""

import hashlib
import secrets

from shop.db import Database
from shop.users import get_user_by_email


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def login(db: Database, email: str, password: str, salt: str) -> str:
    """Verify credentials and return a new session token."""
    user = get_user_by_email(db, email)
    if user.password_hash != hash_password(password, salt):
        raise PermissionError("invalid credentials")
    return secrets.token_urlsafe(32)
