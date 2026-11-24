from pathlib import Path
from PIL import Image, ImageDraw
import random

# Training image folder
folder = Path("Pothole_Clean/train/images")

# Find available images
extensions = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
images = [f for f in folder.iterdir() if f.suffix.lower() in extensions]

if not images:
    print("No images found in the training folder!")
    raise SystemExit

# Select up to 9 random images
random.seed(42)
selected = random.sample(images, min(9, len(images)))

# Create a contact sheet
sheet = Image.new("RGB", (960, 660), "white")
draw = ImageDraw.Draw(sheet)

for i, file in enumerate(selected):
    image = Image.open(file).convert("RGB")
    image.thumbnail((310, 190))

    x = (i % 3) * 320
    y = (i // 3) * 220

    sheet.paste(image, (x, y))
    draw.text((x + 5, y + 195), file.name[:40], fill="black")

# Save the result
output = Path("training_samples.jpg")
sheet.save(output)

print(f"Total training images found: {len(images)}")
print(f"Sample images selected: {len(selected)}")
print(f"Contact sheet saved at: {output.resolve()}")