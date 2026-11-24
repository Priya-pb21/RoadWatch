import cv2

input_video = r"C:\Users\priya\Desktop\Road_pothole\videos\road.mp4"
output_video = r"C:\Users\priya\Desktop\Road_pothole\small_result.mp4"

cap = cv2.VideoCapture(input_video)

if not cap.isOpened():
    print("ERROR: Cannot open input video")
    exit()

fps = cap.get(cv2.CAP_PROP_FPS)
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

print("FPS:", fps)
print("Total frames:", total_frames)

fourcc = cv2.VideoWriter_fourcc(*"mp4v")

out = cv2.VideoWriter(
    output_video,
    fourcc,
    fps,
    (1280, 720)
)

print("Writer opened:", out.isOpened())

frame_count = 0

while True:
    ret, frame = cap.read()

    if not ret:
        break

    frame = cv2.resize(frame, (1280, 720))

    out.write(frame)

    frame_count += 1

    if frame_count % 100 == 0:
        print("Processed:", frame_count, "/", total_frames)

cap.release()
out.release()

print("\nDone!")
print("Frames written:", frame_count)
print("Output:", output_video)