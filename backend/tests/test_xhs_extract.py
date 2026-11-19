"""xhs parsing, ported from the spike's offline cases (xhs-sync-alt/scripts/test_extract.py)."""
from app.sync.readers.xhs import (classify_api_error, clean, guess_ext, normalize_image_list,
                                  normalize_list_item, normalize_note_detail, note_url, page_kind)


def test_list_item_ssr_note_card():
    out = normalize_list_item({
        "id": "66aa00bb11cc22dd33ee44ff", "xsecToken": "FAKE_TOKEN",
        "noteCard": {"type": "normal", "displayTitle": "米白底红色大标题",
                     "user": {"nickName": "参考作者"},
                     "cover": {"infoList": [{"url": "https://sns-img.example/cover.jpg"}]}},
    })
    assert out["note_id"] == "66aa00bb11cc22dd33ee44ff"
    assert out["xsec_token"] == "FAKE_TOKEN"
    assert (out["title"], out["author"], out["type"]) == ("米白底红色大标题", "参考作者", "normal")
    assert out["cover"].endswith("cover.jpg")


def test_list_item_api_snake_case():
    out = normalize_list_item({"note_id": "66aa00bb11cc22dd33ee4400", "xsec_token": "T2",
                               "note_card": {"display_title": "snake", "user": {"nick_name": "U"}}})
    assert (out["note_id"], out["xsec_token"], out["title"], out["author"]) == (
        "66aa00bb11cc22dd33ee4400", "T2", "snake", "U")
    assert normalize_list_item({"noteCard": {}}) is None


def test_images_keep_server_order():
    card = {"imageList": [{"urlDefault": f"https://sns-img.example/{n}.jpg", "width": 1080}
                          for n in (3, 1, 2)]}
    imgs = normalize_image_list(card)
    assert [i["index"] for i in imgs] == [0, 1, 2]
    assert [i["url"].rsplit("/", 1)[-1] for i in imgs] == ["3.jpg", "1.jpg", "2.jpg"]


def test_images_api_fields_and_missing_url_keeps_position():
    card = {"image_list": [{"url_default": "https://sns-img.example/a.webp"},
                           {"info_list": [{"url": "https://sns-img.example/b.jpg"}]},
                           {"width": 5},
                           {"url_pre": "https://sns-img.example/c.jpg"}]}
    imgs = normalize_image_list(card)
    assert [(i["index"], i["url"].rsplit("/", 1)[-1]) for i in imgs] == [
        (0, "a.webp"), (1, "b.jpg"), (2, ""), (3, "c.jpg")]


def test_detail_feed_and_ssr_shapes():
    out = normalize_note_detail({"items": [{"id": "n1", "note_card": {
        "noteId": "n1", "title": "T", "imageList": [{"urlDefault": "https://x/1.jpg"},
                                                     {"urlDefault": "https://x/2.jpg"}]}}]})
    assert out["note_id"] == "n1" and len(out["images"]) == 2
    out = normalize_note_detail({"note": {"noteId": "n2", "imageList": [{"urlDefault": "https://x/x.png"}]}})
    assert out["note_id"] == "n2"
    assert normalize_note_detail({"note": {"title": "no id"}}) is None


def test_ext_url_and_token_cleaning():
    assert guess_ext("https://x/a.JPG") == ".jpg"
    assert guess_ext("https://x/a", "image/webp") == ".webp"
    assert note_url("abc") == "https://www.xiaohongshu.com/explore/abc"
    assert "SECRET" not in clean("goto https://x/explore/a?xsec_token=SECRET&xsec_source=pc_collect failed")


def test_api_error_and_redirect_classification():
    assert classify_api_error(200, {"code": 0, "success": True}) is None
    assert classify_api_error(200, {"code": -100, "success": False}) == "needs_login"
    assert classify_api_error(200, {"code": 300013}) == "rate_limited"
    assert classify_api_error(461, None) == "blocked"
    assert classify_api_error(500, None) == "network"
    assert page_kind("https://www.xiaohongshu.com/website-login/captcha?redirect=x") == "captcha"
    assert page_kind("https://www.xiaohongshu.com/login?redirect=x") == "login"
    assert page_kind("https://www.xiaohongshu.com/404?source=note") == "notfound"
    assert page_kind("https://www.xiaohongshu.com/explore/abc") is None
