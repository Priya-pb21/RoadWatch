import cv2
import asyncio
import math
import csv
from ultralytics import YOLO
from winrt.windows.devices.geolocation import Geolocator


MODEL_PATH = r"C:\Users\priya\Desktop\Road_pothole\runs\pothole_yolo11n\weights\best.pt"

model = YOLO(MODEL_PATH)
locator = Geolocator()

# Store already recorded potholes
detected_potholes = []

# Minimum distance between two potholes in meters
MIN_DISTANCE = 10


def distance_meters(lat1, lon1, lat2, lon2):

    lat_diff = (lat2 - lat1) * 111000
    lon_diff = (lon2 - lon1) * 111000 * math.cos(math.radians(lat1))

    return math.sqrt(lat_diff ** 2 + lon_diff ** 2)


async def get_location():

    position = await locator.get_geoposition_async()

    latitude = position.coordinate.point.position.latitude
    longitude = position.coordinate.point.position.longitude

    return latitude, longitude


async def main():

    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("❌ Camera could not be opened")
        return

    # Create CSV file
    csv_file = open(
        "pothole_locations_live.csv",
        "w",
        newline=""
    )

    writer = csv.writer(csv_file)

    writer.writerow([
        "pothole_id",
        "latitude",
        "longitude",
        "confidence"
    ])

    print("✅ Live pothole detection started")
    print("Press Q to stop\n")

    pothole_id = 0

    while True:

        ret, frame = cap.read()

        if not ret:
            break

        results = model.predict(
            frame,
            conf=0.50,
            verbose=False
        )

        if results[0].boxes is not None and len(results[0].boxes) > 0:

            latitude, longitude = await get_location()

            for confidence in results[0].boxes.conf.tolist():

                # Check whether this location is already recorded
                duplicate = False

                for old_lat, old_lon in detected_potholes:

                    distance = distance_meters(
                        old_lat,
                        old_lon,
                        latitude,
                        longitude
                    )

                    if distance < MIN_DISTANCE:
                        duplicate = True
                        break

                # Save only new pothole
                if not duplicate:

                    pothole_id += 1

                    detected_potholes.append(
                        (latitude, longitude)
                    )

                    writer.writerow([
                        pothole_id,
                        latitude,
                        longitude,
                        round(confidence, 2)
                    ])

                    csv_file.flush()

                    print("\n🕳️ NEW POTHOLE FOUND")
                    print("Pothole ID:", pothole_id)
                    print("Latitude:", latitude)
                    print("Longitude:", longitude)
                    print("Confidence:", round(confidence, 2))
                    print("-----------------------------")

        annotated_frame = results[0].plot()

        cv2.imshow(
            "Live Pothole Detection",
            annotated_frame
        )

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    csv_file.close()
    cv2.destroyAllWindows()

    print("\n✅ Detection stopped")
    print("Pothole data saved to:")
    print("pothole_locations_live.csv")


asyncio.run(main())