"""Review finding 9: redirects must be followed manually, one validated hop at a time.

Automatic follow_redirects=True could be sent to any host returned by a short link,
without another safety.require(); the final URL was never host-checked before the
note id was extracted. No request may ever be sent outside ALLOWED_HOSTS.
"""
import httpx
import pytest

from app import db, imports
from app.sync import safety

NOTE_A = "a" * 24


@pytest.fixture(autouse=True)
def fresh_last_results():
    imports._LAST.update(captured=None, share=None)
    yield


@pytest.fixture
def db_ready(data_dir):
    db.init_db()


def client_factory(handler, seen_kwargs=None):
    def factory(**kwargs):
        if seen_kwargs is not None:
            seen_kwargs.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(handler),
                            follow_redirects=kwargs.get('follow_redirects', True),
                            timeout=kwargs.get('timeout'), headers=kwargs.get('headers'))
    return factory


def note_html(note_id):
    return ('<script>window.__INITIAL_STATE__={"note":{"noteDetailMap":{"' + note_id + '":'
            '{"note":{"noteId":"' + note_id + '","title":"标题","desc":"正文","imageList":[]}}}}}</script>')


def test_cross_domain_redirect_is_rejected_before_the_next_request():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'location': f'https://evil.example/explore/{NOTE_A}'})

    with pytest.raises(imports.ImportRejected):
        imports._http_fetch('https://xhslink.com/short', client_factory=client_factory(handler))
    assert calls == ['https://xhslink.com/short']


@pytest.mark.parametrize('location', [
    f'https://user:pass@www.xiaohongshu.com/explore/{NOTE_A}',
    f'https://www.xiaohongshu.com:8443/explore/{NOTE_A}',
    'file:///etc/passwd',
    'javascript:alert(1)',
])
def test_invalid_redirect_targets_never_get_a_request(location):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'location': location})

    with pytest.raises(imports.ImportRejected):
        imports._http_fetch('https://xhslink.com/short', client_factory=client_factory(handler))
    assert calls == ['https://xhslink.com/short']


def test_allowed_short_link_redirect_reaches_xiaohongshu():
    calls = []
    final = f'https://www.xiaohongshu.com/explore/{NOTE_A}?xsec_token=token-value'

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(302, headers={'location': final})
        return httpx.Response(200, text=note_html(NOTE_A))

    seen = []
    url, html = imports._http_fetch('https://xhslink.com/short',
                                    client_factory=client_factory(handler, seen))
    assert calls == ['https://xhslink.com/short', final]
    assert url == final and 'noteDetailMap' in html
    assert seen[0]['follow_redirects'] is False  # no hidden automatic hops


def test_redirect_limit_is_enforced():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'location': f'https://xhslink.com/hop{len(calls)}'})

    with pytest.raises(imports.ImportRejected):
        imports._http_fetch('https://xhslink.com/short', client_factory=client_factory(handler))
    assert len(calls) == imports.MAX_REDIRECTS + 1


def test_cooldown_tripped_between_hops_blocks_the_next_request():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        safety.trip(manual=True)
        return httpx.Response(302, headers={'location': f'https://www.xiaohongshu.com/explore/{NOTE_A}'})

    with pytest.raises(safety.CoolingDown):
        imports._http_fetch('https://xhslink.com/short', client_factory=client_factory(handler))
    assert len(calls) == 1 and safety.state()['blocked'] is True


def test_custom_fetcher_final_url_must_stay_on_the_allowlist(db_ready):
    def fetcher(url):
        return f'https://evil.example/explore/{NOTE_A}', note_html(NOTE_A)

    result = imports.ShareImporter(fetcher=fetcher, sleep=lambda _: None).import_urls(
        ['https://xhslink.com/short'])
    assert result['created'] == 0 and result['failed']
    assert '只接受' in result['failed'][0]['error']
    assert result['failed'][0]['note_id'] is None
    conn = db.connect()
    try:
        assert conn.execute('SELECT COUNT(*) FROM posts').fetchone()[0] == 0
    finally:
        conn.close()
