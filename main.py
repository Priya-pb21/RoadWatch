import cv2

video = cv2.VideoCapture("videos/road.mp4")

while True:
    success, frame = video.read()

    if not success:
        break

    cv2.imshow("Road Video", frame)

    if cv2.waitKey(25) & 0xFF == ord('q'):
        break

video.release()
cv2.destroyAllWindows()