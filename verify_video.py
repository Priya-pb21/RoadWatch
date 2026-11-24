import argparse
import csv

import cv2
from ultralytics import YOLO


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True, help="path to your trained .pt file")
    ap.add_argument("--video", required=True, help="a video NOT used for training")
    ap.add_argument("--conf", type=float, default=0.5, help="confidence threshold")
    ap.add_argument("--out", default="verify_output.mp4")
    args = ap.parse_args()

    model = YOLO(args.weights)
    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    scale = 0.5  # keep the side-by-side file a reasonable size
    sw, sh = int(w * scale), int(h * scale)
    writer = cv2.VideoWriter(
        args.out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (sw * 2, sh)
    )

    log = open("detections.csv", "w", newline="")
    csv_w = csv.writer(log)
    csv_w.writerow(["frame", "time_s", "conf", "x1", "y1", "x2", "y2"])

    n = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        res = model.predict(frame, conf=args.conf, verbose=False)[0]
        for b in res.boxes:
            x1, y1, x2, y2 = [round(v) for v in b.xyxy[0].tolist()]
            csv_w.writerow([n, round(n / fps, 2), round(float(b.conf[0]), 3),
                            x1, y1, x2, y2])

        pred = res.plot()  # frame with boxes drawn
        left = cv2.resize(frame, (sw, sh))
        right = cv2.resize(pred, (sw, sh))
        cv2.putText(left, "RAW", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
        cv2.putText(right, f"PRED  frame {n}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
        writer.write(cv2.hconcat([left, right]))
        n += 1

    cap.release()
    writer.release()
    log.close()
    print(f"Done: {n} frames -> {args.out}, detections.csv")


if __name__ == "__main__":
    main()
