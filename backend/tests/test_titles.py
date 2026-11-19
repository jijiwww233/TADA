"""短标题：规则校验、文字模型生成、人工标题优先。不联网（MockTransport）。"""
import json

import httpx
import pytest

from app import db, importer, titles, vision
from conftest import png

GOOD = {
    "visible_text": "",
    "subject": ["展览", "海报"],
    "layout": {"title_position": ["top"], "columns": ["single"], "whitespace": ["much"],
               "image_text_ratio": ["balanced"], "density": ["low"], "layout_note": "大标题压顶"},
    "style": {"tags": ["极简"], "medium": "typography", "style_note": "无衬线大字"},
    "description": "一张极简的展览海报",
}


def add_image(name="red") -> int:
    return importer.import_images([importer.Upload(f"{name}.png", png(name))])["added"][0]["id"]


@pytest.fixture
def lib(data_dir):
    db.init_db()
    return data_dir


# ---- 规则 ----

@pytest.mark.parametrize("raw, expected", [
    ("极简展览海报", "极简展览海报"),
    ("  陶土色海报  ", "陶土色海报"),
    ("「陶土色海报」", "陶土色海报"),
    ("标题：极简展览海报。", "极简展览海报"),
    ("《山海经》插画", "《山海经》插画"),  # 只有成对包裹才剥，单侧不猜
])
def test_validate_strips_wrappers(raw, expected):
    assert titles.validate(raw) == expected


@pytest.mark.parametrize("raw, message", [
    ("", "为空"),
    ("插", "太短"),
    ("这是一张展会海报", "描述性开头"),
    ("关于展览和海报的", "半句话"),
    ("极简展览海报与装帧设计海报", "超过"),
    ("Poster", "没有中文"),
    ("极简展览海…", "截断"),
])
def test_validate_rejects(raw, message):
    with pytest.raises(ValueError, match=message):
        titles.validate(raw)


def test_normalize_is_lenient():
    assert titles.normalize({"title": "极简展览海报"}) == "极简展览海报"
    assert titles.normalize("极简的") is None
    assert titles.normalize(None) is None
    assert titles.normalize(3) is None


def test_vision_keeps_a_good_title_and_drops_a_bad_one():
    assert vision.validate({**GOOD, "title": "极简展览海报"})["title"] == "极简展览海报"
    # 标题不合规不影响整条分析：留空，之后用文字模型补
    assert vision.validate({**GOOD, "title": "这是一张"})["title"] is None
    assert vision.validate(GOOD)["title"] is None


# ---- 文字模型 ----

class FakeText:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        content = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


def settings(model="qwen3.7-flash"):
    return titles.TextSettings("https://model.invalid/v1", "sk-test", model)


def test_generate_uses_the_text_model(lib):
    fake = FakeText("极简展览海报")
    assert titles.generate("一张极简的展览海报", settings=settings(), client=fake.client()) == "极简展览海报"
    body = json.loads(fake.requests[0].content)
    assert body["model"] == "qwen3.7-flash"
    assert body["enable_thinking"] is False
    assert "极简" in body["messages"][1]["content"]


def test_generate_retries_once_then_raises(lib):
    fake = FakeText("这是一张", "极简展览海报")
    assert titles.generate("描述", settings=settings(), client=fake.client()) == "极简展览海报"
    fake2 = FakeText("这是一张", "极简的")
    with pytest.raises(titles.TitleError):
        titles.generate("描述", settings=settings(), client=fake2.client())
    assert len(fake2.requests) == 2


# ---- 存储 ----

def test_manual_title_wins_and_generated_never_overwrites_it(lib):
    image_id = add_image()
    assert titles.store_generated(image_id, "极简展览海报") is True
    assert titles.effective_title(image_id) == "极简展览海报"

    assert titles.set_manual(image_id, "我自己的标题") == "我自己的标题"
    assert titles.store_generated(image_id, "另一个标题") is False
    assert titles.effective_title(image_id) == "我自己的标题"

    assert titles.set_manual(image_id, None) is None
    assert titles.effective_title(image_id) == "极简展览海报"


def test_manual_title_validation(lib):
    image_id = add_image()
    with pytest.raises(ValueError):
        titles.set_manual(image_id, "   ")
    with pytest.raises(ValueError):
        titles.set_manual(image_id, "长" * (titles.MAX_MANUAL_CHARS + 1))
    with pytest.raises(KeyError):
        titles.set_manual(99999, "标题")


def test_effective_titles_reach_the_grid_list(lib):
    image_id = add_image()
    titles.store_generated(image_id, "极简展览海报")
    listed = {row["id"]: row for row in importer.list_images()}
    assert listed[image_id]["title"] == "极简展览海报"
    titles.set_manual(image_id, "人工标题")
    listed = {row["id"]: row for row in importer.list_images()}
    assert listed[image_id]["title"] == "人工标题"
