"""Explicit arena target and read-only access to the pre-test package baseline."""
import hashlib
import json
from pathlib import Path
from echo_package import Package, DATA, MANIFEST

LEVELS=('576ed3f8428ebc4b','3f9915d3001dc28e','4d82118c7c91b6bb')
LEVEL_NAME='mpl_arena_a'
EMPTY_LEVEL='3f9915d3001dc28e'
ROOT=Path(__file__).resolve().parents[1]

def baseline_package(game, backups=None):
    backups=Path(backups) if backups is not None else ROOT/'runtime/world-replacement/install-backups'
    latest=backups/'latest.json'
    if not latest.exists(): return Package(game)
    transaction=json.loads(latest.read_text())['transaction']
    if Path(transaction).name!=transaction: raise ValueError('Invalid backup transaction')
    folder=backups/transaction
    state=json.loads((folder/'transaction.json').read_text())
    for target in state['targets']:
        if Path(target['game']).resolve()!=Path(game).resolve(): continue
        current=(Path(game)/DATA/'manifests'/MANIFEST).read_bytes()
        digest=lambda b:hashlib.sha256(b).hexdigest()
        if digest(current)==target['manifestBefore']: return Package(game)
        if digest(current)!=target['manifestAfter']: raise ValueError('Game manifest changed outside the recorded test')
        original=folder/target['original']
        if original.resolve().parent!=folder.resolve(): raise ValueError('Invalid original manifest path')
        data=original.read_bytes()
        if digest(data)!=target['manifestBefore']: raise ValueError('Original manifest backup changed')
        return Package(game,manifest_bytes=data)
    return Package(game)
