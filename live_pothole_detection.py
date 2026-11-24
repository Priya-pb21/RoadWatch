import os
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import cv2
import requests
from ultralytics import YOLO
from dotenv import load_dotenv

from winrt.windows.devices.geolocation import Geolocator


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / "backend" / ".env")

MODEL_PATH = os.getenv(
    "ROADWATCH_MODEL_PATH",
    str(PROJECT_ROOT / "runs" / "pothole_yolo11n" / "weights" / "best.pt"),
)

API_URL = os.getenv(
    "ROADWATCH_INGEST_URL",
    "http://127.0.0.1:8000/api/detections",
)

INGEST_API_KEY = os.getenv("ROADWATCH_INGEST_API_KEY")

CONFIDENCE_THRESHOLD = float(
    os.getenv("ROADWATCH_CONFIDENCE_THRESHOLD", "0.50")
)

DETECTION_COOLDOWN = int(
    os.getenv("ROADWATCH_DETECTION_COOLDOWN_SECONDS", "10")
)

MAX_GPS_ACCURACY_M = float(
    os.getenv("ROADWATCH_MAX_GPS_ACCURACY_M", "100")
)

GPS_REFRESH_SECONDS = 5
MAX_GPS_AGE_SECONDS = 30


async def get_location():
    try:
        locator = Geolocator()
        position = await locator.get_geoposition_async()

        coordinates = position.coordinate.point.position
        latitude = coordinates.latitude
        longitude = coordinates.longitude
        accuracy = position.coordinate.accuracy

        if not (
            -90 <= latitude <= 90
            and -180 <= longitude <= 180
            and accuracy >= 0
        ):
            return None, None, None

        # Reject highly inaccurate location estimates.
        if accuracy > MAX_GPS_ACCURACY_M:
            return None, None, accuracy

        return latitude, longitude, accuracy

    except Exception as error:
        print("Location error:", error)
        return None, None, None


def make_detection_payload(
    frame, box, latitude, longitude, accuracy, confidence
):
    x1, y1, x2, y2 = [
        float(value) for value in box.xyxy[0].tolist()
    ]

    frame_height, frame_width = frame.shape[:2]

    return {
        "event_id": str(uuid4()),
        "source_id": "local-windows-webcam",
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


def send_detection(payload):
    headers = {}

    if INGEST_API_KEY:
        headers["X-Roadwatch-Ingest-Key"] = INGEST_API_KEY

    return requests.post(
        API_URL,
        json=payload,
        headers=headers,
        timeout=2,
    )


def main():
    if not Path(MODEL_PATH).is_file():
        raise SystemExit(f"YOLO model file not found: {MODEL_PATH}")

    print("Loading YOLO model...")
    model = YOLO(MODEL_PATH)
    print("YOLO model loaded successfully!")

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)

    if not cap.isOpened():
        print("ERROR: Could not open camera.")
        raise SystemExit(1)

    print("Camera started. Press Q to quit.")

    last_location_time = 0.0
    last_detection_time = 0.0

    latitude = None
    longitude = None
    accuracy = None
    location_updated_at = 0.0

    try:
        while True:
            ret, frame = cap.read()

            if not ret:
                print("Could not read camera frame.")
                break

            current_time = time.monotonic()

            # Refresh the GPS reading periodically.
            if current_time - last_location_time >= GPS_REFRESH_SECONDS:
                new_lat, new_lon, new_accuracy = asyncio.run(
                    get_location()
                )

                last_location_time = current_time

                if (
                    new_lat is not None
                    and new_lon is not None
                    and new_accuracy is not None
                    and new_accuracy <= MAX_GPS_ACCURACY_M
                ):
                    latitude = new_lat
                    longitude = new_lon
                    accuracy = new_accuracy
                    location_updated_at = current_time

                    print(
                        f"GPS accepted: {latitude:.6f}, "
                        f"{longitude:.6f} | Accuracy: {accuracy:.1f} m"
                    )

                elif new_accuracy is not None:
                    print(
                        f"GPS rejected: accuracy "
                        f"{new_accuracy:.1f} m exceeds "
                        f"{MAX_GPS_ACCURACY_M:.1f} m"
                    )

                else:
                    print("GPS unavailable; waiting for a valid reading.")

            # Do not reuse an old GPS position indefinitely.
            location_is_fresh = (
                latitude is not None
                and longitude is not None
                and accuracy is not None
                and current_time - location_updated_at
                <= MAX_GPS_AGE_SECONDS
            )

            results = model.predict(
                frame,
                imgsz=640,
                conf=CONFIDENCE_THRESHOLD,
                verbose=False,
            )

            result = results[0]

            if result.boxes is not None and len(result.boxes) > 0:
                best_box = max(
                    result.boxes,
                    key=lambda candidate: float(
                        candidate.conf[0].item()
                    ),
                )

                confidence = float(best_box.conf[0].item())

                should_send = (
                    location_is_fresh
                    and accuracy <= MAX_GPS_ACCURACY_M
                    and current_time - last_detection_time
                    >= DETECTION_COOLDOWN
                )

                if should_send:
                    payload = make_detection_payload(
                        frame,
                        best_box,
                        latitude,
                        longitude,
                        accuracy,
                        confidence,
                    )

                    try:
                        response = send_detection(payload)

                        if response.ok:
                            print(
                                "Detection event sent successfully:",
                                response.status_code,
                            )
                            last_detection_time = time.monotonic()
                        else:
                            print(
                                "API error:",
                                response.status_code,
                                response.text[:200],
                            )

                    except requests.RequestException as error:
                        print("Backend connection error:", error)

            annotated_frame = result.plot()

            if location_is_fresh:
                gps_text = (
                    f"GPS: {latitude:.5f}, {longitude:.5f} "
                    f"Accuracy: {accuracy:.0f}m"
                )
                gps_color = (0, 255, 0)
            else:
                gps_text = "GPS: Waiting for accurate location"
                gps_color = (0, 165, 255)

            cv2.putText(
                annotated_frame,
                gps_text,
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                gps_color,
                2,
            )

            cv2.imshow(
                "Live Pothole Detection",
                annotated_frame,
            )

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("Live pothole detection stopped.")


if __name__ == "__main__":
    main()