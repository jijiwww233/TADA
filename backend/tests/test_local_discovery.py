from PIL import Image

from app.local_discovery import color_spread, representative_hits


def test_color_diversity_counts_separate_hues_not_gradient_shades(tmp_path):
    pink = tmp_path / "pink.png"
    colorful = tmp_path / "colorful.png"
    gradient = Image.new("RGB", (60, 16))
    for x in range(60):
        # Adjacent reds and pinks should count as one family.
        gradient.paste((190 + x, 36 + x, 78 + x), (x, 0, x + 1, 16))
    gradient.save(pink)
    for i, color in enumerate(("#e64152", "#3db577", "#2c5ce5")):
        gradient.paste(color, (20 * i, 0, 20 * (i + 1), 16))
    gradient.save(colorful)
    assert color_spread(pink, "pink") == 0
    assert color_spread(colorful, "colorful") > 0


def test_search_wall_uses_highest_ranked_image_from_each_shared_post():
    hits = [
        {"image_id": 4, "sources": [{"post_id": 1}, {"post_id": 2}]},
        {"image_id": 5, "sources": [{"post_id": 2}]},
        {"image_id": 6, "sources": [{"post_id": 3}]},
    ]
    assert [hit["image_id"] for hit in representative_hits(hits)] == [4, 6]


def test_multicolor_wording_uses_the_same_palette_rule():
    from app.local_discovery import COLOR_RICH, search_subject

    phrases = "颜色很多 很多颜色 五颜六色 彩色 多彩 花花绿绿 色彩丰富 缤纷".split()
    assert all(COLOR_RICH.fullmatch(search_subject(f"{phrase}？")) for phrase in phrases)
    assert not COLOR_RICH.fullmatch("黑白")


def test_short_query_expands_before_applying_the_similarity_floor():
    from app.local_discovery import visual_scores

    class MockSimilarity:
        def text_evidence(self, prompts):
            assert prompts == ("猫", "小猫", "猫咪", "家猫")
            return {1: [.29, .35, .34, .33], 2: [.20, .22, .21, .20]}

    scores, channel = visual_scores("猫？", MockSimilarity())
    assert channel == "semantic" and list(scores) == [1]
