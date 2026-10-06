"""Stage native collision candidates against the installed client's exact baseline."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from echo_package import Package
from native_world_collision import replace_physics, replace_bvh, replace_material_map, decode_bvh, body_layout
from native_map_target import LEVELS, EMPTY_LEVEL, LEVEL_NAME, baseline_package

ROOT=Path(__file__).resolve().parents[1]


def build(game, source, output):
    world=json.loads(source.read_text())
    meshes=[m for c in world['chunks'] for m in c['collision']]
    triangles=[tuple(m['verts'][i] for i in f) for m in meshes for f in m['faces']]
    package=baseline_package(game)
    changes=[]
    for level in LEVELS:
        for kind in ('CPhysicsResource','CBVHResource','CMaterialTypesBVHResource'):
            r=package.get(kind,level); original=package.read(r)
            if level==LEVELS[0]:
                if kind=='CPhysicsResource':
                    candidate=replace_physics(original,meshes)
                    cursor=36
                    for _ in meshes: cursor=body_layout(candidate,cursor)[2]
                    if candidate[cursor:]!=bytes(36): raise ValueError('Physics trailing data')
                elif kind=='CBVHResource':
                    candidate=replace_bvh(original,triangles,preserve_root_bounds=True)
                    recovered=decode_bvh(candidate)
                    if set(recovered)!=set(range(len(triangles))): raise ValueError('Missing collision triangles')
                    error=max(abs(x-y) for i,t in enumerate(triangles) for a,b in zip(t,recovered[i]) for x,y in zip(a,b))
                    if error>2e-5: raise ValueError('Raycast geometry differs from body geometry')
                else: candidate=replace_material_map(original,len(triangles))
            else:
                # Native, already installed empty resources are safer than guessed headers.
                candidate=package.read(package.get(kind,EMPTY_LEVEL))
                expected={'CPhysicsResource':72,'CBVHResource':80,'CMaterialTypesBVHResource':64}[kind]
                if len(candidate)!=expected: raise ValueError('Empty resource donor has changed')
            changes.append((r,kind,original,candidate))
    output.mkdir(parents=True,exist_ok=True)
    digest=lambda b:hashlib.sha256(b).hexdigest()
    report=dict(targetLevel=LEVELS[0],targetName=LEVEL_NAME,schema=1,installable=False,status='Offline collision candidate; not a complete map package',
                sourceSha256=digest(source.read_bytes()),manifestSha256=digest(package.manifest_bytes),
                bodyCount=len(meshes),triangleCount=len(triangles),levelCount=len(LEVELS),
                rootFraming='Original central-level BVH outer bounds preserved; old primitives replaced',
                unresolved=['Combine with matching native visual and spawn candidates',
                            'Native grip material and live movement validation',
                            'Client/server collision agreement'],resources=[])
    for r,kind,original,candidate in changes:
        filename=f'{r.type:016x}_{r.name:016x}.candidate'
        (output/filename).write_bytes(candidate)
        report['resources'].append(dict(type=f'{r.type:016x}',name=f'{r.name:016x}',kind=kind,
                                       file=filename,before=digest(original),after=digest(candidate),bytes=len(candidate)))
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','bodyCount','triangleCount','levelCount')}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--game',type=Path,default=Path(r'C:\Program Files\Meta Horizon\Software\Software\ready-at-dawn-echo-arena'))
    p.add_argument('--source',type=Path,default=ROOT/'runtime/world-replacement/world-input.json')
    p.add_argument('--output',type=Path,default=ROOT/'runtime/world-replacement/native-collision')
    a=p.parse_args(); build(a.game,a.source,a.output)
