"""Stage full-scale render and collision inputs for native Echo level replacement.

This does not patch game packages. The output is an intermediate input to a
native resource builder, not an installer or a live world stream.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil

from scene_geometry import Alignment, box_triangles, vector
from native_map_target import LEVELS, LEVEL_NAME

ROOT = Path(__file__).resolve().parents[1]
LOBBY_LEVELS = ('d09afd15b1c75c04', '6daa00a6d33d44b7', 'cb9977f7fc2b4526',
                '3f9915d3001dc28e', '4d82118c7c91b6bb')


def normal(a, b, c):
    u = [b[i]-a[i] for i in range(3)]
    v = [c[i]-a[i] for i in range(3)]
    n = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
    length = math.sqrt(sum(x*x for x in n))
    if length < 1e-9:
        raise ValueError('Degenerate render face')
    return [x/length for x in n]


def stage(scene, echo_head=(0, 1.62, 0), echo_forward=(0, 0, -1), max_vertices=60000, calibration=None):
    if not scene.get('connected') or not scene.get('renderGeometry'):
        raise ValueError('A connected snapshot with baked render geometry is required')
    if scene.get('quadBudgetExhausted'):
        raise ValueError('Snapshot geometry was truncated; recapture before native import')
    if type(max_vertices) is not int or not 4 <= max_vertices <= 65535:
        raise ValueError('Mesh batch limit must fit Echo 16-bit vertex indices')
    if calibration is None:
        calibration = dict(echo_head=list(vector(echo_head)), minecraft_head=list(vector(scene['head'])),
                           echo_forward=list(vector(echo_forward)), minecraft_forward=list(vector(scene['forward'])))
    alignment = Alignment(**calibration)
    chunks = {}
    occupied = set()
    for block in scene['blocks']:
        pos = vector(block['position'])
        if any(x != int(x) for x in pos) or pos in occupied:
            raise ValueError('Block positions must be unique integer coordinates')
        occupied.add(pos)
        key = (math.floor(pos[0]/16), math.floor(pos[2]/16))
        chunk = chunks.setdefault(key, dict(chunk=list(key), render=[], collision=[], blocks=0))
        chunk['blocks'] += 1
        for quad in block.get('quads', []):
            source = quad['vertices']
            if len(source) != 4 or any(len(v) != 6 for v in source):
                raise ValueError('Expected four baked vertices with position, UV and ABGR')
            tint = quad['tint']
            if type(tint) is not int or not 0 <= tint <= 0xffffff:
                raise ValueError('Invalid RGB tint')
            vertices = [list(alignment.to_echo(tuple(pos[i]+vector(v[:3])[i] for i in range(3)))) for v in source]
            n = normal(*vertices[:3])
            normal(vertices[0], vertices[2], vertices[3])
            uvs, colors = [], []
            for v in source:
                uv = [float(v[3]), float(v[4])]
                if not all(math.isfinite(x) and 0 <= x <= 1 for x in uv):
                    raise ValueError('Invalid atlas UV')
                packed = v[5]
                if type(packed) is not int or not 0 <= packed <= 0xffffffff:
                    raise ValueError('Invalid ABGR vertex color')
                uvs.append(uv)
                colors.append([((packed >> shift) & 255)/255*((tint >> color) & 255)/255
                               for shift, color in ((0,16),(8,8),(16,0))]+[((packed >> 24) & 255)/255])
            if not chunk['render'] or len(chunk['render'][-1]['verts'])+4 > max_vertices:
                chunk['render'].append(dict(verts=[], faces=[], uvs=[], colors=[], normals=[]))
            batch = chunk['render'][-1]
            base = len(batch['verts'])
            batch['verts'].extend(vertices)
            batch['uvs'].extend(uvs)
            batch['colors'].extend(colors)
            batch['normals'].extend([n]*4)
            batch['faces'].extend([[base, base+1, base+2], [base, base+2, base+3]])
        # Collision comes from Minecraft shapes, never from visual bounding boxes.
        # Keep boxes independently manifold for the proven static-body builder.
        for shape in block.get('collision', []):
            if len(shape) != 6:
                raise ValueError('Invalid collision shape')
            lo = tuple(pos[i]+vector(shape[:3])[i] for i in range(3))
            hi = tuple(pos[i]+vector(shape[3:])[i] for i in range(3))
            verts, faces, indices = [], [], {}
            for triangle, _ in box_triangles(lo, hi):
                face = []
                for vertex in triangle:
                    p = alignment.to_echo(vertex)
                    if p not in indices:
                        indices[p] = len(verts)
                        verts.append(list(p))
                    face.append(indices[p])
                faces.append(face)
            chunk['collision'].append(dict(block=list(pos), blockId=block['id'], verts=verts, faces=faces))
    values = [chunks[k] for k in sorted(chunks)]
    return dict(schema=1, mode='native-world-replacement-input', installable=False,
                sourceDimension=scene['dimension'], sourceRadius=scene.get('radius'),
                completeWorld=False, units='one Minecraft block = one Echo metre',
                alignment=calibration, chunks=values,
                captureBoundaryFaces=bool(scene.get('captureBoundaryFaces',False)),
                replacement=dict(levels=list(LEVELS),targetName=LEVEL_NAME,
                    replace=['stock terrain meshes and all LODs', 'stock map collision',
                             'stock map grip/disc raycast surfaces', 'stock sky, fog and environment'],
                    preserve=['player and hand systems', 'movement and grab logic',
                              'level/session lifecycle dependencies'],
                    unresolved=['native resource packing and carrier allocation',
                                'actor-owned scenery and environment resource classification',
                                'spawn relocation and old-map boundary rules',
                                'live chunk changes and client/server collision consistency']),
                counts=dict(blocks=len(occupied), chunks=len(values),
                    renderTriangles=sum(len(b['faces']) for c in values for b in c['render']),
                    collisionBodies=sum(len(c['collision']) for c in values),
                    collisionTriangles=sum(len(b['faces']) for c in values for b in c['collision'])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'research/preview-20261003-101243-136248')
    parser.add_argument('--out', type=Path, default=ROOT/'runtime/world-replacement')
    args = parser.parse_args()
    raw = (args.source/'scene.json').read_bytes()
    result = stage(json.loads(raw))
    result['sourceSha256'] = hashlib.sha256(raw).hexdigest()
    result['atlasSha256'] = hashlib.sha256((args.source/'atlas.png').read_bytes()).hexdigest()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'world-input.json').write_text(json.dumps(result, separators=(',', ':'), allow_nan=False)+'\n', encoding='utf-8')
    shutil.copyfile(args.source/'atlas.png', args.out/'atlas.png')
    print(json.dumps(result['counts']))
    print('Staged native-world input only; no game resources changed. Packing and deployment remain unfinished.')


if __name__ == '__main__':
    main()
