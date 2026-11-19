"""Review finding 8: share-import failures must not echo the original URL/token.

The raw url (or url[:300]) used to be stored in the failed list and replayed by
/api/import/status; a short-lived xsec_token or any query parameter leaked both.
"""
import json

import httpx
import pytest

from app import db, imports

NOTE_A = "a" * 24
SECRET = "XSECRETTOKEN123456"
RAW = (f"https://www.xiaohongshu.com/explore/{NOTE_A}"
       f"?xsec_token={SECRET}&xsec_source=pc_share&utm={SECRET}#{SECRET}")
CLEAN = f"https://www.xiaohongshu.com/explore/{NOTE_A}"


@pytest.fixture(autouse=True)
def fresh_last_results():
    imports._LAST.update(captured=None, share=None)
    yield


@pytest.fixture
def db_ready(data_dir):
    db.init_db()


def failing_fetcher(status=404):
    def fetcher(url):
        request = httpx.Request('GET', url)
        raise httpx.HTTPStatusError(f'HTTP {status}', request=request,
                                    response=httpx.Response(status, request=request))
    return fetcher


def note_html(note_id):
    return ('<script>window.__INITIAL_STATE__={"note":{"noteDetailMap":{"' + note_id + '":'
            '{"note":{"noteId":"' + note_id + '","title":"标题","desc":"正文","imageList":[]}}}}}</script>')


def assert_no_secret(value):
    text = json.dumps(value, ensure_ascii=False)
    assert SECRET not in text
    assert 'xsec_token' not in text
    assert 'utm=' not in text


def test_failed_entry_is_sanitized_and_locatable(db_ready):
    result = imports.ShareImporter(fetcher=failing_fetcher(), sleep=lambda _: None).import_urls([RAW])
    entry = result['failed'][0]
    assert entry['input_index'] == 1
    assert entry['note_id'] == NOTE_A
    assert entry['link'] == CLEAN
    # Existing import UI reads "url"; it must be the same sanitized value, not the input.
    assert entry['url'] == CLEAN
    assert_no_secret(result)


def test_last_results_are_sanitized(db_ready):
    imports.ShareImporter(fetcher=failing_fetcher(), sleep=lambda _: None).import_urls([RAW])
    last = imports.status()['last']['share']
    assert last['failed'][0]['link'] == CLEAN
    assert_no_secret(last)


def test_unparseable_link_failure_has_index_without_raw_url(db_ready):
    short = f'https://xhslink.com/short-A?xsec_token={SECRET}#{SECRET}'

    def fetcher(url):
        return 'https://www.xiaohongshu.com/discovery/item/not-a-note', note_html(NOTE_A)

    result = imports.ShareImporter(fetcher=fetcher, sleep=lambda _: None).import_urls([short])
    entry = result['failed'][0]
    assert entry['input_index'] == 1 and entry['note_id'] is None
    assert entry['link'] == 'https://xhslink.com/short-A'  # no query/fragment/userinfo
    assert 'xsec_token' not in entry['link'] and '#' not in entry['link']
    assert_no_secret(result)


def test_userinfo_is_stripped_from_any_link(db_ready):
    assert imports._sanitized_link(
        f'https://user:pass@www.xiaohongshu.com/explore/{NOTE_A}?xsec_token={SECRET}') == CLEAN
    result = imports.ShareImporter(fetcher=failing_fetcher(), sleep=lambda _: None).import_urls(
        [f'https://user:pass@www.xiaohongshu.com/explore/{NOTE_A}?xsec_token={SECRET}'])
    entry = result['failed'][0]
    assert entry['link'] == CLEAN
    assert_no_secret(result)


def test_non_string_entry_is_sanitized(db_ready):
    result = imports.ShareImporter(fetcher=failing_fetcher(), sleep=lambda _: None).import_urls(
        [{'token': SECRET}])
    assert result['failed'][0]['input_index'] == 1
    assert_no_secret(result)


def test_success_path_unchanged_and_has_no_url_echo(db_ready):
    result = imports.ShareImporter(fetcher=lambda url: (url, note_html(NOTE_A)),
                                   sleep=lambda _: None).import_urls([RAW])
    assert result == {'mode': 'share', 'created': 1, 'skipped': 0, 'failed': [], 'total': 1}
    assert_no_secret(imports.status()['last']['share'])
