"""Build the same original sentence in eSpeak NG British and American voices.

Build-time only: python tools/generate_voice_examples.py ENGINE --data-path DATA
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

TEXT='Learning a language takes practice. Tell me about a place you enjoy visiting, and explain what makes it special.'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('engine');parser.add_argument('--data-path')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]/'learning/assets/voice_audio'
    root.mkdir(parents=True,exist_ok=True)
    for accent in ('gb','us'):
        with tempfile.TemporaryDirectory() as work:
            wav=Path(work)/'sample.wav'
            command=[args.engine,'-v','en-'+accent,'-s','145','-w',str(wav),'--stdin']
            if args.data_path:command.insert(1,'--path='+args.data_path)
            subprocess.run(command,input=TEXT.encode(),check=True)
            subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(wav),
                            '-codec:a','libmp3lame','-b:a','48k',str(root/(accent+'.mp3'))],check=True)


if __name__=='__main__':main()
