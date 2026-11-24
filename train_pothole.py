from ultralytics import YOLO

model = YOLO(
    r"C:\Users\priya\Desktop\Road_pothole\runs\pothole_yolo11n\weights\last.pt"
)

model.train(resume=True)

print("Training resumed and completed!")