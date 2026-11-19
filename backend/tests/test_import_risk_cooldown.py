"""Review finding 3: a risk/captcha response during share import must trip the
persistent XHS cooldown and stop the whole batch (no further requests).

Also covers the multi-board Huaban runner: a board paused by a risk response must
stop later boards instead of continuing to request them.
"""
import httpx
import pytest

from app import db, imports
from app.sync import safety
from app.sync.huaban_runner import run_boards

NOTE_A = "a" * 24
NOTE_B = "b" * 24
NOTE_C = "c" * 24
URLS = [f"https://www.xiaohongshu.com/explore/{note_id}" for note_id in (NOTE_A, NOTE_B, NOTE_C)]


@pytest.fixture(autouse=True)
def fresh_last_results():
    imports._LAST.update(captured=None, share=None)
    yield


@pytest.fixture
def db_ready(data_dir):
    db.init_db()


def risky_html():
    return '<html><body>请完成安全验证后继续访问</body></html>'


def note_html(note_id):
    return ('<script>window.__INITIAL_STATE__={"note":{"noteDetailMap":{"' + note_id + '":'
            '{"note":{"noteId":"' + note_id + '","title":"标题","desc":"正文","imageList":[]}}}}}</script>')


def import_urls(fetcher, urls=None):
    return imports.ShareImporter(fetcher=fetcher, sleep=lambda _: None).import_urls(urls or URLS)


def test_risk_html_trips_cooldown_and_stops_batch(db_ready):
    calls = []

    def fetcher(url):
        calls.append(url)
        return url, risky_html()

    with pytest.raises(imports.SafetyBlocked):
        import_urls(fetcher)
    assert calls == [URLS[0]]
    state = safety.state()
    assert state['blocked'] is True
    assert state['reason'] == '分享导入检测到风控响应'


def test_captcha_redirect_trips_cooldown_and_stops_batch(db_ready):
    calls = []

    def fetcher(url):
        calls.append(url)
        return 'https://www.xiaohongshu.com/website-login/verify?redirect=1', note_html(NOTE_A)

    with pytest.raises(imports.SafetyBlocked):
        import_urls(fetcher)
    assert calls == [URLS[0]]
    assert safety.state()['blocked'] is True


@pytest.mark.parametrize('status', [403, 429, 461, 471])
def test_risk_http_status_trips_cooldown_and_stops_batch(db_ready, status):
    calls = []

    def fetcher(url):
        calls.append(url)
        request = httpx.Request('GET', url)
        response = httpx.Response(status, request=request)
        raise httpx.HTTPStatusError(f'HTTP {status}', request=request, response=response)

    with pytest.raises(imports.SafetyBlocked):
        import_urls(fetcher)
    assert calls == [URLS[0]]
    assert safety.state()['blocked'] is True


def test_normal_missing_note_does_not_trip_and_batch_continues(db_ready):
    calls = []

    def fetcher(url):
        calls.append(url)
        request = httpx.Request('GET', url)
        raise httpx.HTTPStatusError('HTTP 404', request=request,
                                    response=httpx.Response(404, request=request))

    result = import_urls(fetcher)
    assert calls == URLS
    assert safety.state()['blocked'] is False
    assert len(result['failed']) == len(URLS)


def test_normal_page_still_imports_without_trip(db_ready):
    calls = []

    def fetcher(url):
        calls.append(url)
        return url, note_html(NOTE_A)

    result = import_urls(fetcher, [URLS[0]])
    assert calls == [URLS[0]]
    assert result['created'] == 1 and result['failed'] == []
    assert safety.state()['blocked'] is False


def test_huaban_runner_stops_remaining_boards_after_blocked():
    class Reader:
        platform = 'huaban'

        def list_boards(self):
            return [{'collection_key': 'board:A'}, {'collection_key': 'board:B'},
                    {'collection_key': 'board:C'}]

    calls = []

    def fake_run(reader, key, *, mode, sleep):
        calls.append(key)
        return {'id': len(calls), 'status': 'paused', 'pause_reason': 'blocked',
                'items_new': 0, 'items_failed': 0, 'pages_done': 1,
                'images_ok': 0, 'images_failed': 0}

    report = run_boards(Reader(), run_one=fake_run, sleep=lambda _: None)
    assert calls == ['board:A']
    assert report.boards[0].status == 'paused' and report.boards[0].pause_reason == 'blocked'
    assert report.totals['boards'] == 1
