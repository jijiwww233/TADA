"""Review finding 10: probe_open must trip the cooldown on an XHS risk page.

It used to return False only, so the UI retry button could launch a fresh browser
probe again and again after 安全限制 / captcha. Ordinary network failures and blank
pages must stay non-tripping.
"""
import sys
import types

import pytest

from app.sync import safety, xhs_source


def install_fake_playwright(monkeypatch, page):
    class FakeBrowser:
        def __init__(self):
            self.closed = False

        def new_page(self):
            return page

        def close(self):
            self.closed = True

    class FakeChromium:
        def launch(self, **kwargs):
            return FakeBrowser()

    class FakePlaywright:
        def __init__(self):
            self.chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    module = types.ModuleType('playwright.sync_api')
    module.sync_playwright = lambda: FakePlaywright()
    package = types.ModuleType('playwright')
    package.sync_api = module
    monkeypatch.setitem(sys.modules, 'playwright', package)
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', module)


class FakePage:
    def __init__(self, url='https://www.xiaohongshu.com/explore/x', title='', body='',
                 error=None):
        self.url, self.title_text, self.body, self.error = url, title, body, error

    def goto(self, *args, **kwargs):
        if self.error is not None:
            raise self.error

    def wait_for_timeout(self, *args):
        pass

    def inner_text(self, selector):
        return self.body

    def title(self):
        return self.title_text


@pytest.fixture(autouse=True)
def fresh_safety(tmp_path, monkeypatch):
    monkeypatch.setenv('WOMINMIN_SAFETY_DIR', str(tmp_path / 'safety'))
    yield


@pytest.mark.parametrize('title,body,url', [
    ('安全限制', '', 'https://www.xiaohongshu.com/explore/x'),
    ('', '当前网络存在风险，请稍后再试', 'https://www.xiaohongshu.com/explore/x'),
    ('', '请完成安全验证后继续', 'https://www.xiaohongshu.com/explore/x'),
    ('', '', 'https://www.xiaohongshu.com/website-login/verify?redirect=x'),
    ('', '暂时无法浏览该笔记', 'https://www.xiaohongshu.com/explore/x'),
])
def test_classify_probe_marks_risk_pages(title, body, url):
    assert xhs_source.classify_probe(title, body, url) == 'blocked'


def test_classify_probe_distinguishes_blank_and_normal():
    assert xhs_source.classify_probe('', '   ', 'https://www.xiaohongshu.com/explore/x') == 'blank'
    assert xhs_source.classify_probe('标题', '正文', 'https://www.xiaohongshu.com/explore/x') == 'normal'


def test_probe_open_trips_cooldown_on_risk_page(monkeypatch):
    page = FakePage(title='安全限制', body='你的操作不像正常人')
    install_fake_playwright(monkeypatch, page)
    assert xhs_source.probe_open('https://www.xiaohongshu.com/explore/x') is False
    state = safety.state()
    assert state['blocked'] is True
    assert state['reason'] == '原帖探测遇到安全限制'


def test_probe_open_does_not_launch_again_while_blocked(monkeypatch):
    page = FakePage(title='安全限制', body='安全限制')
    install_fake_playwright(monkeypatch, page)
    assert xhs_source.probe_open('https://www.xiaohongshu.com/explore/x') is False
    launch_calls = []

    class NoLaunch:
        def __enter__(self):
            launch_calls.append(1)
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setitem(sys.modules, 'playwright.sync_api',
                        types.SimpleNamespace(sync_playwright=NoLaunch))
    assert xhs_source.probe_open('https://www.xiaohongshu.com/explore/x') is False
    assert launch_calls == []


def test_probe_open_network_failure_does_not_trip(monkeypatch):
    install_fake_playwright(monkeypatch, FakePage(error=TimeoutError('timeout')))
    assert xhs_source.probe_open('https://www.xiaohongshu.com/explore/x') is False
    assert safety.state()['blocked'] is False


def test_probe_open_blank_page_does_not_trip(monkeypatch):
    install_fake_playwright(monkeypatch, FakePage(body='   '))
    assert xhs_source.probe_open('https://www.xiaohongshu.com/explore/x', '标题') is False
    assert safety.state()['blocked'] is False


def test_probe_open_matching_title_still_succeeds(monkeypatch):
    install_fake_playwright(monkeypatch, FakePage(title='建筑展板', body='正文 建筑展板 参考案例'))
    assert xhs_source.probe_open('https://www.xiaohongshu.com/explore/x', '建筑展板') is True
    assert safety.state()['blocked'] is False
