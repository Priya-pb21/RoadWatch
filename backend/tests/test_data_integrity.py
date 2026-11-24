import asyncio
import io
import threading
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import camera_stream
import pytest
from bson import ObjectId
from fastapi import HTTPException, UploadFile
from pymongo.errors import DuplicateKeyError, PyMongoError
from starlette.datastructures import Headers

from backend import main


class FakeCursor:
    def __init__(self, documents):
        self.documents = list(documents)

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def __iter__(self):
        return iter(self.documents)


class FakeCollection:
    def __init__(self):
        self.documents = []
        self.last_projection = None

    def insert_one(self, document):
        if "event_id" in document and any(
            item.get("event_id") == document["event_id"] for item in self.documents
        ):
            raise DuplicateKeyError("duplicate event_id")
        saved = deepcopy(document)
        saved["_id"] = ObjectId()
        self.documents.append(saved)
        return SimpleNamespace(inserted_id=saved["_id"])

    def find_one(self, query, *_args, **_kwargs):
        return next(
            (
                deepcopy(item)
                for item in self.documents
                if all(item.get(key) == value for key, value in query.items())
            ),
            None,
        )

    def find(self, query, projection=None, **_kwargs):
        self.last_projection = projection
        documents = self.documents
        if "latitude" in query and "longitude" in query:
            lat_range = query["latitude"]
            lon_range = query["longitude"]
            documents = [
                item
                for item in documents
                if isinstance(item.get("latitude"), (int, float))
                and isinstance(item.get("longitude"), (int, float))
                and lat_range["$gte"] <= item["latitude"] <= lat_range["$lte"]
                and lon_range["$gte"] <= item["longitude"] <= lon_range["$lte"]
            ]
        return FakeCursor(deepcopy(documents))

    def update_one(self, query, update):
        document = next(
            (
                item
                for item in self.documents
                if all(item.get(key) == value for key, value in query.items())
            ),
            None,
        )
        if document is None:
            return SimpleNamespace(matched_count=0)
        for key, value in update.get("$set", {}).items():
            document[key] = deepcopy(value)
        for key, value in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + value
        for key, value in update.get("$push", {}).items():
            document.setdefault(key, []).append(deepcopy(value))
        return SimpleNamespace(matched_count=1)

    def count_documents(self, _query):
        if not _query:
            return len(self.documents)
        return sum(
            1
            for document in self.documents
            if all(
                (
                    document.get(key) in value["$in"]
                    if isinstance(value, dict) and "$in" in value
                    else document.get(key) == value
                )
                for key, value in _query.items()
            )
        )


class FakePredictionBox:
    xyxy = [SimpleNamespace(tolist=lambda: [10.0, 12.0, 50.0, 42.0])]
    conf = [SimpleNamespace(item=lambda: 0.875)]
    cls = [SimpleNamespace(item=lambda: 0)]


class FakePrediction:
    names = {0: "pothole"}

    def __init__(self, image, boxes=None):
        self.boxes = boxes if boxes is not None else [FakePredictionBox()]
        self.image = image

    def plot(self):
        return self.image


class FakeImageModel:
    def __init__(self, boxes=None):
        self.boxes = boxes

    def predict(self, source, **_kwargs):
        return [FakePrediction(source, self.boxes)]


@pytest.fixture
def fake_collections(monkeypatch):
    potholes = FakeCollection()
    detections = FakeCollection()
    monkeypatch.setattr(main, "potholes_collection", potholes)
    monkeypatch.setattr(main, "detections_collection", detections)
    counter = iter(range(1, 10000))
    monkeypatch.setattr(main, "allocate_pothole_id", lambda: f"PH-{next(counter):03d}")
    return potholes, detections


@pytest.fixture(autouse=True)
def isolated_upload_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "UPLOADS_DIR", tmp_path)
    # Keep existing endpoint tests deterministic; dedicated tests below exercise
    # both accepted and rejected validator outcomes without loading model weights.
    accepted_scene = lambda _image, **_kwargs: {"is_road": True, "scene": "street", "confidence": 0.8, "road_scene_score": 0.9, "threshold": 0.2}
    monkeypatch.setattr(main, "validate_road_scene", accepted_scene)
    monkeypatch.setattr(camera_stream, "validate_road_scene", accepted_scene)


def make_upload(image_bytes):
    return UploadFile(
        filename="road.png",
        file=io.BytesIO(image_bytes),
        headers=Headers({"content-type": "image/png"}),
    )


def make_png():
    success, encoded = main.cv2.imencode(
        ".png",
        main.np.zeros((64, 64, 3), dtype=main.np.uint8),
    )
    assert success
    return encoded.tobytes()


def test_valid_coordinate_accepts_numeric_strings_and_rejects_invalid_values():
    assert main.valid_coordinate("16.02", "74.69")
    assert main.valid_coordinate(-90, 180)
    assert not main.valid_coordinate("", 74.69)
    assert not main.valid_coordinate(float("nan"), 74.69)
    assert not main.valid_coordinate(91, 74.69)


def test_gps_accuracy_threshold_marks_nonfinite_or_poor_readings_unreliable():
    assert not main.gps_accuracy_is_low(None)
    assert not main.gps_accuracy_is_low(100)
    assert main.gps_accuracy_is_low(100.1)
    assert main.gps_accuracy_is_low(float("nan"))


@pytest.mark.parametrize(
    ("record", "status", "reason"),
    [
        ({}, "UNVERIFIED", "GPS coordinates unavailable"),
        ({"latitude": 91, "longitude": 74, "gps_accuracy_m": 5}, "UNVERIFIED", "Invalid GPS coordinates"),
        ({"latitude": 16, "longitude": 74}, "UNVERIFIED", "GPS accuracy unavailable"),
        ({"latitude": 16, "longitude": 74, "gps_accuracy_m": 100}, "VERIFIED", None),
        ({"latitude": 16, "longitude": 74, "gps_accuracy_m": 100.1}, "UNVERIFIED", "GPS accuracy exceeds the allowed threshold"),
        ({"latitude": 16, "longitude": 74, "location_verification_method": "MANUAL"}, "VERIFIED", None),
    ],
)
def test_location_verification_status_and_reason(record, status, reason):
    assert main.location_status(record) == status
    assert main.location_reason(record) == reason


def test_exif_reader_handles_images_without_metadata():
    assert main.exif_location(make_png()) == (None, None, None, None)


@pytest.mark.parametrize(
    ("coordinates", "accuracy", "gps_error", "expected_status"),
    [
        ((16.02, 74.69), 24.5, None, "connected"),
        ((16.02, 74.69), 500, None, "low_accuracy"),
        ((16.02, 74.69), None, None, "connected"),
        ((None, None), None, None, "unavailable"),
        ((None, None), None, "Windows location access denied", "permission_error"),
    ],
)
def test_camera_reports_real_gps_quality_and_availability(
    monkeypatch, coordinates, accuracy, gps_error, expected_status
):
    import time

    monkeypatch.setattr(camera_stream, "camera_running", False)
    monkeypatch.setattr(camera_stream, "camera", None)
    monkeypatch.setattr(camera_stream, "latitude", coordinates[0])
    monkeypatch.setattr(camera_stream, "longitude", coordinates[1])
    monkeypatch.setattr(camera_stream, "accuracy", accuracy)
    monkeypatch.setattr(camera_stream, "last_gps_update_time", time.time())
    monkeypatch.setattr(camera_stream, "gps_status_error", gps_error)

    assert camera_stream.get_agent_status()["gps"]["status"] == expected_status


def test_detection_event_requires_coordinates_as_a_pair():
    with pytest.raises(ValueError, match="Provide both latitude and longitude"):
        main.DetectionEventCreate(
            latitude=16.02,
            confidence=0.8,
        )


def test_serialize_pothole_reads_geojson_coordinates_when_fields_are_missing():
    item = main.serialize_pothole(
        {
            "location": {"type": "Point", "coordinates": [74.69, 16.02]},
            "confidence": 0.8,
        }
    )

    assert item["longitude"] == 74.69
    assert item["latitude"] == 16.02
    assert item["has_valid_coordinates"]


def test_serialize_pothole_does_not_invent_a_source_for_legacy_records():
    item = main.serialize_pothole(
        {
            "pothole_id": 42,
            "latitude": 16.02,
            "longitude": 74.69,
            "confidence": 0.4735,
        }
    )

    assert item["source"] == "historical/unknown"
    assert item["record_origin"] == "historical_unknown"


def test_summary_identifies_historical_confidence_separately(fake_collections):
    potholes, detections = fake_collections
    potholes.documents.extend(
        [
            {
                "pothole_id": 1,
                "latitude": 16.02,
                "longitude": 74.69,
                "confidence": 0.4735,
            },
            {
                "pothole_id": 2,
                "latitude": 16.03,
                "longitude": 74.70,
                "confidence": 0.8,
                "source": "camera",
            },
        ]
    )
    detections.documents.extend(
        [
            {"source_id": "image_upload"},
            {"source_id": "local-windows-webcam"},
            {"source_id": "offline-video"},
        ]
    )

    summary = main.analytics_summary()

    assert summary["total_pothole_records"] == 2
    assert summary["historical_unknown_record_count"] == 1
    assert summary["source_recorded_count"] == 1
    assert summary["historical_unknown_average_confidence_percent"] == 47.35
    assert summary["average_confidence_percent"] == 63.68
    assert summary["total_detection_events"] == 3
    assert summary["image_upload_detection_events"] == 1
    assert summary["live_camera_detection_events"] == 1
    assert summary["candidate_records_awaiting_verification"] == 0
    assert summary["verified_pothole_records"] == 0
    assert summary["verification_distribution"]["unknown"] == 2
    assert summary["repair_distribution"]["unknown"] == 2
    assert "severity_distribution" not in summary
    assert "severity_trends" not in summary


def test_summary_counts_saved_severity_and_groups_critical_with_high(fake_collections):
    potholes, _ = fake_collections
    potholes.documents.extend(
        [
            {"pothole_id": "LOW-1", "severity": "low"},
            {"pothole_id": "MED-1", "severity": "medium"},
            {"pothole_id": "HIGH-1", "severity": "high"},
            {"pothole_id": "CRITICAL-1", "severity": "critical"},
        ]
    )

    summary = main.analytics_summary()

    assert summary["severity_counts"] == {"low": 1, "medium": 1, "high": 2}


def test_map_includes_only_verified_valid_gps_records(monkeypatch, fake_collections):
    potholes, _ = fake_collections
    potholes.documents.extend(
        [
            {
                "pothole_id": "LOW-ACCURACY",
                "latitude": 12.5,
                "longitude": 77.6,
                "gps_accuracy_m": 500,
            },
            {"pothole_id": "PENDING-ADMIN", "latitude": 12.55, "longitude": 77.65, "gps_accuracy_m": 10, "verification_status": "pending_verification"},
            {"pothole_id": "VERIFIED-GPS", "latitude": 12.6, "longitude": 77.7, "gps_accuracy_m": 10, "verification_status": "verified"},
            {"pothole_id": "MANUAL-EQUATOR", "latitude": 0, "longitude": 0, "location_verification_method": "MANUAL", "verification_status": "verified"},
            {"pothole_id": "INVALID", "latitude": 91, "longitude": 77.6},
        ]
    )
    monkeypatch.setattr(main, "users_collection", FakeCollection())

    result = main.get_pothole_map(user={"_id": ObjectId(), "role": "admin"})

    assert result["count"] == 2
    assert {record["pothole_id"] for record in result["records"]} == {"VERIFIED-GPS", "MANUAL-EQUATOR"}
    assert result["truncated"] is False
    assert potholes.last_projection["location_verification_method"] == 1


def test_detection_trends_do_not_treat_historical_records_as_new_events(fake_collections):
    potholes, detections = fake_collections
    potholes.documents.append(
        {
            "pothole_id": 1,
            "first_detected_at": main.now_utc(),
            "confidence": 0.7,
        }
    )

    assert main.analytics_trends()["trends"] == []

    detections.documents.append(
        {
            "event_id": str(uuid4()),
            "timestamp": main.now_utc(),
            "pothole_id": 1,
        }
    )
    trends = main.analytics_trends()["trends"]

    assert len(trends) == 1
    assert trends[0]["observations"] == 1
    assert trends[0]["unique_potholes"] == 1


def test_image_upload_saves_confirmed_yolo_event_and_updates_dashboard_data(
    fake_collections, monkeypatch
):
    potholes, detections = fake_collections
    monkeypatch.setattr(main, "get_image_model", lambda: FakeImageModel())
    monkeypatch.setattr(main, "users_collection", FakeCollection())
    submitting_user = {"_id": ObjectId(), "role": "user"}

    response = asyncio.run(
        main.analyze_uploaded_image(
            make_upload(make_png()),
            latitude=16.02,
            longitude=74.69,
            gps_accuracy_m=15,
            user=submitting_user,
        )
    )
    summary = main.analytics_summary()
    user_registry = main.get_potholes(page=1, page_size=25, user=submitting_user)
    admin_registry = main.get_potholes(page=1, page_size=25, user={"_id": ObjectId(), "role": "admin"})

    assert response["detection_count"] == 1
    assert response["detections"][0]["confidence"] == 0.875
    assert response["saved_detection_events"] == 1
    assert response["save_failures"] == 0
    assert len(response["annotated_image_base64"]) > 0
    assert len(detections.documents) == 1
    assert len(potholes.documents) == 1
    assert detections.documents[0]["user_id"] == submitting_user["_id"]
    assert potholes.documents[0]["user_id"] == submitting_user["_id"]
    assert potholes.documents[0]["verification_status"] == "pending_verification"
    assert potholes.documents[0]["repair_status"] == "not_started"
    assert potholes.documents[0]["verified_by"] is None
    assert potholes.documents[0]["verified_at"] is None
    assert potholes.documents[0]["rejection_reason"] is None
    assert summary["image_upload_detection_events"] == 1
    assert user_registry["total"] == 1
    assert admin_registry["total"] == 1
    assert user_registry["potholes"][0]["source"] == "image_upload"


def test_image_upload_returns_analysis_when_database_save_is_unconfirmed(monkeypatch):
    monkeypatch.setattr(main, "get_image_model", lambda: FakeImageModel())

    def fail_save(_event, **_kwargs):
        raise PyMongoError("database unavailable")

    monkeypatch.setattr(main, "process_detection", fail_save)
    response = asyncio.run(
        main.analyze_uploaded_image(
            make_upload(make_png()),
            latitude=16.02,
            longitude=74.69,
            gps_accuracy_m=15,
            user={"_id": ObjectId(), "role": "user"},
        )
    )

    assert response["detection_count"] == 1
    assert response["save_failures"] == 1
    assert response["saved_detection_events"] == 0
    assert response["persistence"][0]["error"] == "MongoDB did not confirm saving this detection."


def test_image_upload_returns_zero_when_yolo_has_no_boxes(monkeypatch):
    monkeypatch.setattr(main, "get_image_model", lambda: FakeImageModel(boxes=[]))

    response = asyncio.run(
        main.analyze_uploaded_image(
            make_upload(make_png()),
            latitude=None,
            longitude=None,
            gps_accuracy_m=None,
            user={"_id": ObjectId(), "role": "user"},
        )
    )

    assert response["detection_count"] == 0
    assert response["detections"] == []
    assert response["saved_detection_events"] == 0
    assert response["save_failures"] == 0


def test_nonroad_image_is_rejected_without_gps_or_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "validate_road_scene", lambda _image, **_kwargs: {"is_road": False, "scene": "cartoon", "confidence": 0.91})
    monkeypatch.setattr(main, "get_image_model", lambda: FakeImageModel(boxes=[]))
    monkeypatch.setattr(main, "exif_location", lambda *_args: (_ for _ in ()).throw(AssertionError("GPS must not be read")))

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.analyze_uploaded_image(make_upload(make_png()), latitude=16, longitude=74, user={"_id": ObjectId(), "role": "user"}))
    assert error.value.status_code == 422
    assert error.value.detail["message"] == main.INVALID_ROAD_IMAGE_MESSAGE
    assert list(tmp_path.iterdir()) == []


def test_nonroad_video_frames_are_rejected_without_persistence(monkeypatch, tmp_path):
    class FakeVideoCapture:
        def __init__(self):
            self.index = 0

        def isOpened(self):
            return True

        def read(self):
            self.index += 1
            if self.index > 10:
                return False, None
            return True, main.np.zeros((64, 64, 3), dtype=main.np.uint8)

        def get(self, property_id):
            return {
                main.cv2.CAP_PROP_FPS: 30,
                main.cv2.CAP_PROP_FRAME_WIDTH: 64,
                main.cv2.CAP_PROP_FRAME_HEIGHT: 64,
            }.get(property_id, 0)

        def release(self):
            pass

    monkeypatch.setattr(main.cv2, "VideoCapture", lambda *_args: FakeVideoCapture())
    monkeypatch.setattr(main, "validate_road_scene", lambda _frame, **_kwargs: {"is_road": False})
    monkeypatch.setattr(main, "get_image_model", lambda: FakeImageModel(boxes=[]))
    upload = UploadFile(filename="cartoon.mp4", file=io.BytesIO(b"fake video"), headers=Headers({"content-type": "video/mp4"}))

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.analyze_uploaded_video(upload, user={"_id": ObjectId(), "role": "user"}))
    assert error.value.status_code == 422
    assert error.value.detail["message"] == main.INVALID_ROAD_IMAGE_MESSAGE
    assert list(tmp_path.iterdir()) == []


def test_image_upload_without_gps_saves_unlocated_pothole_and_event(
    fake_collections, monkeypatch
):
    potholes, detections = fake_collections
    monkeypatch.setattr(main, "get_image_model", lambda: FakeImageModel())

    response = asyncio.run(
        main.analyze_uploaded_image(
            make_upload(make_png()),
            latitude=None,
            longitude=None,
            gps_accuracy_m=None,
            user={"_id": ObjectId(), "role": "user"},
        )
    )
    user = {"_id": detections.documents[0]["user_id"], "role": "user"}
    listed_events = main.get_detections(page=1, page_size=25, user=user)
    summary = main.analytics_summary(user)
    trends = main.analytics_trends(user)["trends"]
    registry = main.get_potholes(page=1, page_size=25, user=user)

    assert response["detection_count"] == 1
    assert response["saved_detection_events"] == 1
    assert response["persistence"][0]["saved_event"]
    assert len(potholes.documents) == 1
    assert potholes.documents[0]["pothole_id"] == "PH-001"
    assert not potholes.documents[0].get("location")
    assert len(detections.documents) == 1
    assert detections.documents[0]["source_id"] == "image_upload"
    assert detections.documents[0]["deduplication"] == "created_new_missing_coordinates"
    assert detections.documents[0]["pothole_id"] == "PH-001"
    assert listed_events["total"] == 1
    assert listed_events["detections"][0]["latitude"] is None
    assert summary["total_detection_events"] == 1
    assert summary["image_upload_detection_events"] == 1
    assert len(trends) == 1
    assert trends[0]["observations"] == 1
    assert registry["total"] == 1


def test_manual_location_verification_updates_event_and_writes_audit(fake_collections, monkeypatch):
    potholes, detections = fake_collections
    admin = {"_id": ObjectId(), "full_name": "Review Admin", "role": "admin"}
    event_id = "image-upload-manual-test"
    detections.insert_one({
        "event_id": event_id,
        "source_id": "image_upload",
        "timestamp": main.now_utc(),
        "confidence": 0.95,
        "latitude": None,
        "longitude": None,
        "gps_accuracy_m": None,
        "image_reference": "/uploads/original.jpg",
        "annotated_image_reference": "/uploads/annotated.jpg",
    })

    result = main.verify_detection_location(
        event_id,
        main.LocationVerification(latitude=16.02, longitude=74.69, administrator_id="spoofed-admin"),
        user=admin,
    )

    assert result["detection"]["location_verification_status"] == "VERIFIED"
    assert result["detection"]["location_verification_method"] == "MANUAL"
    assert result["pothole"]["latitude"] == 16.02
    assert result["pothole"]["verification_status"] == "pending_verification"
    assert result["pothole"]["location_verification_status"] == "VERIFIED"
    assert len(potholes.documents) == 1
    audit = detections.documents[0]["location_verification_audit"][0]
    assert audit["previous"]["location_verification_status"] == "UNVERIFIED"
    assert audit["new"]["latitude"] == 16.02
    assert audit["administrator_id"] == str(admin["_id"])
    assert audit["administrator_name"] == "Review Admin"

    main.verify_detection_location(
        event_id,
        main.LocationVerification(latitude=16.03, longitude=74.70),
        user=admin,
    )
    correction_audit = detections.documents[0]["location_verification_audit"][1]
    assert correction_audit["previous"]["latitude"] == 16.02
    assert correction_audit["previous"]["longitude"] == 74.69
    assert correction_audit["new"]["latitude"] == 16.03
    assert correction_audit["new"]["longitude"] == 74.70
    assert len(potholes.documents) == 1


def test_invalid_upload_coordinates_do_not_block_yolo_event_save(monkeypatch, fake_collections):
    monkeypatch.setattr(main, "get_image_model", lambda: FakeImageModel())
    _potholes, detections = fake_collections
    response = asyncio.run(
        main.analyze_uploaded_image(
            make_upload(make_png()),
            latitude=91,
            longitude=74,
            gps_accuracy_m=None,
            user={"_id": ObjectId(), "role": "user"},
        )
    )
    assert response["saved_detection_events"] == 1
    assert response["location_verification_status"] == "UNVERIFIED"
    assert response["location_verification_reason"] == "Invalid GPS coordinates"
    assert detections.documents[0]["latitude"] is None
    assert detections.documents[0]["confidence"] == pytest.approx(0.875)


def test_repeated_legacy_posts_link_without_inflating_detection_event_count(fake_collections):
    potholes, detections = fake_collections
    first = main.create_pothole(
        main.LegacyPotholeCreate(pothole_id=1, latitude=16.02, longitude=74.69, confidence=0.7)
    )
    second = main.create_pothole(
        main.LegacyPotholeCreate(pothole_id=2, latitude=16.02, longitude=74.69, confidence=0.8)
    )

    assert len(potholes.documents) == 1
    assert len(detections.documents) == 0
    assert first["pothole"]["pothole_id"] == second["pothole"]["pothole_id"]
    assert second["pothole"]["observation_count"] == 2
    assert second["pothole"]["confidence"] == 0.8


def test_replaying_detection_event_id_does_not_create_another_record(fake_collections):
    potholes, detections = fake_collections
    event = main.DetectionEventCreate(
        event_id=str(uuid4()),
        latitude=16.02,
        longitude=74.69,
        confidence=0.7,
    )

    user = {"_id": ObjectId(), "role": "user"}
    first = main.create_detection(event, user=user)
    replay = main.create_detection(event, user=user)

    assert len(potholes.documents) == 1
    assert len(detections.documents) == 1
    assert first["pothole"]["pothole_id"] == replay["event"]["pothole_id"]
    assert replay["duplicate"]


def test_low_accuracy_event_is_saved_as_unverified_location_record(fake_collections):
    potholes, detections = fake_collections
    result = main.create_detection(
        main.DetectionEventCreate(
            event_id=str(uuid4()),
            latitude=16.02,
            longitude=74.69,
            gps_accuracy_m=300,
            confidence=0.7,
        ),
        user={"_id": ObjectId(), "role": "user"},
    )

    assert len(potholes.documents) == 1
    assert len(detections.documents) == 1
    assert result["pothole"]["pothole_id"] == "PH-001"
    assert detections.documents[0]["deduplication"] == "created_new_low_gps_accuracy"


@pytest.mark.parametrize(
    ("coordinates", "accuracy", "deduplication", "box_count"),
    [
        ((None, None), None, "created_new_missing_coordinates", 2),
        ((16.02, 74.69), 500.0, "created_new_low_gps_accuracy", 1),
    ],
)
def test_live_camera_persists_events_without_reliable_gps(
    fake_collections, monkeypatch, coordinates, accuracy, deduplication, box_count
):
    import time

    potholes, detections = fake_collections

    class FakeCapture:
        def __init__(self):
            self.released = False

        def isOpened(self):
            return True

        def read(self):
            if self.released:
                return False, None
            time.sleep(0.01)
            return True, main.np.zeros((64, 64, 3), dtype=main.np.uint8)

        def release(self):
            self.released = True

    class FakeResponse:
        ok = True
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    capture = FakeCapture()
    saved = threading.Event()
    monkeypatch.setattr(camera_stream, "camera_running", False)
    monkeypatch.setattr(camera_stream, "camera", None)
    monkeypatch.setattr(camera_stream, "camera_worker_thread", None)
    boxes = [FakePredictionBox() for _ in range(box_count)]
    monkeypatch.setattr(camera_stream, "model", FakeImageModel(boxes=boxes))
    monkeypatch.setattr(camera_stream.cv2, "VideoCapture", lambda *_args: capture)
    has_coordinates = coordinates[0] is not None
    monkeypatch.setattr(
        camera_stream,
        "latitude",
        coordinates[0] if has_coordinates else 16.02,
    )
    monkeypatch.setattr(
        camera_stream,
        "longitude",
        coordinates[1] if has_coordinates else 74.69,
    )
    monkeypatch.setattr(
        camera_stream,
        "accuracy",
        accuracy if has_coordinates else 5.0,
    )
    monkeypatch.setattr(
        camera_stream,
        "last_location_time",
        time.time() if has_coordinates else 0,
    )
    monkeypatch.setattr(camera_stream, "last_detection_time", 0)
    monkeypatch.setattr(camera_stream, "last_frame_time", 0)
    monkeypatch.setattr(camera_stream, "last_inference_time", 0)
    monkeypatch.setattr(
        camera_stream,
        "last_gps_update_time",
        time.time() if coordinates[0] is not None else 0,
    )
    monkeypatch.setattr(camera_stream, "last_saved_detection_at", None)
    monkeypatch.setattr(camera_stream, "last_saved_detection_confidence", None)
    monkeypatch.setattr(camera_stream, "last_save_error", None)
    monkeypatch.setattr(camera_stream, "last_save_status", "camera_stopped")
    monkeypatch.setattr(camera_stream, "agent_error", None)
    monkeypatch.setattr(camera_stream, "gps_status_error", None)
    if not has_coordinates:
        async def denied_location():
            raise PermissionError("Windows location access denied")

        monkeypatch.setattr(camera_stream, "get_location", denied_location)
    else:
        async def available_location():
            return coordinates[0], coordinates[1], accuracy

        monkeypatch.setattr(camera_stream, "get_location", available_location)
    monkeypatch.setattr(main, "INGEST_API_KEY", "test-ingest-key")
    monkeypatch.setattr(camera_stream, "INGEST_API_KEY", "test-ingest-key")

    def save_event(_url, *, json, headers, **_kwargs):
        main.require_ingest(headers.get("X-Roadwatch-Ingest-Key"))
        result = main.process_detection(main.DetectionEventCreate.model_validate(json))
        saved.set()
        return FakeResponse(result)

    monkeypatch.setattr(camera_stream.requests, "post", save_event)
    assert camera_stream.start_camera("http://127.0.0.1:8000/api/detections") == "running"
    assert saved.wait(timeout=2)
    deadline = time.monotonic() + 2
    while (
        camera_stream.last_saved_detection_count != box_count
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)

    assert camera_stream.last_save_status in {
        "saved",
        "already_saved_for_current_detection",
    }
    assert len(detections.documents) == box_count
    assert len(potholes.documents) == box_count
    assert camera_stream.last_saved_detection_count == box_count
    assert all(event["source_id"] == "local-windows-webcam" for event in detections.documents)
    assert all(event["deduplication"] == deduplication for event in detections.documents)
    assert all(event["latitude"] == coordinates[0] for event in detections.documents)
    assert all(event["longitude"] == coordinates[1] for event in detections.documents)
    assert all(event["gps_accuracy_m"] == accuracy for event in detections.documents)
    assert main.analytics_summary()["live_camera_detection_events"] == box_count
    assert camera_stream.stop_camera() == "stopped"
    assert capture.released


def test_camera_start_is_idempotent_and_stop_releases_capture(monkeypatch):
    started = threading.Event()
    release_worker = threading.Event()

    class FakeCapture:
        released = False

        def isOpened(self):
            return True

        def read(self):
            return True, main.np.zeros((64, 64, 3), dtype=main.np.uint8)

        def release(self):
            self.released = True

    capture = FakeCapture()
    created_captures = []

    def make_capture(*_args):
        created_captures.append(capture)
        return capture

    def fake_worker(_capture, _url, *_args):
        started.set()
        release_worker.wait(timeout=1)

    monkeypatch.setattr(camera_stream, "camera_running", False)
    monkeypatch.setattr(camera_stream, "camera", None)
    monkeypatch.setattr(camera_stream, "camera_worker_thread", None)
    monkeypatch.setattr(camera_stream, "load_model", lambda: None)
    monkeypatch.setattr(camera_stream.cv2, "VideoCapture", make_capture)
    monkeypatch.setattr(camera_stream, "camera_worker", fake_worker)

    assert camera_stream.start_camera("http://127.0.0.1:8000/api/detections") == "running"
    assert started.wait(timeout=1)
    assert camera_stream.start_camera() == "already_running"
    assert len(created_captures) == 1

    release_worker.set()
    assert camera_stream.stop_camera() == "stopped"
    assert capture.released


@pytest.mark.parametrize("backend_ok", [True, False])
def test_camera_worker_streams_frame_and_submits_only_once_per_detection_episode(
    monkeypatch, backend_ok
):
    class FakeCapture:
        def __init__(self):
            self.released = False

        def isOpened(self):
            return True

        def read(self):
            if self.released:
                return False, None
            import time

            time.sleep(0.01)
            return True, main.np.zeros((64, 64, 3), dtype=main.np.uint8)

        def release(self):
            self.released = True

    class FakeResponse:
        def __init__(self):
            self.ok = backend_ok
            self.status_code = 503

        def json(self):
            return {"message": "Detection event saved"}

    capture = FakeCapture()
    sent_payloads = []
    monkeypatch.setattr(camera_stream, "camera_running", False)
    monkeypatch.setattr(camera_stream, "camera", None)
    monkeypatch.setattr(camera_stream, "camera_worker_thread", None)
    monkeypatch.setattr(camera_stream, "model", FakeImageModel())
    monkeypatch.setattr(camera_stream.cv2, "VideoCapture", lambda *_args: capture)
    monkeypatch.setattr(camera_stream, "latitude", 16.02)
    monkeypatch.setattr(camera_stream, "longitude", 74.69)
    monkeypatch.setattr(camera_stream, "accuracy", 12.0)
    monkeypatch.setattr(camera_stream, "last_location_time", __import__("time").time())
    monkeypatch.setattr(camera_stream, "last_detection_time", 0)
    monkeypatch.setattr(camera_stream, "last_frame_time", 0)
    monkeypatch.setattr(camera_stream, "last_inference_time", 0)
    monkeypatch.setattr(camera_stream, "last_gps_update_time", __import__("time").time())
    monkeypatch.setattr(camera_stream, "agent_error", None)
    monkeypatch.setattr(camera_stream, "gps_status_error", None)

    async def available_location():
        return 16.02, 74.69, 12.0

    monkeypatch.setattr(camera_stream, "get_location", available_location)

    def save_event(url, *, json, **_kwargs):
        sent_payloads.append((url, json))
        return FakeResponse()

    monkeypatch.setattr(camera_stream.requests, "post", save_event)
    assert camera_stream.start_camera("http://127.0.0.1:8000/api/detections") == "running"

    import time

    deadline = time.monotonic() + 2
    while camera_stream.last_save_status not in {"saved", "save_failed"} and time.monotonic() < deadline:
        time.sleep(0.01)

    assert camera_stream.last_detection_count == 1
    assert camera_stream.last_detection_confidence == 0.875
    assert len(sent_payloads) == 1
    assert sent_payloads[0][0] == "http://127.0.0.1:8000/api/detections"
    assert sent_payloads[0][1]["source_id"] == "local-windows-webcam"
    if backend_ok:
        assert camera_stream.last_saved_detection_at is not None
        assert camera_stream.last_save_status in {"saved", "already_saved_for_current_detection"}
    else:
        assert camera_stream.last_saved_detection_at is None
        assert camera_stream.last_save_status == "save_failed"
        assert camera_stream.last_save_error == "Backend rejected detection event (HTTP 503)."
    time.sleep(0.1)
    assert len(sent_payloads) == 1

    stream_chunk = next(camera_stream.generate_frames())
    assert stream_chunk.startswith(b"--frame\r\nContent-Type: image/jpeg")

    assert camera_stream.stop_camera() == "stopped"
    assert capture.released
