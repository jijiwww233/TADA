"""Pure source-scoped image visibility; does not change search/recommendation code."""
from copy import deepcopy

from .collection_classification import ClassificationState, effective_category, should_include


def visible_image_sources(images, post_states, surface):
    """Include an image once iff any source post is visible; expose only those sources.

    images: [{image_id, ...display_fields, sources:[{post_id, position?, ...}]}]
    post_states: {post_id: ClassificationState}. Missing state means uncertain.
    No image-level permanent category is emitted or updated. Empty-source images
    remain inspectable in Library; otherwise no source is invented for them.
    """
    should_include("uncertain", surface)  # validate even for an empty input
    merged = {}
    for image in images:
        image_id = image["image_id"]
        if image_id not in merged:
            merged[image_id] = {k: deepcopy(v) for k, v in image.items() if k not in {"sources", "category"}}
            merged[image_id]["sources"] = []
        for source in image.get("sources", []):
            state = post_states.get(source["post_id"], ClassificationState())
            resolved = effective_category(state)
            if should_include(resolved["category"], surface):
                visible = {**deepcopy(source), "effective_classification": resolved}
                if visible not in merged[image_id]["sources"]:
                    merged[image_id]["sources"].append(visible)
    return [image for image in merged.values() if image["sources"] or surface == "library"]
