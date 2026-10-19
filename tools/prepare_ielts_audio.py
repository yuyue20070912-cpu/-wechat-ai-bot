"""Prepare/review/install five fixed MP3s. Export/install are local; generation is explicit opt-in."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from learning.ielts_audio import MATERIALS, ASSET_ROOT, MAX_BYTES, text_hash

INSTRUCTIONS = ('Speak in natural British English with a consistent British accent, at approximately 145 words per minute. '
                'Use clear sentence breaks and brief natural pauses. Read only the supplied text, without adding answers, '
                'an introduction, or sound effects. This is original educational practice, not an official IELTS recording.')


def export_texts(output):
    output.mkdir(parents=True, exist_ok=True)
    for item in MATERIALS.values():
        (output / (item['id'] + '.txt')).write_text(item['transcript'] + '\n', encoding='utf-8')
    words = sum(len(item['transcript'].split()) for item in MATERIALS.values())
    characters = sum(len(item['transcript']) for item in MATERIALS.values())
    plan = dict(clips=5, characters=characters, words=words, estimated_minutes=[round(words/155, 2), round(words/135, 2)],
                locale='en-GB', instructions=INSTRUCTIONS, status='texts only; no audio generated or reviewed')
    (output / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(plan, ensure_ascii=False))


def generate(output, model, voice):
    # Only reached after the CLI's explicit paid-call approval; never imported by the web app.
    if any((output / (audio_id + '.mp3')).exists() for audio_id in MATERIALS):
        raise ValueError('Output already contains MP3s; refusing repeated synthesis/overwrite. Review existing files first.')
    export_texts(output)
    from openai import OpenAI
    client = OpenAI(max_retries=0, timeout=180)
    for item in MATERIALS.values():
        path = output / (item['id'] + '.mp3')
        with client.audio.speech.with_streaming_response.create(model=model, voice=voice,
                input=item['transcript'], instructions=INSTRUCTIONS, response_format='mp3') as response:
            response.stream_to_file(path)
    (output / 'generation.json').write_text(json.dumps(dict(provider='OpenAI', model=model, voice=voice,
        source_sha256={key: text_hash(item) for key, item in MATERIALS.items()}, human_reviewed=False), indent=2)+'\n')
    print('Generated but NOT installed or quality approved. Listen to every clip before local install.')


def inspect_mp3(path):
    if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError(f'Missing/oversize MP3: {path.name}')
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
        'stream=codec_name,sample_rate,channels:format=duration', '-of', 'json', str(path)],
        check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    stream = data['streams'][0]
    duration = float(data['format']['duration'])
    if (stream['codec_name'] != 'mp3' or int(stream['sample_rate']) < 22050 or stream['channels'] not in (1, 2)
            or not math.isfinite(duration) or not 10 <= duration <= 180):
        raise ValueError(f'Invalid MP3 format/duration: {path.name}')
    return duration


def install(source, destination, provider, voice, rights):
    # Validate all five before publishing any manifest entry. Human listening is a CLI prerequisite.
    entries = {}
    for audio_id, item in MATERIALS.items():
        path = source / (audio_id + '.mp3')
        duration = inspect_mp3(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries[audio_id] = dict(filename=f'{audio_id}.{digest}.mp3', sha256=digest, source_sha256=text_hash(item),
            locale='en-GB', provider=provider, voice=voice, rights=rights, human_reviewed=True,
            duration_seconds=round(duration, 3), bytes=path.stat().st_size)
    destination.mkdir(parents=True, exist_ok=True)
    for audio_id, entry in entries.items():
        target = destination / entry['filename']
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError('Existing immutable asset does not match its filename')
            continue
        temporary = target.with_suffix('.mp3.tmp')
        shutil.copyfile(source / (audio_id + '.mp3'), temporary)
        temporary.replace(target)
    temporary = destination / 'manifest.json.tmp'
    temporary.write_text(json.dumps(dict(version=1, assets=entries), ensure_ascii=False, indent=2)+'\n')
    temporary.replace(destination / 'manifest.json')
    print(f'Installed 5 reviewed MP3s ({sum(entry["bytes"] for entry in entries.values())} bytes). Restart application after release.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export', help='Local only: export five texts and size/cost inputs')
    export.add_argument('output', type=Path)
    generation = commands.add_parser('generate', help='PAID provider calls, only after separate user approval')
    generation.add_argument('output', type=Path)
    generation.add_argument('--model', required=True)
    generation.add_argument('--voice', required=True)
    generation.add_argument('--approve-paid-api', action='store_true')
    installation = commands.add_parser('install', help='Local only: validate and install five externally supplied/reviewed MP3s')
    installation.add_argument('source', type=Path)
    installation.add_argument('--destination', type=Path, default=ASSET_ROOT)
    installation.add_argument('--provider', required=True)
    installation.add_argument('--voice', required=True)
    installation.add_argument('--rights', required=True)
    installation.add_argument('--human-reviewed', action='store_true')
    installation.add_argument('--rights-confirmed', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'generate' and not args.approve_paid_api:
        parser.error('No paid API approval. Use export; generation requires explicit user approval and --approve-paid-api.')
    if args.command == 'install' and (not args.human_reviewed or not args.rights_confirmed or not args.rights.strip()
                                    or not args.provider.strip() or not args.voice.strip()):
        parser.error('Install requires listening to all five clips and confirming voice/audio usage rights.')
    try:
        if args.command == 'export': export_texts(args.output)
        elif args.command == 'generate': generate(args.output, args.model, args.voice)
        else: install(args.source, args.destination, args.provider, args.voice, args.rights)
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Audio preparation failed: {error}\n')


if __name__ == '__main__':
    main()
