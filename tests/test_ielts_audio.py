"""Static MP3 pilot plumbing; test audio uses real legacy files, not quality-approved pilot recordings."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
from threading import Thread

import pytest
from werkzeug.serving import make_server

import bot
from learning import ielts_audio
from learning.mock_bank import BANK, public_template
from test_mocks import client, start, write
from tools import prepare_ielts_audio

LEGACY_ROOT = Path(__file__).resolve().parents[1] / 'learning' / 'assets' / 'mock_audio'


@pytest.fixture
def pilot_media(tmp_path, monkeypatch):
    """Never publish these test-only manifests: old eSpeak files only test delivery/decoding."""
    root = tmp_path / 'media'
    root.mkdir()
    entries = {}
    fixtures = sorted(LEGACY_ROOT.glob('*.mp3'))[:5]
    assert len(fixtures) == 5
    for (audio_id, item), path in zip(ielts_audio.MATERIALS.items(), fixtures):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        filename = f'{audio_id}.{digest}.mp3'
        (root / filename).write_bytes(data)
        entries[audio_id] = dict(filename=filename, sha256=digest, source_sha256=ielts_audio.text_hash(item),
            locale='en-GB', human_reviewed=True, provider='TEST FIXTURE ONLY', voice='legacy decoder fixture',
            rights='Project original legacy audio used for tests only')
    (root / 'manifest.json').write_text(json.dumps(dict(version=1, assets=entries)))
    monkeypatch.setattr(ielts_audio, 'ASSET_ROOT', root)
    ielts_audio.published_assets.cache_clear()
    yield root, entries
    ielts_audio.published_assets.cache_clear()


@pytest.fixture
def empty_pilot_media(tmp_path, monkeypatch):
    monkeypatch.setattr(ielts_audio, 'ASSET_ROOT', tmp_path)
    ielts_audio.published_assets.cache_clear()
    yield
    ielts_audio.published_assets.cache_clear()


def test_five_materials_have_stable_mappings_and_pending_truthful_state(empty_pilot_media):
    assert len(ielts_audio.MATERIALS) == 5
    assert ielts_audio.assets() == {}  # absent release must remain explicitly unavailable
    for item in ielts_audio.MATERIALS.values():
        template = BANK[item['template_id']]
        public = public_template(template)
        assert public['has_audio']
        assert 'transcript' not in public and 'audio_file' not in public
        assert public['audio']['id'] == item['id']
        assert public['audio']['url'] is None
        assert public['audio']['status'] == ('legacy' if template['audio_file'] else 'unavailable')
        assert '非官方' in template['notice'] or '官方' in template['notice']
        assert len(template['questions']) == 3
    assert len({item['transcript'] for item in ielts_audio.MATERIALS.values()}) == 5


def test_static_audio_reuses_url_range_etag_and_no_tts(user, ai, pilot_media, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Serving/replaying fixed audio must never synthesise speech')
    monkeypatch.setattr(prepare_ielts_audio.subprocess, 'run', forbidden)
    c = client(user)
    item = next(iter(ielts_audio.MATERIALS.values()))
    session = start(c, item['template_id']).json['session']
    source = session['template']['audio']['url']
    assert source.endswith('.mp3') and session['id'] not in source
    assert session['template']['audio']['status'] == 'ready'
    public_client = bot.app.test_client()
    response = public_client.get(source)
    assert response.status_code == 200 and response.mimetype == 'audio/mpeg'
    assert response.data == (pilot_media[0] / pilot_media[1][item['id']]['filename']).read_bytes()
    assert response.headers['Cache-Control'] == 'public, max-age=31536000, immutable'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    etag = response.headers['ETag']
    for _ in range(3):
        partial = public_client.get(source, headers={'Range': 'bytes=0-255'})
        assert partial.status_code == 206 and partial.data == response.data[:256]
        assert 'bytes 0-255/' in partial.headers['Content-Range']
    assert public_client.head(source).status_code == 200
    cached = public_client.get(source, headers={'If-None-Match': etag})
    assert cached.status_code == 304 and not cached.data
    assert 'immutable' in cached.headers['Cache-Control']
    assert public_client.get(source, headers={'Range': 'bytes=999999999-'}).status_code == 416
    assert c.get('/api/learning/mocks/'+session['id']).headers['Cache-Control'] == 'no-store'
    assert write(c, session, {}, 'submit').status_code == 200
    second = start(c, item['template_id']).json['session']
    assert second['id'] != session['id'] and second['template']['audio']['url'] == source
    assert not ai.calls
    with bot.db_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM learning_attempts').fetchone()[0] == 0


def test_all_five_fixed_files_are_distinct_and_correctly_mapped(pilot_media):
    urls = set()
    for audio_id, item in ielts_audio.MATERIALS.items():
        audio = public_template(BANK[item['template_id']])['audio']
        assert audio['id'] == audio_id and audio['status'] == 'ready'
        response = bot.app.test_client().get(audio['url'])
        assert hashlib.sha256(response.data).hexdigest() == pilot_media[1][audio_id]['sha256']
        urls.add(audio['url'])
    assert len(urls) == 5
    old = copy.deepcopy(BANK[next(iter(ielts_audio.BY_TEMPLATE))])
    old['transcript'] += ' Different saved session text.'
    assert 'audio' not in public_template(old)  # never substitute a different transcript in an old snapshot


@pytest.mark.parametrize('corruption', ['missing', 'hash', 'source', 'unreviewed', 'locale', 'path', 'bad-json', 'bad-type'])
def test_invalid_release_fails_closed(pilot_media, corruption):
    root, entries = pilot_media
    audio_id = next(iter(entries)); entry = entries[audio_id]
    url = '/learning/audio/ielts/' + entry['filename']
    if corruption == 'missing': (root / entry['filename']).unlink()
    elif corruption == 'hash': (root / entry['filename']).write_bytes(b'not an MP3')
    elif corruption == 'source': entry['source_sha256'] = '0'*64
    elif corruption == 'unreviewed': entry['human_reviewed'] = False
    elif corruption == 'locale': entry['locale'] = 'en-US'
    elif corruption == 'path': entry['filename'] = '../../bot.py'
    if corruption == 'bad-json': (root / 'manifest.json').write_text('{')
    elif corruption == 'bad-type': (root / 'manifest.json').write_text('[]')
    else: (root / 'manifest.json').write_text(json.dumps(dict(version=1, assets=entries)))
    assert audio_id not in ielts_audio.assets()
    response = bot.app.test_client().get(url)
    assert response.status_code == 404 and response.headers['Cache-Control'] == 'no-store'
    assert public_template(BANK[ielts_audio.MATERIALS[audio_id]['template_id']])['audio']['status'] == 'legacy'


def test_new_pending_form_has_explicit_no_legacy_error(user, empty_pilot_media):
    c = client(user)
    session = start(c, 'ielts-listening-c-short-v1').json['session']
    assert session['template']['audio']['status'] == 'unavailable'
    response = c.get('/api/learning/mocks/'+session['id']+'/audio')
    assert response.status_code == 404 and '暂无原有音频可回退' in response.json['error']
    assert write(c, session, {}, 'submit').status_code == 200


def test_preparation_defaults_to_local_texts_and_requires_paid_approval(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('No paid approval; generation must not run')
    monkeypatch.setattr(prepare_ielts_audio, 'generate', forbidden)
    prepare_ielts_audio.main(['export', str(tmp_path)])
    assert len(list(tmp_path.glob('*.txt'))) == 5 and not list(tmp_path.glob('*.mp3'))
    plan = json.loads((tmp_path/'plan.json').read_text())
    assert plan['words'] == 500 and plan['clips'] == 5
    with pytest.raises(SystemExit) as error:
        prepare_ielts_audio.main(['generate',str(tmp_path),'--model','not-called','--voice','not-called'])
    assert error.value.code == 2
    with pytest.raises(SystemExit):
        prepare_ielts_audio.main(['install',str(tmp_path),'--provider','x','--voice','x','--rights','x'])


@pytest.mark.skipif(not shutil.which('ffprobe'), reason='ffprobe is a preparation-only dependency')
def test_local_install_validates_all_five_before_publishing(pilot_media, tmp_path):
    root, entries = pilot_media
    source = tmp_path / 'supplied'; source.mkdir()
    for audio_id, entry in entries.items():
        shutil.copyfile(root / entry['filename'], source / (audio_id+'.mp3'))
    target = tmp_path / 'published'
    prepare_ielts_audio.install(source, target, 'TEST ONLY', 'legacy test fixtures', 'test-only original files')
    assert len(ielts_audio.published_assets(str(target))) == 5
    (source / (next(iter(entries))+'.mp3')).write_bytes(b'bad')
    failed = tmp_path/'failed'
    with pytest.raises((ValueError, prepare_ielts_audio.subprocess.CalledProcessError)):
        prepare_ielts_audio.install(source, failed, 'TEST ONLY', 'legacy fixtures', 'test rights')
    assert not (failed/'manifest.json').exists()


@pytest.mark.parametrize('mode', ['ready', 'fallback', 'no-fallback', 'pending'])
def test_iphone_webkit_fixed_playback_and_errors(user, ai, pilot_media, mode):
    playwright = pytest.importorskip('playwright.sync_api')
    root, entries = pilot_media
    # Cache the validated deployment, then simulate a missing/corrupt response on the client.
    assert len(ielts_audio.assets()) == 5
    if mode == 'pending':
        (root/'manifest.json').write_text('{"version":1,"assets":{}}')
        ielts_audio.published_assets.cache_clear()
    server = make_server('127.0.0.1', 0, bot.app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    c = client(user); c.get('/api/learning/profiles')
    try:
        with playwright.sync_playwright() as p:
            if not Path(p.webkit.executable_path).exists():
                pytest.skip('Install WebKit for Safari coverage')
            browser = p.webkit.launch()
            context = browser.new_context(**p.devices['iPhone 13'])
            context.add_cookies([dict(name='session', value=c.get_cookie('session').value, url=origin)])
            page = context.new_page(); requests = []
            page.on('request', lambda request: requests.append(request.url))
            if mode in ('fallback', 'no-fallback'):
                page.route('**/learning/audio/ielts/*.mp3', lambda route: route.fulfill(status=404, body='not found'))
            page.goto(origin+'/learning#mocks')
            page.locator('#mock-template option').first.wait_for(state='attached')
            materials = list(ielts_audio.MATERIALS.values()) if mode == 'ready' else [list(ielts_audio.MATERIALS.values())[2 if mode in ('no-fallback','pending') else 0]]
            for item in materials:
                requests.clear()
                page.locator('#mock-template').select_option(item['template_id'])
                page.locator('#mock-start').click()
                if mode == 'pending':
                    playwright.expect(page.locator('#mock-material')).to_contain_text('本题暂不能播放')
                    assert page.locator('#mock-material audio').count() == 0
                    assert not [url for url in requests if url.endswith('.mp3')]
                else:
                    audio = page.locator('#mock-material audio')
                    audio.wait_for()
                    assert audio.get_attribute('preload') == 'none'
                    assert item['id'] in audio.get_attribute('data-audio-src')
                    assert not [url for url in requests if url.endswith('.mp3') or url.endswith('/audio')]
                    page.get_by_role('button', name='播放听力', exact=True).click()
                    if mode == 'no-fallback':
                        playwright.expect(page.locator('#mock-material')).to_contain_text('音频加载失败')
                        assert not [url for url in requests if url.endswith('/audio')]
                    else:
                        if mode == 'fallback':
                            playwright.expect(page.locator('#mock-material')).to_contain_text('已切换原有 eSpeak')
                            page.get_by_role('button',name='播放听力',exact=True).click()
                        try:
                            page.wait_for_function('document.querySelector("#mock-material audio").currentTime > 0.05', timeout=5000)
                        except Exception:
                            print(audio.evaluate('(a)=>({error:a.error?.message,code:a.error?.code,ready:a.readyState,src:a.src})'))
                            raise
                        assert audio.evaluate('(a)=>a.error===null && !a.paused')
                        audio.evaluate('(a)=>a.pause()')
                        page.get_by_role('button',name='再次播放 / 继续',exact=True).click()
                        page.wait_for_function('!document.querySelector("#mock-material audio").paused')
                        if mode == 'ready':
                            assert not [url for url in requests if url.endswith('/audio')]
                            assert all('/learning/audio/ielts/' in url for url in requests if url.endswith('.mp3'))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.once('dialog', lambda dialog: dialog.accept())
                page.locator('#mock-submit').click()
                page.get_by_text('已交卷，报告已保存。',exact=True).wait_for()
            assert not ai.calls
            context.close(); browser.close()
    finally:
        server.shutdown(); thread.join(timeout=5); server.server_close()
