"""One explicit, paid, DB-free STT probe using the original shipped sample.

LEARNING_VOICE_PROBE=1 OPENAI_API_KEY=... python tools/probe_voice_api.py
Only reports capability outcome; never logs credentials or provider error bodies.
Does not enable the application or change any database/configuration.
"""
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from learning import voice


def main():
    if os.getenv('LEARNING_VOICE_PROBE')!='1' or not os.getenv('OPENAI_API_KEY'):
        print('NOT VERIFIED: requires explicit LEARNING_VOICE_PROBE=1 and a test API key. No request sent.')
        return 2
    from openai import OpenAI
    sample=Path(__file__).resolve().parents[1]/'learning/assets/voice_audio/us.mp3'
    audio,seconds=voice.normalize_audio(sample.read_bytes())
    try:
        client=OpenAI(base_url='https://api.openai.com/v1',max_retries=0,timeout=45)
        result=voice.transcribe(lambda:client,audio)
        # Basic content check avoids treating any nonempty response as compatible speech recognition.
        if not all(word in result['transcript'].lower() for word in ('language','practice','place')):
            raise ValueError('Unexpected transcription')
    except Exception:
        print('NOT VERIFIED: request failed or returned incompatible output. No automatic retry.')
        return 1
    print(f'VERIFIED: {voice.MODEL}; audio.transcriptions.create; JSON; {seconds}s original sample; one call. No pronunciation capability verified.')
    return 0


if __name__=='__main__':raise SystemExit(main())
