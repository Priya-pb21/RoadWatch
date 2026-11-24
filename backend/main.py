from __future__ import annotations

import base64
import calendar
import hashlib
import hmac
import re
import secrets
import threading
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from math import asin, cos, isfinite, radians, sin, sqrt
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4
import logging

logger: logging.Logger = logging.getLogger(__name__)


import cv2
import numpy as np
import os
import sys
from bson import ObjectId
from bson.code import Code
from bson.dbref import DBRef
from bson.decimal128 import Decimal128
from bson.datetime_ms import DatetimeMS
from bson.max_key import MaxKey
from bson.min_key import MinKey
from bson.regex import Regex
from bson.timestamp import Timestamp
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Response, UploadFile, Request
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import bcrypt
from bson.errors import InvalidId
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, GEOSPHERE, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from smtplib import SMTPException

from backend.email_service import email_configuration, send_email
from backend.report_exports import render_pdf, render_xlsx

load_dotenv(Path(__file__).resolve().parent / ".env")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from backend.road_scene import INVALID_ROAD_IMAGE_MESSAGE, validate_road_scene

MONGODB_URL = os.getenv("MONGODB_URL")
DATABASE_NAME = os.getenv("MONGODB_DATABASE", "road_pothole_db")
POTHOLES_COLLECTION = os.getenv("POTHOLES_COLLECTION", "potholes")
DETECTIONS_COLLECTION = os.getenv("DETECTIONS_COLLECTION", "detection_events")
EVENTS_COLLECTION = os.getenv("EVENTS_COLLECTION", "system_events")
USERS_COLLECTION = os.getenv("USERS_COLLECTION", "users")
NOTIFICATIONS_COLLECTION = os.getenv("NOTIFICATIONS_COLLECTION", "notifications")
AUTH_TOKENS_COLLECTION = os.getenv("AUTH_TOKENS_COLLECTION", "auth_tokens")
AUTH_RATE_LIMITS_COLLECTION = os.getenv("AUTH_RATE_LIMITS_COLLECTION", "auth_rate_limits")
REPORT_SCHEDULES_COLLECTION = os.getenv("REPORT_SCHEDULES_COLLECTION", "report_schedules")
REPORT_RUNS_COLLECTION = os.getenv("REPORT_RUNS_COLLECTION", "report_runs")
JWT_SECRET = os.getenv("ROADWATCH_JWT_SECRET")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("ROADWATCH_JWT_EXPIRE_MINUTES", "720"))
COOKIE_SECURE = os.getenv("ROADWATCH_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
ADMIN_API_KEY = os.getenv("ROADWATCH_ADMIN_API_KEY")
INITIAL_ADMIN_SETUP_SECRET = os.getenv("ROADWATCH_INITIAL_ADMIN_SETUP_SECRET")
INGEST_API_KEY = os.getenv("ROADWATCH_INGEST_API_KEY")
DEFAULT_PAGE_SIZE = int(os.getenv("ROADWATCH_PAGE_SIZE", "25"))
MAX_PAGE_SIZE = int(os.getenv("ROADWATCH_MAX_PAGE_SIZE", "100"))
MODEL_PATH = Path(
    os.getenv(
        "ROADWATCH_MODEL_PATH",
        str(PROJECT_ROOT / "runs" / "pothole_yolo11n" / "weights" / "best.pt"),
    )
)
YOLO_CONFIDENCE_THRESHOLD = float(os.getenv("ROADWATCH_CONFIDENCE_THRESHOLD", "0.25"))
GPS_ACCURACY_THRESHOLD_M = float(os.getenv("ROADWATCH_GPS_ACCURACY_THRESHOLD_M", "100"))
if not isfinite(GPS_ACCURACY_THRESHOLD_M) or GPS_ACCURACY_THRESHOLD_M < 0:
    raise RuntimeError("ROADWATCH_GPS_ACCURACY_THRESHOLD_M must be a finite non-negative number")
EMAIL_VERIFICATION_TTL_MINUTES = int(os.getenv("ROADWATCH_EMAIL_VERIFICATION_TTL_MINUTES", "1440"))
PASSWORD_RESET_TTL_MINUTES = int(os.getenv("ROADWATCH_PASSWORD_RESET_TTL_MINUTES", "30"))
REPORT_RETENTION_DAYS = int(os.getenv("ROADWATCH_REPORT_RETENTION_DAYS", "30"))
if EMAIL_VERIFICATION_TTL_MINUTES <= 0 or PASSWORD_RESET_TTL_MINUTES <= 0 or REPORT_RETENTION_DAYS <= 0:
    raise RuntimeError("Email token and report retention durations must be positive.")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
UPLOADS_DIR = Path(os.getenv("ROADWATCH_UPLOADS_DIR", str(Path(__file__).resolve().parent / "uploads")))
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR = Path(os.getenv("ROADWATCH_REPORTS_DIRECTORY") or Path(__file__).resolve().parent / "generated_reports")

if not MONGODB_URL:
    raise RuntimeError("MONGODB_URL is missing from backend/.env")

client = MongoClient(MONGODB_URL, serverSelectionTimeoutMS=10000)
db = client[DATABASE_NAME]
potholes_collection = db[POTHOLES_COLLECTION]
detections_collection = db[DETECTIONS_COLLECTION]
events_collection = db[EVENTS_COLLECTION]
users_collection = db[USERS_COLLECTION]
notifications_collection = db[NOTIFICATIONS_COLLECTION]
auth_tokens_collection = db[AUTH_TOKENS_COLLECTION]
auth_rate_limits_collection = db[AUTH_RATE_LIMITS_COLLECTION]
report_schedules_collection = db[REPORT_SCHEDULES_COLLECTION]
report_runs_collection = db[REPORT_RUNS_COLLECTION]
sequences_collection = db["id_sequences"]
bearer_scheme = HTTPBearer(auto_error=False)
INITIAL_ADMIN_SETUP_ID = ObjectId("000000000000000000000002")
INITIAL_ADMINISTRATOR_ROLE_QUERY = {
    "role": {"$regex": r"^\s*administrator\s*$", "$options": "i"},
}

POTHOLE_ID_PATTERN = re.compile(r"^PH-(\d{3,})$", re.IGNORECASE)
POTHOLE_ID_COUNTER = "pothole_id"
REPORT_SCHEDULER_POLL_SECONDS = 10
REPORT_SCHEDULER_LEASE_MINUTES = 120

app = FastAPI(
    title="RoadWatch AI API",
    description="Road pothole detection, GIS monitoring, and maintenance management API",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Severity = Literal["low", "medium", "high", "critical"]
VerificationStatus = Literal[
    "pending_verification",
    "unverified",
    "awaiting_verification",
    "verified",
    "rejected",
]
RepairStatus = Literal[
    "not_started",
    "under_maintenance",
    "repaired",
    "reported",
    "awaiting_verification",
    "verified",
    "inspection_scheduled",
    "repair_scheduled",
    "repair_in_progress",
    "marked_repaired",
    "awaiting_repair_verification",
    "repair_verified",
    "reopened",
]


class BoundingBox(BaseModel):
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None


class LegacyPotholeCreate(BaseModel):
    pothole_id: int | str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    confidence: float

    @field_validator("confidence")
    @classmethod
    def confidence_is_valid(cls, value: float) -> float:
        if normalize_confidence(value) is None:
            raise ValueError("Confidence must be 0-1 or 0-100.")
        return value


class DetectionEventCreate(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str = "local-windows-agent"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    gps_accuracy_m: float | None = Field(default=None, ge=0)
    location_source: Literal["EXIF", "DEVICE", "USER_SUPPLIED", "GPS"] | None = None
    confidence: float
    bounding_box: BoundingBox | None = None
    severity_estimate: Severity | None = None
    annotated_image_reference: str | None = None

    @field_validator("confidence")
    @classmethod
    def confidence_is_valid(cls, value: float) -> float:
        if normalize_confidence(value) is None:
            raise ValueError("Confidence must be 0-1 or 0-100.")
        return value

    @model_validator(mode="after")
    def coordinates_are_a_pair(self) -> DetectionEventCreate:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude, or neither.")
        return self


class PotholePatch(BaseModel):
    severity: Severity | None = None
    verification_status: VerificationStatus | None = None
    repair_status: RepairStatus | None = None
    rejection_reason: str | None = Field(default=None, max_length=500)
    assigned_team: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def rejected_status_requires_reason(self) -> "PotholePatch":
        if self.verification_status == "rejected" and not (self.rejection_reason or "").strip():
            raise ValueError("A rejection reason is required.")
        if self.rejection_reason and self.verification_status != "rejected":
            raise ValueError("A rejection reason is only valid for a rejected report.")
        return self


class VerificationUpdate(BaseModel):
    status: VerificationStatus = "verified"
    rejection_reason: str | None = Field(default=None, max_length=500)
    notes: str | None = None

    @model_validator(mode="after")
    def rejected_status_requires_reason(self) -> "VerificationUpdate":
        if self.status == "rejected" and not (self.rejection_reason or "").strip():
            raise ValueError("A rejection reason is required.")
        if self.rejection_reason and self.status != "rejected":
            raise ValueError("A rejection reason is only valid for a rejected report.")
        return self


class RepairUpdate(BaseModel):
    status: RepairStatus = "not_started"
    notes: str | None = None
    assigned_team: str | None = None


class LocationVerification(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    administrator_id: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def finite_coordinates(self) -> "LocationVerification":
        if not valid_coordinate(self.latitude, self.longitude):
            raise ValueError("Select a valid latitude and longitude on the map.")
        return self


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def auth_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_auth_token(user_id: ObjectId, purpose: str, ttl_minutes: int) -> str:
    now = now_utc()
    auth_tokens_collection.update_many(
        {"user_id": user_id, "purpose": purpose, "consumed_at": {"$exists": False}},
        {"$set": {"consumed_at": now}},
    )
    token = secrets.token_urlsafe(32)
    auth_tokens_collection.insert_one(
        {
            "user_id": user_id,
            "purpose": purpose,
            "token_hash": auth_token_digest(token),
            "created_at": now,
            "expires_at": now + timedelta(minutes=ttl_minutes),
        }
    )
    return token


def consume_auth_token(token: str, purpose: str) -> dict[str, Any] | None:
    return auth_tokens_collection.find_one_and_update(
        {
            "token_hash": auth_token_digest(token),
            "purpose": purpose,
            "expires_at": {"$gt": now_utc()},
            "consumed_at": {"$exists": False},
        },
        {"$set": {"consumed_at": now_utc()}},
        return_document=ReturnDocument.BEFORE,
    )


def send_account_email(user: Mapping[str, Any], token: str, purpose: str) -> bool:
    config = email_configuration()
    if not config.get("configured"):
        return False
    email = str(user.get("email") or "")
    base_url = str(config["public_base_url"])
    if purpose == "email_verification":
        ttl_minutes = EMAIL_VERIFICATION_TTL_MINUTES
        route = "/verify-email"
        subject = "Verify your RoadWatch AI email"
        instruction = "Use this link to verify your email address."
    else:
        ttl_minutes = PASSWORD_RESET_TTL_MINUTES
        route = "/reset-password"
        subject = "Reset your RoadWatch AI password"
        instruction = "Use this link to set a new password."
    link = f"{base_url}{route}?token={token}"
    message = (
        f"Hello {user.get('full_name') or 'RoadWatch user'},\n\n"
        f"{instruction}\n\n{link}\n\n"
        f"This link expires in {ttl_minutes} minutes and can be used once. "
        "If you did not request this, you can ignore this message."
    )
    try:
        send_email(email, subject, message)
    except (OSError, RuntimeError, SMTPException):
        logger.exception("RoadWatch could not deliver an account email.")
        return False
    return True


def enforce_auth_rate_limit(
    scope: str,
    request: Request,
    maximum: int,
    window_seconds: int,
) -> None:
    secret = JWT_SECRET
    if not secret:
        raise HTTPException(status_code=503, detail="Authentication is not configured.")
    remote = request.client.host if request.client else "unknown"
    now = now_utc()
    bucket = int(now.timestamp()) // window_seconds
    material = f"{scope}:{remote}:{bucket}".encode("utf-8")
    key = hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()
    window_end = datetime.fromtimestamp((bucket + 1) * window_seconds, timezone.utc)
    try:
        result = auth_rate_limits_collection.find_one_and_update(
            {"_id": key},
            {
                "$inc": {"count": 1},
                "$setOnInsert": {"expires_at": window_end + timedelta(seconds=window_seconds)},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        result = auth_rate_limits_collection.find_one_and_update(
            {"_id": key},
            {"$inc": {"count": 1}},
            return_document=ReturnDocument.AFTER,
        )
    if not result:
        raise HTTPException(status_code=503, detail="Authentication rate limiting is temporarily unavailable.")
    if result and int(result.get("count", 0)) > maximum:
        retry_after = max(1, int((window_end - now).total_seconds()))
        raise HTTPException(
            status_code=429,
            detail=f"Too many {scope.replace('-', ' ')} attempts. Try again after the rate limit resets.",
            headers={"Retry-After": str(retry_after)},
        )


def make_auth_rate_limit(scope: str, maximum: int, window_seconds: int):
    def dependency(request: Request) -> None:
        enforce_auth_rate_limit(scope, request, maximum, window_seconds)

    dependency.__name__ = f"limit_{scope.replace('-', '_')}"
    return dependency


limit_login = make_auth_rate_limit("login", 10, 15 * 60)
limit_registration = make_auth_rate_limit("registration", 5, 60 * 60)
limit_initial_setup = make_auth_rate_limit("initial-administrator-setup", 5, 60 * 60)
limit_password_reset_request = make_auth_rate_limit("password-reset", 4, 15 * 60)
limit_password_reset_confirm = make_auth_rate_limit("password-reset-confirmation", 8, 15 * 60)
limit_email_verification = make_auth_rate_limit("email-verification", 10, 15 * 60)
limit_email_verification_resend = make_auth_rate_limit("verification-resend", 3, 60 * 60)


def canonical_role(value: Any) -> str:
    """Normalize stored role labels without granting privileges from token claims."""
    return str(value or "user").strip().casefold()


def is_readable_pothole_id(value: Any) -> bool:
    return isinstance(value, str) and POTHOLE_ID_PATTERN.fullmatch(value) is not None


def allocate_pothole_id() -> str:
    """Allocate a globally unique, monotonically increasing human-readable ID."""
    counter = sequences_collection.find_one_and_update(
        {"_id": POTHOLE_ID_COUNTER},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"PH-{int(counter['value']):03d}"


def insert_pothole_with_unique_id(record: dict[str, Any]) -> Any:
    """Insert a new record, advancing again if the unique index detects a collision."""
    for attempt in range(5):
        try:
            return potholes_collection.insert_one(record)
        except DuplicateKeyError:
            if attempt == 4:
                raise
            record["pothole_id"] = allocate_pothole_id()
    raise RuntimeError("Could not allocate a unique pothole ID.")


def migrate_pothole_ids() -> None:
    """Backfill readable IDs and carry unambiguous links over to detection events."""
    records = list(
        potholes_collection.find({}, {"_id": 1, "pothole_id": 1}).sort("_id", ASCENDING)
    )
    maximum_sequence = max(
        (
            int(match.group(1))
            for record in records
            if (match := POTHOLE_ID_PATTERN.fullmatch(str(record.get("pothole_id", ""))))
        ),
        default=0,
    )

    try:
        sequences_collection.update_one(
            {"_id": POTHOLE_ID_COUNTER},
            {"$max": {"value": maximum_sequence}},
            upsert=True,
        )
    except DuplicateKeyError:
        # Another application worker initialized the counter concurrently.
        sequences_collection.update_one(
            {"_id": POTHOLE_ID_COUNTER},
            {"$max": {"value": maximum_sequence}},
        )

    assigned_ids: set[str] = set()
    migrated_old_ids: set[str] = set()
    for record in records:
        old_id = record.get("pothole_id")
        old_id_repr = repr(old_id) if "pothole_id" in record else "<missing>"
        readable_id = old_id.upper() if is_readable_pothole_id(old_id) else None
        if readable_id and readable_id not in assigned_ids:
            assigned_ids.add(readable_id)
            if old_id != readable_id:
                normalized = potholes_collection.find_one_and_update(
                    {"_id": record["_id"], "pothole_id": old_id},
                    {"$set": {"pothole_id": readable_id}},
                    return_document=ReturnDocument.AFTER,
                )
                if normalized is not None and old_id_repr not in migrated_old_ids:
                    detections_collection.update_many(
                        {"pothole_id": old_id},
                        {"$set": {"pothole_id": readable_id}},
                    )
            migrated_old_ids.add(old_id_repr)
            continue

        new_id = allocate_pothole_id()
        while new_id in assigned_ids:
            new_id = allocate_pothole_id()
        old_id_filter = (
            {"pothole_id": old_id}
            if "pothole_id" in record
            else {"pothole_id": {"$exists": False}}
        )
        updated = potholes_collection.find_one_and_update(
            {"_id": record["_id"], **old_id_filter},
            {"$set": {"pothole_id": new_id}},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            current = potholes_collection.find_one({"_id": record["_id"]})
            current_id = current.get("pothole_id") if current else None
            if is_readable_pothole_id(current_id):
                assigned_ids.add(current_id.upper())
            continue

        assigned_ids.add(new_id)
        if old_id is not None and old_id_repr not in migrated_old_ids:
            detections_collection.update_many(
                {"pothole_id": old_id},
                {"$set": {"pothole_id": new_id}},
            )
            migrated_old_ids.add(old_id_repr)


def dump_model(model: BaseModel) -> dict[str, Any]:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def normalize_confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= number <= 1:
        return number
    if 1 < number <= 100:
        return number / 100
    return None


def confidence_percent(value: Any) -> float | None:
    normalized = normalize_confidence(value)
    return round(normalized * 100, 2) if normalized is not None else None


def valid_coordinate(latitude: Any, longitude: Any) -> bool:
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return False
    return isfinite(lat) and isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180


def gps_accuracy_is_low(value: Any, threshold_m: float = GPS_ACCURACY_THRESHOLD_M) -> bool:
    if value is None:
        return False
    try:
        accuracy = float(value)
    except (TypeError, ValueError):
        return True
    return not isfinite(accuracy) or accuracy < 0 or accuracy > threshold_m


def iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def mongo_to_json(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return iso_or_none(value)
    if isinstance(value, DatetimeMS):
        return {"$date": {"$numberLong": str(int(value))}}
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal128):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, Timestamp):
        return {"$timestamp": {"t": value.time, "i": value.inc}}
    if isinstance(value, Regex):
        return {"$regex": value.pattern, "$options": value.flags}
    if isinstance(value, DBRef):
        return mongo_to_json(value.as_doc())
    if isinstance(value, Code):
        code: dict[str, Any] = {"$code": str(value)}
        if value.scope is not None:
            code["$scope"] = mongo_to_json(value.scope)
        return code
    if isinstance(value, MinKey):
        return {"$minKey": 1}
    if isinstance(value, MaxKey):
        return {"$maxKey": 1}
    if isinstance(value, Mapping):
        return {str(key): mongo_to_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [mongo_to_json(item) for item in value]
    if isinstance(value, float) and not isfinite(value):
        return None
    if value is None or isinstance(value, (str, bool, int, float)):
        return value

    encoded = jsonable_encoder(value)
    if encoded is value:
        raise TypeError(f"Cannot serialize value of type {type(value).__name__} to JSON.")
    return mongo_to_json(encoded)


def create_notification(
    user_id: ObjectId | str | None,
    notification_type: str,
    action: str,
    message: str,
    *,
    administrator: dict[str, Any] | None = None,
    pothole_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    if user_id is None:
        return
    owner_id = user_id if isinstance(user_id, ObjectId) else ObjectId(str(user_id))
    administrator_name = None
    administrator_id = None
    if administrator:
        administrator_id = str(administrator.get("_id") or "")
        administrator_name = (
            administrator.get("full_name")
            or "Administrator"
        )
    notification = {
        "user_id": owner_id,
        "type": notification_type,
        "action": action,
        "pothole_id": pothole_id,
        "message": message,
        "administrator_id": administrator_id,
        "administrator_name": administrator_name,
        "details": mongo_to_json(details or {}),
        "created_at": now_utc(),
        "is_read": False,
        "read_at": None,
    }
    notifications_collection.insert_one(notification)


def serialize_notification(document: dict[str, Any]) -> dict[str, Any]:
    notification = dict(document)
    for field in ("_id", "user_id", "administrator_id"):
        if notification.get(field) is not None:
            notification[field] = str(notification[field])
    notification["created_at"] = iso_or_none(notification.get("created_at"))
    notification["read_at"] = iso_or_none(notification.get("read_at"))
    notification["details"] = mongo_to_json(notification.get("details") or {})
    return mongo_to_json(notification)


def mask_email(value: Any) -> str | None:
    if not isinstance(value, str) or "@" not in value:
        return None
    local, domain = value.split("@", 1)
    if not local or not domain:
        return None
    return f"{local[:1]}***@{domain}"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * radius * asin(sqrt(a))


def estimate_severity(confidence: Any, bounding_box: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    normalized = normalize_confidence(confidence) or 0
    relative_area = None
    if bounding_box:
        width = bounding_box.get("width")
        height = bounding_box.get("height")
        if isinstance(width, (int, float)) and isinstance(height, (int, float)) and 0 < width <= 1 and 0 < height <= 1:
            relative_area = width * height

    score = normalized * 55 + (min(relative_area * 220, 35) if relative_area is not None else 10)
    if score >= 80:
        severity = "critical"
    elif score >= 62:
        severity = "high"
    elif score >= 40:
        severity = "medium"
    else:
        severity = "low"
    return severity, {
        "score": round(score, 1),
        "factors": [
            "YOLO confidence contribution",
            "Relative bounding-box size when supplied",
            "Estimated from RGB imagery; physical depth is not measured",
        ],
    }


def canonical_severity(value: Any, confidence: Any = None, bounding_box: dict[str, Any] | None = None) -> str:
    """Return a chartable severity, deriving one from real detection measurements if absent."""
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"critical", "high", "medium", "low"}:
            return normalized
    return estimate_severity(confidence, bounding_box)[0]


def canonical_verification_status(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().casefold().replace(" ", "_").replace("-", "_")
    if normalized in {"pending", "pending_verification", "awaiting_verification", "unverified"}:
        return "pending_verification"
    if normalized in {"verified", "rejected"}:
        return normalized
    return "unknown"


def canonical_repair_status(value: Any) -> str:
    if not isinstance(value, str):
        return "not_started"
    normalized = value.strip().casefold().replace(" ", "_").replace("-", "_")
    if normalized in {
        "under_maintenance",
        "inspection_scheduled",
        "repair_scheduled",
        "repair_in_progress",
        "awaiting_repair_verification",
        "reopened",
    }:
        return "under_maintenance"
    if normalized in {"repaired", "verified", "marked_repaired", "repair_verified"}:
        return "repaired"
    return "not_started"


def priority_for(record: dict[str, Any]) -> tuple[int, str, list[str]]:
    severity_weights = {"low": 20, "medium": 40, "high": 65, "critical": 85}
    severity = record.get("severity") or estimate_severity(record.get("confidence"))[0]
    score = severity_weights.get(severity, 30)
    reasons = [f"{severity.title()} estimated severity"]
    confidence = normalize_confidence(record.get("confidence"))
    if confidence is not None:
        score += int(confidence * 10)
        reasons.append(f"{round(confidence * 100, 1)}% normalized confidence")
    observations = int(record.get("observation_count") or 1)
    if observations > 1:
        score += min(observations * 3, 15)
        reasons.append(f"{observations} linked observations")
    repair_status = record.get("repair_status")
    if repair_status not in {None, "unknown", "repair_verified", "marked_repaired"}:
        score += 8
        reasons.append("Issue remains unresolved")
    verification_status = record.get("verification_status")
    if verification_status not in {None, "unknown", "verified"}:
        reasons.append("Verification is pending")
    score = max(0, min(100, score))
    category = "critical" if score >= 80 else "high" if score >= 60 else "medium" if score >= 35 else "low"
    return score, category, reasons


def serialize_pothole(doc: dict[str, Any]) -> dict[str, Any]:
    item = dict(doc)
    if "_id" in item:
        item["_id"] = str(item["_id"])
    if isinstance(item.get("user_id"), ObjectId):
        item["user_id"] = str(item["user_id"])
    if item.get("latitude") is None and item.get("lat") is not None:
        item["latitude"] = item["lat"]
    if item.get("longitude") is None and item.get("lon") is not None:
        item["longitude"] = item["lon"]
    location = item.get("location")
    if isinstance(location, dict) and location.get("type") == "Point":
        coordinates = location.get("coordinates")
        if isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
            if item.get("longitude") is None:
                item["longitude"] = coordinates[0]
            if item.get("latitude") is None:
                item["latitude"] = coordinates[1]
    for field in ("latitude", "longitude"):
        try:
            value = item.get(field)
            if value is not None and isfinite(float(value)):
                item[field] = float(value)
        except (TypeError, ValueError):
            pass
    item.setdefault("pothole_id", None)
    stored_severity = item.get("severity")
    item["severity_recorded"] = isinstance(stored_severity, str) and stored_severity.strip().casefold() in {"critical", "high", "medium", "low"}
    item["severity"] = canonical_severity(stored_severity, item.get("confidence"), item.get("bounding_box"))
    item["severity_estimated"] = not item["severity_recorded"]
    item["confidence_normalized"] = normalize_confidence(item.get("confidence"))
    item["confidence_percent"] = confidence_percent(item.get("confidence"))
    source = item.get("source") or item.get("source_id")
    item["source"] = source or "historical/unknown"
    item["record_origin"] = "source_recorded" if source else "historical_unknown"
    item["verification_status_recorded"] = item.get("verification_status") is not None
    item["repair_status_recorded"] = item.get("repair_status") is not None
    item["verification_status"] = (
        canonical_verification_status(item.get("verification_status"))
        if item["verification_status_recorded"]
        else "unknown"
    )
    item["repair_status"] = (
        canonical_repair_status(item.get("repair_status"))
        if item["repair_status_recorded"]
        else "unknown"
    )
    for field in (
        "verified_by",
        "rejected_by",
        "repair_updated_by",
        "location_verified_by",
    ):
        if isinstance(item.get(field), ObjectId):
            item[field] = str(item[field])
    item.setdefault("observation_count", 1)
    score, category, reasons = priority_for(item)
    item.setdefault("priority_score", score)
    item.setdefault("priority_category", category)
    item.setdefault("priority_reasons", reasons)
    for key in (
        "first_detected_at",
        "last_seen_at",
        "created_at",
        "updated_at",
        "timestamp",
        "verified_at",
        "rejected_at",
        "repair_updated_at",
        "location_verified_at",
    ):
        if key in item:
            item[key] = iso_or_none(item[key])
    item["audit_history"] = mongo_to_json(item.get("audit_history") or [])
    item["has_valid_coordinates"] = valid_coordinate(item.get("latitude"), item.get("longitude"))
    item.setdefault("location_verification_status", location_status(item))
    item.setdefault("location_verification_reason", location_reason(item))
    return mongo_to_json(item)


def serialize_event(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    item = dict(doc)
    mongo_id = item.get("_id")
    if "_id" in item:
        item["_id"] = str(item["_id"])
    if isinstance(item.get("user_id"), ObjectId):
        item["user_id"] = str(item["user_id"])
    if mongo_id is not None:
        ownership = {"user_id": doc["user_id"]} if doc.get("user_id") is not None else {"user_id": {"$exists": False}}
        item["display_id"] = detections_collection.count_documents({"_id": {"$lte": mongo_id}, **ownership})
    for key in ("timestamp", "created_at"):
        if key in item:
            item[key] = iso_or_none(item[key])
    item["confidence_normalized"] = normalize_confidence(item.get("confidence"))
    item["confidence_percent"] = confidence_percent(item.get("confidence"))
    item.setdefault("location_verification_status", location_status(item))
    item.setdefault("location_verification_reason", location_reason(item))
    return mongo_to_json(item)


def location_status(record: dict[str, Any]) -> str:
    if record.get("location_verification_method") == "MANUAL" and valid_coordinate(record.get("latitude"), record.get("longitude")):
        return "VERIFIED"
    if record.get("location_verification_method") == "USER_SUPPLIED" or record.get("location_source") == "USER_SUPPLIED":
        return "UNVERIFIED"
    if not valid_coordinate(record.get("latitude"), record.get("longitude")):
        return "UNVERIFIED"
    accuracy = record.get("gps_accuracy_m")
    if accuracy is None:
        return "UNVERIFIED"
    try:
        if not isfinite(float(accuracy)):
            return "UNVERIFIED"
    except (TypeError, ValueError):
        return "UNVERIFIED"
    return "VERIFIED" if 0 <= float(accuracy) <= GPS_ACCURACY_THRESHOLD_M else "UNVERIFIED"


def location_reason(record: dict[str, Any]) -> str | None:
    if location_status(record) == "VERIFIED":
        return None
    if record.get("location_verification_method") == "USER_SUPPLIED" or record.get("location_source") == "USER_SUPPLIED":
        return "User-selected coordinates require Admin verification"
    if record.get("latitude") is None or record.get("longitude") is None:
        return "GPS coordinates unavailable"
    if not valid_coordinate(record.get("latitude"), record.get("longitude")):
        return "Invalid GPS coordinates"
    if record.get("gps_accuracy_m") is None:
        return "GPS accuracy unavailable"
    return "GPS accuracy exceeds the allowed threshold"


def exif_location(image_bytes: bytes) -> tuple[float | None, float | None, datetime | None, float | None]:
    try:
        from PIL import Image
        from PIL.ExifTags import GPSTAGS
        import io

        with Image.open(io.BytesIO(image_bytes)) as image:
            raw = image.getexif()
            gps = raw.get_ifd(34853) if raw else {}
            decoded = {GPSTAGS.get(key, key): value for key, value in gps.items()}
            def decimal(value: Any) -> float:
                parts = [float(item) for item in value]
                return parts[0] + parts[1] / 60 + parts[2] / 3600
            lat = decimal(decoded["GPSLatitude"]) if "GPSLatitude" in decoded else None
            lon = decimal(decoded["GPSLongitude"]) if "GPSLongitude" in decoded else None
            if lat is not None and decoded.get("GPSLatitudeRef") == "S": lat = -lat
            if lon is not None and decoded.get("GPSLongitudeRef") == "W": lon = -lon
            captured = None
            try:
                date_text = decoded.get("GPSDateStamp")
                time_value = decoded.get("GPSTimeStamp")
                if date_text and time_value:
                    captured = datetime.strptime(f"{date_text} {int(float(time_value[0])):02}:{int(float(time_value[1])):02}:{int(float(time_value[2])):02}", "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if captured is None:
                    date_text = raw.get(36867) or raw.get(306)
                    if date_text:
                        captured = datetime.strptime(str(date_text), "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except (TypeError, ValueError, IndexError, OverflowError):
                captured = None
            accuracy = decoded.get("GPSHPositioningError")
            try: accuracy = float(accuracy) if accuracy is not None else None
            except (TypeError, ValueError): accuracy = None
            if accuracy is not None and (not isfinite(accuracy) or accuracy < 0): accuracy = None
            return lat, lon, captured, accuracy
    except Exception:
        return None, None, None, None


def build_filters(
    severity: str | None,
    verification_status: str | None,
    repair_status: str | None,
    source: str | None,
    search: str | None,
    location: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    alternatives: list[dict[str, Any]] = []
    if severity:
        query["severity"] = severity
    if verification_status:
        normalized_verification = canonical_verification_status(verification_status)
        query["verification_status"] = (
            {"$in": ["pending_verification", "awaiting_verification", "unverified"]}
            if normalized_verification == "pending_verification"
            else normalized_verification
        )
    if repair_status:
        normalized_repair = canonical_repair_status(repair_status)
        repair_aliases = {
            "not_started": ["not_started", "reported", "awaiting_verification"],
            "under_maintenance": [
                "under_maintenance",
                "inspection_scheduled",
                "repair_scheduled",
                "repair_in_progress",
                "awaiting_repair_verification",
                "reopened",
            ],
            "repaired": ["repaired", "verified", "marked_repaired", "repair_verified"],
        }
        query["repair_status"] = {"$in": repair_aliases[normalized_repair]}
    if source:
        query["source"] = source
    if search:
        escaped_search = re.escape(search.strip())
        choices: list[dict[str, Any]] = [
            {"pothole_id": {"$regex": f"^{escaped_search}$", "$options": "i"}},
            {"source": {"$regex": escaped_search, "$options": "i"}},
            {"notes": {"$regex": escaped_search, "$options": "i"}},
            {"road_name": {"$regex": escaped_search, "$options": "i"}},
            {"location": {"$regex": escaped_search, "$options": "i"}},
        ]
        if search.isdigit():
            choices.append({"pothole_id": int(search)})
        alternatives.append({"$or": choices})
    if location:
        escaped_location = re.escape(location.strip())
        location_choices = [
            {field: {"$regex": escaped_location, "$options": "i"}}
            for field in ("region", "district", "city", "state", "address", "location_name", "road_name", "location")
        ]
        alternatives.append({"$or": location_choices})
    if len(alternatives) == 1:
        query.update(alternatives[0])
    elif alternatives:
        query["$and"] = alternatives
    return query


def created_at_date_filter(
    date_from: date | None,
    date_to: date | None,
) -> dict[str, Any]:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="Start date must be on or before end date.")

    date_range: dict[str, datetime] = {}
    if date_from:
        date_range["$gte"] = datetime.combine(date_from, time.min).replace(tzinfo=timezone.utc)
    if date_to:
        try:
            end_exclusive = date_to + timedelta(days=1)
        except OverflowError as error:
            raise HTTPException(status_code=422, detail="End date is outside the supported range.") from error
        date_range["$lt"] = datetime.combine(end_exclusive, time.min).replace(tzinfo=timezone.utc)
    return {"created_at": date_range} if date_range else {}


def count_report_statuses(
    query: dict[str, Any],
    field: str,
    values: list[str],
) -> int:
    status_filter = query.get(field)
    status_query = {key: value for key, value in query.items() if key != field}
    if status_filter is None:
        status_query[field] = {"$in": values}
    else:
        status_query["$and"] = [
            {field: status_filter},
            {field: {"$in": values}},
        ]
    return potholes_collection.count_documents(status_query)


def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> dict[str, Any]:
    if not JWT_SECRET:
        raise HTTPException(status_code=503, detail="Authentication is not configured.")
    token = credentials.credentials if credentials else (request.cookies.get("roadwatch_access_token") if request else None)
    if token is None:
        raise HTTPException(status_code=401, detail="Authentication required.", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = ObjectId(payload.get("sub", ""))
    except (JWTError, ValueError, InvalidId):
        raise HTTPException(status_code=401, detail="Invalid or expired access token.", headers={"WWW-Authenticate": "Bearer"})
    user = users_collection.find_one({"_id": user_id})
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=401, detail="Account is inactive or unavailable.")
    if not user.get("email_verified", True):
        raise HTTPException(status_code=403, detail="Email verification is required before using this account.")
    if int(payload.get("sv", 0)) != int(user.get("session_version", 0)):
        raise HTTPException(status_code=401, detail="Session has expired. Sign in again.")
    user["role"] = canonical_role(user.get("role"))
    return user

@app.get("/uploads/{filename}")
def serve_owned_upload(filename: str, user: dict[str, Any] = Depends(get_current_user)) -> FileResponse:
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=404, detail="Upload not found.")
    reference = f"/uploads/{safe_name}"
    query: dict[str, Any] = {"$or": [{"image_reference": reference}, {"annotated_image_reference": reference}]}
    if canonical_role(user.get("role")) not in {"admin", "administrator"}: query["user_id"] = user["_id"]
    found = detections_collection.find_one(query) or potholes_collection.find_one(query)
    path = UPLOADS_DIR / safe_name
    if not found or not path.is_file():
        raise HTTPException(status_code=404, detail="Upload not found.")
    return FileResponse(path)

def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if canonical_role(user.get("role")) not in {"admin", "administrator"}:
        raise HTTPException(status_code=403, detail="Admin or Administrator access required.")
    return user

def require_administrator(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if canonical_role(user.get("role")) != "administrator":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user

def owner_filter(user: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(user, dict):
        return {}
    return {} if canonical_role(user.get("role")) in {"admin", "administrator"} else {"user_id": user["_id"]}


def add_reporter_details(records: list[dict[str, Any]], user: dict[str, Any]) -> None:
    if not isinstance(user, dict) or canonical_role(user.get("role")) not in {"admin", "administrator"}:
        return
    for record in records:
        owner_id = record.get("user_id")
        if not owner_id:
            record["reported_by_name"] = None
            record["reported_by_id"] = None
            record["reported_by_display"] = "Legacy / unassigned"
            continue
        owner_id = str(owner_id)
        owner = (
            users_collection.find_one(
                {"_id": ObjectId(owner_id)},
                {"full_name": 1},
            )
            if ObjectId.is_valid(owner_id)
            else None
        )
        name = owner.get("full_name") if owner else None
        record["reported_by_name"] = name
        record["reported_by_id"] = owner_id
        record["reported_by_display"] = name or "Registered account"


def add_status_actor_details(records: list[dict[str, Any]]) -> None:
    identities: dict[str, str] = {}

    def account_display(account_id: Any) -> str:
        if not account_id:
            return "Unavailable"
        normalized_id = str(account_id)
        if normalized_id not in identities:
            account = (
                users_collection.find_one(
                    {"_id": ObjectId(normalized_id)},
                    {"full_name": 1},
                )
                if ObjectId.is_valid(normalized_id)
                else None
            )
            identities[normalized_id] = account.get("full_name") or "Registered account" if account else "Unavailable"
        return identities[normalized_id]

    for record in records:
        record["verified_by_display"] = account_display(record.get("verified_by"))
        record["rejected_by_display"] = account_display(record.get("rejected_by"))
        record["repair_updated_by_display"] = account_display(record.get("repair_updated_by"))
        record["location_verified_by_display"] = (
            record.get("location_verified_by_name")
            or account_display(record.get("location_verified_by"))
        )


def validate_password_policy(value: str, minimum_length: int = 8) -> str:
    if len(value) < minimum_length:
        raise ValueError(f"Password must contain at least {minimum_length} characters.")
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password must be no more than 72 UTF-8 bytes.")
    missing = []
    if not any(character.isupper() for character in value):
        missing.append("an uppercase letter")
    if not any(character.islower() for character in value):
        missing.append("a lowercase letter")
    if not any(character.isdigit() for character in value):
        missing.append("a number")
    if not any(not character.isalnum() for character in value):
        missing.append("a special character")
    if missing:
        raise ValueError("Password must include " + ", ".join(missing) + ".")
    return value


def normalize_auth_email(value: str) -> str:
    email = value.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Enter a valid email address.")
    return email


class Registration(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    requested_role: Literal["user", "admin"] = "user"

    @field_validator("password")
    @classmethod
    def bcrypt_length(cls, value: str) -> str:
        return validate_password_policy(value)

    @field_validator("full_name")
    @classmethod
    def normalized_full_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Full name is required.")
        return name

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        return normalize_auth_email(value)

class InitialAdminSetup(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(min_length=12, max_length=128)
    setup_secret: SecretStr = Field(min_length=1, max_length=512)

    @field_validator("full_name")
    @classmethod
    def normalized_full_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Full name is required.")
        return name

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ValueError("Enter a valid email address.")
        return email

    @field_validator("password", "confirm_password")
    @classmethod
    def bcrypt_length(cls, value: str) -> str:
        return validate_password_policy(value, minimum_length=12)

    @model_validator(mode="after")
    def passwords_match(self) -> "InitialAdminSetup":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self

class Login(BaseModel):
    email: str
    password: str


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        return normalize_auth_email(value)


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(min_length=12, max_length=128)

    @field_validator("password", "confirm_password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        return validate_password_policy(value, minimum_length=12)

    @model_validator(mode="after")
    def passwords_match(self) -> "PasswordResetConfirm":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class EmailVerificationConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class ReportScheduleCreate(BaseModel):
    frequency: Literal["daily", "weekly", "monthly"]
    format: Literal["pdf", "xlsx"]
    search: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=120)
    severity: Severity | None = None
    verification_status: VerificationStatus | None = None
    repair_status: RepairStatus | None = None
    date_from: date | None = None
    date_to: date | None = None

    @field_validator("search", "location")
    @classmethod
    def clean_filter_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def valid_date_range(self) -> "ReportScheduleCreate":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Start date must be on or before end date.")
        return self


class ReportScheduleAction(BaseModel):
    action: Literal["pause", "resume"]

def initial_admin_setup_available() -> bool:
    secret = INITIAL_ADMIN_SETUP_SECRET
    if not secret or len(secret.encode("utf-8")) < 32:
        return False
    if users_collection.find_one({"_id": INITIAL_ADMIN_SETUP_ID}):
        return False
    if users_collection.find_one({"initial_administrator_setup_completed": True}):
        return False
    return users_collection.find_one(INITIAL_ADMINISTRATOR_ROLE_QUERY) is None

@app.get("/api/auth/admin-setup/status")
def admin_setup_status() -> dict[str, bool]:
    return {"available": initial_admin_setup_available()}

@app.post("/api/auth/admin-setup", status_code=201, dependencies=[Depends(limit_initial_setup)])
def setup_initial_admin(payload: InitialAdminSetup) -> dict[str, Any]:
    configured_secret = INITIAL_ADMIN_SETUP_SECRET
    if not configured_secret or len(configured_secret.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="Initial Administrator setup is not configured.")
    if not hmac.compare_digest(
        payload.setup_secret.get_secret_value().encode("utf-8"),
        configured_secret.encode("utf-8"),
    ):
        raise HTTPException(status_code=403, detail="Invalid Administrator setup secret.")
    if not initial_admin_setup_available():
        raise HTTPException(status_code=409, detail="Initial Administrator setup is unavailable or already completed.")

    email = payload.email
    if users_collection.find_one({
        "email": {"$regex": f"^{re.escape(email)}$", "$options": "i"},
    }):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    # A stable _id uses MongoDB's built-in unique index to serialize concurrent setup requests.
    if not initial_admin_setup_available():
        raise HTTPException(status_code=409, detail="Initial Administrator setup is unavailable or already completed.")
    now = now_utc()
    document = {
        "_id": INITIAL_ADMIN_SETUP_ID,
        "full_name": payload.full_name,
        "email": email,
        "password_hash": bcrypt.hashpw(payload.password.encode("utf-8"), bcrypt.gensalt()).decode(),
        "role": "administrator",
        "requested_role": "administrator",
        "admin_request_status": "not_requested",
        "admin_request_notice": None,
        "initial_administrator_setup_completed": True,
        "is_active": True,
        "email_verified": True,
        "session_version": 0,
        "created_at": now,
        "last_login": None,
    }
    try:
        users_collection.insert_one(document)
    except DuplicateKeyError as error:
        if users_collection.find_one({
            "email": {"$regex": f"^{re.escape(email)}$", "$options": "i"},
        }):
            raise HTTPException(status_code=409, detail="An account with this email already exists.") from error
        raise HTTPException(
            status_code=409,
            detail="Initial Administrator setup is unavailable or already completed.",
        ) from error

    return {
        "id": str(INITIAL_ADMIN_SETUP_ID),
        "full_name": document["full_name"],
        "email": email,
        "role": "administrator",
    }

@app.get("/api/auth/email-service/status")
def email_service_status() -> dict[str, bool]:
    return {"configured": bool(email_configuration().get("configured"))}


@app.post("/api/auth/register", status_code=201, dependencies=[Depends(limit_registration)])
def register(payload: Registration) -> dict[str, Any]:
    email = payload.email.strip().lower()
    admin_request_status = "pending" if payload.requested_role == "admin" else "not_requested"
    doc = {"full_name": payload.full_name.strip(), "email": email,
           "password_hash": bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode(),
           "role": "user", "requested_role": payload.requested_role,
           "admin_request_status": admin_request_status, "admin_request_notice": None,
           "is_active": True, "email_verified": False, "session_version": 0,
           "created_at": now_utc(), "last_login": None}
    try:
        result = users_collection.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    email_configured = bool(email_configuration().get("configured"))
    verification_email_sent = False
    if email_configured:
        token = issue_auth_token(result.inserted_id, "email_verification", EMAIL_VERIFICATION_TTL_MINUTES)
        verification_email_sent = send_account_email(doc, token, "email_verification")
    return {"id": str(result.inserted_id), "full_name": doc["full_name"], "email": email,
            "role": "user", "requested_role": payload.requested_role,
            "admin_request_status": admin_request_status,
            "email_verified": False,
            "email_delivery_configured": email_configured,
            "verification_email_sent": verification_email_sent}

@app.post("/api/auth/login", dependencies=[Depends(limit_login)])
def login(payload: Login, response: Response) -> dict[str, Any]:
    user = users_collection.find_one({"email": payload.email.strip().lower()})
    if not user or not bcrypt.checkpw(payload.password.encode(), user.get("password_hash", "").encode()):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="This account has been deactivated.")
    if not user.get("email_verified", True):
        raise HTTPException(
            status_code=403,
            detail="Email verification is required. Use the resend verification option before signing in.",
        )
    from datetime import timedelta
    role = canonical_role(user.get("role"))
    token = jwt.encode({
        "sub": str(user["_id"]),
        "role": role,
        "sv": int(user.get("session_version", 0)),
        "exp": now_utc() + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }, JWT_SECRET, algorithm=JWT_ALGORITHM)
    response.set_cookie("roadwatch_access_token", token, httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=JWT_EXPIRE_MINUTES * 60, path="/")
    users_collection.update_one({"_id": user["_id"]}, {"$set": {"last_login": now_utc()}})
    return {"user": {
        "id": str(user["_id"]), "full_name": user["full_name"], "email": user["email"],
        "role": role, "admin_request_status": user.get("admin_request_status", "not_requested"),
        "admin_request_notice": user.get("admin_request_notice"),
        "created_at": iso_or_none(user.get("created_at")),
        "email_verified": bool(user.get("email_verified", True)),
    }}

@app.get("/api/auth/me")
def auth_me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"id": str(user["_id"]), "full_name": user["full_name"], "email": user["email"],
            "role": canonical_role(user.get("role")),
            "admin_request_status": user.get("admin_request_status", "not_requested"),
            "admin_request_notice": user.get("admin_request_notice"),
            "created_at": iso_or_none(user.get("created_at")),
            "email_verified": bool(user.get("email_verified", True))}


@app.post("/api/auth/email-verification/confirm", dependencies=[Depends(limit_email_verification)])
def confirm_email_verification(payload: EmailVerificationConfirm) -> dict[str, Any]:
    token_record = consume_auth_token(payload.token, "email_verification")
    if not token_record:
        raise HTTPException(status_code=400, detail="Verification link is invalid, expired, or already used.")
    result = users_collection.update_one(
        {"_id": token_record["user_id"], "email_verified": {"$ne": True}},
        {"$set": {"email_verified": True, "email_verified_at": now_utc()}},
    )
    if result.matched_count == 0:
        account = users_collection.find_one({"_id": token_record["user_id"]}, {"email_verified": 1})
        if not account or not account.get("email_verified"):
            raise HTTPException(status_code=404, detail="Account is unavailable.")
    auth_tokens_collection.update_many(
        {
            "user_id": token_record["user_id"],
            "purpose": "email_verification",
            "consumed_at": {"$exists": False},
        },
        {"$set": {"consumed_at": now_utc()}},
    )
    return {"email_verified": True, "message": "Email verified. You can now sign in."}


@app.post("/api/auth/email-verification/resend", dependencies=[Depends(limit_email_verification_resend)])
def resend_email_verification(payload: PasswordResetRequest) -> dict[str, Any]:
    configured = bool(email_configuration().get("configured"))
    if configured:
        account = users_collection.find_one({"email": payload.email, "email_verified": {"$ne": True}})
        if account:
            token = issue_auth_token(
                account["_id"],
                "email_verification",
                EMAIL_VERIFICATION_TTL_MINUTES,
            )
            send_account_email(account, token, "email_verification")
    return {
        "message": "If an unverified account exists for that address, a verification email will be sent when email delivery is configured.",
        "email_delivery_configured": configured,
    }


@app.post("/api/auth/password-reset/request", dependencies=[Depends(limit_password_reset_request)])
def request_password_reset(payload: PasswordResetRequest) -> dict[str, Any]:
    configured = bool(email_configuration().get("configured"))
    if configured:
        account = users_collection.find_one({"email": payload.email, "is_active": {"$ne": False}})
        if account:
            token = issue_auth_token(account["_id"], "password_reset", PASSWORD_RESET_TTL_MINUTES)
            send_account_email(account, token, "password_reset")
    return {
        "message": "If an eligible account exists for that address, password reset instructions will be sent when email delivery is configured.",
        "email_delivery_configured": configured,
    }


@app.post("/api/auth/password-reset/confirm", dependencies=[Depends(limit_password_reset_confirm)])
def confirm_password_reset(payload: PasswordResetConfirm) -> dict[str, str]:
    token_record = consume_auth_token(payload.token, "password_reset")
    if not token_record:
        raise HTTPException(status_code=400, detail="Password reset link is invalid, expired, or already used.")
    result = users_collection.update_one(
        {"_id": token_record["user_id"], "is_active": {"$ne": False}},
        {
            "$set": {"password_hash": bcrypt.hashpw(payload.password.encode("utf-8"), bcrypt.gensalt()).decode()},
            "$inc": {"session_version": 1},
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=400, detail="Password reset link is invalid, expired, or already used.")
    auth_tokens_collection.update_many(
        {
            "user_id": token_record["user_id"],
            "purpose": "password_reset",
            "consumed_at": {"$exists": False},
        },
        {"$set": {"consumed_at": now_utc()}},
    )
    return {"message": "Password updated. Sign in with your new password."}

@app.post("/api/auth/admin-request/acknowledge")
def acknowledge_admin_request_notice(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, bool]:
    users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"admin_request_notice": None, "admin_request_notice_acknowledged_at": now_utc()}},
    )
    return {"acknowledged": True}

@app.post("/api/auth/logout")
def auth_logout(response: Response, _: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    response.delete_cookie("roadwatch_access_token", path="/")
    return {"message": "Logged out."}


@app.get("/api/notifications")
def list_notifications(
    limit: int = Query(50, ge=1, le=100),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    query = {"user_id": user["_id"]}
    documents = notifications_collection.find(query).sort("created_at", DESCENDING).limit(limit)
    return {
        "notifications": [serialize_notification(document) for document in documents],
        "total": notifications_collection.count_documents(query),
        "unread_count": notifications_collection.count_documents({**query, "is_read": False}),
    }


@app.post("/api/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    if not ObjectId.is_valid(notification_id):
        raise HTTPException(status_code=404, detail="Notification not found.")
    read_at = now_utc()
    result = notifications_collection.update_one(
        {"_id": ObjectId(notification_id), "user_id": user["_id"]},
        {"$set": {"is_read": True, "read_at": read_at}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {
        "id": notification_id,
        "is_read": True,
        "read_at": iso_or_none(read_at),
        "unread_count": notifications_collection.count_documents(
            {"user_id": user["_id"], "is_read": False}
        ),
    }


@app.post("/api/notifications/read-all")
def mark_all_notifications_read(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    read_at = now_utc()
    notifications_collection.update_many(
        {"user_id": user["_id"], "is_read": False},
        {"$set": {"is_read": True, "read_at": read_at}},
    )
    return {"read_at": iso_or_none(read_at), "unread_count": 0}


def require_ingest(x_roadwatch_ingest_key: str | None = Header(default=None)) -> None:
    if INGEST_API_KEY and x_roadwatch_ingest_key != INGEST_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid ingestion API key.")


def ensure_indexes() -> None:
    migrate_pothole_ids()
    existing_pothole_id_index = next(
        (
            index
            for index in potholes_collection.list_indexes()
            if dict(index.get("key", {})) == {"pothole_id": ASCENDING}
        ),
        None,
    )
    if existing_pothole_id_index and not existing_pothole_id_index.get("unique"):
        potholes_collection.drop_index(existing_pothole_id_index["name"])
    potholes_collection.create_index(
        [("pothole_id", ASCENDING)], unique=True, sparse=True
    )
    try:
        potholes_collection.create_index([("severity", ASCENDING), ("repair_status", ASCENDING)])
        potholes_collection.create_index([("verification_status", ASCENDING)])
        potholes_collection.create_index([("last_seen_at", DESCENDING)])
        potholes_collection.create_index([("location", GEOSPHERE)], sparse=True)
        detections_collection.create_index([("event_id", ASCENDING)], unique=True)
        detections_collection.create_index([("timestamp", DESCENDING)])
        detections_collection.create_index([("pothole_id", ASCENDING)])
        events_collection.create_index([("created_at", DESCENDING)])
        users_collection.create_index([("email", ASCENDING)], unique=True)
        potholes_collection.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        detections_collection.create_index([("user_id", ASCENDING), ("timestamp", DESCENDING)])
        notifications_collection.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        notifications_collection.create_index([("user_id", ASCENDING), ("is_read", ASCENDING)])
        auth_tokens_collection.create_index("expires_at", expireAfterSeconds=0)
        auth_tokens_collection.create_index([("user_id", ASCENDING), ("purpose", ASCENDING)])
        auth_tokens_collection.create_index([("token_hash", ASCENDING)], unique=True)
        auth_rate_limits_collection.create_index("expires_at", expireAfterSeconds=0)
        report_schedules_collection.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)])
        report_schedules_collection.create_index([("status", ASCENDING), ("next_run_at", ASCENDING), ("lease_expires_at", ASCENDING)])
        report_runs_collection.create_index([("schedule_id", ASCENDING), ("created_at", DESCENDING)])
        report_runs_collection.create_index([("expires_at", ASCENDING)])
    except PyMongoError as error:
        print(f"Index setup warning: {error}")


@app.on_event("startup")
def startup() -> None:
    ensure_indexes()
    start_report_scheduler()


@app.on_event("shutdown")
def shutdown() -> None:
    stop_report_scheduler()


@app.get("/")
def home() -> dict[str, str]:
    return {"message": "RoadWatch AI API is running"}


@app.get("/health")
def health_check() -> dict[str, Any]:
    try:
        client.admin.command("ping")
    except PyMongoError as error:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed. Check backend configuration.",
        ) from error
    return {"status": "healthy", "database": "connected", "database_name": DATABASE_NAME, "collection": POTHOLES_COLLECTION, "timestamp": now_utc().isoformat()}


@app.get("/api/system/status")
def system_status(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    database_status = "disconnected"
    database_error = None
    agent_status: dict[str, Any] = {
        "yolo_inference": {"status": "unknown", "error": "Local camera agent is unavailable."},
        "camera": {"status": "unknown", "error": "Local camera agent is unavailable."},
        "gps": {"status": "unknown", "error": "Local GPS agent is unavailable."},
    }
    try:
        client.admin.command("ping")
        database_status = "connected"
    except PyMongoError:
        database_error = "Database ping failed. Check backend configuration."
    try:
        from camera_stream import get_agent_status

        agent_status = get_agent_status()
    except Exception as error:
        agent_status = {
            "yolo_inference": {"status": "unknown", "error": str(error)},
            "camera": {"status": "unknown", "error": str(error)},
            "gps": {"status": "unknown", "error": str(error)},
        }
    last_detection = None
    if database_status == "connected":
        try:
            latest = detections_collection.find_one(owner_filter(user), sort=[("timestamp", DESCENDING)])
            last_detection = iso_or_none(latest.get("timestamp")) if latest else None
        except PyMongoError:
            database_status = "disconnected"
            database_error = "Database query failed. Check backend configuration."
    return {
        "frontend": {"status": "connected", "note": "Status requested by dashboard"},
        "backend": {"status": "connected", "timestamp": now_utc().isoformat()},
        "database": {"status": database_status, "error": database_error},
        **agent_status,
        "last_successful_detection": last_detection,
    }


@app.post("/api/potholes")
def create_pothole(pothole: LegacyPotholeCreate, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    event = DetectionEventCreate(
        source_id="legacy-pothole-api",
        timestamp=now_utc(),
        latitude=pothole.latitude,
        longitude=pothole.longitude,
        confidence=pothole.confidence,
    )
    result = process_detection(event, pothole.pothole_id, store_event=False, user_id=user.get("_id") if isinstance(user, dict) else None)
    result["message"] = (
        "Pothole observation linked to an existing record"
        if result.get("linked_existing")
        else "Pothole saved successfully"
    )
    if result.get("pothole"):
        result["mongo_id"] = result["pothole"].get("_id")
    return result


@app.get("/api/potholes")
def get_potholes(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    severity: str | None = None,
    verification_status: str | None = None,
    repair_status: str | None = None,
    source: str | None = None,
    search: str | None = None,
    location: Annotated[str | None, Query(max_length=120)] = None,
    all_records: bool = False,
    include_statistics: bool = False,
    user_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    date_query = created_at_date_filter(date_from, date_to)
    try:
        query = build_filters(severity, verification_status, repair_status, source, search, location)
        if isinstance(user, dict) and canonical_role(user.get("role")) not in {"admin", "administrator"}: query["user_id"] = user["_id"]
        else:
            if user_id:
                if not ObjectId.is_valid(user_id): raise HTTPException(status_code=422, detail="Invalid user_id.")
                query["user_id"] = ObjectId(user_id)
        query.update(date_query)
        total = potholes_collection.count_documents(query)
        limit = min(total or page_size, 10000) if all_records else page_size
        skip = 0 if all_records else (page - 1) * page_size
        cursor = potholes_collection.find(query).sort([("last_seen_at", DESCENDING), ("created_at", DESCENDING)]).skip(skip).limit(limit)
        potholes = [serialize_pothole(doc) for doc in cursor]
        add_reporter_details(potholes, user)
        add_status_actor_details(potholes)
        response = {
            "count": len(potholes),
            "total": total,
            "page": 1 if all_records else page,
            "page_size": limit,
            "truncated": all_records and total > len(potholes),
            "potholes": potholes,
        }
        if include_statistics:
            pending_verification = count_report_statuses(
                query,
                "verification_status",
                ["pending", "pending_verification", "awaiting_verification", "unverified"],
            )
            verified = count_report_statuses(query, "verification_status", ["verified"])
            rejected = count_report_statuses(query, "verification_status", ["rejected"])
            not_started = count_report_statuses(
                query,
                "repair_status",
                ["not_started", "reported", "awaiting_verification"],
            )
            under_maintenance = count_report_statuses(
                query,
                "repair_status",
                [
                    "under_maintenance",
                    "inspection_scheduled",
                    "repair_scheduled",
                    "repair_in_progress",
                    "awaiting_repair_verification",
                    "reopened",
                ],
            )
            repaired = count_report_statuses(
                query,
                "repair_status",
                ["repaired", "verified", "marked_repaired", "repair_verified"],
            )
            response["statistics"] = {
                "verification": {
                    "pending": pending_verification,
                    "verified": verified,
                    "rejected": rejected,
                    "other": max(total - pending_verification - verified - rejected, 0),
                },
                "repair": {
                    "not_started": not_started,
                    "under_maintenance": under_maintenance,
                    "repaired": repaired,
                    "other": max(total - not_started - under_maintenance - repaired, 0),
                },
            }
        return mongo_to_json(response)
    except Exception as error:
        print(f"Error retrieving potholes: {error}")
        raise HTTPException(status_code=500, detail="Failed to retrieve potholes")


@app.get("/api/potholes/map")
def get_pothole_map(
    severity: str | None = None,
    verification_status: str | None = None,
    repair_status: str | None = None,
    source: str | None = None,
    search: str | None = None,
    location: Annotated[str | None, Query(max_length=120)] = None,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    query = build_filters(severity, verification_status, repair_status, source, search, location)
    if isinstance(user, dict) and canonical_role(user.get("role")) not in {"admin", "administrator"}: query["user_id"] = user["_id"]
    total = potholes_collection.count_documents(query)
    projection = {
        "pothole_id": 1,
        "latitude": 1,
        "longitude": 1,
        "lat": 1,
        "lon": 1,
        "location": 1,
        "location_name": 1,
        "region": 1,
        "district": 1,
        "city": 1,
        "state": 1,
        "address": 1,
        "road_name": 1,
        "confidence": 1,
        "last_detection_confidence": 1,
        "image_reference": 1,
        "annotated_image_reference": 1,
        "severity": 1,
        "verification_status": 1,
        "repair_status": 1,
        "verified_by": 1,
        "verified_at": 1,
        "rejected_by": 1,
        "rejected_at": 1,
        "rejection_reason": 1,
        "repair_updated_by": 1,
        "repair_updated_at": 1,
        "location_verified_by": 1,
        "location_verified_by_name": 1,
        "location_verification_notes": 1,
        "gps_accuracy_m": 1,
        "location_verification_status": 1,
        "location_verification_method": 1,
        "location_verification_reason": 1,
        "location_verified_at": 1,
        "source": 1,
        "observation_count": 1,
        "notes": 1,
        "audit_history": 1,
        "first_detected_at": 1,
        "last_seen_at": 1,
        "created_at": 1,
        "user_id": 1,
    }
    cursor = potholes_collection.find(query, projection).sort([("last_seen_at", DESCENDING), ("created_at", DESCENDING)])
    candidates = [serialize_pothole(document) for document in cursor]
    verified_records = [
        item
        for item in candidates
        if (
            item["has_valid_coordinates"]
            and item["verification_status"] == "verified"
            and location_status(item) == "VERIFIED"
        )
    ]
    review_records = (
        [
            item
            for item in candidates
            if (
                item["has_valid_coordinates"]
                and location_status(item) == "VERIFIED"
                and not (
                    item["verification_status"] == "verified"
                )
            )
        ]
        if canonical_role(user.get("role")) in {"admin", "administrator"}
        else []
    )
    records = verified_records
    add_reporter_details(records, user)
    add_status_actor_details(records)
    add_reporter_details(review_records, user)
    add_status_actor_details(review_records)
    return mongo_to_json({
        "count": len(records),
        "review_count": len(review_records),
        "total_records": total,
        "truncated": False,
        "records": records,
        "review_records": review_records,
    })


@app.get("/api/potholes/{pothole_id}")
def get_pothole(pothole_id: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    normalized_id = pothole_id.upper() if POTHOLE_ID_PATTERN.fullmatch(pothole_id) else pothole_id
    query: dict[str, Any] = {"pothole_id": int(pothole_id)} if pothole_id.isdigit() else {"pothole_id": normalized_id}
    query.update(owner_filter(user))
    doc = potholes_collection.find_one(query)
    if not doc and ObjectId.is_valid(pothole_id):
        doc = potholes_collection.find_one({"_id": ObjectId(pothole_id), **owner_filter(user)})
    if not doc:
        raise HTTPException(status_code=404, detail="Pothole record not found")
    events = [serialize_event(event) for event in detections_collection.find({"pothole_id": doc.get("pothole_id"), **owner_filter(user)}).sort("timestamp", DESCENDING).limit(50)]
    pothole = serialize_pothole(doc)
    add_reporter_details([pothole], user)
    add_status_actor_details([pothole])
    add_reporter_details(events, user)
    return mongo_to_json({"pothole": pothole, "observations": events})


def find_duplicate_candidate(event: DetectionEventCreate, user_id: ObjectId | None = None) -> dict[str, Any] | None:
    if (
        event.latitude is None
        or event.longitude is None
        or (event.gps_accuracy_m is not None and event.gps_accuracy_m > 250)
    ):
        return None
    radius = max(20, min(float(event.gps_accuracy_m or 35) * 1.5, 150))
    latitude_delta = radius / 110_574
    longitude_scale = max(cos(radians(event.latitude)), 0.01)
    longitude_delta = radius / (111_320 * longitude_scale)
    duplicate_query: dict[str, Any] = {
            "latitude": {
                "$gte": event.latitude - latitude_delta,
                "$lte": event.latitude + latitude_delta,
            },
            "longitude": {
                "$gte": event.longitude - longitude_delta,
                "$lte": event.longitude + longitude_delta,
            },
        }
    if user_id is not None:
        duplicate_query["user_id"] = user_id
    candidates = potholes_collection.find(duplicate_query).limit(200)
    closest = None
    closest_distance = None
    for candidate in candidates:
        if not valid_coordinate(candidate.get("latitude"), candidate.get("longitude")):
            continue
        distance = haversine_m(event.latitude, event.longitude, float(candidate["latitude"]), float(candidate["longitude"]))
        if distance <= radius and (closest_distance is None or distance < closest_distance):
            closest = candidate
            closest_distance = distance
    if closest:
        closest["_match_distance_m"] = round(closest_distance or 0, 2)
    return closest


@app.get("/api/detections")
def get_detections(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    query = owner_filter(user)
    total = detections_collection.count_documents(query)
    cursor = detections_collection.find(query).sort("timestamp", DESCENDING).skip((page - 1) * page_size).limit(page_size)
    detections = [serialize_event(doc) for doc in cursor]
    add_reporter_details(detections, user)
    return mongo_to_json({"total": total, "page": page, "page_size": page_size, "detections": detections})


@app.get("/api/detections/{detection_id}")
def get_detection(detection_id: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    query: dict[str, Any] = {"event_id": detection_id}
    query.update(owner_filter(user))
    doc = detections_collection.find_one(query)
    if not doc and detection_id.isdigit() and int(detection_id) > 0:
        matches = list(detections_collection.find(owner_filter(user)).sort("_id", ASCENDING).skip(int(detection_id) - 1).limit(1))
        doc = matches[0] if matches else None
    if not doc and ObjectId.is_valid(detection_id):
        doc = detections_collection.find_one({"_id": ObjectId(detection_id), **owner_filter(user)})
    if not doc:
        raise HTTPException(status_code=404, detail="Detection record not found")
    detection = serialize_event(doc)
    add_reporter_details([detection], user)
    return mongo_to_json({"detection": detection})


@lru_cache(maxsize=1)
def get_image_model() -> Any:
    if not MODEL_PATH.is_file():
        raise HTTPException(status_code=503, detail="Configured YOLO model file was not found.")
    from ultralytics import YOLO

    return YOLO(str(MODEL_PATH))

@app.post("/api/detections/image")
async def analyze_uploaded_image(
    file: UploadFile = File(...),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    gps_accuracy_m: float | None = Form(default=None),
    device_location: bool = Form(default=False),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:

    # 1. Validate file type
    if file.content_type not in {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/bmp",
    }:
        raise HTTPException(
            status_code=415,
            detail="Upload a JPEG, PNG, WebP, or BMP image.",
        )

    # 2. Read uploaded image
    image_bytes = await file.read(MAX_UPLOAD_BYTES + 1)

    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Image exceeds the 10 MB upload limit.",
        )

    image = cv2.imdecode(
        np.frombuffer(image_bytes, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )

    if image is None:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is not a decodable image.",
        )

    # 3. First check using Places365
    try:
        road_scene = validate_road_scene(image)
        if (
            not isinstance(road_scene, dict)
            or not isinstance(road_scene.get("is_road"), bool)
        ):
            raise RuntimeError("Road-scene validator returned an invalid result.")
    except Exception as error:
        logger.exception("Road-scene validation failed")
        raise HTTPException(
            status_code=503,
            detail=f"Road-scene validation is unavailable: {error}",
        )

    # 4. Run YOLO11 to check for potholes.
    # This also acts as a fallback when Places365 misclassifies
    # a genuine road/pothole image.
    try:
        results = get_image_model().predict(
            source=image,
            imgsz=640,
            conf=YOLO_CONFIDENCE_THRESHOLD,
            verbose=False,
        )
    except Exception as error:
        logger.exception("YOLO image prediction failed")
        raise HTTPException(
            status_code=500,
            detail=f"Image detection failed: {error}",
        )

    result = results[0]
    boxes = result.boxes

    yolo_found_pothole = boxes is not None and len(boxes) > 0
    places365_approved = bool(road_scene.get("is_road"))

    # 5. Reject only when BOTH checks fail.
    if not places365_approved and not yolo_found_pothole:

        logger.warning(
            "Image rejected. Scene: %s | Confidence: %.4f | "
            "Road score: %.4f | YOLO potholes: 0",
            road_scene.get("scene"),
            road_scene.get("confidence", 0),
            road_scene.get("road_scene_score", 0),
        )

        raise HTTPException(
            status_code=422,
            detail={
                "message": INVALID_ROAD_IMAGE_MESSAGE,
                "reason": "The image was not recognized as a road scene and no pothole was detected.",
                "predicted_scene": road_scene.get("scene"),
                "road_scene_score": road_scene.get("road_scene_score"),
                "threshold": road_scene.get("threshold"),
                "top5": road_scene.get("top5"),
            },
        )

    # Record whether YOLO allowed an image that Places365 rejected.
    road_scene["yolo_fallback_used"] = (
        not places365_approved and yolo_found_pothole
    )

    if road_scene["yolo_fallback_used"]:
        logger.warning(
            "Places365 rejected scene '%s', but YOLO detected a pothole. "
            "Accepting image through YOLO fallback.",
            road_scene.get("scene"),
        )

    # 6. Extract GPS information only after image validation.
    exif_latitude, exif_longitude, capture_timestamp, exif_accuracy = (
        exif_location(image_bytes)
    )

    exif_invalid_coordinates = (
        exif_latitude is not None or exif_longitude is not None
    ) and not valid_coordinate(exif_latitude, exif_longitude)

    supplied_invalid_coordinates = (
        latitude is not None or longitude is not None
    ) and not valid_coordinate(latitude, longitude)

    location_source = (
        "EXIF"
        if exif_latitude is not None or exif_longitude is not None
        else None
    )

    if not valid_coordinate(latitude, longitude) and valid_coordinate(
        exif_latitude, exif_longitude
    ):
        latitude = exif_latitude
        longitude = exif_longitude
        gps_accuracy_m = exif_accuracy

    elif valid_coordinate(latitude, longitude):
        location_source = "DEVICE" if device_location else "USER_SUPPLIED"

    if not valid_coordinate(latitude, longitude):
        latitude = None
        longitude = None

    if gps_accuracy_m is not None:
        try:
            gps_accuracy_m = float(gps_accuracy_m)

            if not isfinite(gps_accuracy_m) or gps_accuracy_m < 0:
                gps_accuracy_m = None

        except (TypeError, ValueError):
            gps_accuracy_m = None

    # 7. Save the original image only after it passes validation.
    upload_id = uuid4().hex

    suffix = Path(file.filename or "upload.jpg").suffix.lower()

    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        suffix = ".img"

    image_path = UPLOADS_DIR / f"{upload_id}{suffix}"
    image_path.write_bytes(image_bytes)

    image_reference = f"/uploads/{image_path.name}"

    # 8. Process YOLO detections.
    detections: list[dict[str, Any]] = []
    persistence_results: list[dict[str, Any]] = []

    if boxes is not None:
        height, width = image.shape[:2]
        names = result.names

        for index, box in enumerate(boxes):

            x1, y1, x2, y2 = [
                float(value)
                for value in box.xyxy[0].tolist()
            ]

            confidence = float(box.conf[0].item())
            class_id = int(box.cls[0].item())

            class_name = (
                names.get(class_id, str(class_id))
                if isinstance(names, dict)
                else names[class_id]
            )

            detection = {
                "class_id": class_id,
                "class_name": class_name,
                "confidence": confidence,
                "confidence_percent": round(confidence * 100, 2),
                "bounding_box": {
                    "x": round(x1 / width, 6),
                    "y": round(y1 / height, 6),
                    "width": round((x2 - x1) / width, 6),
                    "height": round((y2 - y1) / height, 6),
                },
            }

            detections.append(detection)

            event = DetectionEventCreate(
                event_id=f"image-upload-{uuid4().hex}-{index}",
                source_id="image_upload",
                timestamp=capture_timestamp or now_utc(),
                latitude=latitude,
                longitude=longitude,
                gps_accuracy_m=gps_accuracy_m,
                location_source=location_source,
                confidence=confidence,
                bounding_box=BoundingBox(
                    **detection["bounding_box"]
                ),
                severity_estimate=estimate_severity(
                    confidence,
                    detection["bounding_box"],
                )[0],
                annotated_image_reference=image_reference,
            )

            try:
                saved = process_detection(
                    event,
                    user_id=user.get("_id"),
                )

            except PyMongoError:
                logger.exception("Could not save detection event")

                persistence_results.append({
                    "event_id": event.event_id,
                    "saved_event": False,
                    "error": "MongoDB did not confirm saving this detection.",
                })

            else:
                persistence_results.append({
                    "event_id": event.event_id,
                    "saved_event": True,
                    "duplicate": saved.get("duplicate", False),
                    "linked_existing": saved.get("linked_existing", False),
                    "pothole_id": (
                        saved.get("pothole_id")
                        or (saved.get("pothole") or {}).get("pothole_id")
                    ),
                    "message": saved.get("message"),
                })

    # 9. Generate annotated image.
    annotated_image = result.plot()

    encoded_ok, encoded_image = cv2.imencode(
        ".jpg",
        annotated_image,
    )

    if not encoded_ok:
        raise HTTPException(
            status_code=500,
            detail="Could not encode the annotated image.",
        )

    annotated_path = UPLOADS_DIR / f"{upload_id}-yolo.jpg"
    annotated_path.write_bytes(encoded_image.tobytes())

    annotated_reference = f"/uploads/{annotated_path.name}"

    # 10. Update saved detection references.
    for saved_item in persistence_results:

        if saved_item.get("saved_event"):

            detections_collection.update_one(
                {"event_id": saved_item["event_id"]},
                {
                    "$set": {
                        "image_reference": image_reference,
                        "annotated_image_reference": annotated_reference,
                        "location_source": location_source,
                        "location_verification_method": (
                            location_source
                            or (
                                "GPS"
                                if valid_coordinate(latitude, longitude)
                                else None
                            )
                        ),
                        "location_verification_status": location_status({
                            "latitude": latitude,
                            "longitude": longitude,
                            "gps_accuracy_m": gps_accuracy_m,
                            "location_verification_method": location_source,
                        }),
                        "location_verification_reason": (
                            "Invalid GPS coordinates"
                            if (
                                exif_invalid_coordinates
                                or supplied_invalid_coordinates
                            ) and not valid_coordinate(latitude, longitude)
                            else location_reason({
                                "latitude": latitude,
                                "longitude": longitude,
                                "gps_accuracy_m": gps_accuracy_m,
                                "location_verification_method": location_source,
                            })
                        ),
                        "capture_timestamp": capture_timestamp,
                    }
                },
            )
            if saved_item.get("pothole_id") is not None:
                pothole_query: dict[str, Any] = {
                    "pothole_id": saved_item["pothole_id"],
                }
                if user.get("_id") is not None:
                    pothole_query["user_id"] = user["_id"]
                potholes_collection.update_one(
                    pothole_query,
                    {"$set": {
                        "image_reference": image_reference,
                        "annotated_image_reference": annotated_reference,
                    }},
                )

    # 11. Return response.
    return {
        "source": "image_upload",
        "road_scene": road_scene,
        "image_url": image_reference,
        "annotated_image_url": annotated_reference,
        "location_source": location_source,
        "capture_timestamp": (
            capture_timestamp.isoformat()
            if capture_timestamp
            else None
        ),
        "location_verification_status": location_status({
            "latitude": latitude,
            "longitude": longitude,
            "gps_accuracy_m": gps_accuracy_m,
            "location_verification_method": location_source,
        }),
        "location_verification_reason": (
            "Invalid GPS coordinates"
            if (
                exif_invalid_coordinates
                or supplied_invalid_coordinates
            ) and not valid_coordinate(latitude, longitude)
            else location_reason({
                "latitude": latitude,
                "longitude": longitude,
                "gps_accuracy_m": gps_accuracy_m,
                "location_verification_method": location_source,
            })
        ),
        "filename": Path(file.filename or "upload").name,
        "detection_count": len(detections),
        "detections": detections,
        "annotated_image_base64": base64.b64encode(
            encoded_image.tobytes()
        ).decode("ascii"),
        "saved_detection_events": sum(
            1 for item in persistence_results
            if item["saved_event"]
        ),
        "persistence": persistence_results,
        "save_failures": sum(
            1 for item in persistence_results
            if item.get("error")
        ),
        "persistence_note": (
            "YOLO returned zero detections; nothing was written to MongoDB."
            if not detections
            else (
                "Analysis succeeded, but one or more MongoDB saves were not confirmed."
                if any(
                    not item["saved_event"]
                    for item in persistence_results
                )
                else (
                    "GPS coordinates were not supplied. YOLO detections are saved as events but are not linked to mapped pothole records."
                    if latitude is None
                    else "Detections were saved or linked using the existing GPS validation and duplicate-prevention rules."
                )
            )
        ),
    }


def cleanup_expired_video_outputs() -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - 3600
    for output_path in UPLOADS_DIR.glob("roadwatch-video-*.mp4"):
        try:
            if output_path.stat().st_mtime < cutoff:
                output_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not clean expired video output %s", output_path.name)


@app.post("/api/detections/video")
async def analyze_uploaded_video(
    file: UploadFile = File(...),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    gps_accuracy_m: float | None = Form(default=None),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Validate, infer, annotate, and persist an uploaded road video."""
    allowed_video_types = {
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "video/x-msvideo",
    }
    allowed_video_extensions = {".mp4", ".mov", ".webm", ".avi"}
    file_extension = Path(file.filename or "").suffix.lower()
    if (
        file.content_type not in allowed_video_types
        and file_extension not in allowed_video_extensions
    ):
        raise HTTPException(status_code=415, detail="Upload an MP4, MOV, WebM, or AVI video.")

    video_bytes = await file.read(50 * 1024 * 1024 + 1)
    if len(video_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Video exceeds the 50 MB upload limit.")

    if not video_bytes:
        raise HTTPException(status_code=400, detail="The uploaded video is empty.")

    cleanup_expired_video_outputs()
    video_path = UPLOADS_DIR / f"{uuid4().hex}-video{file_extension or '.mp4'}"
    video_path.write_bytes(video_bytes)
    capture: cv2.VideoCapture | None = None
    writer: cv2.VideoWriter | None = None
    annotated_path: Path | None = None
    artifact_ready = False
    events: list[dict[str, Any]] = []
    seen_boxes: list[tuple[float, float, float]] = []

    try:
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise HTTPException(status_code=400, detail="The uploaded video could not be opened. Use a valid MP4, MOV, WebM, or AVI file.")

        fps = float(capture.get(cv2.CAP_PROP_FPS))
        if not isfinite(fps) or fps <= 0:
            fps = 25.0
        frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if frame_width <= 0 or frame_height <= 0:
            ok, first_frame = capture.read()
            if not ok or first_frame is None:
                raise HTTPException(status_code=400, detail="The uploaded video contains no readable frames.")
            frame_height, frame_width = first_frame.shape[:2]
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)

        # Validate one frame in each short segment. If Places365 misses a
        # genuine pothole scene, the existing YOLO pothole detector is the
        # fallback, as it is for image uploads. No records are saved in pass 1.
        validation_interval = 10
        approved_segment_starts: set[int] = set()
        frame_index = 0
        sample_count = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frame_index += 1
            if (frame_index - 1) % validation_interval:
                continue

            sample_count += 1
            try:
                scene = validate_road_scene(
                    frame,
                    source="video_upload_frame",
                    log_diagnostics=False,
                )
            except Exception as error:
                logger.exception("Road-scene validation failed for uploaded video")
                raise HTTPException(
                    status_code=503,
                    detail=f"Road-scene validation is unavailable: {error}",
                ) from error

            if not isinstance(scene, dict) or not isinstance(scene.get("is_road"), bool):
                raise HTTPException(status_code=503, detail="Road-scene validation returned an invalid result.")

            accepted = scene["is_road"]
            if not accepted:
                try:
                    sample_result = get_image_model().predict(
                        source=frame,
                        imgsz=640,
                        conf=YOLO_CONFIDENCE_THRESHOLD,
                        verbose=False,
                    )[0]
                except HTTPException:
                    raise
                except Exception as error:
                    logger.exception("YOLO video validation fallback failed")
                    raise HTTPException(status_code=500, detail=f"Video detection failed: {error}") from error
                accepted = sample_result.boxes is not None and len(sample_result.boxes) > 0

            if accepted:
                approved_segment_starts.add(frame_index)

        capture.release()
        capture = None
        if not approved_segment_starts:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": INVALID_ROAD_IMAGE_MESSAGE,
                    "reason": "No sampled video frame passed road-scene validation or the YOLO pothole fallback.",
                    "frames_sampled": sample_count,
                    "road_frames": 0,
                },
            )

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise HTTPException(status_code=400, detail="The uploaded video could not be reopened for detection.")

        artifact_id = uuid4().hex
        annotated_path = UPLOADS_DIR / f"roadwatch-video-{artifact_id}.mp4"
        for codec in ("mp4v", "avc1"):
            candidate_writer = cv2.VideoWriter(
                str(annotated_path),
                cv2.VideoWriter_fourcc(*codec),
                fps,
                (frame_width, frame_height),
            )
            if candidate_writer.isOpened():
                writer = candidate_writer
                break
            candidate_writer.release()
        if writer is None:
            raise HTTPException(status_code=500, detail="This backend cannot encode an annotated MP4 video.")

        frame_index = 0
        frames_processed = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frame_index += 1
            frames_processed += 1
            segment_start = ((frame_index - 1) // validation_interval) * validation_interval + 1
            output_frame = frame

            if segment_start in approved_segment_starts:
                try:
                    result = get_image_model().predict(
                        source=frame,
                        imgsz=640,
                        conf=YOLO_CONFIDENCE_THRESHOLD,
                        verbose=False,
                    )[0]
                except HTTPException:
                    raise
                except Exception as error:
                    logger.exception("YOLO video inference failed")
                    raise HTTPException(status_code=500, detail=f"Video detection failed: {error}") from error

                output_frame = result.plot()
                boxes = result.boxes
                if boxes is not None:
                    for box in boxes:
                        confidence = float(box.conf[0].item())
                        xy = [float(value) for value in box.xywhn[0].tolist()]
                        if any((abs(xy[0] - x) + abs(xy[1] - y)) < 0.08 for x, y, _ in seen_boxes):
                            continue
                        seen_boxes.append((xy[0], xy[1], confidence))
                        normalized_box = {
                            "x": xy[0] - xy[2] / 2,
                            "y": xy[1] - xy[3] / 2,
                            "width": xy[2],
                            "height": xy[3],
                        }
                        event = DetectionEventCreate(
                            event_id=f"video-{uuid4().hex}",
                            source_id="video_upload",
                            timestamp=now_utc(),
                            latitude=latitude if valid_coordinate(latitude, longitude) else None,
                            longitude=longitude if valid_coordinate(latitude, longitude) else None,
                            gps_accuracy_m=gps_accuracy_m,
                            location_source="USER_SUPPLIED" if valid_coordinate(latitude, longitude) else None,
                            confidence=confidence,
                            bounding_box=BoundingBox(**normalized_box),
                            severity_estimate=estimate_severity(confidence, normalized_box)[0],
                        )
                        saved_result = process_detection(event, user_id=user["_id"])
                        events.append({
                            "event_id": event.event_id,
                            "confidence": confidence,
                            "bounding_box": normalized_box,
                            "severity_estimate": event.severity_estimate,
                            **saved_result,
                        })

            writer.write(output_frame)

        writer.release()
        writer = None
        capture.release()
        capture = None
        if frames_processed == 0 or not annotated_path.is_file() or annotated_path.stat().st_size == 0:
            raise HTTPException(status_code=500, detail="The backend could not generate the annotated video.")

        artifact_token = jwt.encode(
            {
                "sub": str(user["_id"]),
                "video_id": artifact_id,
                "exp": int(datetime.now(timezone.utc).timestamp()) + 3600,
            },
            JWT_SECRET,
            algorithm=JWT_ALGORITHM,
        )
        artifact_ready = True
    finally:
        if writer is not None:
            writer.release()
        if capture is not None:
            capture.release()
        video_path.unlink(missing_ok=True)
        if not artifact_ready and annotated_path is not None:
            annotated_path.unlink(missing_ok=True)

    return {
        "source": "video_upload",
        "frames_sampled": sample_count,
        "frames_processed": frames_processed,
        "road_frames": len(approved_segment_starts),
        "detection_count": len(events),
        "results": events,
        "annotated_video_url": f"/api/detections/video/{artifact_id}/annotated?token={artifact_token}",
    }


@app.get("/api/detections/video/{video_id}/annotated")
def get_annotated_video(
    video_id: str,
    token: str = Query(...),
    user: dict[str, Any] = Depends(get_current_user),
) -> FileResponse:
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as error:
        raise HTTPException(status_code=401, detail="The annotated video link is invalid or expired.") from error

    if claims.get("video_id") != video_id or claims.get("sub") != str(user["_id"]):
        raise HTTPException(status_code=404, detail="Annotated video not found.")

    if not re.fullmatch(r"[0-9a-f]{32}", video_id):
        raise HTTPException(status_code=404, detail="Annotated video not found.")

    path = UPLOADS_DIR / f"roadwatch-video-{video_id}.mp4"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Annotated video not found or expired.")

    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Cache-Control": "private, no-store"},
    )


@app.post("/api/detections/{detection_id}/verify-location")
def verify_detection_location(
    detection_id: str,
    location: LocationVerification,
    user: dict[str, Any] = Depends(require_admin),
    admin_key: str | None = Header(default=None, alias="X-Roadwatch-Admin-Key"),
) -> dict[str, Any]:
    # Compatibility for direct legacy unit calls; HTTP requests are authorized by require_admin.
    if not isinstance(user, dict):
        if not ADMIN_API_KEY or admin_key != ADMIN_API_KEY:
            raise HTTPException(status_code=401, detail="Administrator authentication required.")
    query: dict[str, Any] = {"event_id": detection_id}
    event = detections_collection.find_one(query)
    if not event and ObjectId.is_valid(detection_id):
        event = detections_collection.find_one({"_id": ObjectId(detection_id)})
    if not event:
        raise HTTPException(status_code=404, detail="Detection record not found")
    if not valid_coordinate(location.latitude, location.longitude):
        raise HTTPException(status_code=422, detail="Select valid coordinates on the map before confirming.")
    previous = {
        key: event.get(key)
        for key in (
            "latitude",
            "longitude",
            "gps_accuracy_m",
            "location_verification_method",
            "location_verification_status",
        )
    }
    previous["location_verification_status"] = event.get("location_verification_status") or location_status(event)
    verified_at = now_utc()
    administrator_name = user.get("full_name") or "Administrator"
    severity, explanation = estimate_severity(event.get("confidence"), event.get("bounding_box"))
    previous_pothole_id = event.get("pothole_id")
    existing_pothole = (
        potholes_collection.find_one({"pothole_id": previous_pothole_id})
        if previous_pothole_id is not None
        else None
    )
    if existing_pothole and is_readable_pothole_id(existing_pothole.get("pothole_id")):
        pothole_id = existing_pothole["pothole_id"].upper()
    else:
        pothole_id = allocate_pothole_id()
        if existing_pothole:
            potholes_collection.update_one(
                {"_id": existing_pothole["_id"]},
                {"$set": {"pothole_id": pothole_id}},
            )
            if previous_pothole_id is not None:
                detections_collection.update_many(
                    {"pothole_id": previous_pothole_id},
                    {"$set": {"pothole_id": pothole_id}},
                )
            existing_pothole["pothole_id"] = pothole_id
    record = {
        "pothole_id": pothole_id,
        "image_reference": event.get("image_reference"),
        "annotated_image_reference": event.get("annotated_image_reference"),
        "latitude": location.latitude,
        "longitude": location.longitude,
        "location": {"type": "Point", "coordinates": [location.longitude, location.latitude]},
        "first_detected_at": event.get("timestamp"), "last_seen_at": event.get("timestamp"),
        "confidence": event.get("confidence"), "severity": event.get("severity_estimate") or severity,
        "severity_explanation": explanation, "verification_status": "pending_verification", "location_verification_status": "VERIFIED",
        "location_verification_method": "MANUAL", "location_verified_at": verified_at,
        "repair_status": "not_started", "verified_by": None, "verified_at": None, "rejection_reason": None,
        "source": event.get("source_id"), "observation_count": 1,
        "gps_accuracy_m": None, "created_at": now_utc(), "updated_at": now_utc(),
        "location_verified_by": user["_id"] if isinstance(user, dict) else None,
        "location_verified_by_name": administrator_name,
        "location_verification_notes": location.notes,
        "audit_history": [
            {
                "action": "report_submitted",
                "previous": {},
                "new": {
                    "verification_status": "pending_verification",
                    "repair_status": "not_started",
                },
                "timestamp": event.get("timestamp") or verified_at,
                "submitted_by_id": str(event["user_id"]) if event.get("user_id") else None,
            },
            {
                "action": "location_verification",
                "previous": mongo_to_json(previous),
                "new": {
                    "status": "VERIFIED",
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "notes": location.notes,
                },
                "timestamp": verified_at,
                "administrator_id": str(user["_id"]) if isinstance(user, dict) else location.administrator_id,
                "administrator_name": administrator_name,
            },
        ],
        "detection_event_id": event.get("event_id"),
    }
    location_audit_entry = record["audit_history"][1]
    if event.get("user_id") is not None:
        record["user_id"] = event["user_id"]
        record["reported_by"] = str(event["user_id"])
    score, category, reasons = priority_for(record)
    record.update({"priority_score": score, "priority_category": category, "priority_reasons": reasons})
    if existing_pothole:
        potholes_collection.update_one({"_id": existing_pothole["_id"]}, {"$set": {
            "latitude": location.latitude, "longitude": location.longitude,
            "location": record["location"], "location_verification_status": "VERIFIED",
            "location_verification_method": "MANUAL", "location_verified_at": verified_at,
            "location_verified_by": user["_id"] if isinstance(user, dict) else None,
            "location_verified_by_name": administrator_name,
            "location_verification_notes": location.notes,
            "gps_accuracy_m": None,
            "updated_at": now_utc(), "image_reference": record["image_reference"],
            "annotated_image_reference": record["annotated_image_reference"],
        }, "$push": {"audit_history": location_audit_entry}})
        saved_record = potholes_collection.find_one({"_id": existing_pothole["_id"]})
        serialized_pothole = serialize_pothole(saved_record) if saved_record else serialize_pothole(existing_pothole | record)
    else:
        saved = insert_pothole_with_unique_id(record)
        pothole_id = record["pothole_id"]
        serialized_pothole = serialize_pothole(record | {"_id": saved.inserted_id})
    detections_collection.update_one({"_id": event["_id"]}, {"$set": {
        "latitude": location.latitude, "longitude": location.longitude, "location_verification_status": "VERIFIED",
        "location_verification_method": "MANUAL", "location_verified_at": verified_at,
        "location_verified_by": user["_id"] if isinstance(user, dict) else None,
        "location_verified_by_name": administrator_name,
        "location_verification_notes": location.notes,
        "location_verification_reason": None, "pothole_id": pothole_id,
    }, "$push": {"location_verification_audit": location_audit_entry}})
    previous_coordinates = (previous.get("latitude"), previous.get("longitude"))
    corrected = (
        valid_coordinate(*previous_coordinates)
        and previous_coordinates != (location.latitude, location.longitude)
    )
    if event.get("user_id") is not None:
        action = "location_corrected" if corrected else "location_verified"
        verb = "corrected" if corrected else "verified"
        create_notification(
            event["user_id"],
            "location",
            action,
            f"The location for pothole {pothole_id} was {verb} by Admin {administrator_name}.",
            administrator=user,
            pothole_id=str(pothole_id),
            details={
                "previous_latitude": previous_coordinates[0],
                "previous_longitude": previous_coordinates[1],
                "latitude": location.latitude,
                "longitude": location.longitude,
            },
        )
    return {"message": "Location verified", "detection": serialize_event(detections_collection.find_one({"_id": event["_id"]})),
            "pothole": serialized_pothole}


@app.post("/api/detections", status_code=201)
def create_detection(event: DetectionEventCreate, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return process_detection(event, user_id=user["_id"])


def process_detection(
    event: DetectionEventCreate,
    legacy_pothole_id: int | str | None = None,
    *,
    store_event: bool = True,
    user_id: ObjectId | None = None,
) -> dict[str, Any]:
    event_data = dump_model(event)
    event_data["confidence"] = normalize_confidence(event_data["confidence"])
    event_data["created_at"] = now_utc()
    if user_id is not None:
        event_data["user_id"] = user_id
        event_data["detection_source"] = event.source_id
    event_data["location_verification_method"] = event.location_source or (
        "GPS" if valid_coordinate(event.latitude, event.longitude) else None
    )
    event_data["location_verification_status"] = location_status(event_data)
    event_data["location_verification_reason"] = location_reason(event_data)
    inserted_event_id = None
    if store_event:
        try:
            inserted = detections_collection.insert_one(event_data)
            inserted_event_id = inserted.inserted_id
        except DuplicateKeyError:
            existing = detections_collection.find_one({"event_id": event.event_id})
            if user_id is not None and existing and existing.get("user_id") != user_id:
                raise HTTPException(status_code=409, detail="This event identifier is already in use.")
            return {"message": "Duplicate event ignored", "duplicate": True, "event": serialize_event(existing)}

    candidate = find_duplicate_candidate(event, user_id)
    if candidate:
        pothole_id = candidate.get("pothole_id")
        if not is_readable_pothole_id(pothole_id):
            previous_pothole_id = pothole_id
            pothole_id = allocate_pothole_id()
            potholes_collection.update_one(
                {"_id": candidate["_id"]},
                {"$set": {"pothole_id": pothole_id}},
            )
            if previous_pothole_id is not None:
                detections_collection.update_many(
                    {"pothole_id": previous_pothole_id},
                    {"$set": {"pothole_id": pothole_id}},
                )
            candidate["pothole_id"] = pothole_id
        updates: dict[str, Any] = {
            "last_seen_at": event.timestamp,
            "updated_at": now_utc(),
            "confidence": event_data["confidence"],
            "last_detection_confidence": event_data["confidence"],
        }
        if event.gps_accuracy_m is not None:
            updates["gps_accuracy_m"] = event.gps_accuracy_m
        updates["location_verification_status"] = event_data["location_verification_status"]
        updates["location_verification_method"] = event_data["location_verification_method"]
        updates["location_verification_reason"] = event_data["location_verification_reason"]
        update_operation: dict[str, Any] = {
            "$inc": {"observation_count": 1},
            "$set": updates,
        }
        if store_event:
            update_operation["$push"] = {
                "deduplication_history": {
                    "event_id": event.event_id,
                    "matched_at": now_utc(),
                    "distance_m": candidate.get("_match_distance_m"),
                    "reason": "Matched by GPS proximity with reported accuracy",
                }
            }
        potholes_collection.update_one({"_id": candidate["_id"]}, update_operation)
        updated_candidate = {
            **candidate,
            **updates,
            "observation_count": int(candidate.get("observation_count") or 1) + 1,
        }
        score, category, reasons = priority_for(updated_candidate)
        potholes_collection.update_one(
            {"_id": candidate["_id"]},
            {"$set": {"priority_score": score, "priority_category": category, "priority_reasons": reasons}},
        )
        if store_event:
            detections_collection.update_one(
                {"event_id": event.event_id},
                {"$set": {"pothole_id": pothole_id, "deduplication": "linked_existing"}},
            )
        updated = potholes_collection.find_one({"_id": candidate["_id"]})
        return {
            "message": "Detection linked to existing pothole",
            "duplicate": False,
            "linked_existing": True,
            "pothole_id": pothole_id,
            "pothole": serialize_pothole(updated) if updated else None,
        }

    pothole_id = allocate_pothole_id()
    severity, explanation = estimate_severity(event_data["confidence"], event_data.get("bounding_box"))
    recorded_at = now_utc()
    record = {
        "pothole_id": pothole_id,
        "first_detected_at": event.timestamp,
        "last_seen_at": event.timestamp,
        "confidence": event_data["confidence"],
        "severity": event.severity_estimate or severity,
        "severity_explanation": explanation,
        "verification_status": "pending_verification",
        "repair_status": "not_started",
        "verified_by": None,
        "verified_at": None,
        "rejection_reason": None,
        "source": event.source_id,
        "observation_count": 1,
        "gps_accuracy_m": event.gps_accuracy_m,
        "location_verification_status": event_data["location_verification_status"],
        "location_verification_method": event_data["location_verification_method"],
        "location_verification_reason": event_data["location_verification_reason"],
        "annotated_image_reference": event.annotated_image_reference,
        "detection_event_id": event.event_id,
        "created_at": recorded_at,
        "updated_at": recorded_at,
        "audit_history": [{
            "action": "report_submitted",
            "previous": {},
            "new": {
                "verification_status": "pending_verification",
                "repair_status": "not_started",
                "latitude": event.latitude,
                "longitude": event.longitude,
                "location_verification_status": event_data["location_verification_status"],
            },
            "timestamp": recorded_at,
            "submitted_by_id": str(user_id) if user_id is not None else None,
        }],
    }
    if valid_coordinate(event.latitude, event.longitude):
        record.update({
            "latitude": event.latitude,
            "longitude": event.longitude,
            "location": {"type": "Point", "coordinates": [event.longitude, event.latitude]},
        })
    if user_id is not None:
        record["user_id"] = user_id
        record["reported_by"] = str(user_id)
    record["last_detection_confidence"] = event_data["confidence"]
    score, category, reasons = priority_for(record)
    record.update({"priority_score": score, "priority_category": category, "priority_reasons": reasons})
    result = insert_pothole_with_unique_id(record)
    pothole_id = record["pothole_id"]
    if store_event:
        deduplication = (
            "created_new_missing_coordinates"
            if not valid_coordinate(event.latitude, event.longitude)
            else "created_new_low_gps_accuracy"
            if event.gps_accuracy_m is not None and event.gps_accuracy_m > 250
            else "created_new"
        )
        detections_collection.update_one(
            {"event_id": event.event_id},
            {"$set": {"pothole_id": pothole_id, "deduplication": deduplication}},
        )
    if not valid_coordinate(event.latitude, event.longitude):
        message = "Detection and pothole record saved; GPS location is unavailable and can be verified later."
    elif event.gps_accuracy_m is not None and event.gps_accuracy_m > 250:
        message = "Detection and pothole record saved; low-accuracy GPS is excluded from the map until verified."
    else:
        message = "Detection event saved"
    return {"message": message, "duplicate": False, "linked_existing": False, "pothole": serialize_pothole(record | {"_id": result.inserted_id})}


def create_pothole_change_notification(
    previous: dict[str, Any],
    updates: dict[str, Any],
    administrator: dict[str, Any],
) -> None:
    user_id = previous.get("user_id")
    if user_id is None:
        return
    pothole_id = str(previous.get("pothole_id") or "record")
    administrator_name = (
        administrator.get("full_name")
        or "Administrator"
    )
    previous_verification = canonical_verification_status(previous.get("verification_status"))
    next_verification = canonical_verification_status(updates.get("verification_status"))
    if "verification_status" in updates and previous_verification != next_verification:
        if next_verification == "verified":
            action = "pothole_verified"
            message = f"Your pothole {pothole_id} was verified by Admin {administrator_name}."
        elif next_verification == "rejected":
            action = "pothole_rejected"
            reason = updates.get("rejection_reason") or "No reason provided."
            message = f"Your pothole {pothole_id} was rejected by Admin {administrator_name}. Reason: {reason}"
        else:
            action = "verification_pending"
            message = f"Admin {administrator_name} returned pothole {pothole_id} to Pending Verification."
        create_notification(
            user_id,
            "pothole_verification",
            action,
            message,
            administrator=administrator,
            pothole_id=pothole_id,
            details={
                "previous_status": previous_verification,
                "new_status": next_verification,
                "rejection_reason": updates.get("rejection_reason"),
            },
        )

    previous_repair = canonical_repair_status(previous.get("repair_status"))
    next_repair = canonical_repair_status(updates.get("repair_status"))
    if "repair_status" in updates and previous_repair != next_repair:
        if next_repair == "under_maintenance":
            action = "maintenance_started"
            message = f"Maintenance has started for pothole {pothole_id} by Admin {administrator_name}."
        elif next_repair == "repaired":
            action = "repair_completed"
            message = f"Your pothole {pothole_id} has been successfully repaired by Admin {administrator_name}."
        else:
            action = "repair_status_changed"
            message = f"Repair status for pothole {pothole_id} was updated to Not Started by Admin {administrator_name}."
        create_notification(
            user_id,
            "repair_status",
            action,
            message,
            administrator=administrator,
            pothole_id=pothole_id,
            details={
                "previous_status": previous_repair,
                "new_status": next_repair,
                "remarks": updates.get("notes"),
            },
        )

    location_verified = (
        updates.get("location_verification_status") == "VERIFIED"
        and updates.get("location_verification_method") == "MANUAL"
    )
    if location_verified:
        previous_coordinates = (previous.get("latitude"), previous.get("longitude"))
        next_coordinates = (
            updates.get("latitude", previous.get("latitude")),
            updates.get("longitude", previous.get("longitude")),
        )
        corrected = (
            valid_coordinate(*previous_coordinates)
            and previous_coordinates != next_coordinates
        )
        was_verified = location_status(previous) == "VERIFIED"
        if corrected or not was_verified:
            action = "location_corrected" if corrected else "location_verified"
            verb = "corrected" if corrected else "verified"
            message = f"The location for pothole {pothole_id} was {verb} by Admin {administrator_name}."
            create_notification(
                user_id,
                "location",
                action,
                message,
                administrator=administrator,
                pothole_id=pothole_id,
                details={
                    "previous_latitude": previous_coordinates[0],
                    "previous_longitude": previous_coordinates[1],
                    "latitude": next_coordinates[0],
                    "longitude": next_coordinates[1],
                },
            )


def update_pothole_record(
    pothole_id: str,
    updates: dict[str, Any],
    actor: dict[str, Any] | None = None,
    action: str = "update",
) -> dict[str, Any]:
    updated_at = now_utc()
    updates["updated_at"] = updated_at
    normalized_id = pothole_id.upper() if POTHOLE_ID_PATTERN.fullmatch(pothole_id) else pothole_id
    query = {"pothole_id": int(pothole_id)} if pothole_id.isdigit() else {"pothole_id": normalized_id}
    previous_record = potholes_collection.find_one(query)
    if previous_record is None and ObjectId.is_valid(pothole_id):
        previous_record = potholes_collection.find_one({"_id": ObjectId(pothole_id)})
    audit_updates = {
        key: str(value) if isinstance(value, ObjectId) else iso_or_none(value) if isinstance(value, datetime) else value
        for key, value in updates.items()
    }
    previous_values = {
        key: previous_record.get(key)
        for key in updates
        if previous_record is not None
    }
    audit_entry = {
        "action": action,
        "previous": mongo_to_json(previous_values),
        "new": mongo_to_json(audit_updates),
        "updates": audit_updates,
        "timestamp": updated_at,
    }
    if isinstance(actor, dict):
        audit_entry["administrator_id"] = str(actor["_id"])
        audit_entry["administrator_name"] = (
            actor.get("full_name") or "Administrator"
        )
    operation = {"$set": updates, "$push": {"audit_history": audit_entry}}
    result = potholes_collection.update_one(query, operation)
    if result.matched_count == 0 and ObjectId.is_valid(pothole_id):
        result = potholes_collection.update_one({"_id": ObjectId(pothole_id)}, operation)
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Pothole record not found")
    if previous_record is not None and isinstance(actor, dict):
        create_pothole_change_notification(previous_record, updates, actor)
    if isinstance(actor, dict):
        return get_pothole(pothole_id, user=actor)
    return get_pothole(pothole_id)


@app.patch("/api/potholes/{pothole_id}")
def patch_pothole(pothole_id: str, patch: PotholePatch, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    updates = {key: value for key, value in dump_model(patch).items() if value is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields supplied")
    now = now_utc()
    if "verification_status" in updates:
        status = canonical_verification_status(updates["verification_status"])
        updates["verification_status"] = status
        if status == "verified":
            updates.update({"verified_by": admin["_id"], "verified_at": now, "rejection_reason": None, "rejected_by": None, "rejected_at": None})
        elif status == "rejected":
            updates.update({"verified_by": None, "verified_at": None, "rejected_by": admin["_id"], "rejected_at": now})
        else:
            updates.update({"verified_by": None, "verified_at": None, "rejection_reason": None, "rejected_by": None, "rejected_at": None})
    if "repair_status" in updates:
        updates.update({
            "repair_status": canonical_repair_status(updates["repair_status"]),
            "repair_updated_by": admin["_id"],
            "repair_updated_at": now,
        })
    return update_pothole_record(pothole_id, updates, actor=admin, action="status_update")


@app.post("/api/potholes/{pothole_id}/verify")
def verify_pothole(
    pothole_id: str,
    update: VerificationUpdate,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    verified_at = now_utc()
    verification_status = canonical_verification_status(update.status)
    if verification_status == "rejected":
        updates = {
            "verification_status": "rejected",
            "verified_by": None,
            "verified_at": None,
            "rejection_reason": update.rejection_reason.strip(),
            "rejected_by": admin["_id"],
            "rejected_at": verified_at,
        }
    elif verification_status == "verified":
        updates = {
            "verification_status": "verified",
            "verified_by": admin["_id"],
            "verified_at": verified_at,
            "rejection_reason": None,
            "rejected_by": None,
            "rejected_at": None,
        }
    else:
        updates = {
            "verification_status": "pending_verification",
            "verified_by": None,
            "verified_at": None,
            "rejection_reason": None,
            "rejected_by": None,
            "rejected_at": None,
        }
    if update.notes:
        updates["notes"] = update.notes
    return update_pothole_record(pothole_id, updates, actor=admin, action="verification")


@app.post("/api/potholes/{pothole_id}/verify-location")
def verify_pothole_location(
    pothole_id: str,
    location: LocationVerification,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    existing = get_pothole(pothole_id, user=admin)["pothole"]
    if existing.get("verification_status") == "rejected":
        raise HTTPException(
            status_code=409,
            detail="A rejected pothole report cannot be added to the verified map.",
        )
    verified_at = now_utc()
    updates = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "location": {
            "type": "Point",
            "coordinates": [location.longitude, location.latitude],
        },
        "gps_accuracy_m": None,
        "location_verification_status": "VERIFIED",
        "location_verification_method": "MANUAL",
        "location_verification_reason": None,
        "location_verified_by": admin["_id"],
        "location_verified_by_name": (
            admin.get("full_name") or "Administrator"
        ),
        "location_verified_at": verified_at,
    }
    if location.notes:
        updates["location_verification_notes"] = location.notes
    return update_pothole_record(
        pothole_id,
        updates,
        actor=admin,
        action="location_verification",
    )


@app.post("/api/potholes/{pothole_id}/repair")
def repair_pothole(
    pothole_id: str,
    update: RepairUpdate,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    updates = {
        "repair_status": canonical_repair_status(update.status),
        "repair_updated_by": admin["_id"],
        "repair_updated_at": now_utc(),
    }
    if update.assigned_team:
        updates["assigned_team"] = update.assigned_team
    if update.notes:
        updates["notes"] = update.notes
    return update_pothole_record(pothole_id, updates, actor=admin, action="repair")


@app.get("/api/analytics/summary", dependencies=[Depends(require_admin)])
def analytics_summary(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    scope = owner_filter(user)
    docs = [serialize_pothole(doc) for doc in potholes_collection.find(scope)]
    valid_confidences = [doc["confidence_normalized"] for doc in docs if doc.get("confidence_normalized") is not None]
    historical_docs = [doc for doc in docs if doc["record_origin"] == "historical_unknown"]
    historical_confidences = [
        doc["confidence_normalized"]
        for doc in historical_docs
        if doc.get("confidence_normalized") is not None
    ]
    severity_counts = {"low": 0, "medium": 0, "high": 0}
    location_counts: dict[str, int] = {}
    verification_counts: dict[str, int] = {}
    repair_counts: dict[str, int] = {}
    for doc in docs:
        severity = "high" if doc.get("severity") == "critical" else doc.get("severity")
        if severity in severity_counts:
            severity_counts[severity] += 1
        location = doc.get("location")
        if isinstance(location, str) and location.strip():
            location_counts[location.strip()] = location_counts.get(location.strip(), 0) + 1
        elif isinstance(doc.get("road_name"), str) and doc["road_name"].strip():
            name = doc["road_name"].strip()
            location_counts[name] = location_counts.get(name, 0) + 1
        elif valid_coordinate(doc.get("latitude"), doc.get("longitude")):
            name = f"{float(doc['latitude']):.3f}, {float(doc['longitude']):.3f}"
            location_counts[name] = location_counts.get(name, 0) + 1
        verification_counts[doc["verification_status"]] = verification_counts.get(doc["verification_status"], 0) + 1
        repair_counts[doc["repair_status"]] = repair_counts.get(doc["repair_status"], 0) + 1
    total_records = potholes_collection.count_documents(scope)
    detection_events = detections_collection.count_documents(scope)
    image_upload_events = detections_collection.count_documents(
        {"source_id": "image_upload", **scope}
    )
    video_upload_events = detections_collection.count_documents(
        {"source_id": "video_upload", **scope}
    )
    live_camera_events = detections_collection.count_documents(
        {"source_id": {"$in": ["local-windows-webcam", "local-windows-agent"]}, **scope}
    )
    detection_counts = {
        "image": image_upload_events,
        "video": video_upload_events,
        "live": live_camera_events,
        "other": max(detection_events - image_upload_events - video_upload_events - live_camera_events, 0),
    }
    observations = sum(int(doc.get("observation_count") or 1) for doc in docs)
    unique_gps_locations = {
        (round(float(doc["latitude"]), 4), round(float(doc["longitude"]), 4))
        for doc in docs
        if doc.get("has_valid_coordinates")
    }
    events = list(detections_collection.find(scope))
    events_awaiting_gps_verification = sum(
        1
        for event in events
        if location_status(event) != "VERIFIED"
    )
    verified_potholes = sum(
        1
        for doc in docs
        if doc["verification_status_recorded"]
        and doc["verification_status"] == "verified"
    )
    return {
        "generated_at": now_utc().isoformat(),
        "data_source": "live_mongodb",
        "total_detection_events": detection_events,
        "verified_locations": sum(1 for doc in docs if location_status(doc) == "VERIFIED"),
        "unverified_locations": events_awaiting_gps_verification,
        "gps_accuracy_threshold_m": GPS_ACCURACY_THRESHOLD_M,
        "image_upload_detection_events": image_upload_events,
        "video_upload_detection_events": video_upload_events,
        "live_camera_detection_events": live_camera_events,
        "detection_counts": detection_counts,
        "total_pothole_records": total_records,
        "visible_pothole_records": total_records,
        "map_eligible_pothole_records": sum(
            1
            for doc in docs
            if (
                doc.get("has_valid_coordinates")
                and doc["verification_status"] == "verified"
                and location_status(doc) == "VERIFIED"
            )
        ),
        "historical_unknown_record_count": len(historical_docs),
        "source_recorded_count": total_records - len(historical_docs),
        "detections_awaiting_gps_verification": events_awaiting_gps_verification,
        "unique_gps_locations_approx": len(unique_gps_locations),
        "unique_location_precision": "Coordinates grouped to 4 decimal places (approximately 11 metres at the equator).",
        "observations_linked_to_existing_records": max(observations - total_records, 0),
        "candidate_records_awaiting_verification": sum(
            1
            for doc in docs
            if doc["verification_status_recorded"]
            and doc["verification_status"] == "pending_verification"
        ),
        "verified_pothole_records": verified_potholes,
        "awaiting_repair": sum(count for status, count in repair_counts.items() if status not in {"repaired", "unknown"}),
        "verified_repairs": repair_counts.get("repaired", 0),
        "average_confidence_percent": round(sum(valid_confidences) / len(valid_confidences) * 100, 2) if valid_confidences else None,
        "historical_unknown_average_confidence_percent": round(sum(historical_confidences) / len(historical_confidences) * 100, 2) if historical_confidences else None,
        "confidence_scope": "Stored pothole-record confidence; records without source metadata are historical/unknown and this is not model accuracy.",
        "valid_gps_records": sum(1 for doc in docs if doc.get("has_valid_coordinates")),
        "severity_counts": severity_counts,
        "location_distribution": dict(sorted(location_counts.items(), key=lambda entry: entry[1], reverse=True)[:20]),
        "verification_distribution": verification_counts,
        "repair_distribution": repair_counts,
        "confidence_note": "Confidence is model confidence for detections, not measured model accuracy.",
    }


@app.get("/api/analytics/trends", dependencies=[Depends(require_admin)])
def analytics_trends(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    monthly: dict[str, dict[str, Any]] = {}
    for event in detections_collection.find(owner_filter(user)):
        timestamp = event.get("timestamp")
        if timestamp is None:
            continue
        day = str(iso_or_none(timestamp))[:10]
        bucket = buckets.setdefault(
            day,
            {"date": day, "unique_potholes": set(), "observations": 0},
        )
        bucket["observations"] += 1
        month = day[:7]
        month_bucket = monthly.setdefault(month, {"month": month, "unique_potholes": set(), "observations": 0})
        month_bucket["observations"] += 1
        if event.get("pothole_id") is not None:
            bucket["unique_potholes"].add(event["pothole_id"])
            month_bucket["unique_potholes"].add(event["pothole_id"])
    trends = [
        {
            "date": day,
            "unique_potholes": len(bucket["unique_potholes"]),
            "observations": bucket["observations"],
        }
        for day, bucket in sorted(buckets.items())
    ]
    monthly_trends = [{"month": month, "unique_potholes": len(bucket["unique_potholes"]), "observations": bucket["observations"]} for month, bucket in sorted(monthly.items())]
    return {"trends": trends, "monthly_trends": monthly_trends}


@app.get("/api/reports/export.csv")
def export_csv(
    severity: str | None = None,
    verification_status: str | None = None,
    repair_status: str | None = None,
    source: str | None = None,
    search: str | None = None,
    location: Annotated[str | None, Query(max_length=120)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    import csv
    import io

    filters = {
        "severity": severity,
        "verification_status": verification_status,
        "repair_status": repair_status,
        "source": source,
        "search": search,
        "location": location,
        "date_from": date_from,
        "date_to": date_to,
    }
    records = fetch_report_records(user, filters)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["pothole_id", "latitude", "longitude", "confidence_percent", "verification_status", "repair_status", "source", "observation_count"])
    for item in records:
        writer.writerow([item.get("pothole_id"), item.get("latitude"), item.get("longitude"), item.get("confidence_percent"), item.get("verification_status"), item.get("repair_status"), item.get("source"), item.get("observation_count")])
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=roadwatch-potholes.csv"})


def fetch_report_records(user: dict[str, Any], filters: Mapping[str, Any]) -> list[dict[str, Any]]:
    query = build_filters(
        filters.get("severity"),
        filters.get("verification_status"),
        filters.get("repair_status"),
        filters.get("source"),
        filters.get("search"),
        filters.get("location"),
    )
    query.update(owner_filter(user))
    query.update(created_at_date_filter(filters.get("date_from"), filters.get("date_to")))
    cursor = potholes_collection.find(query).sort(
        [("last_seen_at", DESCENDING), ("created_at", DESCENDING)]
    )
    records = [serialize_pothole(document) for document in cursor]
    add_reporter_details(records, user)
    return records


def report_filter_labels(filters: Mapping[str, Any]) -> list[str]:
    labels = []
    if filters.get("location"):
        labels.append(f"Region/location: {filters['location']}")
    if filters.get("severity"):
        labels.append(f"Severity: {filters['severity']}")
    if filters.get("verification_status"):
        labels.append(f"Verification: {filters['verification_status']}")
    if filters.get("repair_status"):
        labels.append(f"Repair: {filters['repair_status']}")
    if filters.get("search"):
        labels.append(f"Search: {filters['search']}")
    if filters.get("source"):
        labels.append(f"Source: {filters['source']}")
    return labels


def report_date_range_label(date_from: date | None, date_to: date | None) -> str:
    if not date_from and not date_to:
        return "All available dates"
    return f"{date_from.isoformat() if date_from else 'Beginning'} – {date_to.isoformat() if date_to else 'Present'}"


def report_export_response(
    file_format: Literal["pdf", "xlsx"],
    records: list[dict[str, Any]],
    filters: Mapping[str, Any],
) -> Response:
    generated_at = now_utc()
    date_range = report_date_range_label(filters.get("date_from"), filters.get("date_to"))
    labels = report_filter_labels(filters)
    if file_format == "pdf":
        content = render_pdf(records, date_range=date_range, filters=labels, generated_at=generated_at)
        media_type = "application/pdf"
        filename = "roadwatch-potholes.pdf"
    else:
        content = render_xlsx(records, date_range=date_range, filters=labels, generated_at=generated_at)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "roadwatch-potholes.xlsx"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def next_scheduled_report_run(reference: datetime, frequency: str, anchor_day: int | None = None) -> datetime:
    current = reference.astimezone(timezone.utc)
    if frequency == "daily":
        return current + timedelta(days=1)
    if frequency == "weekly":
        return current + timedelta(days=7)
    if frequency != "monthly":
        raise ValueError("Unsupported report frequency.")
    month_index = current.month
    year = current.year + (1 if month_index == 12 else 0)
    month = 1 if month_index == 12 else month_index + 1
    day = min(anchor_day or current.day, calendar.monthrange(year, month)[1])
    return current.replace(year=year, month=month, day=day)


def report_schedule_owner_filter(user: dict[str, Any]) -> dict[str, Any]:
    if canonical_role(user.get("role")) in {"admin", "administrator"}:
        return {}
    return {"owner_id": user["_id"]}


def serialize_report_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(run["_id"]),
        "status": run.get("status", "completed"),
        "format": run.get("format"),
        "record_count": run.get("record_count", 0),
        "created_at": iso_or_none(run.get("created_at")),
        "expires_at": iso_or_none(run.get("expires_at")),
        "download_url": (
            f"/api/reports/schedules/{run['schedule_id']}/runs/{run['_id']}/download"
            if run.get("status") == "completed"
            else None
        ),
    }


def serialize_report_schedule(schedule: dict[str, Any], runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    filters = dict(schedule.get("filters") or {})
    return {
        "id": str(schedule["_id"]),
        "frequency": schedule.get("frequency"),
        "format": schedule.get("format"),
        "filters": mongo_to_json(filters),
        "status": schedule.get("status", "active"),
        "next_run_at": iso_or_none(schedule.get("next_run_at")),
        "last_run_at": iso_or_none(schedule.get("last_run_at")),
        "last_run_status": schedule.get("last_run_status"),
        "last_error": schedule.get("last_error"),
        "created_at": iso_or_none(schedule.get("created_at")),
        "runs": [serialize_report_run(run) for run in (runs or [])],
    }


def list_report_schedules(user: dict[str, Any]) -> list[dict[str, Any]]:
    owner_query = report_schedule_owner_filter(user)
    schedules = []
    for schedule in report_schedules_collection.find(owner_query).sort("created_at", DESCENDING):
        runs = list(
            report_runs_collection.find({"schedule_id": schedule["_id"]})
            .sort("created_at", DESCENDING)
            .limit(5)
        )
        schedules.append(serialize_report_schedule(schedule, runs))
    return schedules


def _schedule_report_filters(schedule: Mapping[str, Any]) -> dict[str, Any]:
    filters = dict(schedule.get("filters") or {})
    for field in ("date_from", "date_to"):
        value = filters.get(field)
        if isinstance(value, str) and value:
            filters[field] = date.fromisoformat(value)
    return filters


def _claim_due_report_schedule() -> dict[str, Any] | None:
    now = now_utc()
    return report_schedules_collection.find_one_and_update(
        {
            "status": {"$in": ["active", "running"]},
            "next_run_at": {"$lte": now},
            "$or": [
                {"lease_expires_at": {"$lte": now}},
                {"lease_expires_at": {"$exists": False}},
            ],
        },
        {
            "$set": {
                "status": "running",
                "lease_expires_at": now + timedelta(minutes=REPORT_SCHEDULER_LEASE_MINUTES),
            }
        },
        sort=[("next_run_at", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )


def _complete_scheduled_report(schedule: dict[str, Any]) -> None:
    owner = users_collection.find_one({"_id": schedule["owner_id"]})
    if not owner or not owner.get("is_active", True) or not owner.get("email_verified", True):
        report_schedules_collection.update_one(
            {"_id": schedule["_id"], "status": "running"},
            {
                "$set": {
                    "status": "paused",
                    "last_run_status": "skipped",
                    "last_error": "The schedule owner is inactive or unverified.",
                },
                "$unset": {"lease_expires_at": ""},
            },
        )
        return

    owner["role"] = canonical_role(owner.get("role"))
    filters = _schedule_report_filters(schedule)
    records = fetch_report_records(owner, filters)
    file_format = schedule["format"]
    response = report_export_response(file_format, records, filters)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    storage_filename = f"{uuid4().hex}.{file_format}"
    storage_path = REPORTS_DIR / storage_filename
    storage_path.write_bytes(response.body)
    generated_at = now_utc()
    expires_at = generated_at + timedelta(days=REPORT_RETENTION_DAYS)
    scope = "global" if owner["role"] in {"admin", "administrator"} else "user"
    try:
        run_result = report_runs_collection.insert_one(
            {
                "schedule_id": schedule["_id"],
                "owner_id": schedule["owner_id"],
                "format": file_format,
                "status": "completed",
                "record_count": len(records),
                "access_scope": scope,
                "storage_filename": storage_filename,
                "created_at": generated_at,
                "expires_at": expires_at,
            }
        )
    except Exception:
        try:
            storage_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not remove a report artifact after its run record failed.")
        raise

    report_schedules_collection.update_one(
        {"_id": schedule["_id"], "status": "running"},
        {
            "$set": {
                "status": "active",
                "last_run_at": generated_at,
                "last_run_status": "completed",
                "last_error": None,
                "next_run_at": next_scheduled_report_run(
                    generated_at,
                    schedule["frequency"],
                    schedule.get("anchor_day"),
                ),
            },
            "$unset": {"lease_expires_at": ""},
        },
    )
    if not run_result.inserted_id:
        raise RuntimeError("Scheduled report run was not recorded.")


def process_due_scheduled_reports() -> None:
    while True:
        schedule = _claim_due_report_schedule()
        if not schedule:
            return
        try:
            _complete_scheduled_report(schedule)
        except Exception:
            logger.exception("Scheduled report generation failed.")
            completed_at = now_utc()
            report_schedules_collection.update_one(
                {"_id": schedule["_id"], "status": "running"},
                {
                    "$set": {
                        "status": "active",
                        "last_run_at": completed_at,
                        "last_run_status": "failed",
                        "last_error": "Report generation failed. Check backend logs.",
                        "next_run_at": next_scheduled_report_run(
                            completed_at,
                            schedule["frequency"],
                            schedule.get("anchor_day"),
                        ),
                    },
                    "$unset": {"lease_expires_at": ""},
                },
            )


def cleanup_expired_report_runs() -> None:
    now = now_utc()
    for run in report_runs_collection.find({"expires_at": {"$lte": now}}):
        filename = str(run.get("storage_filename") or "")
        if filename and Path(filename).name == filename:
            try:
                (REPORTS_DIR / filename).unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove an expired report artifact.")
                continue
        report_runs_collection.delete_one({"_id": run["_id"]})


_report_scheduler_stop = threading.Event()
_report_scheduler_thread: threading.Thread | None = None


def _report_scheduler_loop() -> None:
    while not _report_scheduler_stop.wait(REPORT_SCHEDULER_POLL_SECONDS):
        try:
            process_due_scheduled_reports()
            cleanup_expired_report_runs()
        except PyMongoError:
            logger.exception("The scheduled report worker could not access MongoDB.")
        except Exception:
            logger.exception("The scheduled report worker encountered an unexpected error.")


def start_report_scheduler() -> None:
    global _report_scheduler_thread
    if _report_scheduler_thread and _report_scheduler_thread.is_alive():
        return
    _report_scheduler_stop.clear()
    _report_scheduler_thread = threading.Thread(
        target=_report_scheduler_loop,
        daemon=True,
        name="roadwatch-report-scheduler",
    )
    _report_scheduler_thread.start()


def stop_report_scheduler() -> None:
    _report_scheduler_stop.set()
    if _report_scheduler_thread and _report_scheduler_thread.is_alive():
        _report_scheduler_thread.join(timeout=5)


@app.get("/api/reports/schedules")
def get_report_schedules(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    schedules = list_report_schedules(user)
    return {"schedules": schedules, "total": len(schedules)}


@app.post("/api/reports/schedules", status_code=201)
def create_report_schedule(
    payload: ReportScheduleCreate,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    filters = payload.model_dump(exclude_none=True)
    filters.pop("frequency", None)
    filters.pop("format", None)
    for field in ("date_from", "date_to"):
        if isinstance(filters.get(field), date):
            filters[field] = filters[field].isoformat()
    now = now_utc()
    document = {
        "owner_id": user["_id"],
        "created_by_role": canonical_role(user.get("role")),
        "frequency": payload.frequency,
        "format": payload.format,
        "anchor_day": now.day,
        "filters": filters,
        "status": "active",
        "created_at": now,
        "next_run_at": next_scheduled_report_run(now, payload.frequency),
        "last_run_at": None,
        "last_run_status": None,
        "last_error": None,
    }
    result = report_schedules_collection.insert_one(document)
    saved = document | {"_id": result.inserted_id}
    return {"schedule": serialize_report_schedule(saved), "message": "Scheduled report created."}


@app.patch("/api/reports/schedules/{schedule_id}")
def update_report_schedule(
    schedule_id: str,
    payload: ReportScheduleAction,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    if not ObjectId.is_valid(schedule_id):
        raise HTTPException(status_code=404, detail="Scheduled report not found.")
    query = {"_id": ObjectId(schedule_id), **report_schedule_owner_filter(user)}
    schedule = report_schedules_collection.find_one(query)
    if not schedule:
        raise HTTPException(status_code=404, detail="Scheduled report not found.")
    if schedule.get("status") == "cancelled":
        raise HTTPException(status_code=409, detail="A cancelled report schedule cannot be changed.")
    if payload.action == "pause":
        if schedule.get("status") not in {"active", "running", "paused"}:
            raise HTTPException(status_code=409, detail="This report schedule cannot be paused in its current state.")
        updates = {"status": "paused"}
        message = "Scheduled report paused."
    else:
        if schedule.get("status") != "paused":
            raise HTTPException(status_code=409, detail="Only a paused report schedule can be resumed.")
        updates = {
            "status": "active",
            "next_run_at": next_scheduled_report_run(
                now_utc(),
                schedule["frequency"],
                schedule.get("anchor_day"),
            ),
        }
        message = "Scheduled report resumed."
    report_schedules_collection.update_one(query, {"$set": updates, "$unset": {"lease_expires_at": ""}})
    updated = report_schedules_collection.find_one(query) or (schedule | updates)
    return {"schedule": serialize_report_schedule(updated), "message": message}


@app.delete("/api/reports/schedules/{schedule_id}")
def cancel_report_schedule(
    schedule_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    if not ObjectId.is_valid(schedule_id):
        raise HTTPException(status_code=404, detail="Scheduled report not found.")
    query = {"_id": ObjectId(schedule_id), **report_schedule_owner_filter(user)}
    result = report_schedules_collection.update_one(
        query,
        {
            "$set": {"status": "cancelled", "cancelled_at": now_utc()},
            "$unset": {"lease_expires_at": ""},
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Scheduled report not found.")
    return {"message": "Scheduled report cancelled."}


@app.get("/api/reports/schedules/{schedule_id}/runs/{run_id}/download")
def download_scheduled_report(
    schedule_id: str,
    run_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> FileResponse:
    if not ObjectId.is_valid(schedule_id) or not ObjectId.is_valid(run_id):
        raise HTTPException(status_code=404, detail="Report download not found.")
    access_filter = report_schedule_owner_filter(user)
    schedule = report_schedules_collection.find_one({"_id": ObjectId(schedule_id), **access_filter})
    run = report_runs_collection.find_one(
        {"_id": ObjectId(run_id), "schedule_id": ObjectId(schedule_id), **access_filter}
    )
    if not schedule or not run or not run.get("expires_at") or run["expires_at"] <= now_utc():
        raise HTTPException(status_code=404, detail="Report download not found or expired.")
    if run.get("access_scope") == "global" and canonical_role(user.get("role")) not in {"admin", "administrator"}:
        raise HTTPException(status_code=403, detail="Admin access is required for this report.")
    filename = str(run.get("storage_filename") or "")
    if not filename or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Report download not found.")
    path = REPORTS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Report download not found or expired.")
    media_type = (
        "application/pdf"
        if run.get("format") == "pdf"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"roadwatch-potholes.{run.get('format', 'pdf')}",
    )


@app.get("/api/reports/export.pdf")
def export_pdf(
    severity: Severity | None = None,
    verification_status: VerificationStatus | None = None,
    repair_status: RepairStatus | None = None,
    source: str | None = None,
    search: str | None = None,
    location: Annotated[str | None, Query(max_length=120)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    filters = {
        "severity": severity,
        "verification_status": verification_status,
        "repair_status": repair_status,
        "source": source,
        "search": search,
        "location": location,
        "date_from": date_from,
        "date_to": date_to,
    }
    return report_export_response("pdf", fetch_report_records(user, filters), filters)


@app.get("/api/reports/export.xlsx")
def export_xlsx(
    severity: Severity | None = None,
    verification_status: VerificationStatus | None = None,
    repair_status: RepairStatus | None = None,
    source: str | None = None,
    search: str | None = None,
    location: Annotated[str | None, Query(max_length=120)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    filters = {
        "severity": severity,
        "verification_status": verification_status,
        "repair_status": repair_status,
        "source": source,
        "search": search,
        "location": location,
        "date_from": date_from,
        "date_to": date_to,
    }
    return report_export_response("xlsx", fetch_report_records(user, filters), filters)


@app.get("/api/admin/users")
def admin_users(_: dict[str, Any] = Depends(require_administrator)) -> dict[str, Any]:
    users = []
    safe_account_fields = {
        "full_name": 1,
        "email": 1,
        "role": 1,
        "requested_role": 1,
        "admin_request_status": 1,
        "is_active": 1,
        "email_verified": 1,
        "created_at": 1,
        "last_login": 1,
    }
    for user in users_collection.find({}, safe_account_fields):
        request_status = user.get("admin_request_status", "not_requested")
        masked_email = mask_email(user.get("email"))
        users.append({
            "id": str(user["_id"]),
            "full_name": user.get("full_name"),
            "email": masked_email,
            "email_masked": masked_email,
            "role": canonical_role(user.get("role")),
            "requested_role": user.get(
                "requested_role",
                "admin" if request_status in {"pending", "approved", "rejected"} else "user",
            ),
            "admin_request_status": request_status,
            "is_active": user.get("is_active", True),
            "email_verified": bool(user.get("email_verified", True)),
            "created_at": iso_or_none(user.get("created_at")),
            "last_login": iso_or_none(user.get("last_login")),
        })
    request_counts = {
        status: users_collection.count_documents({"admin_request_status": status})
        for status in ("pending", "approved", "rejected", "revoked")
    }
    return mongo_to_json({"users": users, "total": len(users), "admin_request_counts": request_counts})

@app.get("/api/admin/dashboard")
def admin_dashboard(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    dashboard = {
        "total_users": users_collection.count_documents({}),
        "active_users": users_collection.count_documents({"is_active": True}),
        "total_potholes": potholes_collection.count_documents({}),
        "total_detections": detections_collection.count_documents({}),
        "legacy_potholes": potholes_collection.count_documents({"user_id": {"$exists": False}}),
        "legacy_detections": detections_collection.count_documents({"user_id": {"$exists": False}}),
    }
    if canonical_role(user.get("role")) == "administrator":
        dashboard["pending_admin_requests"] = users_collection.count_documents(
            {"admin_request_status": "pending"}
        )
    return dashboard

@app.get("/api/admin/analytics")
def admin_analytics(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    contributions = []
    if canonical_role(admin.get("role")) == "administrator":
        for account in users_collection.find({}, {"full_name": 1, "email": 1, "role": 1}):
            contributions.append({"user_id": str(account["_id"]), "full_name": account.get("full_name"),
                "email": mask_email(account.get("email")), "email_masked": mask_email(account.get("email")),
                "role": canonical_role(account.get("role")),
                "potholes": potholes_collection.count_documents({"user_id": account["_id"]}),
                "detections": detections_collection.count_documents({"user_id": account["_id"]})})
    return {"summary": analytics_summary(admin), "trends": analytics_trends(admin), "user_contributions": contributions}

@app.put("/api/admin/users/{user_id}/status")
def admin_user_status(user_id: str, payload: dict[str, bool], admin: dict[str, Any] = Depends(require_administrator)) -> dict[str, Any]:
    if "is_active" not in payload or not isinstance(payload["is_active"], bool):
        raise HTTPException(status_code=422, detail="is_active must be a boolean.")
    if not ObjectId.is_valid(user_id): raise HTTPException(status_code=404, detail="User not found.")
    target = users_collection.find_one({"_id": ObjectId(user_id)})
    if not target: raise HTTPException(status_code=404, detail="User not found.")
    if target["_id"] == admin["_id"] and not payload["is_active"]: raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
    if canonical_role(target.get("role")) == "administrator":
        raise HTTPException(status_code=400, detail="Administrator accounts cannot be deactivated here.")
    users_collection.update_one({"_id": target["_id"]}, {"$set": {"is_active": payload["is_active"]}})
    return {"id": user_id, "is_active": payload["is_active"]}

@app.post("/api/admin/users/{user_id}/approve-admin")
def approve_admin_request(user_id: str, admin: dict[str, Any] = Depends(require_administrator)) -> dict[str, Any]:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=404, detail="User not found.")
    target_id = ObjectId(user_id)
    if target_id == admin["_id"]:
        raise HTTPException(status_code=400, detail="You cannot approve your own administrator request.")
    target = users_collection.find_one({"_id": target_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if canonical_role(target.get("role")) != "user":
        raise HTTPException(status_code=409, detail="Only User accounts can be approved for Admin access.")
    if not target.get("email_verified", True):
        raise HTTPException(status_code=409, detail="The account must verify its email before Admin access can be granted.")
    result = users_collection.update_one(
        {"_id": target_id, "admin_request_status": "pending", "role": target.get("role")},
        {"$set": {"role": "admin", "admin_request_status": "approved",
                  "requested_role": "admin", "admin_request_notice": "approved",
                  "admin_approved_by": admin["_id"], "admin_approved_at": now_utc()}},
    )
    if result.matched_count == 0:
        target = users_collection.find_one({"_id": target_id})
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        raise HTTPException(status_code=409, detail="No pending administrator request exists for this account.")
    administrator_name = admin.get("full_name") or "Administrator"
    create_notification(
        target_id,
        "admin_request",
        "approved",
        f"Your Admin access request was approved by {administrator_name}.",
        administrator=admin,
        details={"status": "approved"},
    )
    return {"id": user_id, "role": "admin", "admin_request_status": "approved"}

@app.post("/api/admin/users/{user_id}/reject-admin")
def reject_admin_request(user_id: str, admin: dict[str, Any] = Depends(require_administrator)) -> dict[str, Any]:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=404, detail="User not found.")
    target_id = ObjectId(user_id)
    if target_id == admin["_id"]:
        raise HTTPException(status_code=400, detail="You cannot reject your own administrator request.")
    target = users_collection.find_one({"_id": target_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if canonical_role(target.get("role")) != "user":
        raise HTTPException(status_code=409, detail="Only User accounts can be rejected for Admin access.")
    result = users_collection.update_one(
        {"_id": target_id, "admin_request_status": "pending", "role": target.get("role")},
        {"$set": {"role": "user", "admin_request_status": "rejected",
                  "requested_role": "admin", "admin_request_notice": "rejected",
                  "admin_rejected_by": admin["_id"], "admin_rejected_at": now_utc()}},
    )
    if result.matched_count == 0:
        target = users_collection.find_one({"_id": target_id})
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        raise HTTPException(status_code=409, detail="No pending administrator request exists for this account.")
    administrator_name = admin.get("full_name") or "Administrator"
    create_notification(
        target_id,
        "admin_request",
        "rejected",
        f"Your Admin access request was rejected by {administrator_name}. Your account remains a User account.",
        administrator=admin,
        details={"status": "rejected"},
    )
    return {"id": user_id, "role": "user", "admin_request_status": "rejected"}

@app.post("/api/admin/users/{user_id}/promote-admin")
def promote_user_to_admin(user_id: str, administrator: dict[str, Any] = Depends(require_administrator)) -> dict[str, Any]:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=404, detail="User not found.")
    target_id = ObjectId(user_id)
    if target_id == administrator["_id"]:
        raise HTTPException(status_code=400, detail="You cannot promote your own account.")
    target = users_collection.find_one({"_id": target_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if canonical_role(target.get("role")) != "user":
        raise HTTPException(status_code=409, detail="Only User accounts can be promoted to Admin.")
    if not target.get("email_verified", True):
        raise HTTPException(status_code=409, detail="The account must verify its email before Admin access can be granted.")
    promoted_at = now_utc()
    result = users_collection.update_one(
        {"_id": target_id, "role": target.get("role")},
        {"$set": {
            "role": "admin",
            "requested_role": "admin",
            "admin_request_status": "approved",
            "admin_request_notice": "approved",
            "admin_approved_by": administrator["_id"],
            "admin_approved_at": promoted_at,
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="This account's role changed. Refresh and try again.")
    administrator_name = administrator.get("full_name") or "Administrator"
    create_notification(
        target_id,
        "admin_request",
        "approved",
        f"Your account was promoted to Admin by {administrator_name}.",
        administrator=administrator,
        details={"status": "approved"},
    )
    return {"id": user_id, "role": "admin", "admin_request_status": "approved"}

@app.post("/api/admin/users/{user_id}/revoke-admin")
def revoke_admin_access(user_id: str, administrator: dict[str, Any] = Depends(require_administrator)) -> dict[str, Any]:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=404, detail="User not found.")
    target_id = ObjectId(user_id)
    target = users_collection.find_one({"_id": target_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if canonical_role(target.get("role")) != "admin":
        raise HTTPException(status_code=409, detail="Only Admin accounts can have Admin access revoked.")
    revoked_at = now_utc()
    result = users_collection.update_one(
        {"_id": target_id, "role": target.get("role")},
        {"$set": {
            "role": "user",
            "requested_role": "admin",
            "admin_request_status": "revoked",
            "admin_request_notice": "revoked",
            "admin_revoked_by": administrator["_id"],
            "admin_revoked_at": revoked_at,
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="This account's role changed. Refresh and try again.")
    administrator_name = administrator.get("full_name") or "Administrator"
    create_notification(
        target_id,
        "admin_request",
        "revoked",
        f"Your Admin privileges were revoked by {administrator_name}. Your account remains available with User access.",
        administrator=administrator,
        details={"status": "revoked"},
    )
    return {"id": user_id, "role": "user", "admin_request_status": "revoked"}

@app.delete("/api/admin/potholes/{pothole_id}")
def admin_delete_pothole(pothole_id: str, _: dict[str, Any] = Depends(require_admin)) -> dict[str, str]:
    normalized_id = pothole_id.upper() if POTHOLE_ID_PATTERN.fullmatch(pothole_id) else pothole_id
    query = {"pothole_id": int(pothole_id)} if pothole_id.isdigit() else {"pothole_id": normalized_id}
    if not potholes_collection.delete_one(query).deleted_count:
        raise HTTPException(status_code=404, detail="Pothole record not found.")
    return {"message": "Pothole record deleted."}

@app.get("/api/user/dashboard")
def user_dashboard(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    scope = owner_filter(user)
    potholes = [serialize_pothole(doc) for doc in potholes_collection.find(scope)]
    severity_counts = {"low": 0, "medium": 0, "high": 0}
    for pothole in potholes:
        severity = "high" if pothole.get("severity") == "critical" else pothole.get("severity")
        if severity in severity_counts:
            severity_counts[severity] += 1

    total_detections = detections_collection.count_documents(scope)
    image_detections = detections_collection.count_documents({"source_id": "image_upload", **scope})
    video_detections = detections_collection.count_documents({"source_id": "video_upload", **scope})
    live_detections = detections_collection.count_documents({
        "source_id": {"$in": ["local-windows-webcam", "local-windows-agent"]},
        **scope,
    })
    return {
        "total_potholes": potholes_collection.count_documents(scope),
        "severity_counts": severity_counts,
        "total_detections": total_detections,
        "detection_counts": {
            "image": image_detections,
            "video": video_detections,
            "live": live_detections,
            "other": max(total_detections - image_detections - video_detections - live_detections, 0),
        },
        "user": {"full_name": user.get("full_name")},
    }


try:
    from camera_stream import router as camera_router

    app.include_router(camera_router)
except Exception as error:
    print(f"Camera router unavailable: {error}")
