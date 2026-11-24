import requests
API_URL = "http://127.0.0.1:8000/api/potholes"
from ultralytics import YOLO

model = YOLO(
    r"C:\Users\priya\Desktop\Road_pothole\runs\pothole_yolo11n\weights\best.pt"
)

results = model.predict(
    source=r"C:\Users\priya\Desktop\Road_pothole\videos\road.mp4",
    save=True
)