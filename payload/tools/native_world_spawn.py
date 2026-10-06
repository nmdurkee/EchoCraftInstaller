"""Relocate measured lobby spawn actors without changing their gameplay records."""
import math
import struct


def actor_layout(blob):
    """Strict PCVR layout: 3 middle arrays, 5 bit descriptors, 48-byte TRS."""
    if len(blob)<784: raise ValueError('Short actor data')
    def desc(off,stride=None,marker=0):
        pointer,size,allocator=struct.unpack_from('<3Q',blob,off)
        base,capacity,count=struct.unpack_from('<3Q',blob,off+32)
        if pointer or allocator or base!=marker or count!=capacity or count>1000000:
            raise ValueError('Unsupported actor descriptor')
        if stride is not None and size!=count*stride: raise ValueError('Actor descriptor stride mismatch')
        return count
    headers=desc(0,16); components=desc(64,64)
    count=desc(128,8)
    if any(desc(off,stride)!=count for off,stride in ((184,8),(240,8),(296,48),(352,2),(408,2))):
        raise ValueError('Actor arrays disagree')
    for off in range(464,784,64): desc(off)
    cursor=784+headers*16
    if cursor+components*64>len(blob): raise ValueError('Truncated actor component directory')
    indexcount=sum(desc(cursor+i*64+8,2,32) for i in range(components))
    cursor+=components*64
    if cursor+indexcount*2>len(blob): raise ValueError('Truncated component actor indices')
    if any(struct.unpack_from('<H',blob,cursor+i*2)[0]>=count for i in range(indexcount)):
        raise ValueError('Component actor index out of range')
    cursor=(cursor+indexcount*2+7)&~7
    if cursor+count*(8*3+48+2*2)>len(blob): raise ValueError('Truncated actor tables')
    ids=struct.unpack_from('<'+'Q'*count,blob,cursor)
    if len(set(ids))!=count: raise ValueError('Duplicate actor IDs')
    transforms=cursor+count*24; parents=transforms+count*50
    return dict(count=count,ids=ids,transforms=transforms,parents=parents)


def spawn_ids(blob):
    if len(blob)<56 or (len(blob)-56)%48: raise ValueError('Unsupported spawn component layout')
    count=(len(blob)-56)//48
    if struct.unpack_from('<Q',blob,8)[0]!=count*48 or struct.unpack_from('<2Q',blob,40)!=(count,count):
        raise ValueError('Spawn component count mismatch')
    ids=[]; types=set()
    for at in range(56,len(blob),48):
        kind,entity,term,slot,sentinel,spawn_id=struct.unpack_from('<6Q',blob,at)
        # Arena contains one unassigned spawn placeholder (flags 48, ID 65535).
        # Preserve its bytes and relocate only the 24 assigned spawn records.
        if term==0xffffffff and slot==48 and sentinel==0xffffffffffffffff and spawn_id==0xffff:
            continue
        if term!=0xffffffff or slot!=32 or sentinel!=0xffffffffffffffff:
            raise ValueError('Unexpected spawn component record')
        ids.append(entity); types.add(kind)
    if len(types)!=1 or not ids or len(set(ids))!=len(ids): raise ValueError('Invalid spawn entity list')
    return ids


def spawn_meshes(source,world):
    """Collision meshes that spawn placement measures floors on. When the saved terrain patch was reduced
    (world-input.full.json kept beside it), spawns stay where the full capture put them."""
    from pathlib import Path
    import json
    full=Path(source).parent/'world-input.full.json'
    if full.exists(): world=json.loads(full.read_text())
    return [m for c in world['chunks'] for m in c['collision']]


def floor_height(meshes,x,z):
    heights=[]
    for m in meshes:
        for face in m['faces']:
            a,b,c=(m['verts'][i] for i in face)
            denominator=(b[2]-c[2])*(a[0]-c[0])+(c[0]-b[0])*(a[2]-c[2])
            # Positive denominator means upward-facing normal for this XZ basis.
            if denominator>=-1e-8: continue
            u=((b[2]-c[2])*(x-c[0])+(c[0]-b[0])*(z-c[2]))/denominator
            v=((c[2]-a[2])*(x-c[0])+(a[0]-c[0])*(z-c[2]))/denominator
            if min(u,v,1-u-v)>=-1e-7: heights.append(u*a[1]+v*b[1]+(1-u-v)*c[1])
    return max(heights) if heights else None


def choose_positions(meshes,count):
    # This is a saved, small test patch. Fail rather than spawn outside it.
    candidates=sorted(((x*1.1,z*1.1) for x in range(-3,4) for z in range(-3,4)),key=lambda p:(p[0]**2+p[1]**2,p))
    boxes=[([min(v[a] for v in m['verts']) for a in range(3)],
            [max(v[a] for v in m['verts']) for a in range(3)]) for m in meshes]
    positions=[]
    for x,z in candidates:
        floors=[floor_height(meshes,x+dx,z+dz) for dx in (-.35,0,.35) for dz in (-.35,0,.35)]
        if any(h is None for h in floors) or max(floors)-min(floors)>.05: continue
        floor=max(floors)
        if any(lo[0]<x+.35 and hi[0]>x-.35 and lo[2]<z+.35 and hi[2]>z-.35 and
               lo[1]<floor+2.3 and hi[1]>floor+.05 for lo,hi in boxes): continue
        positions.append((x,floor+1.62,z))
        if len(positions)==count: return positions
    raise ValueError(f'Only {len(positions)} clear spawn positions for {count} spawn records')


def relocate(actors,transforms,spawns,meshes):
    layout=actor_layout(actors); ids=spawn_ids(spawns); positions=choose_positions(meshes,len(ids))
    index={entity:i for i,entity in enumerate(layout['ids'])}
    rows={}
    if len(transforms)<56 or (len(transforms)-56)%176: raise ValueError('Invalid transform table')
    count=(len(transforms)-56)//176
    if struct.unpack_from('<2Q',transforms,40)!=(count,count): raise ValueError('Invalid transform counts')
    for at in range(56,len(transforms),176):
        entity=struct.unpack_from('<Q',transforms,at+8)[0]
        if entity not in ids: continue
        if entity in rows: raise ValueError('Duplicate transform entity')
        rows[entity]=at
    actorout=bytearray(actors); xfout=bytearray(transforms); report=[]
    for entity,position in zip(ids,positions):
        if entity not in index or entity not in rows: raise ValueError('Spawn lacks actor/transform mapping')
        i=index[entity]
        if struct.unpack_from('<H',actors,layout['parents']+i*2)[0]!=0xffff:
            raise ValueError('Parented spawn needs a parent-space conversion')
        aoff=layout['transforms']+i*48; xoff=rows[entity]+32
        av=struct.unpack_from('<10f',actors,aoff); xv=struct.unpack_from('<10f',transforms,xoff)
        if not all(math.isfinite(x) for x in av+xv) or max(abs(a-b) for a,b in zip(av,xv))>.002:
            raise ValueError('Actor and component spawn transforms disagree')
        new=(0.,0.,0.,1.,*position,1.,1.,1.)
        struct.pack_into('<10f',actorout,aoff,*new); struct.pack_into('<10f',xfout,xoff,*new)
        report.append(dict(entity=f'{entity:016x}',before=list(av[4:7]),after=list(position)))
    return bytes(actorout),bytes(xfout),report
