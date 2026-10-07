"""Where this PC's Echo VR client lives.

The installer writes the folder the player chose to %LOCALAPPDATA%\\EchoCraft\\echo-path.txt (next to the app
folder). Without that file, the default Meta install location is used.
"""
from pathlib import Path

DEFAULT = Path(r'C:\Program Files\Meta Horizon\Software\Software\ready-at-dawn-echo-arena')
SAVED = Path(__file__).resolve().parents[2]/'echo-path.txt'
TESTED_EXE_SHA256 = '3dae0cdab2eb298f9b04fc6baac83f8dd304a8f1d9fea057ab30438fe271df9a'
ACCEPTED_EXE = SAVED.with_name('echo-exe-sha256.txt')  # a different build the player chose to continue with


def echo_game():
    try:
        saved = SAVED.read_text(encoding='utf-8-sig').strip()
        if saved: return Path(saved)
    except OSError: pass
    return DEFAULT


def accepted_exe_hashes():
    hashes = {TESTED_EXE_SHA256}
    try:
        accepted = ACCEPTED_EXE.read_text(encoding='utf-8-sig').strip().lower()
        if accepted: hashes.add(accepted)
    except OSError: pass
    return hashes
