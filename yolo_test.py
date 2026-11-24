from ultralytics import YOLO

# Load the trained pothole detection model
model = YOLO("runs/pothole_yolo11n/weights/best.pt")

# Detect potholes in the test image
results = model.predict(
    source="images/pothole.jpg",
    conf=0.10,
    iou=0.45,
    save=True
)

# Print detection details
for result in results:
    print("\nPothole detection results:")

    if len(result.boxes) == 0:
        print("No potholes detected.")
    else:
        for box in result.boxes:
            confidence = float(box.conf[0])
            coordinates = box.xyxy[0].tolist()

            print(f"Confidence: {confidence:.2%}")
            print(f"Bounding box: {coordinates}")

    result.show()

print("\nTest completed!")