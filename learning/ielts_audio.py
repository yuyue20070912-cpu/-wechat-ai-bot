"""Five original texts and optional, human-reviewed static MP3s. Never synthesise at runtime."""
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

PILOT = json.loads((Path(__file__).with_name('ielts_listening_pilot.json')).read_text())
MATERIALS = {item['id']: item for item in PILOT['materials']}
BY_TEMPLATE = {item['template_id']: item for item in MATERIALS.values()}
ASSET_ROOT = Path(__file__).resolve().parents[1] / 'static' / 'learning-audio' / 'ielts'
MAX_BYTES = 8 * 1024 * 1024


def text_hash(item):
    return hashlib.sha256(item['transcript'].encode('utf-8')).hexdigest()


@lru_cache(maxsize=4)
def published_assets(root):
    """Deployment assets are immutable for a process lifetime; reject incomplete releases."""
    root = Path(root)
    try:
        manifest = json.loads((root / 'manifest.json').read_text())
        if not isinstance(manifest, dict):
            return {}
        if manifest.get('version') != 1 or not isinstance(manifest.get('assets'), dict):
            return {}
    except (OSError, ValueError, TypeError):
        return {}
    valid = {}
    for audio_id, entry in manifest['assets'].items():
        item = MATERIALS.get(audio_id)
        if not item or not isinstance(entry, dict):
            continue
        digest = entry.get('sha256', '')
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            continue
        filename = f'{audio_id}.{digest}.mp3'
        if (entry.get('filename') != filename or entry.get('source_sha256') != text_hash(item)
                or entry.get('locale') != 'en-GB' or entry.get('human_reviewed') is not True
                or not all(isinstance(entry.get(key), str) and entry[key].strip() for key in ('provider', 'voice', 'rights'))):
            continue
        try:
            path = root / filename
            if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= MAX_BYTES:
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                continue
        except OSError:
            continue
        valid[audio_id] = {**entry, 'url': '/learning/audio/ielts/' + filename}
    return valid


def assets():
    return published_assets(str(ASSET_ROOT))


def descriptor(template):
    item = BY_TEMPLATE.get(template['id'])
    # Old session snapshots can use the pilot only when the exact original text matches.
    if not item or template.get('transcript') != item['transcript']:
        return None
    asset = assets().get(item['id'])
    fallback = bool(template.get('audio_file'))
    return dict(id=item['id'], status='ready' if asset else 'legacy' if fallback else 'unavailable',
                url=asset['url'] if asset else None, has_fallback=fallback,
                notice='原创 IELTS 风格材料，非官方音频。已接入人工确认试听的预生成 MP3。' if asset else
                '高质量 MP3 尚未接入；当前为原有 eSpeak 合成样例，非官方音频。' if fallback else
                '高质量 MP3 尚未接入，本题暂不能播放；请改选其他听力题组。')
