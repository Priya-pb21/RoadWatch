from ultralytics import YOLO
import cv2
import threading
import time
import asyncio
import requests
import os
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import StreamingResponse
from winrt.windows.devices.geolocation import Geolocator, PositionAccuracy
from backend.road_scene import validate_road_scene

# -----------------------------------
# CONFIGURATION
# -----------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
MODEL_PATH = os.getenv(
    "ROADWATCH_MODEL_PATH",
    str(PROJECT_ROOT / "runs" / "pothole_yolo11n" / "weights" / "best.pt")
)
API_URL = os.getenv(
    "ROADWATCH_INGEST_URL",
    "http://127.0.0.1:8000/api/detections"
)
INGEST_API_KEY = os.getenv("ROADWATCH_INGEST_API_KEY")


def camera_current_user(
    request: Request,
    access_token: str | None = Query(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
):
    """Use the application's auth and shared MongoDB client for camera APIs."""
    from backend.main import get_current_user

    if credentials is None and access_token:
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=access_token,
        )
    return get_current_user(request, credentials)


CONFIDENCE_THRESHOLD = float(
    os.getenv("ROADWATCH_CONFIDENCE_THRESHOLD", "0.25")
)
DETECTION_COOLDOWN = 10
GPS_MAX_MAP_ACCURACY_M = 100
GPS_FRESH_SECONDS = 15

router = APIRouter()

camera = None
model = None

camera_lock = threading.Lock()
state_lock = threading.Lock()
camera_worker_thread = None

camera_running = False
latest_frame = None

latitude = None
longitude = None
accuracy = None

last_location_time = 0
last_detection_time = 0
pothole_id = int(time.time())
last_frame_time = 0.0
last_inference_time = 0.0
last_gps_update_time = 0.0
last_detection_count = 0
last_detection_confidence = None
last_detection_at = None
last_saved_detection_at = None
last_saved_detection_confidence = None
last_saved_detection_count = 0
last_save_error = None
last_save_status = "camera_stopped"
gps_status_error = None
agent_error = None
road_scene_status = "waiting_for_frame"


# -----------------------------------
# LOAD MODEL ONCE
# -----------------------------------

def load_model():
    global model

    if model is None:
        if not Path(MODEL_PATH).exists():
            raise RuntimeError(f"YOLO model file not found: {MODEL_PATH}")

        print("Loading YOLO model...")
        model = YOLO(MODEL_PATH)
        print("YOLO model loaded successfully!")


def valid_gps_coordinates(lat, lon):
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return False
    return (
        isfinite(lat)
        and isfinite(lon)
        and -90 <= lat <= 90
        and -180 <= lon <= 180
    )


def normalize_gps_accuracy(value):
    try:
        accuracy_value = float(value)
    except (TypeError, ValueError):
        return None
    return accuracy_value if isfinite(accuracy_value) and accuracy_value >= 0 else None


# -----------------------------------
# GET WINDOWS GPS
# -----------------------------------

async def get_location():
    global gps_status_error

    try:
        locator = Geolocator()
        locator.desired_accuracy = PositionAccuracy.HIGH
        position = await locator.get_geoposition_async()

        coordinates = position.coordinate.point.position
        gps_status_error = None

        return (
            coordinates.latitude,
            coordinates.longitude,
            position.coordinate.accuracy,
        )

    except Exception as error:
        print("GPS error:", error)
        gps_status_error = str(error)
        return None, None, None


# -----------------------------------
# START CAMERA
# -----------------------------------

def start_camera(ingest_url=None, access_token=None):
    global camera, camera_running, camera_worker_thread
    global latest_frame, last_detection_count, last_detection_confidence
    global last_detection_at, last_saved_detection_at
    global last_saved_detection_confidence, last_saved_detection_count
    global last_save_error, last_save_status
    global agent_error
    global latitude, longitude, accuracy
    global last_location_time, last_frame_time, last_inference_time
    global last_gps_update_time, gps_status_error, road_scene_status

    with camera_lock:
        if camera_running:
            return "already_running"
        if camera_worker_thread is not None and camera_worker_thread.is_alive():
            raise RuntimeError("The previous camera worker is still stopping.")

        load_model()

        capture = None
        first_frame = None
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
            candidate = cv2.VideoCapture(0, backend)
            if candidate.isOpened():
                ok, probe_frame = candidate.read()
                if ok and probe_frame is not None:
                    capture = candidate
                    first_frame = probe_frame
                    break
            candidate.release()

        if capture is None:
            raise RuntimeError(
                "Could not open and read frames from camera 0. Check Windows "
                "camera privacy permission, close other apps using the camera, "
                "and verify that the webcam is connected."
            )

        camera = capture
        camera_running = True
        agent_error = None
        last_save_error = None
        last_detection_count = 0
        last_detection_confidence = None
        last_frame_time = 0.0
        last_inference_time = 0.0
        last_gps_update_time = 0.0
        last_location_time = 0.0
        latitude = None
        longitude = None
        accuracy = None
        gps_status_error = None
        road_scene_status = "waiting_for_frame"
        last_detection_at = None
        last_saved_detection_at = None
        last_saved_detection_confidence = None
        last_saved_detection_count = 0
        last_save_status = "waiting_for_frame"
        with state_lock:
            latest_frame = None
        camera_worker_thread = threading.Thread(
            target=camera_worker,
            args=(capture, ingest_url or API_URL, access_token, first_frame),
            daemon=True,
            name="roadwatch-camera-worker",
        )
        camera_worker_thread.start()
        return "running"


# -----------------------------------
# CAMERA + YOLO WORKER
# -----------------------------------

def camera_worker(capture, ingest_url, access_token=None, initial_frame=None):
    global latest_frame
    global latitude, longitude, accuracy
    global last_location_time, last_detection_time
    global pothole_id, camera_running, camera
    global last_frame_time, last_inference_time, last_gps_update_time
    global agent_error, gps_status_error
    global last_detection_count, last_detection_confidence, last_detection_at
    global last_saved_detection_at, last_saved_detection_confidence
    global last_saved_detection_count, last_save_error
    global last_save_status
    global road_scene_status

    detection_episode_saved = False
    detection_episode_submitted = False
    detection_episode_failed = False
    detection_clear_since = None
    last_scene_check = 0.0
    scene_is_road = False
    yolo_road_fallback_until = 0.0
    try:
        while camera_running:
            if initial_frame is not None:
                success, frame = True, initial_frame
                initial_frame = None
            else:
                success, frame = capture.read()

            if not success:
                if camera_running:
                    raise RuntimeError("Camera stopped returning frames.")
                break

            current_time = time.time()

            # Update GPS periodically.
            if current_time - last_location_time >= 5:
                try:
                    new_lat, new_lon, new_accuracy = asyncio.run(
                        get_location()
                    )

                    if (
                        valid_gps_coordinates(new_lat, new_lon)
                    ):
                        latitude = float(new_lat)
                        longitude = float(new_lon)
                        accuracy = normalize_gps_accuracy(new_accuracy)
                        last_gps_update_time = current_time
                        gps_status_error = None
                    else:
                        latitude = None
                        longitude = None
                        accuracy = None
                        gps_status_error = "GPS did not return valid coordinates."

                except Exception as error:
                    print("GPS update error:", error)
                    latitude = None
                    longitude = None
                    accuracy = None
                    gps_status_error = str(error)

                last_location_time = current_time

            # Road-scene classification precedes YOLO. It runs at most once a
            # second, rather than once per frame, and fails closed on errors.
            fallback_results = None
            if current_time - last_scene_check >= 1.0:
                last_scene_check = current_time
                try:
                    scene_is_road = bool(validate_road_scene(frame, source="live_camera", log_diagnostics=False).get("is_road"))
                    road_scene_status = "road" if scene_is_road else "not_road"
                    if scene_is_road:
                        yolo_road_fallback_until = 0.0
                        agent_error = None
                except Exception as error:
                    scene_is_road = False
                    yolo_road_fallback_until = 0.0
                    road_scene_status = "unavailable"
                    agent_error = f"Road-scene validation failed: {error}"

                # A sampled live frame may be a close-up pothole surface that
                # Places365 classifies as terrain. Reuse YOLO as the narrow
                # pothole fallback, matching image/video upload behavior.
                if road_scene_status == "not_road":
                    fallback_results = model.predict(
                        frame,
                        imgsz=640,
                        conf=CONFIDENCE_THRESHOLD,
                        verbose=False,
                    )
                    fallback_boxes = fallback_results[0].boxes
                    if fallback_boxes is not None and len(fallback_boxes) > 0:
                        scene_is_road = True
                        road_scene_status = "road_yolo_fallback"
                        yolo_road_fallback_until = current_time + 1.5

            if not scene_is_road and current_time < yolo_road_fallback_until:
                scene_is_road = True
                road_scene_status = "road_yolo_fallback"

            if not scene_is_road:
                with state_lock:
                    latest_frame = frame
                    last_frame_time = current_time
                    last_detection_count = 0
                    last_detection_confidence = None
                    last_save_status = "invalid_road_scene" if road_scene_status == "not_road" else "road_validation_unavailable"
                if detection_clear_since is None:
                    detection_clear_since = current_time
                elif current_time - detection_clear_since >= 3:
                    detection_episode_saved = False
                    detection_episode_submitted = False
                    detection_episode_failed = False
                continue

            # YOLO only sees frames after the road-scene gate accepts the view.
            results = fallback_results or model.predict(
                frame,
                imgsz=640,
                conf=CONFIDENCE_THRESHOLD,
                verbose=False,
            )
            last_inference_time = current_time
            agent_error = None

            result = results[0]
            annotated = result.plot()
            boxes = result.boxes
            detection_count = len(boxes) if boxes is not None else 0
            confidence = (
                max(float(box.conf[0].item()) for box in boxes)
                if detection_count
                else None
            )
            detection_at = datetime.now(timezone.utc) if detection_count else None
            with state_lock:
                last_detection_count = detection_count
                last_detection_confidence = confidence
                if detection_at is not None:
                    last_detection_at = detection_at.isoformat()
                if detection_count == 0:
                    if detection_clear_since is None:
                        detection_clear_since = current_time
                    elif current_time - detection_clear_since >= 3:
                        detection_episode_saved = False
                        detection_episode_submitted = False
                        detection_episode_failed = False
                    last_save_status = "no_detection"
                else:
                    detection_clear_since = None
                    if detection_episode_saved and last_save_error:
                        last_save_status = "partially_saved"
                    elif detection_episode_saved:
                        last_save_status = "already_saved_for_current_detection"
                    elif detection_episode_failed:
                        last_save_status = "save_failed"
                    elif detection_episode_submitted:
                        last_save_status = "awaiting_backend_confirmation"
                    elif not valid_gps_coordinates(latitude, longitude):
                        last_save_status = "ready_to_save_without_gps"
                    elif accuracy is None:
                        last_save_status = "gps_accuracy_unavailable"
                    elif accuracy > GPS_MAX_MAP_ACCURACY_M:
                        last_save_status = "ready_to_save_low_accuracy"
                    elif current_time - last_detection_time < DETECTION_COOLDOWN:
                        last_save_status = "cooldown"
                    else:
                        last_save_status = "ready_to_save"

            # Save each continuous detection episode; GPS quality only affects mapping.
            if detection_count:
                if (
                    not detection_episode_saved
                    and not detection_episode_submitted
                    and current_time - last_detection_time
                    >= DETECTION_COOLDOWN
                ):
                    frame_height, frame_width = frame.shape[:2]
                    detection_episode_submitted = True
                    saved_confidences = []
                    save_errors = []
                    with state_lock:
                        last_save_error = None
                        last_save_status = "awaiting_backend_confirmation"
                    headers = {}
                    if INGEST_API_KEY:
                        headers["X-Roadwatch-Ingest-Key"] = INGEST_API_KEY
                    if access_token:
                        headers["Authorization"] = f"Bearer {access_token}"

                    for detection_box in sorted(
                        boxes,
                        key=lambda box: float(box.conf[0].item()),
                        reverse=True,
                    ):
                        box_confidence = float(detection_box.conf[0].item())
                        x1, y1, x2, y2 = [
                            float(value)
                            for value in detection_box.xyxy[0].tolist()
                        ]
                        payload = {
                            "event_id": str(uuid4()),
                            "source_id": "local-windows-webcam",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "confidence": round(box_confidence, 4),
                            "bounding_box": {
                                "x": round(x1 / frame_width, 5),
                                "y": round(y1 / frame_height, 5),
                                "width": round((x2 - x1) / frame_width, 5),
                                "height": round((y2 - y1) / frame_height, 5),
                            },
                        }
                        if valid_gps_coordinates(latitude, longitude):
                            payload["latitude"] = float(latitude)
                            payload["longitude"] = float(longitude)
                            if accuracy is not None:
                                payload["gps_accuracy_m"] = accuracy

                        try:
                            response = requests.post(
                                ingest_url,
                                json=payload,
                                headers=headers,
                                timeout=2,
                            )
                            if not response.ok:
                                error_detail = None
                                try:
                                    error_payload = response.json()
                                    if isinstance(error_payload, dict):
                                        error_detail = error_payload.get("detail")
                                except ValueError:
                                    pass
                                save_errors.append(
                                    f"Backend rejected detection event (HTTP {response.status_code})"
                                    + (f": {error_detail}" if error_detail else ".")
                                )
                                continue
                            saved_result = response.json()
                            saved_confidences.append(box_confidence)
                            pothole_id += 1
                            print(
                                "Detection event confirmed by backend:",
                                saved_result.get("message"),
                            )
                        except (requests.RequestException, ValueError) as error:
                            save_errors.append(
                                f"Backend did not confirm saving a detection: {error}"
                            )

                    if saved_confidences:
                        with state_lock:
                            last_saved_detection_at = datetime.now(timezone.utc).isoformat()
                            last_saved_detection_confidence = max(saved_confidences)
                            last_saved_detection_count = len(saved_confidences)
                            last_save_error = "; ".join(save_errors) if save_errors else None
                            last_save_status = "partially_saved" if save_errors else "saved"
                        last_detection_time = current_time
                        detection_episode_saved = True
                    if save_errors:
                        with state_lock:
                            last_save_error = "; ".join(save_errors)
                            last_save_status = "partially_saved" if saved_confidences else "save_failed"
                        detection_episode_failed = not saved_confidences
                        print(last_save_error)

            # Show GPS and camera status on the frame.
            if latitude is not None and longitude is not None:
                cv2.putText(
                    annotated,
                    f"GPS: {latitude:.5f}, {longitude:.5f} ({accuracy:.1f} m)"
                    if accuracy is not None
                    else f"GPS: {latitude:.5f}, {longitude:.5f} (accuracy unavailable)",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 0),
                    2
                )

            with state_lock:
                latest_frame = annotated
                last_frame_time = current_time

    except Exception as error:
        print("Camera worker error:", error)
        agent_error = str(error)

    finally:
        with camera_lock:
            camera_running = False

            if camera is capture:
                camera = None
            capture.release()


def get_agent_status():
    current_time = time.time()
    camera_open = False
    if camera is not None and camera_running:
        try:
            camera_open = camera.isOpened()
        except Exception:
            camera_open = False

    camera_fresh = last_frame_time > 0 and current_time - last_frame_time <= 5
    inference_fresh = last_inference_time > 0 and current_time - last_inference_time <= 15
    gps_fresh = (
        last_gps_update_time > 0
        and current_time - last_gps_update_time <= GPS_FRESH_SECONDS
        and valid_gps_coordinates(latitude, longitude)
    )
    gps_accuracy = normalize_gps_accuracy(accuracy)
    gps_status = (
        "low_accuracy"
        if gps_fresh and gps_accuracy is not None and gps_accuracy > GPS_MAX_MAP_ACCURACY_M
        else "connected"
        if gps_fresh
        else "permission_error"
        if gps_status_error
        and any(
            marker in gps_status_error.lower()
            for marker in ("permission", "access is denied", "access denied", "unauthorized")
        )
        else "disconnected"
        if gps_status_error
        else "acquiring"
        if camera_running
        else "unavailable"
    )
    model_exists = Path(MODEL_PATH).is_file()
    model_loaded = model is not None

    return {
        "yolo_inference": {
            "status": "connected" if camera_running and model_loaded and inference_fresh else "disconnected" if agent_error or last_inference_time else "unknown",
            "model_loaded": model_loaded,
            "model_file_exists": model_exists,
            "last_inference_at": datetime.fromtimestamp(last_inference_time, timezone.utc).isoformat() if last_inference_time else None,
            "error": agent_error,
        },
        "camera": {
            "status": "connected" if camera_open and camera_fresh else "disconnected" if not camera_running or not camera_open else "unknown",
            "running": camera_running,
            "last_frame_at": datetime.fromtimestamp(last_frame_time, timezone.utc).isoformat() if last_frame_time else None,
            "error": agent_error or (None if camera_open and camera_fresh else "Camera is stopped or no recent frame has been received."),
        },
        "detection": {
            "count": last_detection_count,
            "confidence": last_detection_confidence,
            "detected_at": last_detection_at,
            "saved_at": last_saved_detection_at,
            "saved_confidence": last_saved_detection_confidence,
            "saved_count": last_saved_detection_count,
            "save_status": last_save_status,
            "save_error": last_save_error,
        },
        "road_scene": {"status": road_scene_status},
        "gps": {
            "status": gps_status,
            "latitude": latitude if gps_fresh else None,
            "longitude": longitude if gps_fresh else None,
            "accuracy_m": gps_accuracy if gps_fresh else None,
            "last_updated_at": datetime.fromtimestamp(last_gps_update_time, timezone.utc).isoformat() if last_gps_update_time else None,
            "error": gps_status_error,
        },
    }


# -----------------------------------
# STOP CAMERA
# -----------------------------------

def stop_camera():
    global camera_running
    with camera_lock:
        camera_running = False
        capture = camera
        worker = camera_worker_thread
        if capture is not None:
            capture.release()
    if worker is not None and worker is not threading.current_thread():
        worker.join(timeout=5)
        if worker.is_alive():
            return "stopping"
    return "stopped"


# -----------------------------------
# MJPEG VIDEO STREAM
# -----------------------------------

def generate_frames():
    while camera_running:

        with state_lock:
            frame = (
                latest_frame.copy()
                if latest_frame is not None
                else None
            )

        if frame is None:
            time.sleep(0.03)
            continue

        success, buffer = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, 75]
        )

        if not success:
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + buffer.tobytes()
            + b"\r\n"
        )


# -----------------------------------
# FASTAPI ENDPOINTS
# -----------------------------------

@router.post("/api/camera/start")
def camera_start_endpoint(request: Request, credentials=Depends(HTTPBearer(auto_error=False)), _: dict = Depends(camera_current_user)):
    try:
        ingest_url = (
            f"{str(request.base_url).rstrip('/')}/api/detections"
            if request is not None
            else API_URL
        )
        access_token = credentials.credentials if credentials else request.cookies.get("roadwatch_access_token")
        status = start_camera(ingest_url, access_token)
        return {
            "status": "running",
            "message": "Camera is already running" if status == "already_running" else "Camera started",
        }

    except Exception as error:
        return {
            "status": "error",
            "message": str(error),
        }


@router.post("/api/camera/stop")
def camera_stop_endpoint(_: dict = Depends(camera_current_user)):
    status = stop_camera()
    return {"status": status, "message": f"Camera {status}"}


@router.get("/api/camera/status")
def camera_status_endpoint(_: dict = Depends(camera_current_user)):
    status = get_agent_status()
    return {
        "status": "running" if camera_running else "stopped",
        "gps_available": status["gps"]["status"] in {"connected", "low_accuracy"},
        **status,
    }


@router.get("/api/camera/stream")
def camera_stream_endpoint(_: dict = Depends(camera_current_user)):
    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
