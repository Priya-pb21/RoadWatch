import csv

# Pretend YOLO detected a pothole at this time
pothole_time = "18:39:57"

with open("gps.csv", "r") as file:
    reader = csv.DictReader(file)

    for row in reader:
        if row["timestamp"] == pothole_time:
            print("🕳️ Pothole detected!")
            print("Time:", pothole_time)
            print("Latitude:", row["latitude"])
            print("Longitude:", row["longitude"])