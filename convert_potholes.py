import os
import shutil
from pathlib import Path

# ============================================================
# PATHS
# ============================================================

SOURCE = Path(r"C:\Users\priya\Downloads\Pothole detection.v1i.yolov11 (1)")
OUTPUT = Path(r"C:\Users\priya\Desktop\Road_pothole\Pothole_Clean")

# Class 2 = Pothole
POTHOLE_CLASS = 2


# ============================================================
# CREATE OUTPUT FOLDERS
# ============================================================

for split in ["train", "valid", "test"]:
    (OUTPUT / split / "images").mkdir(parents=True, exist_ok=True)
    (OUTPUT / split / "labels").mkdir(parents=True, exist_ok=True)


# ============================================================
# CONVERT ONE SPLIT
# ============================================================

def process_split(split):

    image_dir = SOURCE / split / "images"
    label_dir = SOURCE / split / "labels"

    output_image_dir = OUTPUT / split / "images"
    output_label_dir = OUTPUT / split / "labels"

    images_copied = 0
    labels_created = 0
    polygons_converted = 0
    malformed = 0

    print(f"\n========== {split.upper()} ==========")

    for label_file in label_dir.glob("*.txt"):

        lines = label_file.read_text(encoding="utf-8", errors="ignore").splitlines()

        pothole_boxes = []

        for line in lines:

            parts = line.strip().split()

            if not parts:
                continue

            # First value must be class ID
            try:
                class_id = int(parts[0])
            except ValueError:
                continue

            # We only want class 2 = pothole
            if class_id != POTHOLE_CLASS:
                continue

            try:
                values = list(map(float, parts[1:]))
            except ValueError:
                malformed += 1
                continue

            # ------------------------------------------------
            # Polygon format:
            #
            # class x1 y1 x2 y2 x3 y3 ...
            #
            # Need at least 3 points
            # ------------------------------------------------

            if len(values) < 6 or len(values) % 2 != 0:
                malformed += 1
                continue

            x_points = values[0::2]
            y_points = values[1::2]

            # Bounding box around polygon
            xmin = min(x_points)
            xmax = max(x_points)
            ymin = min(y_points)
            ymax = max(y_points)

            # Safety: keep coordinates inside image
            xmin = max(0.0, min(1.0, xmin))
            xmax = max(0.0, min(1.0, xmax))
            ymin = max(0.0, min(1.0, ymin))
            ymax = max(0.0, min(1.0, ymax))

            width = xmax - xmin
            height = ymax - ymin

            if width <= 0 or height <= 0:
                malformed += 1
                continue

            x_center = (xmin + xmax) / 2
            y_center = (ymin + ymax) / 2

            # YOLO detection format
            pothole_boxes.append(
                f"0 {x_center:.6f} {y_center:.6f} "
                f"{width:.6f} {height:.6f}"
            )

            polygons_converted += 1

        # ----------------------------------------------------
        # If this image contains a pothole, copy it
        # ----------------------------------------------------

        if pothole_boxes:

            # Find corresponding image
            possible_extensions = [
                ".jpg",
                ".jpeg",
                ".png",
                ".JPG",
                ".JPEG",
                ".PNG"
            ]

            image_file = None

            for ext in possible_extensions:
                candidate = image_dir / (label_file.stem + ext)

                if candidate.exists():
                    image_file = candidate
                    break

            if image_file is None:
                print(f"WARNING: Image not found for {label_file.name}")
                continue

            # Copy image
            shutil.copy2(
                image_file,
                output_image_dir / image_file.name
            )

            # Write clean label
            output_label_file = output_label_dir / label_file.name

            output_label_file.write_text(
                "\n".join(pothole_boxes) + "\n",
                encoding="utf-8"
            )

            images_copied += 1
            labels_created += 1

    print(f"Images copied       : {images_copied}")
    print(f"Labels created      : {labels_created}")
    print(f"Polygons converted  : {polygons_converted}")
    print(f"Malformed annotations: {malformed}")

    return images_copied, labels_created, polygons_converted


# ============================================================
# PROCESS ALL SPLITS
# ============================================================

train_stats = process_split("train")
valid_stats = process_split("valid")
test_stats = process_split("test")


# ============================================================
# CREATE data.yaml
# ============================================================

yaml_content = f"""train: ../train/images
val: ../valid/images
test: ../test/images

nc: 1
names: ['pothole']
"""

yaml_file = OUTPUT / "data.yaml"
yaml_file.write_text(yaml_content, encoding="utf-8")


# ============================================================
# FINAL SUMMARY
# ============================================================

print("\n========================================")
print("       DATASET CREATION COMPLETE")
print("========================================")

print(f"Train images : {train_stats[0]}")
print(f"Valid images : {valid_stats[0]}")
print(f"Test images  : {test_stats[0]}")

print(f"\nDataset created at:")
print(OUTPUT)

print("\ndata.yaml:")
print(yaml_content)

print("\nOriginal dataset was NOT modified.")