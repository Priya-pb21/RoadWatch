import cv2

input_video = r"C:\Users\priya\Desktop\Road_pothole\runs\detect\predict\road.mp4"
output_video = r"C:\Users\priya\Desktop\Road_pothole\small_result.mp4"

cap = cv2.VideoCapture(input_video)

fps = cap.get(cv2.CAP_PROP_FPS)
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

# Make video half the original size
new_width = width // 2
new_height = height // 2

out = cv2.VideoWriter(
    output_video,
    cv2.VideoWriter_fourcc(*"mp4v"),
    fps,
    (new_width, new_height)
)

# Only take first 30 seconds
max_frames = int(fps * 30)
count = 0

while count < max_frames:
    ret, frame = cap.read()

    if not ret:
        break

    frame = cv2.resize(frame, (new_width, new_height))
    out.write(frame)

    count += 1

cap.release()
out.release()

print("Small video created successfully!")
print("Saved at:", output_video)