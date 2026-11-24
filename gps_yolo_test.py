import csv

gps_file = "gps.csv"

with open(gps_file, "r") as file:
    reader = csv.DictReader(file)

    for row in reader:
        print("Time:", row["timestamp"])
        print("Latitude:", row["latitude"])
        print("Longitude:", row["longitude"])
        print("--------------------")