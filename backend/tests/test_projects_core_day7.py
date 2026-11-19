from dataclasses import FrozenInstanceError
from copy import deepcopy

import pytest

from app.projects import (Pin, create_project, update_brief, pin_image,
                          remove_purpose, encode_purposes, decode_purposes)


def test_project_create_and_update_brief_are_immutable():
    project = create_project(1, " 建筑展 ", " 复古建筑 ")
    updated = update_brief(project, "现代极简")
    assert (project.name, project.brief, updated.brief) == ("建筑展", "复古建筑", "现代极简")
    assert (updated.id, updated.name) == (project.id, project.name)
    with pytest.raises(FrozenInstanceError):
        project.brief = "changed"


def test_repeated_pin_merges_without_duplicate_and_preserves_note():
    first = pin_image([], 1, 20, ["style", "layout"], note="看标题")
    merged = pin_image(first, 1, 20, ["palette", "style"])
    assert len(merged) == 1
    assert merged[0] == Pin(1, 20, ("layout", "style", "palette"), "看标题")
    assert first[0].purposes == ("layout", "style")
    assert pin_image(merged, 1, 20, ["style"]) == merged
    assert pin_image(merged, 1, 20, ["style"], note="")[0].note == ""


def test_remove_one_keeps_other_uses_and_delete_only_last():
    pins = pin_image([], 1, 20, ["layout", "style"])
    once = remove_purpose(pins, 1, 20, "layout")
    assert once == (Pin(1, 20, ("style",)),)
    assert remove_purpose(once, 1, 20, "layout") == once
    assert remove_purpose(once, 1, 20, "style") == ()


def test_cross_project_same_image_is_independent_and_unrelated_pins_unchanged():
    pins = pin_image([], 1, 20, ["layout"])
    pins = pin_image(pins, 2, 20, ["palette"])
    pins = pin_image(pins, 1, 21, ["subject"])
    assert remove_purpose(pins, 1, 20, "layout") == pins[1:]
    assert remove_purpose(pins, 9, 20, "layout") == pins


def test_purposes_fit_existing_text_column_and_legacy_values():
    assert encode_purposes(["subject", "style", "style"]) == '["style","subject"]'
    assert decode_purposes('["palette","layout"]') == ("layout", "palette")
    assert decode_purposes("layout") == ("layout",)
    assert decode_purposes(None) == decode_purposes("") == ()


@pytest.mark.parametrize("value", ['{"layout":true}', '"layout"', "oops", '["fake"]', 42])
def test_bad_persisted_purpose_rejected(value):
    with pytest.raises(ValueError):
        decode_purposes(value)


@pytest.mark.parametrize("purposes", [[], ["fake"], "layout", [None]])
def test_invalid_or_empty_pin_command_rejected(purposes):
    with pytest.raises(ValueError):
        pin_image([], 1, 20, purposes)


@pytest.mark.parametrize("project_id,image_id", [(True, 1), (0, 1), (1, -1), (1, "2")])
def test_invalid_identity_rejected(project_id, image_id):
    with pytest.raises(ValueError):
        pin_image([], project_id, image_id, ["layout"])


def test_duplicate_snapshot_cannot_silently_overwrite_a_pin():
    with pytest.raises(ValueError, match="duplicate"):
        pin_image([Pin(1, 2, ("layout",)), Pin(1, 2, ("style",))], 1, 2, ["palette"])




def test_brief_update_does_not_reclassify_pins():
    project = create_project(1, "海报", "复古")
    pins = pin_image([], project.id, 20, ["style"])
    update_brief(project, "现代")
    assert pins == (Pin(1, 20, ("style",)),)


def test_all_four_purposes_and_caller_owned_inputs_are_preserved():
    requested = ["subject", "palette", "style", "layout"]
    original = requested.copy()
    pins = pin_image([], 1, 2, requested)
    requested.clear()
    assert pins[0].purposes == ("layout", "style", "palette", "subject")
    assert decode_purposes(encode_purposes(original)) == pins[0].purposes
    assert remove_purpose(pins, 1, 2, "palette")[0].purposes == ("layout", "style", "subject")


def test_project_validation_and_invalid_removal_leave_originals_intact():
    project = create_project(1, "展板", "建筑")
    with pytest.raises(ValueError):
        update_brief(project, None)
    with pytest.raises(ValueError):
        create_project(1, "  ", "建筑")
    pins = (Pin(1, 2, ("layout",)),)
    with pytest.raises(ValueError):
        remove_purpose(pins, 1, 2, "invented")
    assert project.brief == "建筑" and pins == (Pin(1, 2, ("layout",)),)
