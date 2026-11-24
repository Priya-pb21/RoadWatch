"""
Road-scene validation using the Places365 ResNet18 classifier.

This classifier runs separately from YOLO11.
It validates road scenes before pothole detection.
"""

from __future__ import annotations

import os
import logging

from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from PIL import Image, ImageOps


# --------------------------------------------------
# PATHS AND CONFIGURATION
# --------------------------------------------------

MODEL_PATH = (
    Path(__file__).resolve().parent
    / "models"
    / "resnet18_places365.pth.tar"
)

CATEGORY_PATH = (
    Path(__file__).resolve().parent
    / "models"
    / "categories_places365.txt"
)

ROAD_SCENE_THRESHOLD = float(
    os.getenv("ROADWATCH_ROAD_SCENE_THRESHOLD", "0.20")
)

if not 0.0 < ROAD_SCENE_THRESHOLD < 1.0:
    raise RuntimeError(
        "ROADWATCH_ROAD_SCENE_THRESHOLD must be between 0 and 1."
    )

INVALID_ROAD_IMAGE_MESSAGE = (
    "Invalid Image! Please upload a valid road image containing a road scene."
)

logger: logging.Logger = logging.getLogger(__name__)


# --------------------------------------------------
# ROAD-RELATED PLACES365 CATEGORIES
# --------------------------------------------------

ROAD_SCENE_NAMES = frozenset({
    "alley",
    "bridge",
    "crosswalk",
    "desert_road",
    "downtown",
    "driveway",
    "field_road",
    "forest_path",
    "forest_road",
    "highway",
    "parking_garage/indoor",
    "parking_garage/outdoor",
    "parking_lot",
    "raceway",
    "residential_neighborhood",
    "street",
    "viaduct",
})


# --------------------------------------------------
# LOAD PLACES365 CATEGORIES
# --------------------------------------------------

def load_places365_categories(
    path: Path = CATEGORY_PATH,
) -> tuple[str, ...]:

    if not path.is_file():
        raise RuntimeError(
            f"Places365 category file is missing: {path}"
        )

    entries = {}

    for line in path.read_text(
        encoding="utf-8"
    ).splitlines():

        parts = line.strip().split()

        if len(parts) != 2:
            continue

        raw_label, raw_index = parts

        label = raw_label.split("/", 2)[-1]

        entries[int(raw_index)] = label

    if set(entries) != set(range(365)):
        raise RuntimeError(
            "Places365 category file must contain "
            f"365 classes. Found {len(entries)}."
        )

    return tuple(
        entries[index]
        for index in range(365)
    )


PLACES365_CATEGORIES = load_places365_categories()


if not ROAD_SCENE_NAMES.issubset(
    set(PLACES365_CATEGORIES)
):
    missing = (
        ROAD_SCENE_NAMES
        - set(PLACES365_CATEGORIES)
    )

    raise RuntimeError(
        f"Road categories missing from Places365: {missing}"
    )


ROAD_SCENE_CLASS_IDS = frozenset(
    index
    for index, label in enumerate(PLACES365_CATEGORIES)
    if label in ROAD_SCENE_NAMES
)


# --------------------------------------------------
# ROAD-SCENE DECISION
# --------------------------------------------------

def road_scene_decision(
    probabilities: list[float] | tuple[float, ...],
) -> dict[str, Any]:

    if len(probabilities) != 365:
        raise ValueError(
            "Places365 must return 365 class probabilities."
        )

    top_five_ids = sorted(
        range(365),
        key=probabilities.__getitem__,
        reverse=True,
    )[:5]

    top_id = top_five_ids[0]

    top_score = float(
        probabilities[top_id]
    )

    top_five_road = [
        index
        for index in top_five_ids
        if index in ROAD_SCENE_CLASS_IDS
    ]

    road_score = float(
        sum(
            probabilities[index]
            for index in ROAD_SCENE_CLASS_IDS
        )
    )

    road_top5_score = float(
        sum(
            probabilities[index]
            for index in top_five_road
        )
    )

    passed = (
        road_score >= ROAD_SCENE_THRESHOLD
    )

    rejection_reason = None

    if not passed:
        rejection_reason = (
            "Combined Places365 probability across "
            f"road-related classes {road_score:.4f} "
            f"is below threshold "
            f"{ROAD_SCENE_THRESHOLD:.2f}"
        )

    return {
        "is_road": passed,

        "scene": PLACES365_CATEGORIES[top_id],

        "confidence": top_score,

        "road_scene_score": road_score,

        "road_scene_top5_score": road_top5_score,

        "threshold": ROAD_SCENE_THRESHOLD,

        "top5": [
            {
                "class_id": index,

                "class_name": PLACES365_CATEGORIES[index],

                "confidence": float(
                    probabilities[index]
                ),
            }

            for index in top_five_ids
        ],

        "rejection_reason": rejection_reason,
    }


# --------------------------------------------------
# LOAD PLACES365 MODEL
# --------------------------------------------------

@lru_cache(maxsize=1)
def _classifier() -> tuple[Any, Any]:

    import torch

    from torchvision import models, transforms

    if not MODEL_PATH.is_file():
        raise RuntimeError(
            f"Places365 weights are missing: {MODEL_PATH}"
        )

    # The official Places365 .pth.tar is a trusted legacy PyTorch checkpoint
    # containing metadata alongside its state_dict. PyTorch 2.6 changed the
    # default unpickler to weights_only=True, which cannot load this format.
    # Detect text-encoding damage before unpickling: this checkpoint must stay
    # a binary file and must never be interpreted as arbitrary uploaded input.
    with MODEL_PATH.open("rb") as checkpoint_file:
        header = checkpoint_file.read(4)

    if header.startswith(b"\xef\xbf\xbd"):
        raise RuntimeError(
            "Places365 checkpoint is corrupted: its binary header contains "
            "UTF-8 replacement characters. Restore the original official "
            "resnet18_places365.pth.tar checkpoint."
        )

    try:
        try:
            checkpoint = torch.load(
                MODEL_PATH,
                map_location="cpu",
                weights_only=False,
            )
        except TypeError as error:
            # Compatibility with PyTorch versions predating weights_only.
            if "weights_only" not in str(error):
                raise
            checkpoint = torch.load(MODEL_PATH, map_location="cpu")
    except Exception as error:
        logger.exception("Could not deserialize the Places365 checkpoint")
        raise RuntimeError(
            "Could not deserialize the Places365 checkpoint. Verify that "
            "resnet18_places365.pth.tar is the original, unmodified binary "
            "checkpoint."
        ) from error

    if not isinstance(checkpoint, dict):
        raise RuntimeError(
            "Places365 checkpoint has an unsupported format; expected a "
            "checkpoint dictionary containing state_dict."
        )

    state = checkpoint.get("state_dict", checkpoint)

    if not isinstance(state, dict) or not state:
        raise RuntimeError(
            "Places365 checkpoint does not contain a usable state_dict."
        )

    state = {
        key.removeprefix("module."): value
        for key, value in state.items()
    }

    model = models.resnet18(
        num_classes=365
    )

    model.load_state_dict(
        state,
        strict=True,
    )

    model.eval()

    preprocess = transforms.Compose([

        transforms.Resize(256),

        transforms.CenterCrop(224),

        transforms.ToTensor(),

        transforms.Normalize(
            mean=[
                0.485,
                0.456,
                0.406,
            ],
            std=[
                0.229,
                0.224,
                0.225,
            ],
        ),
    ])

    logger.info(
        "Places365 ResNet18 loaded successfully. "
        "Number of classes: 365"
    )

    return model, preprocess

# --------------------------------------------------
# VALIDATE ROAD IMAGE
# --------------------------------------------------

def validate_road_scene(
    image: Image.Image | np.ndarray,
    *,
    source: str = "image_upload",
    log_diagnostics: bool = True,
) -> dict[str, Any]:

    import torch

    # Convert OpenCV image to RGB.

    if isinstance(image, np.ndarray):

        if (
            image.ndim != 3
            or image.shape[2] not in (3, 4)
        ):
            raise ValueError(
                "Expected a valid color image frame."
            )

        if image.shape[2] == 4:

            rgb = cv2.cvtColor(
                image,
                cv2.COLOR_BGRA2RGB,
            )

        else:

            rgb = cv2.cvtColor(
                image,
                cv2.COLOR_BGR2RGB,
            )

        pil_image = Image.fromarray(rgb)

    elif isinstance(image, Image.Image):

        pil_image = ImageOps.exif_transpose(
            image
        ).convert("RGB")

    else:

        raise ValueError(
            "Unsupported image format."
        )

    # Load model and preprocessing.

    model, preprocess = _classifier()

    tensor = preprocess(
        pil_image
    ).unsqueeze(0)

    # Run inference.

    with torch.inference_mode():

        logits = model(tensor)[0]

        probabilities = torch.softmax(
            logits,
            dim=0,
        )

    decision = road_scene_decision(
        probabilities.tolist()
    )

    # Diagnostic logging.

    if log_diagnostics:

        top5_details = [
            (
                f"{item['class_name']}="
                f"{item['confidence']:.4f}"
            )

            for item in decision["top5"]
        ]

        logger.info(
            "Places365 validation | "
            "Source=%s | "
            "Top5=%s | "
            "Predicted=%s | "
            "Road probability=%.4f | "
            "Threshold=%.2f | "
            "Passed=%s | "
            "Reason=%s",

            source,

            top5_details,

            decision["scene"],

            decision["road_scene_score"],

            decision["threshold"],

            decision["is_road"],

            decision["rejection_reason"] or "none",
        )

    return decision
