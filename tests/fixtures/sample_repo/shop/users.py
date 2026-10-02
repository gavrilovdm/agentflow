"""User accounts: registration and lookup."""

from dataclasses import dataclass

from shop.db import Database


@dataclass
class User:
    id: int
    email: str
    password_hash: str


class UserNotFound(Exception):
    pass


def register_user(db: Database, email: str, password_hash: str) -> User:
    """Create a user row and return it. Emails are normalised to lowercase."""
    user_id = db.insert("users", {"email": email.lower(), "password_hash": password_hash})
    return User(user_id, email.lower(), password_hash)


def get_user_by_email(db: Database, email: str) -> User:
    row = db.find_one("users", email=email.lower())
    if row is None:
        raise UserNotFound(email)
    return User(row["id"], row["email"], row["password_hash"])
