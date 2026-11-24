import pytest

from backend.road_scene import (
    PLACES365_CATEGORIES,
    ROAD_SCENE_CLASS_IDS,
    ROAD_SCENE_NAMES,
    ROAD_SCENE_THRESHOLD,
    road_scene_decision,
)


def probabilities_for(class_id, confidence):
    values = [0.0] * 365
    values[class_id] = confidence
    remaining = (1.0 - confidence) / 364
    for index in range(365):
        if index != class_id:
            values[index] = remaining
    return values


def test_places365_street_scene_above_threshold_is_accepted():
    result = road_scene_decision(probabilities_for(319, max(ROAD_SCENE_THRESHOLD, 0.35)))
    assert result["is_road"] is True
    assert result["scene"] == "street"
    assert len(result["top5"]) == 5
    assert result["top5"][0]["class_name"] == "street"
    assert result["rejection_reason"] is None


def test_road_scene_confidence_split_across_top_five_is_accepted():
    values = [0.0] * 365
    values[51] = 0.40  # top non-road context class
    values[175] = 0.13  # highway
    values[319] = 0.12  # street
    values[257] = 0.05  # parking_lot
    residual = (1.0 - sum(values)) / (365 - 4)
    for index in range(365):
        if values[index] == 0.0:
            values[index] = residual
    result = road_scene_decision(values)
    assert result["scene"] == PLACES365_CATEGORIES[51]
    assert result["is_road"] is True
    assert result["road_scene_score"] >= ROAD_SCENE_THRESHOLD


def test_road_scene_mass_can_pass_when_road_labels_rank_below_top_five():
    values = [0.0] * 365
    for class_id, score in zip([51, 86, 210, 335, 300], [0.30, 0.20, 0.10, 0.08, 0.06]):
        values[class_id] = score
    road_ids = sorted(ROAD_SCENE_CLASS_IDS)
    for class_id in road_ids:
        values[class_id] = 0.22 / len(road_ids)
    residual = (1.0 - sum(values)) / (365 - len(road_ids) - 5)
    for index in range(365):
        if values[index] == 0.0:
            values[index] = residual

    result = road_scene_decision(values)
    assert all(item["class_id"] not in ROAD_SCENE_CLASS_IDS for item in result["top5"])
    assert result["road_scene_score"] >= ROAD_SCENE_THRESHOLD
    assert result["is_road"] is True


@pytest.mark.parametrize("class_id", [51, 86, 210, 335])  # bedroom, cemetery, lecture room, toyshop
def test_unrelated_places365_scene_is_rejected(class_id):
    result = road_scene_decision(probabilities_for(class_id, 0.9))
    assert result["is_road"] is False
    assert result["rejection_reason"]


def test_low_confidence_road_guess_is_rejected():
    values = [0.0] * 365
    values[319] = ROAD_SCENE_THRESHOLD - 0.01
    values[51] = 1.0 - values[319]
    result = road_scene_decision(values)
    assert result["is_road"] is False


def test_road_scene_names_resolve_through_official_365_index_table():
    assert len(PLACES365_CATEGORIES) == 365
    assert {PLACES365_CATEGORIES[index] for index in ROAD_SCENE_CLASS_IDS} == ROAD_SCENE_NAMES
    assert PLACES365_CATEGORIES[175] == "highway"
    assert PLACES365_CATEGORIES[319] == "street"
    assert PLACES365_CATEGORIES[257] == "parking_lot"
    assert PLACES365_CATEGORIES[66] == "bridge"
    assert PLACES365_CATEGORIES[112] == "crosswalk"


def test_wrong_number_of_classes_fails_closed():
    with pytest.raises(ValueError):
        road_scene_decision([1.0, 0.0])
