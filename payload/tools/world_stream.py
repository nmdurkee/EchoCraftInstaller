"""Incremental Minecraft world -> immutable native GPU section packets.

This is a renderer transport, not a physics acknowledgement. Pending, unloaded,
changed-dimension and resource-reload states are explicit. No world actions are
sent. Test captures are tagged and rejected by the production native reader.
"""
import argparse
import base64
import hashlib
import json
import math
import os
import re
from pathlib import Path
import struct
import time
import uuid
from baked_geometry import pack_quads
from bridge_cli import Bridge, DEFAULT_CONFIG, ROOT
from native_world_material import read_png

HEADER=256
ENTRY=80
MAX_SECTIONS=2048
MAX_TRIANGLES=131072
MAX_BOXES=65536
NEAR_SQ=24.0**2  # sections whose centre is this close always stream first

def atomic_bytes(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('wb') as f:f.write(data)
    for attempt in range(40):
        try:os.replace(temp,path);return
        except PermissionError:
            if attempt==39:raise
            time.sleep(.005)

def section_packet(scene):
    if not scene.get('sectionLoaded') or not scene.get('completeSection') or scene.get('quadBudgetExhausted'):
        raise ValueError('Incomplete terrain cannot replace a resident section')
    xyz=scene['section']
    if len(xyz)!=3 or any(type(n) is not int for n in xyz):raise ValueError('Invalid section address')
    origin=[n*16 for n in xyz];triangles=bytearray();boxes=bytearray();seen=set()
    for block in scene['blocks']:
        pos=block['position']
        if len(pos)!=3 or any(not math.isfinite(v) or v!=int(v) for v in pos):raise ValueError('Invalid block address')
        local=tuple(pos[i]-origin[i] for i in range(3))
        if any(not 0<=v<16 for v in local) or local in seen:raise ValueError('Duplicate/outside section block')
        seen.add(local)
        quads=block.get('quads',[])
        data,count=pack_quads(quads,lambda p:tuple(p[i]+local[i] for i in range(3)))
        if count!=2*len(quads):raise ValueError('Block geometry exceeds per-block budget')
        triangles.extend(data)
        for bounds in block.get('collision',[]):
            if len(bounds)!=6 or not all(math.isfinite(v) and -32<=v<=48 for v in bounds) or any(bounds[i]>=bounds[i+3] for i in range(3)):
                raise ValueError('Invalid Minecraft collision box')
            boxes.extend(struct.pack('<9f',*local,*bounds))
    tc,bc=len(triangles)//144,len(boxes)//36
    if tc>MAX_TRIANGLES or bc>MAX_BOXES:raise ValueError('Section allocation budget exceeded')
    header=bytearray(64)
    struct.pack_into('<II3iII',header,0,0x53574345,1,*xyz,tc,bc)
    return bytes(header+triangles+boxes),tc,bc

class WorldStream:
    def __init__(self,directory,test_only=False):
        self.directory=Path(directory);self.test_only=test_only;self.stream=uuid.uuid4().bytes
        self.identity=None;self.resident={};self.wanted={};self.atlas=None;self.atlas_size=None
        self.serial=0;self.exports=0;self.reuses=0;self.state=None
        self.last_prune=0
    def sync(self,state):
        if not state.get('connected') or not state.get('sectionRevisions'):raise ValueError('Live revision index required')
        test=state.get('vivecraft',{}).get('integration',{}).get('recordedTracking',False)
        if bool(test)!=self.test_only:raise ValueError('Test/production world stream mismatch')
        identity=(state['worldSession'],state['dimension'],state['resourceGeneration'])
        uuid.UUID(identity[0]);dimension=identity[1].encode('utf-8')
        if not dimension or len(dimension)>63:raise ValueError('Unsupported dimension identifier')
        if len(state['sections'])>MAX_SECTIONS:raise ValueError('Too many resident sections')
        if identity!=self.identity:
            self.identity=identity;self.resident.clear();self.atlas=None;self.atlas_size=None
        self.wanted={tuple(e['section']):dict(e) for e in state['sections']}
        if len(self.wanted)!=len(state['sections']):raise ValueError('Duplicate section index')
        self.resident={k:v for k,v in self.resident.items() if k in self.wanted and self.wanted[k]['loaded']}
        self.state=state
    def pending(self):
        head=self.state['head']
        wanted=[k for k,v in self.wanted.items() if v['loaded'] and (k not in self.resident or self.resident[k]['revision']!=v['revision'])]
        # Collision-critical first: anything within ~24 m of the head (new OR edited), nearest first, so you never fly
        # into blocks that have not streamed yet. Then edited sections (a placed/broken block must not wait behind
        # the window edge), then the rest by distance.
        def key(k):
            d=sum((k[i]*16+8-head[i])**2 for i in range(3))
            return (d>NEAR_SQ,d>NEAR_SQ and k not in self.resident,d,k)
        return sorted(wanted,key=key)
    def set_atlas(self,png):
        w,h,pixels=read_png(png)
        if not 0<w<=8192 or not 0<h<=8192 or len(pixels)!=w*h*4:raise ValueError('Invalid atlas dimensions')
        self.atlas=self.blob(pixels,'.rgba');self.atlas_size=(w,h)
    def blob(self,data,suffix):
        digest=hashlib.sha256(data).digest();path=self.directory/(digest.hex()+suffix)
        if not path.exists():atomic_bytes(path,data)
        return digest
    def accept(self,scene):
        if (scene['worldSession'],scene['dimension'])!=self.identity[:2]:raise ValueError('World changed during section capture')
        key=tuple(scene['section']);wanted=self.wanted.get(key)
        if not wanted or not wanted['loaded'] or scene.get('sectionRevision')!=wanted['revision']:raise ValueError('Stale section revision')
        packet,tc,bc=section_packet(scene);digest=self.blob(packet,'.ecs')
        previous=self.resident.get(key)
        if previous and previous['hash']==digest:self.reuses+=1
        self.resident[key]=dict(hash=digest,triangles=tc,boxes=bc,revision=wanted['revision'])
        self.exports+=1
    def accept_packet(self,response):
        """Binary twin of accept(): /section_packet returns the finished native packet (base64)."""
        if (response['worldSession'],response['dimension'])!=self.identity[:2]:raise ValueError('World changed during section capture')
        key=tuple(response['section']);wanted=self.wanted.get(key)
        if not wanted or not wanted['loaded'] or response.get('sectionRevision')!=wanted['revision']:raise ValueError('Stale section revision')
        if not response.get('sectionLoaded') or not response.get('completeSection') or 'packet' not in response:
            raise ValueError('Incomplete terrain cannot replace a resident section')
        packet=base64.b64decode(response['packet'],validate=True)
        if len(packet)<64:raise ValueError('Truncated section packet')
        magic,version,x,y,z,tc,bc=struct.unpack_from('<II3iII',packet,0)
        if magic!=0x53574345 or version!=1 or (x,y,z)!=key or len(packet)!=64+tc*144+bc*36 or tc>MAX_TRIANGLES or bc>MAX_BOXES:
            raise ValueError('Invalid section packet')
        digest=self.blob(packet,'.ecs');previous=self.resident.get(key)
        if previous and previous['hash']==digest:self.reuses+=1
        self.resident[key]=dict(hash=digest,triangles=tc,boxes=bc,revision=wanted['revision'])
        self.exports+=1
    def publish(self):
        if not self.atlas:return
        self.serial+=1;header=bytearray(HEADER)
        struct.pack_into('<IIQ',header,0,0x4d574345,1,int(time.time()*1000))
        header[16:32]=self.stream;header[32:48]=uuid.UUID(self.identity[0]).bytes
        struct.pack_into('<QQ4I',header,48,self.identity[2],self.serial,int(self.test_only),len(self.wanted),*self.atlas_size)
        header[80:112]=self.atlas;dimension=self.identity[1].encode();header[112:112+len(dimension)]=dimension
        values=[*self.state['head'],*self.state['forward'],*self.state['position']]
        if not all(math.isfinite(v) for v in values):raise ValueError('Invalid player pose')
        struct.pack_into('<9d',header,176,*values)
        entries=bytearray()
        for key,wanted in sorted(self.wanted.items()):
            item=self.resident.get(key);entry=bytearray(ENTRY)
            flags=int(wanted['loaded']) | (2 if item else 0) | (4 if item and item['revision']==wanted['revision'] else 0)
            struct.pack_into('<3i3IQQ',entry,0,*key,item['triangles'] if item else 0,item['boxes'] if item else 0,flags,wanted.get('revision',0),item['revision'] if item else 0)
            if item:entry[40:72]=item['hash']
            entries.extend(entry)
        atomic_bytes(self.directory/'world.ecw',header+entries)
        # Metadata stays readable without dumping mesh payloads or authentication.
        atomic_bytes(self.directory/'status.json',json.dumps(dict(worldSession=self.identity[0],dimension=self.identity[1],revision=self.serial,
            requested=len(self.wanted),resident=len(self.resident),pending=len(self.pending()),exports=self.exports,reusedGeometry=self.reuses,
            triangles=sum(v['triangles'] for v in self.resident.values()),boxes=sum(v['boxes'] for v in self.resident.values()),
            testOnly=self.test_only,echoWorldApplied=False,echoCollisionApplied=False)).encode())
        atomic_bytes(self.directory/'entities.json',json.dumps(dict(worldSession=self.identity[0],dimension=self.identity[1],gameTime=self.state['gameTime'],
            entities=self.state['entities'],health=self.state['health'],food=self.state['food'],mainHand=self.state['mainHand'],offHand=self.state['offHand'])).encode())
        if time.monotonic()-self.last_prune>30:self.prune();self.last_prune=time.monotonic()
    def prune(self,grace=120):
        # Delete only this transport's content-addressed files, inside its exact
        # output directory. Old readers have a much shorter (2s) manifest lease.
        # Never traverse subdirectories, links, or arbitrary user-named files.
        root=self.directory.resolve();active={v['hash'].hex()+'.ecs' for v in self.resident.values()}
        if self.atlas:active.add(self.atlas.hex()+'.rgba')
        cutoff=time.time()-grace
        for candidate in root.iterdir():
            if candidate.name in active or not re.fullmatch(r'[0-9a-f]{64}\.(ecs|rgba)',candidate.name):continue
            if candidate.is_symlink() or not candidate.is_file() or candidate.resolve().parent!=root:continue
            if candidate.stat().st_mtime<cutoff:
                try:candidate.unlink()
                except (PermissionError,FileNotFoundError):pass

def run(args):
    bridge=Bridge();stream=WorldStream(args.output,args.test_only);deadline=time.monotonic()+args.seconds
    last_report=0
    while time.monotonic()<deadline:
        start=time.monotonic();stream.sync(bridge.request('/world',dict(horizontal=args.horizontal,vertical=args.vertical)))
        if stream.atlas is None:
            bridge.request('/assets');stream.set_atlas((DEFAULT_CONFIG.parent.parent/'echocraft-export/echocraft_blocks_0.png').read_bytes())
        for key in stream.pending()[:args.sections_per_tick]:
            wanted=stream.wanted[key]
            try:stream.accept(bridge.request('/section',dict(section=list(key),worldSession=stream.identity[0],dimension=stream.identity[1],revision=wanted['revision'])))
            except __import__('urllib.error',fromlist=['HTTPError']).HTTPError as error:
                detail=json.loads(error.read()).get('error','')
                if error.code==400 and ('Section changed' in detail or 'World changed' in detail):break
                raise RuntimeError(f'Section export {error.code}: {detail}') from None
        stream.publish()
        if time.monotonic()-last_report>5:
            print(f'resident={len(stream.resident)} pending={len(stream.pending())} exports={stream.exports} unchanged={stream.reuses}',flush=True);last_report=time.monotonic()
        time.sleep(max(0,args.interval-(time.monotonic()-start)))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(os.environ.get('LOCALAPPDATA',str(ROOT/'runtime')))/'EchoCraft/world-stream')
    parser.add_argument('--seconds',type=float,default=60)
    parser.add_argument('--horizontal',type=int,choices=range(7),default=2)
    parser.add_argument('--vertical',type=int,choices=range(5),default=1)
    parser.add_argument('--sections-per-tick',type=int,choices=range(1,5),default=1)
    parser.add_argument('--interval',type=float,default=.1)
    parser.add_argument('--test-only',action='store_true')
    args=parser.parse_args()
    if not 0<args.seconds<=14400 or not .05<=args.interval<=5:parser.error('Invalid duration/interval')
    run(args)
