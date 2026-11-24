import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import re
from types import SimpleNamespace

import bcrypt
import pytest
from bson import ObjectId
from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials
from pymongo.errors import DuplicateKeyError
from starlette.requests import Request

from backend.create_admin import create_initial_admin
from backend import main


def session_token(response):
    cookie = response.headers.get("set-cookie", "")
    return cookie.split("roadwatch_access_token=", 1)[1].split(";", 1)[0]


class MemoryCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, n):
        self.docs = self.docs[n:]
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    def __iter__(self):
        return iter(deepcopy(self.docs))


class MemoryCollection:
    def __init__(self, documents=()):
        self.documents = list(deepcopy(documents))

    def insert_one(self, doc):
        saved = deepcopy(doc)
        saved.setdefault("_id", ObjectId())
        self.documents.append(saved)
        return SimpleNamespace(inserted_id=saved["_id"])

    def find_one(self, query, *_args, **_kwargs):
        for item in self.documents:
            if all(item.get(key) == value for key, value in query.items()):
                return deepcopy(item)
        return None

    def find(self, query, *_args, **_kwargs):
        def matches(item, key, expected):
            if isinstance(expected, dict) and "$in" in expected:
                return item.get(key) in expected["$in"]
            return item.get(key) == expected

        return MemoryCursor([
            item for item in self.documents
            if all(matches(item, key, value) for key, value in query.items())
        ])

    def count_documents(self, query):
        def matches(item, key, expected):
            if isinstance(expected, dict) and "$in" in expected:
                return item.get(key) in expected["$in"]
            return item.get(key) == expected

        return sum(
            1
            for item in self.documents
            if all(matches(item, key, value) for key, value in query.items())
        )

    def update_one(self, query, update):
        for item in self.documents:
            if all(item.get(k) != v["$ne"] if isinstance(v, dict) and "$ne" in v else item.get(k) == v for k, v in query.items()):
                item.update(deepcopy(update.get("$set", {})))
                for key, value in update.get("$push", {}).items():
                    item.setdefault(key, []).append(deepcopy(value))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class SetupUsers(MemoryCollection):
    def find_one(self, query, *_args, **_kwargs):
        def matches(item, key, expected):
            actual = item.get(key)
            if isinstance(expected, dict) and "$regex" in expected:
                flags = re.IGNORECASE if "i" in expected.get("$options", "") else 0
                return re.search(expected["$regex"], str(actual or ""), flags) is not None
            return actual == expected

        return next(
            (deepcopy(item) for item in self.documents if all(matches(item, key, value) for key, value in query.items())),
            None,
        )

    def insert_one(self, doc):
        if any(item.get("_id") == doc.get("_id") for item in self.documents):
            raise DuplicateKeyError("duplicate _id")
        if any(item.get("email") == doc.get("email") for item in self.documents):
            raise DuplicateKeyError("duplicate email")
        return super().insert_one(doc)


def test_controlled_first_admin_setup_refuses_existing_admin_and_account():
    class SetupCollection:
        def __init__(self, documents=()):
            self.documents = list(deepcopy(documents))

        def find_one(self, query):
            if "role" in query:
                return next(
                    (deepcopy(item) for item in self.documents if item.get("role", "").casefold() in {"admin", "administrator"}),
                    None,
                )
            email_pattern = query["email"]["$regex"]
            return next(
                (
                    deepcopy(item)
                    for item in self.documents
                    if re.fullmatch(email_pattern, item.get("email", ""), re.IGNORECASE)
                ),
                None,
            )

        def insert_one(self, document):
            saved = deepcopy(document)
            saved["_id"] = ObjectId()
            self.documents.append(saved)
            return SimpleNamespace(inserted_id=saved["_id"])

    users = SetupCollection()
    email = create_initial_admin(users, " FIRST@example.test ", "First Admin", "Legacy-Strong-123!")
    saved = users.documents[0]
    assert email == "first@example.test"
    assert saved["role"] == "admin"
    assert saved["admin_request_status"] == "not_requested"
    assert bcrypt.checkpw(b"Legacy-Strong-123!", saved["password_hash"].encode())
    assert "password" not in saved
    with pytest.raises(RuntimeError, match="administrator already exists"):
        create_initial_admin(users, "second@example.test", "Second Admin", "another-strong-password")

    existing_user = SetupCollection([{
        "email": "existing@example.test",
        "role": "user",
        "password_hash": "unchanged",
    }])
    with pytest.raises(RuntimeError, match="already belongs to an account"):
        create_initial_admin(existing_user, "EXISTING@example.test", "Existing User", "Legacy-Strong-123!")
    assert existing_user.documents[0]["role"] == "user"
    assert existing_user.documents[0]["password_hash"] == "unchanged"


def test_web_admin_setup_requires_secret_and_creates_only_one_admin(monkeypatch):
    users = SetupUsers()
    setup_secret = "a-private-random-setup-secret-at-least-32-bytes"
    monkeypatch.setattr(main, "users_collection", users)
    monkeypatch.setattr(main, "INITIAL_ADMIN_SETUP_SECRET", setup_secret)

    payload = main.InitialAdminSetup.model_validate({
        "full_name": " First Admin ",
        "email": " FIRST@example.test ",
        "password": "Roadwatch-Strong-123!",
        "confirm_password": "Roadwatch-Strong-123!",
        "setup_secret": "invalid-secret",
    })
    with pytest.raises(HTTPException) as invalid_secret:
        main.setup_initial_admin(payload)
    assert invalid_secret.value.status_code == 403
    assert users.documents == []

    payload = main.InitialAdminSetup.model_validate({
        "full_name": " First Admin ",
        "email": " FIRST@example.test ",
        "password": "Roadwatch-Strong-123!",
        "confirm_password": "Roadwatch-Strong-123!",
        "setup_secret": setup_secret,
    })
    created = main.setup_initial_admin(payload)
    saved = users.find_one({"initial_administrator_setup_completed": True})

    assert created["role"] == "administrator"
    assert created["email"] == "first@example.test"
    assert saved["full_name"] == "First Admin"
    assert saved["role"] == "administrator"
    assert saved["admin_request_status"] == "not_requested"
    assert saved["initial_administrator_setup_completed"] is True
    assert bcrypt.checkpw(b"Roadwatch-Strong-123!", saved["password_hash"].encode())
    assert "password_hash" not in created
    assert "setup_secret" not in saved
    monkeypatch.setattr(main, "JWT_SECRET", "test-secret-for-auth-tests")
    login_response = Response()
    logged_in = main.login(
        main.Login(email="first@example.test", password="Roadwatch-Strong-123!"),
        login_response,
    )
    assert logged_in["user"]["role"] == "administrator"
    assert "access_token" not in logged_in
    assert main.jwt.decode(
        session_token(login_response),
        main.JWT_SECRET,
        algorithms=["HS256"],
    )["role"] == "administrator"
    assert main.admin_setup_status() == {"available": False}

    another_payload = payload.model_copy(update={
        "email": "second@example.test",
    })
    with pytest.raises(HTTPException) as duplicate_setup:
        main.setup_initial_admin(another_payload)
    assert duplicate_setup.value.status_code == 409
    assert len(users.documents) == 1


def test_web_admin_setup_validates_password_confirmation_and_existing_admin(monkeypatch):
    existing_admin_id = ObjectId()
    users = SetupUsers([{
        "_id": existing_admin_id,
        "email": "existing-admin@example.test",
        "role": "admin",
    }])
    setup_secret = "a-private-random-setup-secret-at-least-32-bytes"
    monkeypatch.setattr(main, "users_collection", users)
    monkeypatch.setattr(main, "INITIAL_ADMIN_SETUP_SECRET", setup_secret)
    assert main.admin_setup_status() == {"available": True}

    created = main.setup_initial_admin(main.InitialAdminSetup.model_validate({
        "full_name": "First Administrator",
        "email": "first-administrator@example.test",
        "password": "Roadwatch-Strong-123!",
        "confirm_password": "Roadwatch-Strong-123!",
        "setup_secret": setup_secret,
    }))
    assert created["role"] == "administrator"
    assert users.find_one({"_id": existing_admin_id})["role"] == "admin"
    assert main.admin_setup_status() == {"available": False}

    existing_administrator = SetupUsers([{
        "_id": ObjectId(),
        "email": "existing-administrator@example.test",
        "role": "administrator",
    }])
    monkeypatch.setattr(main, "users_collection", existing_administrator)
    assert main.admin_setup_status() == {"available": False}
    with pytest.raises(HTTPException) as already_initialized:
        main.setup_initial_admin(main.InitialAdminSetup.model_validate({
            "full_name": "Another Admin",
            "email": "another@example.test",
            "password": "Roadwatch-Strong-123!",
            "confirm_password": "Roadwatch-Strong-123!",
            "setup_secret": setup_secret,
        }))
    assert already_initialized.value.status_code == 409

    with pytest.raises(ValueError, match="Passwords do not match"):
        main.InitialAdminSetup.model_validate({
            "full_name": "Another Admin",
            "email": "another@example.test",
            "password": "Roadwatch-Strong-123!",
            "confirm_password": "Different-Strong-456!",
            "setup_secret": setup_secret,
        })

    existing_user = SetupUsers([{
        "_id": ObjectId(),
        "email": "existing-user@example.test",
        "full_name": "Existing User",
        "role": "user",
        "password_hash": "unchanged",
    }])
    monkeypatch.setattr(main, "users_collection", existing_user)
    with pytest.raises(HTTPException) as duplicate_account:
        main.setup_initial_admin(main.InitialAdminSetup.model_validate({
            "full_name": "Existing User",
            "email": "EXISTING-USER@example.test",
            "password": "Roadwatch-Strong-123!",
            "confirm_password": "Roadwatch-Strong-123!",
            "setup_secret": setup_secret,
        }))
    assert duplicate_account.value.status_code == 409
    assert existing_user.documents[0]["role"] == "user"
    assert existing_user.documents[0]["password_hash"] == "unchanged"


def test_public_registration_is_always_user_and_login_issues_cookie(monkeypatch):
    users = MemoryCollection()
    monkeypatch.setattr(main, "users_collection", users)
    monkeypatch.setattr(main, "JWT_SECRET", "test-secret-for-auth-tests")
    registration = main.Registration.model_validate({
        "full_name": "Surveyor One", "email": "one@example.test",
        "password": "Survey-password-123!", "requested_role": "admin",
    })

    created = main.register(registration)
    assert created["role"] == "user"
    assert created["admin_request_status"] == "pending"
    assert "password_hash" not in created
    user = users.find_one({"email": "one@example.test"})
    assert user["role"] == "user"
    assert user["requested_role"] == "admin"
    assert user["admin_request_status"] == "pending"
    assert user["email_verified"] is False
    assert bcrypt.checkpw(b"Survey-password-123!", user["password_hash"].encode())

    users.documents[0]["email_verified"] = True
    response = Response()
    logged_in = main.login(main.Login(email="ONE@example.test", password="Survey-password-123!"), response)
    assert response.headers.get("set-cookie", "").startswith("roadwatch_access_token=")
    assert logged_in["user"]["role"] == "user"
    assert logged_in["user"]["created_at"] == main.iso_or_none(user["created_at"])
    assert main.auth_me(user)["created_at"] == main.iso_or_none(user["created_at"])

    normal_user = main.Registration.model_validate({
        "full_name": "Surveyor Two", "email": "two@example.test",
        "password": "Survey-password-456!",
    })
    normal_created = main.register(normal_user)
    assert normal_created["role"] == "user"
    assert normal_created["admin_request_status"] == "not_requested"


def test_admin_request_requires_an_existing_admin_to_approve(monkeypatch):
    applicant_id = ObjectId()
    admin_id = ObjectId()
    users = MemoryCollection([{
        "_id": applicant_id, "role": "user", "admin_request_status": "pending",
    }])
    monkeypatch.setattr(main, "users_collection", users)

    approved = main.approve_admin_request(str(applicant_id), {"_id": admin_id, "role": "administrator"})

    assert approved["role"] == "admin"
    saved = users.find_one({"_id": applicant_id})
    assert saved["role"] == "admin"
    assert saved["admin_request_status"] == "approved"
    assert saved["admin_request_notice"] == "approved"


def test_rejected_admin_request_keeps_user_role_and_sets_notification(monkeypatch):
    applicant_id = ObjectId()
    admin_id = ObjectId()
    users = MemoryCollection([{
        "_id": applicant_id, "role": "user", "requested_role": "admin",
        "admin_request_status": "pending",
    }])
    monkeypatch.setattr(main, "users_collection", users)

    rejected = main.reject_admin_request(str(applicant_id), {"_id": admin_id, "role": "administrator"})

    saved = users.find_one({"_id": applicant_id})
    assert rejected["admin_request_status"] == "rejected"
    assert saved["role"] == "user"
    assert saved["admin_request_status"] == "rejected"
    assert saved["admin_request_notice"] == "rejected"


def test_admin_request_counts_include_pending_approved_and_rejected(monkeypatch):
    users = MemoryCollection([
        {"_id": ObjectId(), "admin_request_status": "pending"},
        {"_id": ObjectId(), "admin_request_status": "approved"},
        {"_id": ObjectId(), "admin_request_status": "rejected"},
        {"_id": ObjectId(), "admin_request_status": "not_requested"},
    ])
    monkeypatch.setattr(main, "users_collection", users)

    result = main.admin_users({"_id": ObjectId(), "role": "administrator"})

    assert result["admin_request_counts"] == {"pending": 1, "approved": 1, "rejected": 1, "revoked": 0}


def test_administrator_can_promote_and_revoke_admin_without_deleting_account(monkeypatch):
    user_id = ObjectId()
    administrator = {"_id": ObjectId(), "full_name": "Root Admin", "role": "administrator"}
    users = MemoryCollection([{
        "_id": user_id,
        "full_name": "Surveyor",
        "email": "surveyor@example.test",
        "role": "user",
        "is_active": True,
    }])
    notifications = MemoryCollection()
    monkeypatch.setattr(main, "users_collection", users)
    monkeypatch.setattr(main, "notifications_collection", notifications)

    promoted = main.promote_user_to_admin(str(user_id), administrator)
    assert promoted["role"] == "admin"
    assert users.find_one({"_id": user_id})["role"] == "admin"

    revoked = main.revoke_admin_access(str(user_id), administrator)
    saved = users.find_one({"_id": user_id})
    assert revoked["role"] == "user"
    assert saved["role"] == "user"
    assert saved["admin_request_status"] == "revoked"
    assert saved["is_active"] is True
    assert len(users.documents) == 1
    assert len(notifications.documents) == 2


def test_approved_request_changes_role_on_next_login_and_notice_can_be_acknowledged(monkeypatch):
    applicant_id = ObjectId()
    admin_id = ObjectId()
    users = MemoryCollection([{
        "_id": applicant_id, "full_name": "Surveyor", "email": "one@example.test",
        "role": "user", "requested_role": "admin", "admin_request_status": "pending",
        "is_active": True, "password_hash": bcrypt.hashpw(b"Survey-password-123!", bcrypt.gensalt()).decode(),
    }])
    monkeypatch.setattr(main, "users_collection", users)
    monkeypatch.setattr(main, "JWT_SECRET", "test-secret-for-auth-tests")

    response = Response()
    user_login = main.login(main.Login(email="one@example.test", password="Survey-password-123!"), response)
    main.approve_admin_request(str(applicant_id), {"_id": admin_id, "role": "administrator"})
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    refreshed_user = main.get_current_user(
        request,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=session_token(response)),
    )
    assert refreshed_user["role"] == "admin"
    assert main.auth_me(refreshed_user)["admin_request_notice"] == "approved"

    logged_in = main.login(main.Login(email="one@example.test", password="Survey-password-123!"), Response())

    assert logged_in["user"]["role"] == "admin"
    assert logged_in["user"]["admin_request_notice"] == "approved"
    main.acknowledge_admin_request_notice(users.find_one({"_id": applicant_id}))
    assert users.find_one({"_id": applicant_id})["admin_request_notice"] is None


def test_database_administrator_role_label_is_canonicalized():
    assert main.canonical_role(" Administrator ") == "administrator"
    assert main.canonical_role(" Admin ") == "admin"
    assert main.canonical_role("USER") == "user"


def test_verified_token_resolves_user_and_rejects_user_as_admin(monkeypatch):
    user = {"_id": ObjectId(), "full_name": "Surveyor", "email": "one@example.test", "role": "user", "is_active": True}
    monkeypatch.setattr(main, "users_collection", MemoryCollection([user]))
    monkeypatch.setattr(main, "JWT_SECRET", "test-secret-for-auth-tests")
    token = main.jwt.encode({"sub": str(user["_id"]), "exp": datetime.now(timezone.utc).timestamp() + 300}, main.JWT_SECRET, algorithm="HS256")
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    assert main.get_current_user(request, credentials)["_id"] == user["_id"]
    try:
        main.require_admin(user)
    except HTTPException as error:
        assert error.status_code == 403
    else:
        raise AssertionError("Regular users must not satisfy require_admin")
    try:
        main.require_administrator(user)
    except HTTPException as error:
        assert error.status_code == 403
    else:
        raise AssertionError("Regular users must not satisfy require_administrator")


def test_admin_and_administrator_have_separate_account_authority():
    admin = {"_id": ObjectId(), "role": "admin"}
    administrator = {"_id": ObjectId(), "role": "administrator"}

    assert main.require_admin(admin) is admin
    assert main.require_admin(administrator) is administrator
    with pytest.raises(HTTPException) as admin_cannot_manage_accounts:
        main.require_administrator(admin)
    assert admin_cannot_manage_accounts.value.status_code == 403
    assert main.require_administrator(administrator) is administrator


def test_mongo_to_json_recursively_converts_bson_values():
    from bson.binary import Binary
    from bson.dbref import DBRef
    from bson.decimal128 import Decimal128
    from bson.datetime_ms import DatetimeMS
    from bson.timestamp import Timestamp

    identifier = ObjectId()
    value = main.mongo_to_json({
        "_id": identifier,
        "created_at": datetime(2026, 11, 21, 4, 44, tzinfo=timezone.utc),
        "nested": [{"reference": identifier, "decimal": Decimal128("12.340")}],
        "binary": Binary(b"\x00\xff"),
        "timestamp": Timestamp(123, 2),
        "reference": DBRef("users", identifier),
        "out_of_range_date": DatetimeMS(253402300800000),
        "not_finite": float("nan"),
    })

    json.dumps(value)
    assert value["_id"] == str(identifier)
    assert value["nested"][0]["reference"] == str(identifier)
    assert value["nested"][0]["decimal"] == "12.340"
    assert value["created_at"] == "2026-11-21T04:44:00+00:00"
    assert value["binary"] == "AP8="
    assert value["timestamp"] == {"$timestamp": {"t": 123, "i": 2}}
    assert value["reference"]["$id"] == str(identifier)
    assert value["out_of_range_date"] == {"$date": {"$numberLong": "253402300800000"}}
    assert value["not_finite"] is None


def test_mongodb_get_endpoints_return_json_safe_allowlisted_records(monkeypatch):
    from bson.decimal128 import Decimal128

    user_id = ObjectId()
    administrator_id = ObjectId()
    nested_id = ObjectId()
    record_time = datetime(2026, 11, 21, 4, 44, tzinfo=timezone.utc)
    account = {
        "_id": user_id,
        "full_name": "Surveyor",
        "email": "surveyor@example.test",
        "role": "user",
        "password_hash": "must-not-be-returned",
        "setup_secret": "must-not-be-returned",
        "access_token": "must-not-be-returned",
        "created_at": record_time,
    }
    administrator = {
        "_id": administrator_id,
        "full_name": "Administrator",
        "email": "administrator@example.test",
        "role": "administrator",
        "is_active": True,
        "created_at": record_time,
    }
    pothole = {
        "_id": ObjectId(),
        "pothole_id": "PH-101",
        "user_id": user_id,
        "created_at": record_time,
        "last_seen_at": record_time,
        "latitude": 10,
        "longitude": 20,
        "gps_accuracy_m": 5,
        "verification_status": "verified",
        "location_verification_method": "MANUAL",
        "nested": {"reference": nested_id, "decimal": Decimal128("9.25")},
        "audit_history": [{"actor": nested_id, "timestamp": record_time}],
    }
    detection = {
        "_id": ObjectId(),
        "event_id": "event-101",
        "user_id": user_id,
        "timestamp": record_time,
        "nested": [{"reference": nested_id}],
    }
    notification = {
        "_id": ObjectId(),
        "user_id": administrator_id,
        "created_at": record_time,
        "details": {"nested": {"reference": nested_id}},
    }

    monkeypatch.setattr(main, "users_collection", MemoryCollection([account, administrator]))
    monkeypatch.setattr(main, "potholes_collection", MemoryCollection([pothole]))
    monkeypatch.setattr(main, "detections_collection", MemoryCollection([detection]))
    monkeypatch.setattr(main, "notifications_collection", MemoryCollection([notification]))
    responses = {
        "detections": main.get_detections(page=1, page_size=50, user=administrator),
        "detection": main.get_detection("event-101", user=administrator),
        "potholes": main.get_potholes(page=1, page_size=25, user=administrator),
        "pothole_map": main.get_pothole_map(user=administrator),
        "pothole": main.get_pothole("PH-101", user=administrator),
        "notifications": main.list_notifications(limit=50, user=administrator),
        "analytics_summary": main.analytics_summary(user=administrator),
        "analytics_trends": main.analytics_trends(user=administrator),
        "admin_users": main.admin_users(_=administrator),
        "admin_dashboard": main.admin_dashboard(user=administrator),
        "admin_analytics": main.admin_analytics(admin=administrator),
        "user_dashboard": main.user_dashboard(user=administrator),
    }

    for response in responses.values():
        json.dumps(response, allow_nan=False)

    detection_response = responses["detections"]
    pothole_response = responses["potholes"]
    notification_response = responses["notifications"]
    user_response = responses["admin_users"]

    assert detection_response["detections"][0]["_id"] == str(detection["_id"])
    assert detection_response["detections"][0]["nested"][0]["reference"] == str(nested_id)
    assert detection_response["detections"][0]["timestamp"] == record_time.isoformat()
    assert pothole_response["potholes"][0]["_id"] == str(pothole["_id"])
    assert pothole_response["potholes"][0]["nested"]["reference"] == str(nested_id)
    assert pothole_response["potholes"][0]["created_at"] == record_time.isoformat()
    assert notification_response["notifications"][0]["_id"] == str(notification["_id"])
    assert notification_response["notifications"][0]["details"]["nested"]["reference"] == str(nested_id)
    assert "password_hash" not in json.dumps(user_response)
    assert "must-not-be-returned" not in json.dumps(user_response)


def test_fastapi_serializes_detection_and_pothole_get_responses(monkeypatch):
    pytest.importorskip("httpx2")
    from fastapi.testclient import TestClient

    user_id = ObjectId()
    nested_id = ObjectId()
    record_time = datetime(2026, 11, 21, 4, 44, tzinfo=timezone.utc)
    user = {"_id": user_id, "full_name": "Surveyor", "role": "user"}
    detection = {
        "_id": ObjectId(),
        "event_id": "event-http",
        "user_id": user_id,
        "timestamp": record_time,
        "nested": {"reference": nested_id},
    }
    pothole = {
        "_id": ObjectId(),
        "pothole_id": "PH-HTTP",
        "user_id": user_id,
        "created_at": record_time,
        "last_seen_at": record_time,
        "latitude": 10,
        "longitude": 20,
        "gps_accuracy_m": 5,
        "nested": {"reference": nested_id},
    }
    monkeypatch.setattr(main, "users_collection", MemoryCollection([user]))
    monkeypatch.setattr(main, "detections_collection", MemoryCollection([detection]))
    monkeypatch.setattr(main, "potholes_collection", MemoryCollection([pothole]))
    monkeypatch.setitem(main.app.dependency_overrides, main.get_current_user, lambda: user)

    client = TestClient(main.app)
    detection_response = client.get("/api/detections?page=1&page_size=50")
    pothole_response = client.get("/api/potholes?page=1&page_size=25")

    assert detection_response.status_code == 200
    assert pothole_response.status_code == 200
    serialized_detection = detection_response.json()["detections"][0]
    serialized_pothole = pothole_response.json()["potholes"][0]
    assert serialized_detection["_id"] == str(detection["_id"])
    assert serialized_detection["nested"]["reference"] == str(nested_id)
    assert serialized_detection["timestamp"] == record_time.isoformat()
    assert serialized_pothole["_id"] == str(pothole["_id"])
    assert serialized_pothole["nested"]["reference"] == str(nested_id)
    assert serialized_pothole["created_at"] == record_time.isoformat()


def test_pothole_report_date_filter_is_inclusive_scoped_and_exportable(monkeypatch):
    pytest.importorskip("httpx2")
    from fastapi.testclient import TestClient

    class DateAwareCollection(MemoryCollection):
        @classmethod
        def matches(cls, item, query):
            for key, expected in query.items():
                if key == "$and":
                    if not all(cls.matches(item, clause) for clause in expected):
                        return False
                    continue
                actual = item.get(key)
                if isinstance(expected, dict):
                    if "$in" in expected and actual not in expected["$in"]:
                        return False
                    if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                        return False
                    if "$lt" in expected and (actual is None or actual >= expected["$lt"]):
                        return False
                    continue
                if actual != expected:
                    return False
            return True

        def find(self, query, *_args, **_kwargs):
            return MemoryCursor([item for item in self.documents if self.matches(item, query)])

        def count_documents(self, query):
            return sum(1 for item in self.documents if self.matches(item, query))

    user_id, other_user_id = ObjectId(), ObjectId()
    user = {"_id": user_id, "full_name": "Report Owner", "role": "user"}
    records = [
        {
            "_id": ObjectId(), "pothole_id": "PH-START", "user_id": user_id,
            "created_at": datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc),
            "verification_status": "pending_verification", "repair_status": "not_started",
        },
        {
            "_id": ObjectId(), "pothole_id": "PH-END", "user_id": user_id,
            "created_at": datetime(2026, 11, 21, 23, 59, 59, tzinfo=timezone.utc),
            "verification_status": "verified", "repair_status": "repaired",
        },
        {
            "_id": ObjectId(), "pothole_id": "PH-NEXT-DAY", "user_id": user_id,
            "created_at": datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc),
            "verification_status": "verified", "repair_status": "repaired",
        },
        {
            "_id": ObjectId(), "pothole_id": "PH-OTHER-USER", "user_id": other_user_id,
            "created_at": datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc),
            "verification_status": "rejected", "repair_status": "not_started",
        },
    ]
    monkeypatch.setattr(main, "potholes_collection", DateAwareCollection(records))
    monkeypatch.setattr(main, "users_collection", MemoryCollection())
    monkeypatch.setitem(main.app.dependency_overrides, main.get_current_user, lambda: user)
    client = TestClient(main.app)
    params = "all_records=true&include_statistics=true&date_from=2026-11-20&date_to=2026-11-21"

    response = client.get(f"/api/potholes?{params}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert {item["pothole_id"] for item in payload["potholes"]} == {"PH-START", "PH-END"}
    assert payload["statistics"]["verification"] == {
        "pending": 1, "verified": 1, "rejected": 0, "other": 0,
    }
    assert payload["statistics"]["repair"]["repaired"] == 1

    csv_response = client.get("/api/reports/export.csv?date_from=2026-11-20&date_to=2026-11-21")
    assert csv_response.status_code == 200
    assert "PH-START" in csv_response.text
    assert "PH-END" in csv_response.text
    assert "PH-NEXT-DAY" not in csv_response.text
    assert "PH-OTHER-USER" not in csv_response.text

    invalid_range = client.get("/api/potholes?date_from=2026-11-22&date_to=2026-11-20")
    assert invalid_range.status_code == 422


def test_analytics_and_admin_routes_require_admin_authorization():
    admin_paths = {
        "/api/analytics/summary", "/api/analytics/trends", "/api/admin/dashboard",
        "/api/admin/analytics",
        "/api/potholes/{pothole_id}/verify", "/api/potholes/{pothole_id}/repair",
    }
    administrator_paths = {
        "/api/admin/users",
        "/api/admin/users/{user_id}/status",
        "/api/admin/users/{user_id}/approve-admin", "/api/admin/users/{user_id}/reject-admin",
        "/api/admin/users/{user_id}/promote-admin", "/api/admin/users/{user_id}/revoke-admin",
    }
    for path in admin_paths:
        route = next(route for route in main.app.routes if getattr(route, "path", None) == path)
        assert any(dependency.call is main.require_admin for dependency in route.dependant.dependencies)
    for path in administrator_paths:
        route = next(route for route in main.app.routes if getattr(route, "path", None) == path)
        assert any(dependency.call is main.require_administrator for dependency in route.dependant.dependencies)

    report_schedule_routes = {
        "/api/reports/schedules",
        "/api/reports/schedules/{schedule_id}",
        "/api/reports/schedules/{schedule_id}/runs/{run_id}/download",
    }
    for path in report_schedule_routes:
        route = next(route for route in main.app.routes if getattr(route, "path", None) == path)
        assert any(dependency.call is main.get_current_user for dependency in route.dependant.dependencies)

    rate_limited_auth_routes = {
        "/api/auth/register": main.limit_registration,
        "/api/auth/login": main.limit_login,
        "/api/auth/admin-setup": main.limit_initial_setup,
        "/api/auth/email-verification/confirm": main.limit_email_verification,
        "/api/auth/email-verification/resend": main.limit_email_verification_resend,
        "/api/auth/password-reset/request": main.limit_password_reset_request,
        "/api/auth/password-reset/confirm": main.limit_password_reset_confirm,
    }
    for path, limiter in rate_limited_auth_routes.items():
        route = next(route for route in main.app.routes if getattr(route, "path", None) == path)
        assert any(dependency.call is limiter for dependency in route.dependant.dependencies)


def test_setup_created_admin_logs_in_with_database_role(monkeypatch):
    admin = {
        "_id": ObjectId(), "full_name": "Initial Administrator",
        "email": "admin@example.test", "role": "admin", "is_active": True,
        "password_hash": bcrypt.hashpw(b"long-admin-password", bcrypt.gensalt()).decode(),
    }
    users = MemoryCollection([admin])
    monkeypatch.setattr(main, "users_collection", users)
    monkeypatch.setattr(main, "JWT_SECRET", "test-secret-for-auth-tests")

    response = Response()
    logged_in = main.login(main.Login(email=admin["email"], password="long-admin-password"), response)
    claims = main.jwt.decode(session_token(response), main.JWT_SECRET, algorithms=["HS256"])
    assert "access_token" not in logged_in

    assert logged_in["user"]["role"] == "admin"
    assert claims["sub"] == str(admin["_id"])
    assert claims["role"] == "admin"
    assert response.headers.get("set-cookie", "").startswith("roadwatch_access_token=")


def test_report_export_formats_and_combined_filters():
    record = {
        "pothole_id": "PH-101",
        "created_at": datetime(2026, 11, 21, 4, 44, tzinfo=timezone.utc),
        "region": "North District",
        "severity": "high",
        "confidence_percent": 91.2,
        "verification_status": "verified",
        "repair_status": "under_maintenance",
        "source": "image_upload",
        "observation_count": 2,
    }
    filters = {
        "date_from": date(2026, 11, 1),
        "date_to": date(2026, 11, 30),
        "location": "North",
        "severity": "high",
        "verification_status": "verified",
        "repair_status": "under_maintenance",
    }

    pdf = main.report_export_response("pdf", [record], filters)
    workbook = main.report_export_response("xlsx", [record], filters)
    combined = main.build_filters("high", "verified", "under_maintenance", None, "PH-101", "North")

    assert pdf.media_type == "application/pdf"
    assert pdf.body.startswith(b"%PDF")
    assert "roadwatch-potholes.pdf" in pdf.headers["content-disposition"]
    assert workbook.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert workbook.body.startswith(b"PK")
    assert "roadwatch-potholes.xlsx" in workbook.headers["content-disposition"]
    assert combined["severity"] == "high"
    assert combined["verification_status"] == "verified"
    assert combined["repair_status"]["$in"]
    assert len(combined["$and"]) == 2
    assert main.report_date_range_label(filters["date_from"], filters["date_to"]) == "2026-11-01 – 2026-11-30"


def test_report_schedule_dates_clamp_month_end_and_limit_visibility(monkeypatch):
    month_end = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
    assert main.next_scheduled_report_run(month_end, "daily") == month_end + timedelta(days=1)
    assert main.next_scheduled_report_run(month_end, "weekly") == month_end + timedelta(days=7)
    february_run = main.next_scheduled_report_run(month_end, "monthly", anchor_day=31)
    assert february_run == datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc)
    assert main.next_scheduled_report_run(february_run, "monthly", anchor_day=31) == datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)

    schedules = MemoryCollection()
    monkeypatch.setattr(main, "report_schedules_collection", schedules)
    user_id = ObjectId()
    user = {"_id": user_id, "role": "user"}
    result = main.create_report_schedule(
        main.ReportScheduleCreate.model_validate({
            "frequency": "monthly",
            "format": "xlsx",
            "date_from": "2026-11-01",
            "date_to": "2026-11-30",
            "location": "North",
            "severity": "high",
        }),
        user,
    )
    saved = schedules.documents[0]
    assert saved["owner_id"] == user_id
    assert saved["filters"] == {
        "location": "North",
        "severity": "high",
        "date_from": "2026-11-01",
        "date_to": "2026-11-30",
    }
    assert result["schedule"]["next_run_at"]
    assert main.report_schedule_owner_filter(user) == {"owner_id": user_id}
    assert main.report_schedule_owner_filter({"_id": ObjectId(), "role": "administrator"}) == {}


@pytest.mark.parametrize("status", ["active", "running"])
def test_report_schedule_cannot_resume_unless_paused(status, monkeypatch):
    schedule_id = ObjectId()
    owner_id = ObjectId()
    lease_expires_at = datetime(2026, 11, 21, 12, 5, tzinfo=timezone.utc)
    schedules = MemoryCollection([{
        "_id": schedule_id,
        "owner_id": owner_id,
        "status": status,
        "frequency": "daily",
        "next_run_at": datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc),
        "lease_expires_at": lease_expires_at,
    }])
    monkeypatch.setattr(main, "report_schedules_collection", schedules)

    with pytest.raises(HTTPException) as error:
        main.update_report_schedule(
            str(schedule_id),
            main.ReportScheduleAction(action="resume"),
            user={"_id": owner_id, "role": "user"},
        )

    assert error.value.status_code == 409
    assert schedules.documents[0]["status"] == status
    assert schedules.documents[0]["lease_expires_at"] == lease_expires_at


def test_scheduled_report_worker_writes_a_private_filtered_run(tmp_path, monkeypatch):
    owner_id = ObjectId()
    schedule_id = ObjectId()
    owner = {
        "_id": owner_id,
        "full_name": "Report Owner",
        "role": "user",
        "is_active": True,
        "email_verified": True,
    }
    schedule = {
        "_id": schedule_id,
        "owner_id": owner_id,
        "frequency": "weekly",
        "format": "xlsx",
        "filters": {"severity": "high"},
        "status": "running",
    }
    record = {
        "_id": ObjectId(),
        "pothole_id": "PH-101",
        "user_id": owner_id,
        "created_at": datetime(2026, 11, 21, 4, 44, tzinfo=timezone.utc),
        "severity": "high",
        "verification_status": "verified",
        "repair_status": "repaired",
    }
    schedules = MemoryCollection([schedule])
    runs = MemoryCollection()
    monkeypatch.setattr(main, "users_collection", MemoryCollection([owner]))
    monkeypatch.setattr(main, "potholes_collection", MemoryCollection([record]))
    monkeypatch.setattr(main, "report_schedules_collection", schedules)
    monkeypatch.setattr(main, "report_runs_collection", runs)
    monkeypatch.setattr(main, "REPORTS_DIR", tmp_path)

    main._complete_scheduled_report(schedule)

    assert schedules.documents[0]["status"] == "active"
    assert schedules.documents[0]["last_run_status"] == "completed"
    assert len(runs.documents) == 1
    run = runs.documents[0]
    assert run["record_count"] == 1
    assert run["access_scope"] == "user"
    assert (tmp_path / run["storage_filename"]).read_bytes().startswith(b"PK")


def test_pothole_list_is_scoped_to_owner_and_admin_sees_legacy(monkeypatch):
    user_a = {"_id": ObjectId(), "role": "user"}
    user_b = {"_id": ObjectId(), "role": "user"}
    admin = {"_id": ObjectId(), "role": "admin"}
    administrator = {"_id": ObjectId(), "role": "administrator"}
    legacy_id = ObjectId()
    potholes = MemoryCollection([
        {"pothole_id": "A-1", "user_id": user_a["_id"], "created_at": datetime.now(timezone.utc)},
        {"pothole_id": "B-1", "user_id": user_b["_id"], "created_at": datetime.now(timezone.utc)},
        {"pothole_id": "LEGACY", "_id": legacy_id, "created_at": datetime.now(timezone.utc)},
    ])
    monkeypatch.setattr(main, "potholes_collection", potholes)
    monkeypatch.setattr(main, "users_collection", MemoryCollection())

    assert [r["pothole_id"] for r in main.get_potholes(page=1, page_size=25, user=user_a)["potholes"]] == ["A-1"]
    assert [r["pothole_id"] for r in main.get_potholes(page=1, page_size=25, user=user_b)["potholes"]] == ["B-1"]
    assert {r["pothole_id"] for r in main.get_potholes(page=1, page_size=25, user=admin)["potholes"]} == {"A-1", "B-1", "LEGACY"}
    assert {r["pothole_id"] for r in main.get_potholes(page=1, page_size=25, user=administrator)["potholes"]} == {"A-1", "B-1", "LEGACY"}


def test_map_records_are_owner_scoped_for_users_and_global_for_admin(monkeypatch):
    user_a = {"_id": ObjectId(), "role": "user"}
    user_b = {"_id": ObjectId(), "role": "user"}
    admin = {"_id": ObjectId(), "role": "admin"}
    administrator = {"_id": ObjectId(), "role": "administrator"}
    potholes = MemoryCollection([
        {"pothole_id": "A-1", "latitude": 10, "longitude": 20, "gps_accuracy_m": 10, "verification_status": "verified", "user_id": user_a["_id"]},
        {"pothole_id": "B-1", "latitude": 11, "longitude": 21, "gps_accuracy_m": 10, "verification_status": "verified", "user_id": user_b["_id"]},
        {"pothole_id": "LEGACY", "latitude": 12, "longitude": 22, "gps_accuracy_m": 10, "verification_status": "verified"},
    ])
    monkeypatch.setattr(main, "potholes_collection", potholes)
    monkeypatch.setattr(main, "users_collection", MemoryCollection())

    user_map = main.get_pothole_map(user=user_a)
    admin_map = main.get_pothole_map(user=admin)
    administrator_map = main.get_pothole_map(user=administrator)

    assert {record["pothole_id"] for record in user_map["records"]} == {"A-1"}
    assert {record["pothole_id"] for record in admin_map["records"]} == {"A-1", "B-1", "LEGACY"}
    assert {record["pothole_id"] for record in administrator_map["records"]} == {"A-1", "B-1", "LEGACY"}


def test_two_users_and_admin_get_scoped_records_stats_and_submitter_identity(monkeypatch):
    user_a_id, user_b_id = ObjectId(), ObjectId()
    user_a = {"_id": user_a_id, "full_name": "Surveyor A", "email": "a@example.test", "role": "user"}
    user_b = {"_id": user_b_id, "full_name": "Surveyor B", "email": "b@example.test", "role": "user"}
    admin = {"_id": ObjectId(), "full_name": "Administrator", "role": "admin"}
    users = MemoryCollection([user_a, user_b, admin])
    potholes = MemoryCollection([
        {"pothole_id": "A-1", "severity": "low", "latitude": 10, "longitude": 20, "gps_accuracy_m": 10, "verification_status": "verified", "user_id": user_a_id},
        {"pothole_id": "B-1", "severity": "critical", "latitude": 11, "longitude": 21, "location_verification_method": "MANUAL", "verification_status": "verified", "user_id": user_b_id},
        {"pothole_id": "LEGACY", "severity": "medium"},
    ])
    detections = MemoryCollection([
        {"event_id": "event-a-image", "source_id": "image_upload", "pothole_id": "A-1", "user_id": user_a_id},
        {"event_id": "event-a-live", "source_id": "local-windows-agent", "pothole_id": "A-1", "user_id": user_a_id},
        {"event_id": "event-b-video", "source_id": "video_upload", "pothole_id": "B-1", "user_id": user_b_id},
        {"event_id": "legacy-event", "source_id": "legacy"},
    ])
    monkeypatch.setattr(main, "users_collection", users)
    monkeypatch.setattr(main, "potholes_collection", potholes)
    monkeypatch.setattr(main, "detections_collection", detections)

    user_a_records = main.get_potholes(page=1, page_size=25, user=user_a)["potholes"]
    user_b_records = main.get_potholes(page=1, page_size=25, user=user_b)["potholes"]
    admin_records = main.get_potholes(page=1, page_size=25, user=admin)["potholes"]
    user_a_events = main.get_detections(page=1, page_size=25, user=user_a)["detections"]
    user_b_events = main.get_detections(page=1, page_size=25, user=user_b)["detections"]
    admin_events = main.get_detections(page=1, page_size=25, user=admin)["detections"]
    user_a_map = main.get_pothole_map(user=user_a)["records"]
    user_b_map = main.get_pothole_map(user=user_b)["records"]
    admin_map = main.get_pothole_map(user=admin)["records"]

    assert [record["pothole_id"] for record in user_a_records] == ["A-1"]
    assert [record["pothole_id"] for record in user_b_records] == ["B-1"]
    assert {record["pothole_id"] for record in admin_records} == {"A-1", "B-1", "LEGACY"}
    assert {event["event_id"] for event in user_a_events} == {"event-a-image", "event-a-live"}
    assert {event["event_id"] for event in user_b_events} == {"event-b-video"}
    assert len(admin_events) == 4
    assert {record["pothole_id"] for record in user_a_map} == {"A-1"}
    assert {record["pothole_id"] for record in user_b_map} == {"B-1"}
    assert {record["pothole_id"] for record in admin_map} == {"A-1", "B-1"}
    assert next(record for record in admin_records if record["pothole_id"] == "A-1")["reported_by_display"] == "Surveyor A"
    assert next(event for event in admin_events if event["event_id"] == "event-b-video")["reported_by_display"] == "Surveyor B"
    assert next(record for record in admin_records if record["pothole_id"] == "LEGACY")["reported_by_display"] == "Legacy / unassigned"

    user_a_dashboard = main.user_dashboard(user_a)
    user_b_dashboard = main.user_dashboard(user_b)
    assert user_a_dashboard["total_potholes"] == 1
    assert user_a_dashboard["total_detections"] == 2
    assert user_a_dashboard["severity_counts"] == {"low": 1, "medium": 0, "high": 0}
    assert user_a_dashboard["detection_counts"] == {"image": 1, "video": 0, "live": 1, "other": 0}
    assert user_b_dashboard["total_potholes"] == 1
    assert user_b_dashboard["total_detections"] == 1
    assert user_b_dashboard["severity_counts"] == {"low": 0, "medium": 0, "high": 1}


def test_admin_verification_and_repair_updates_are_audited_and_visible_to_owner(monkeypatch):
    user_id, admin_id = ObjectId(), ObjectId()
    user = {"_id": user_id, "full_name": "Submitting User", "email": "owner@example.test", "role": "user"}
    admin = {"_id": admin_id, "full_name": "Review Admin", "role": "admin"}
    potholes = MemoryCollection([{
        "pothole_id": "PH-STATUS",
        "user_id": user_id,
        "verification_status": "pending_verification",
        "repair_status": "not_started",
        "latitude": 10,
        "longitude": 20,
        "gps_accuracy_m": 10,
        "confidence": 0.9,
        "source": "image_upload",
    }])
    monkeypatch.setattr(main, "users_collection", MemoryCollection([user, admin]))
    monkeypatch.setattr(main, "potholes_collection", potholes)
    monkeypatch.setattr(main, "detections_collection", MemoryCollection())
    notifications = MemoryCollection()
    monkeypatch.setattr(main, "notifications_collection", notifications)

    verified = main.verify_pothole(
        "PH-STATUS",
        main.VerificationUpdate(status="verified"),
        admin=admin,
    )
    assert verified["pothole"]["verification_status"] == "verified"
    assert verified["pothole"]["verified_by_display"] == "Review Admin"
    assert verified["pothole"]["verified_at"]

    rejected = main.verify_pothole(
        "PH-STATUS",
        main.VerificationUpdate(status="rejected", rejection_reason="Duplicate report"),
        admin=admin,
    )
    assert rejected["pothole"]["verification_status"] == "rejected"
    assert rejected["pothole"]["rejection_reason"] == "Duplicate report"
    assert rejected["pothole"]["verified_by"] is None

    main.repair_pothole(
        "PH-STATUS",
        main.RepairUpdate(status="under_maintenance"),
        admin=admin,
    )
    owner_record = main.get_potholes(page=1, page_size=25, user=user)["potholes"][0]
    filtered_records = main.get_potholes(
        page=1,
        page_size=25,
        verification_status="rejected",
        repair_status="under_maintenance",
        user=user,
    )["potholes"]
    saved = potholes.find_one({"pothole_id": "PH-STATUS"})
    assert owner_record["verification_status"] == "rejected"
    assert owner_record["repair_status"] == "under_maintenance"
    assert owner_record["rejection_reason"] == "Duplicate report"
    assert [record["pothole_id"] for record in filtered_records] == ["PH-STATUS"]
    assert saved["repair_updated_by"] == admin_id
    assert [entry["action"] for entry in saved["audit_history"]] == ["verification", "verification", "repair"]
    assert all(entry["administrator_id"] == str(admin_id) and entry["timestamp"] for entry in saved["audit_history"])
    assert [item["action"] for item in notifications.documents] == [
        "pothole_verified",
        "pothole_rejected",
        "maintenance_started",
    ]

    with pytest.raises(ValueError, match="rejection reason"):
        main.VerificationUpdate(status="rejected")


def test_pothole_summary_count_is_scoped_for_users_and_global_for_admin(monkeypatch):
    user_a = {"_id": ObjectId(), "role": "user"}
    user_b = {"_id": ObjectId(), "role": "user"}
    admin = {"_id": ObjectId(), "role": "admin"}
    potholes = MemoryCollection([
        {"pothole_id": "A-1", "user_id": user_a["_id"]},
        {"pothole_id": "B-1", "user_id": user_b["_id"]},
        {"pothole_id": "LEGACY"},
    ])
    monkeypatch.setattr(main, "potholes_collection", potholes)
    monkeypatch.setattr(main, "detections_collection", MemoryCollection())

    user_summary = main.analytics_summary(user_a)
    admin_summary = main.analytics_summary(admin)

    assert user_summary["total_pothole_records"] == 1
    assert user_summary["visible_pothole_records"] == 1
    assert admin_summary["total_pothole_records"] == 3
