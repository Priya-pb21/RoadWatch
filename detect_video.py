import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import cv2
import requests
from ultralytics import YOLO
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / "backend" / ".env")
MODEL_PATH = Path(
    os.getenv(
        "ROADWATCH_MODEL_PATH",
        str(PROJECT_ROOT / "runs" / "pothole_yolo11n" / "weights" / "best.pt"),
    )
)
INPUT_VIDEO = Path(
    os.getenv("ROADWATCH_INPUT_VIDEO", str(PROJECT_ROOT / "videos" / "road.mp4"))
)
OUTPUT_VIDEO = Path(
    os.getenv("ROADWATCH_OUTPUT_VIDEO", str(PROJECT_ROOT / "pothole_detection.mp4"))
)
API_URL = os.getenv(
    "ROADWATCH_INGEST_URL",
    "http://127.0.0.1:8000/api/detections",
)
INGEST_API_KEY = os.getenv("ROADWATCH_INGEST_API_KEY")
VIDEO_INGEST_ENABLED = os.getenv("ROADWATCH_VIDEO_INGEST_ENABLED", "").lower() in {
    "1",
    "true",
    "yes",
}
CONFIDENCE_THRESHOLD = float(os.getenv("ROADWATCH_CONFIDENCE_THRESHOLD", "0.25"))
DETECTION_COOLDOWN = max(1, int(os.getenv("ROADWATCH_DETECTION_COOLDOWN_SECONDS", "10")))


def video_location():
    if not VIDEO_INGEST_ENABLED:
        return None

    try:
        latitude = float(os.environ["ROADWATCH_VIDEO_LATITUDE"])
        longitude = float(os.environ["ROADWATCH_VIDEO_LONGITUDE"])
        accuracy = float(os.environ["ROADWATCH_VIDEO_GPS_ACCURACY_M"])
    except (KeyError, ValueError) as error:
        raise SystemExit(
            "Video ingestion requires ROADWATCH_VIDEO_LATITUDE, "
            "ROADWATCH_VIDEO_LONGITUDE, and ROADWATCH_VIDEO_GPS_ACCURACY_M."
        ) from error

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180 and accuracy >= 0):
        raise SystemExit("Configured video GPS coordinates or accuracy are invalid.")
    if accuracy > 100:
        raise SystemExit("Video ingestion requires GPS accuracy of 100 metres or better.")
    return latitude, longitude, accuracy


def send_detection(frame, box, confidence, location):
    latitude, longitude, accuracy = location
    x1, y1, x2, y2 = [float(value) for value in box.xyxy[0].tolist()]
    frame_height, frame_width = frame.shape[:2]
    payload = {
        "event_id": str(uuid4()),
        "source_id": "offline-video",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "latitude": latitude,
        "longitude": longitude,
        "gps_accuracy_m": accuracy,
        "confidence": round(confidence, 4),
        "bounding_box": {
            "x": round(x1 / frame_width, 5),
            "y": round(y1 / frame_height, 5),
            "width": round((x2 - x1) / frame_width, 5),
            "height": round((y2 - y1) / frame_height, 5),
        },
    }
    headers = {}
    if INGEST_API_KEY:
        headers["X-Roadwatch-Ingest-Key"] = INGEST_API_KEY
    response = requests.post(API_URL, json=payload, headers=headers, timeout=5)
    response.raise_for_status()


def main():
    location = video_location()
    if not MODEL_PATH.is_file():
        raise SystemExit(f"YOLO model file not found: {MODEL_PATH}")
    if not INPUT_VIDEO.is_file():
        raise SystemExit(f"Input video file not found: {INPUT_VIDEO}")

    model = YOLO(str(MODEL_PATH))
    cap = cv2.VideoCapture(str(INPUT_VIDEO))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open input video: {INPUT_VIDEO}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    OUTPUT_VIDEO.parent.mkdir(parents=True, exist_ok=True)
    out = cv2.VideoWriter(
        str(OUTPUT_VIDEO),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not out.isOpened():
        cap.release()
        raise SystemExit(f"Cannot create output video: {OUTPUT_VIDEO}")

    print("Video ingestion:", "enabled with configured GPS" if location else "disabled; no database writes")
    frame_count = 0
    last_detection_time = 0.0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            results = model.predict(
                frame,
                imgsz=640,
                conf=CONFIDENCE_THRESHOLD,
                verbose=False,
            )
            result = results[0]
            if (
                location
                and result.boxes is not None
                and len(result.boxes) > 0
                and time.monotonic() - last_detection_time >= DETECTION_COOLDOWN
            ):
                best_box = max(
                    result.boxes,
                    key=lambda box: float(box.conf[0].item()),
                )
                last_detection_time = time.monotonic()
                try:
                    send_detection(
                        frame,
                        best_box,
                        float(best_box.conf[0].item()),
                        location,
                    )
                except requests.RequestException as error:
                    print("Detection upload failed:", error)

            out.write(result.plot())
            frame_count += 1
            if frame_count % 50 == 0:
                print(f"Processed {frame_count} / {total_frames} frames")
    finally:
        cap.release()
        out.release()

    print(f"Finished. Processed {frame_count} frames. Output: {OUTPUT_VIDEO}")


if __name__ == "__main__":
    main()
