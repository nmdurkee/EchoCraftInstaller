"""Read live Minecraft terrain into atomic, revisioned updates. No Echo mutation.

Chunk entries cover only the current bounded capture, not whole Minecraft chunks.
A consumer must replace each entry in full and remove evicted entries. Dimensions
reset the entire generation; revisions are never acknowledgements from Echo.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from bridge_cli import Bridge, DEFAULT_CONFIG, ROOT
from prepare_world_replacement import stage

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

class WorldFeed:
    def __init__(self):
        self.feed_id=str(uuid.uuid4())
        self.identity=None
        self.calibration=None
        self.chunks={}
        self.revision=0
        self.epoch=0
        self.atlas_sha=None

    def update(self, scene, atlas_sha):
        if not scene.get('connected'): raise ValueError('Minecraft is disconnected')
        if not scene.get('captureBoundaryFaces'): raise ValueError('Update the exporter before streaming terrain')
        identity=(scene['dimension'],scene.get('worldSession'))
        reset=identity!=self.identity
        atlas_changed=atlas_sha!=self.atlas_sha
        world=stage(scene,calibration=None if reset else self.calibration)
        current={','.join(map(str,c['chunk'])):dict(hash=digest(c),data=c) for c in world['chunks']}
        before={} if reset else self.chunks
        changed={key:value['data'] for key,value in current.items() if key not in before or value['hash']!=before[key]['hash']}
        removed=sorted(set(before)-set(current))
        if reset or changed or removed or atlas_changed:self.revision+=1
        if reset:self.epoch+=1
        self.identity=identity;self.calibration=world['alignment'];self.chunks=current
        self.atlas_sha=atlas_sha
        return dict(schema=1,feedId=self.feed_id,epoch=self.epoch,revision=self.revision,reset=reset,
                    dimension=scene['dimension'],worldSession=scene.get('worldSession'),
                    worldSessionVerified=bool(scene.get('worldSession')),
                    alignment=self.calibration,atlasSha256=atlas_sha,atlasChanged=atlas_changed,
                    changedChunks=changed,removedChunks=removed,
                    residentChunks={key:value['data'] for key,value in current.items()},
                    counts=world['counts'],sourceRadius=scene['radius'],
                    echoApplied=False,authoritativeServerApplied=False,
                    completeWorld=False,scope='Bounded terrain feed; no native runtime consumer attached')

def atomic_json(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(data,f,separators=(',',':'),sort_keys=True,allow_nan=False);f.flush();os.fsync(f.fileno())
    for attempt in range(40):
        try:os.replace(temp,path);return
        except PermissionError:
            if attempt==39:raise
            time.sleep(.025)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples',type=int,default=3)
    parser.add_argument('--interval',type=float,default=1)
    parser.add_argument('--radius',type=int,choices=range(1,9),default=8)
    parser.add_argument('--output',type=Path,default=ROOT/'runtime/live-world')
    args=parser.parse_args()
    if not 1<=args.samples<=10000 or not .1<=args.interval<=60:parser.error('Invalid sample count or interval')
    bridge=Bridge();feed=WorldFeed()
    for sample in range(args.samples):
        try:
            scene=bridge.request('/scene',dict(radius=args.radius,renderGeometry=True))
            bridge.request('/assets')
            atlas=(DEFAULT_CONFIG.parent.parent/'echocraft-export/echocraft_blocks_0.png').read_bytes()
            atlas_sha=hashlib.sha256(atlas).hexdigest()
            args.output.mkdir(parents=True,exist_ok=True)
            atlas_path=args.output/(atlas_sha+'.png')
            if not atlas_path.exists():
                temp=atlas_path.with_suffix('.tmp');temp.write_bytes(atlas);os.replace(temp,atlas_path)
            update=feed.update(scene,atlas_sha)
            update.update(capturedAtUnix=time.time(),atlasFile=atlas_path.name)
            atomic_json(args.output/'current.json',update)
            status={key:update[key] for key in ('feedId','epoch','revision','dimension','counts','echoApplied','worldSessionVerified')}
            status.update(connected=True,capturedAtUnix=update['capturedAtUnix'],changedChunks=len(update['changedChunks']),removedChunks=len(update['removedChunks']))
            atomic_json(args.output/'status.json',status);print(json.dumps(status),flush=True)
        except Exception as error:
            atomic_json(args.output/'status.json',dict(connected=False,echoApplied=False,error=type(error).__name__,capturedAtUnix=time.time()))
            raise
        if sample+1<args.samples:time.sleep(args.interval)
    status['samplingComplete']=True
    atomic_json(args.output/'status.json',status)

if __name__=='__main__':main()
