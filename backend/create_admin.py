"""Create the first administrator through a controlled local terminal setup."""

import os
import re
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
from typing import Any

import bcrypt
from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError


def create_initial_admin(users: Any, email: str, name: str, password: str) -> str:
    email = email.strip().lower()
    name = name.strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Enter a valid email address.")
    if not name or len(password) < 12 or len(password.encode("utf-8")) > 72:
        raise ValueError("Name is required and password must be 12-72 UTF-8 bytes.")
    if users.find_one({
        "role": {"$regex": r"^\s*(admin|administrator)\s*$", "$options": "i"},
    }):
        raise RuntimeError("An administrator already exists. Approve future requests through Admin Approval Requests.")
    if users.find_one({"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}}):
        raise RuntimeError("That email already belongs to an account. Choose a new email; do not overwrite an existing account.")

    now = datetime.now(timezone.utc)
    users.insert_one({
        "full_name": name,
        "email": email,
        "role": "admin",
        "requested_role": "admin",
        "admin_request_status": "not_requested",
        "admin_request_notice": None,
        "is_active": True,
        "created_at": now,
        "last_login": None,
        "admin_approved_at": now,
        "password_hash": bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode(),
    })
    return email


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    url = os.getenv("MONGODB_URL")
    if not url:
        raise SystemExit("MONGODB_URL must be set in backend/.env")

    email = input("Administrator email: ")
    name = input("Administrator full name: ")
    password = getpass("Administrator password (minimum 12 characters): ")
    try:
        with MongoClient(url) as client:
            database = client[os.getenv("MONGODB_DATABASE", "road_pothole_db")]
            users = database[os.getenv("USERS_COLLECTION", "users")]
            users.create_index([("email", ASCENDING)], unique=True)
            created_email = create_initial_admin(users, email, name, password)
    except (DuplicateKeyError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(f"Administrator account ready for {created_email}. Sign in to the dashboard.")


if __name__ == "__main__":
    main()
