"""Regenerate original, prebuilt samples without any paid API.

Usage: python tools/generate_mock_audio.py /path/to/espeak-ng --data-path /path/containing/espeak-ng-data
ffmpeg must be installed. The speech engine is a build-time tool, not a runtime dependency.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from learning.mock_bank import BANK


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('engine');parser.add_argument('--data-path')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]/'learning'/'assets'/'mock_audio'
    root.mkdir(parents=True,exist_ok=True)
    for template in BANK.values():
        if not template['audio_file']:continue
        speed={'ielts':160,'cet4':145,'cet6':170}[template['exam_type']]
        with tempfile.TemporaryDirectory() as work:
            wav=Path(work)/'sample.wav'
            command=[args.engine,'-v','en-us','-s',str(speed),'-w',str(wav),'--stdin']
            if args.data_path:command.insert(1,'--path='+args.data_path)
            subprocess.run(command,input=template['transcript'].encode(),check=True)
            subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(wav),'-codec:a','libmp3lame','-b:a','48k',
                            str(root/template['audio_file'])],check=True)


if __name__=='__main__':main()
