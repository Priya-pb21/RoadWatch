from ultralytics import YOLO
import cv2
import csv
from datetime import datetime, timedelta

# Paths
#MODEL_PATH = r"C:\Users\priya\Desktop\Road_pothole\runs\detect\train-3\weights\best.pt"
MODEL_PATH = r"C:\Users\priya\Desktop\Road_pothole\runs\pothole_yolo11n\weights\best.pt"
VIDEO_PATH = r"C:\Users\priya\Desktop\Road_pothole\videos\road.mp4"
GPS_PATH = r"C:\Users\priya\Desktop\Road_pothole\gps.csv"

# Demo assumption:
# Video starts at this GPS time
VIDEO_START_TIME = datetime.strptime("18:39:55", "%H:%M:%S")

# Load YOLO model
model = YOLO(MODEL_PATH)

# Load GPS data
gps_data = []

with open(GPS_PATH, "r") as file:
    reader = csv.DictReader(file)

    for row in reader:
        gps_time = datetime.strptime(row["timestamp"], "%H:%M:%S")

        gps_data.append({
            "time": gps_time,
            "latitude": row["latitude"],
            "longitude": row["longitude"]
        })

# Open video
cap = cv2.VideoCapture(VIDEO_PATH)

fps = cap.get(cv2.CAP_PROP_FPS)
frame_number = 0

# Output CSV
output_file = open("pothole_locations.csv", "w", newline="")

writer = csv.writer(output_file)

writer.writerow([
    "frame",
    "video_time",
    "gps_time",
    "latitude",
    "longitude",
    "confidence"
])

print("\nStarting pothole detection...\n")

while True:

    ret, frame = cap.read()

    if not ret:
        break

    # Current video time
    video_seconds = frame_number / fps

    gps_time_target = VIDEO_START_TIME + timedelta(
        seconds=video_seconds
    )

    # Find closest GPS point
    closest_gps = min(
        gps_data,
        key=lambda x: abs(
            (x["time"] - gps_time_target).total_seconds()
        )
    )

    # YOLO prediction
    results = model.predict(
        frame,
        conf=0.25,
        verbose=False
    )

    for result in results:

        if result.boxes is not None and len(result.boxes) > 0:

            for confidence in result.boxes.conf.tolist():

                print("🕳️ POTHOLE DETECTED")

                print("Frame:", frame_number)

                print(
                    "Video time:",
                    round(video_seconds, 2),
                    "seconds"
                )

                print(
                    "GPS time:",
                    closest_gps["time"].strftime("%H:%M:%S")
                )

                print(
                    "Latitude:",
                    closest_gps["latitude"]
                )

                print(
                    "Longitude:",
                    closest_gps["longitude"]
                )

                print(
                    "Confidence:",
                    round(confidence, 2)
                )

                print("-----------------------------")

                writer.writerow([
                    frame_number,
                    round(video_seconds, 2),
                    closest_gps["time"].strftime("%H:%M:%S"),
                    closest_gps["latitude"],
                    closest_gps["longitude"],
                    round(confidence, 2)
                ])

    frame_number += 1

cap.release()
output_file.close()

print("\n✅ Detection completed!")
print("GPS results saved to:")
print("pothole_locations.csv")