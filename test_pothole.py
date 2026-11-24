from ultralytics import YOLO

# Load our trained pothole model
model = YOLO("runs/detect/train-2/weights/best.pt")

# Test it on our pothole image
results = model("images/pothole.jpg")

# Show the detection
for result in results:
    result.show()