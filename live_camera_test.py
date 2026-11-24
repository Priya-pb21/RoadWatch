import cv2

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("ERROR: Could not open laptop camera.")
    raise SystemExit(1)

print("Camera opened successfully!")
print("Press Q to close the camera window.")

while True:
    ret, frame = cap.read()

    if not ret:
        print("ERROR: Could not read camera frame.")
        break

    cv2.imshow("Live Camera Test", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()