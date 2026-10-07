"""Where this PC's Echo VR client lives.

The installer writes the folder the player chose to %LOCALAPPDATA%\\EchoCraft\\echo-path.txt (next to the app
folder). Without that file, the default Meta install location is used.
"""
from pathlib import Path

DEFAULT = Path(r'C:\Program Files\Meta Horizon\Software\Software\ready-at-dawn-echo-arena')
SAVED = Path(__file__).resolve().parents[2]/'echo-path.txt'


def echo_game():
    try:
        saved = SAVED.read_text(encoding='utf-8-sig').strip()
        if saved: return Path(saved)
    except OSError: pass
    return DEFAULT
