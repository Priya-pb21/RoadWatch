# Road scene validation model

`resnet18_places365.pth.tar` is the Places365 ResNet18 scene-classification
checkpoint. It is separate from the YOLO pothole detector. The application
loads it lazily on CPU the first time an image or sampled live/video frame is
checked, then caches the model for subsequent requests.

`categories_places365.txt` is the official 365-line category-to-index table
used by this model. The application loads and validates all 365 indices rather
than relying on an independently hardcoded ID list. The road allowlist uses
category names present in that table. Places365 has no generic `road` class;
relevant labels include `highway`, `street`, `parking_lot`, `bridge`,
`crosswalk`, `alley`, `driveway`, `desert_road`, and `forest_road`.

Preprocessing matches the official inference recipe: RGB, resize to
`(256, 256)`, center crop to 224 pixels, convert to tensor, and ImageNet channel
normalization. A frame passes when the sum of probability across the configured
road-scene classes over the full 365-class output is at least `0.20`. This
allows road-scene probability to count when the top predicted class is another
context or when road-scene probability is split across related classes. The
threshold remains `0.20`; it has not been lowered. Configure it with
`ROADWATCH_ROAD_SCENE_THRESHOLD` (strictly between 0 and 1). This score is
independent of YOLO confidence.

Uploaded-image checks log each top-five class and score, top class, combined
road-class probability across all 365 classes, pass/fail, and rejection reason
at INFO level. The decision is also returned as `road_scene` metadata for
accepted images. Video and webcam frame checks do not write per-frame
diagnostics to the log.

The repository has pothole detection training imagery, but the dedicated
`image_validation_dataset/road` and `non_road` folders are empty. Therefore,
the threshold has not been statistically calibrated against project-specific
road and negative examples; the temporary logs should be reviewed with the
requested representative images.

Model source and inference recipe: https://github.com/CSAILVision/places365
