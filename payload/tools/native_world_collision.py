"""Native Echo collision compiler. Produces offline resources, never edits a game.

Layouts derived from the owner's backrooms_collisions.py and evr_geo_move.py.
Live physics, grip material behavior and spawn placement still require validation.
"""
import math
import struct

NONE = 0xffffffff
ARRAYS = ((8,12),(24,52),(40,48),(56,40),(176,84),(192,12),
          (208,12),(224,4),(240,4),(256,4),(272,4),(288,8),
          (304,8),(320,8),(336,8),(352,4),(368,8),(400,12),
          (416,48),(432,8),(448,64),(464,160),(480,2),(496,4),
          (512,12),(528,40),(544,2),(560,4),(576,12),(592,48),
          (608,100),(624,8),(640,16))


def sub(a,b): return tuple(x-y for x,y in zip(a,b))
def cross(a,b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def length(v): return math.sqrt(sum(x*x for x in v))
def project(v):
    x,y,z=v
    return x,y,z,x+y,x+z,y+z,x-y,x-z,y-z


def body_layout(blob, start):
    if start+656 > len(blob) or struct.unpack_from('<Q',blob,start)[0] != 1:
        raise ValueError('Unsupported physics body')
    geo=start+8
    cursor=geo+648
    arrays={}
    for offset,stride in ARRAYS:
        count=struct.unpack_from('<I',blob,geo+offset)[0]
        arrays[offset]=(cursor,count)
        cursor+=count*stride
        if cursor+880 > len(blob): raise ValueError('Truncated physics arrays')
    return geo, arrays, cursor+880


def body(template, donor, vertices, faces):
    geo,_,end=body_layout(template,donor)
    tail=template[end-880:end]
    if struct.unpack_from('<10f',tail,800)!=(0,0,0,1,0,0,0,1,1,1):
        raise ValueError('Physics donor is not an identity static body')
    if not vertices or not faces or len(vertices)>32767 or len(faces)>32767:
        raise ValueError('Unsupported collision size')
    if any(len(v)!=3 or not all(math.isfinite(x) for x in v) for v in vertices):
        raise ValueError('Invalid vertex')
    pairs={}; normals=[]; rows=[]; owners=[NONE]*len(vertices)
    for ti,t in enumerate(faces):
        if len(t)!=3 or len(set(t))!=3 or any(type(i)!=int or not 0<=i<len(vertices) for i in t):
            raise ValueError('Invalid triangle indices')
        n=cross(sub(vertices[t[1]],vertices[t[0]]),sub(vertices[t[2]],vertices[t[0]]))
        scale=length(n)
        if scale<1e-9: raise ValueError('Degenerate collision triangle')
        normals.append(tuple(x/scale for x in n))
        rows.append(list(t)+[NONE]*3+[0]*3+[NONE]*2+[0]*2)
        for slot in range(3):
            pair=tuple(sorted((t[slot],t[(slot+1)%3])))
            pairs.setdefault(pair,[]).append((ti,slot,t[(slot+2)%3]))
            owners[t[slot]]=ti
    if len(pairs)>32767 or any(len(a)!=2 for a in pairs.values()) or NONE in owners:
        raise ValueError('Expected closed manifold collision body')
    edges=[]; edgeowners=[]
    for ei,((a,b),adj) in enumerate(pairs.items()):
        ta,sa,oa=adj[0]; tb,sb,ob=adj[1]
        # An outward manifold has opposite directed edges on its two triangles.
        if faces[ta][sa] == faces[tb][sb]: raise ValueError('Inconsistent winding')
        e=bytearray(struct.pack('<12I',a,b,oa,ob,ta,tb,2,0,0,0,sa,sb))
        cosine=max(-1.,min(1.,sum(x*y for x,y in zip(normals[ta],normals[tb]))))
        struct.pack_into('<3f',e,28,length(sub(vertices[b],vertices[a])),
                         math.pi-math.acos(cosine),length(sub(vertices[ob],vertices[oa])))
        for ti,slot,_ in adj: rows[ti][6+slot]=ei
        edges.append(e); edgeowners.append(ta)
    projections=[project(v) for v in vertices]
    tree=struct.pack('<3I18f',len(vertices)<<16,len(edges)<<16,len(faces)<<16,
                     *[min(p[i] for p in projections) for i in range(9)],
                     *[max(p[i] for p in projections) for i in range(9)])
    payload={8:b''.join(struct.pack('<3f',*v) for v in vertices),
             24:b''.join(struct.pack('<13I',*r) for r in rows),40:b''.join(edges),
             176:tree,224:struct.pack('<'+'I'*len(owners),*owners),
             240:struct.pack('<'+'I'*len(edgeowners),*edgeowners),624:b'\xff'*(8*len(faces))}
    header=bytearray(template[geo:geo+648])
    for off,stride in ARRAYS: struct.pack_into('<I',header,off,len(payload.get(off,b''))//stride)
    struct.pack_into('<3I',header,64,len(vertices),len(faces),len(edges))
    result=struct.pack('<Q',1)+header+b''.join(payload.get(o,b'') for o,_ in ARRAYS)+tail
    if body_layout(result,0)[2]!=len(result): raise ValueError('Body serialization mismatch')
    return bytes(result)


def replace_physics(template, meshes):
    count=struct.unpack_from('<I',template,32)[0]
    if not count: raise ValueError('A static donor body is required')
    cursor=36
    for _ in range(count): cursor=body_layout(template,cursor)[2]
    suffix=template[cursor:]
    if suffix!=bytes(36): raise ValueError('Unknown physics resource suffix')
    bodies=[body(template,36,m['verts'],m['faces']) for m in meshes]
    header=bytearray(template[:36]); struct.pack_into('<I',header,32,len(bodies))
    return bytes(header)+b''.join(bodies)+suffix


def replace_bvh(template, triangles, preserve_root_bounds=False):
    if len(template)<80: raise ValueError('Truncated BVH header')
    for t in triangles:
        if len(t)!=3 or any(len(v)!=3 or not all(math.isfinite(x) for x in v) for v in t):
            raise ValueError('Invalid collision triangle')
        if length(cross(sub(t[1],t[0]),sub(t[2],t[0])))<1e-9:
            raise ValueError('Degenerate collision triangle')
    primitives=bytearray(); nodes=bytearray()
    def build(ids,root=False):
        points=[v for i in ids for v in triangles[i]]
        lo=[min((v[a] for v in points),default=0)-.0001 for a in range(3)]
        hi=[max((v[a] for v in points),default=0)+.0001 for a in range(3)]
        # Echo's finite-segment query (7104f0) treats a zero node buffer as
        # empty, even when the infinite ray query accepts a root leaf. Keep an
        # internal root for tiny/empty worlds too, with masked empty lanes.
        if len(ids)<=16 and not root:
            first=len(primitives)//224
            for offset in range(0,max(1,len(ids)),4):
                block=bytearray(224); block[192:]=b'\xff'*32
                for lane,ti in enumerate(ids[offset:offset+4]):
                    # Embree Triangle4 convention (Echo's BVH2Traverser): e1=v0-v1, e2=v2-v0, Ng=e1xe2.
                    a,b,c=triangles[ti]; e1=sub(a,b); e2=sub(c,a)
                    for base,v in ((0,a),(48,e1),(96,e2),(144,cross(e1,e2))):
                        for axis,x in enumerate(v): struct.pack_into('<f',block,base+axis*16+lane*4,x)
                    struct.pack_into('<I',block,192+lane*4,0)
                    struct.pack_into('<I',block,208+lane*4,ti)
                primitives.extend(block)
            return 0x80000000|(first<<5)|max(1,(len(ids)+3)//4),lo,hi
        axis=max(range(3),key=lambda a:hi[a]-lo[a])
        ids=sorted(ids,key=lambda i:sum(v[axis] for v in triangles[i]))
        mid=len(ids)//2; children=(build(ids[:mid]),build(ids[mid:]))
        index=len(nodes)//64; node=bytearray(64)
        for child,(ref,l,h) in enumerate(children):
            for a in range(3):
                struct.pack_into('<f',node,a*16+child*4,l[a])
                struct.pack_into('<f',node,a*16+8+child*4,h[a])
            struct.pack_into('<I',node,48+child*4,ref)
        struct.pack_into('<2I',node,56,0x80000000,0x80000000)
        nodes.extend(node)
        return index<<3,lo,hi
    root,_,_=build(list(range(len(triangles))),root=True)
    if preserve_root_bounds:
        oldpsize=struct.unpack_from('<Q',template,8)[0]
        oldnsize=struct.unpack_from('<Q',template,40)[0]
        oldroot=struct.unpack_from('<I',template,64)[0]
        if len(template)!=80+oldpsize+oldnsize or oldroot&0x80000007 or root&0x80000000:
            raise ValueError('Root bounds preservation requires valid internal roots')
        oldat=80+oldpsize+(oldroot>>3)*64
        if oldat+64>len(template): raise ValueError('Original root exceeds node buffer')
        original=struct.unpack_from('<12f',template,oldat)
        lo=[min(original[a*4:a*4+2]) for a in range(3)]
        hi=[max(original[a*4+2:a*4+4]) for a in range(3)]
        if not all(math.isfinite(v) for v in lo+hi): raise ValueError('Invalid original root bounds')
        if any(not lo[a]<=v[a]<=hi[a] for t in triangles for v in t for a in range(3)):
            raise ValueError('New terrain exceeds original level root framing')
        # Conservative top-level child boxes retain the original global framing.
        # Lower nodes still have tight bounds; no old geometry or primitive IDs remain.
        at=(root>>3)*64
        for a in range(3): struct.pack_into('<4f',nodes,at+a*16,lo[a],lo[a],hi[a],hi[a])
    if len(primitives)%448:
        padding=bytearray(224); padding[192:]=b'\xff'*32; primitives.extend(padding)
    header=bytearray(template[:80])
    struct.pack_into('<Q',header,8,len(primitives)); struct.pack_into('<Q',header,40,len(nodes))
    struct.pack_into('<I',header,64,root)
    return bytes(header+primitives+nodes)


def replace_material_map(template, count):
    header=bytearray(template[:64])
    for off,val in ((8,count*16),(40,count),(48,count)): struct.pack_into('<Q',header,off,val)
    return bytes(header)+b''.join(struct.pack('<QQ',i,0xffffffffffffffff) for i in range(count))


def decode_bvh(blob):
    """Independent reachable-leaf decoder used for roundtrip validation."""
    psize=struct.unpack_from('<Q',blob,8)[0]; nsize=struct.unpack_from('<Q',blob,40)[0]
    if psize%448 or nsize%64 or len(blob)!=80+psize+nsize: raise ValueError('Invalid BVH size')
    seen=set(); recovered={}; visited_blocks=set()
    def walk(ref):
        if ref&0x80000000:
            start=(ref&0x7fffffff)>>5; count=ref&31
            if not 1<=count<=8 or (start+count)*224>psize: raise ValueError('Invalid leaf')
            for index in range(start,start+count):
                if index in visited_blocks: raise ValueError('Repeated primitive block')
                visited_blocks.add(index); at=80+index*224
                for lane in range(4):
                    geom,ti=(struct.unpack_from('<I',blob,at+b+lane*4)[0] for b in (192,208))
                    if ti==NONE: continue
                    if geom!=0 or ti in recovered: raise ValueError('Invalid primitive ID')
                    vectors=[tuple(struct.unpack_from('<f',blob,at+b+a*16+lane*4)[0] for a in range(3)) for b in (0,48,96)]
                    recovered[ti]=(vectors[0],tuple(x-y for x,y in zip(vectors[0],vectors[1])),tuple(x+y for x,y in zip(vectors[0],vectors[2])))
            return
        if ref&7 or ref in seen or (ref>>3)*64>=nsize: raise ValueError('Invalid node')
        seen.add(ref); at=80+psize+(ref>>3)*64
        for child in struct.unpack_from('<2I',blob,at+48): walk(child)
    walk(struct.unpack_from('<I',blob,64)[0])
    if len(seen)*64!=nsize: raise ValueError('Unreachable BVH nodes')
    return recovered
