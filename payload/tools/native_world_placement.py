"""Measured static-instance tables and world-space carrier placement.

Only identified static scenery transforms are collapsed. Actor transforms and
gameplay/spawn records are preserved pending their own integration.
"""
import math
import struct

SECTIONS=((0,'assets','<QIIII',24),(0x40,'instances','<QIHHII',24),
          (0x80,'meshes','<QII',16),(0xc0,'shaders','<QQ',16),
          (0xf8,'brokenNodes','<Q',8),(0x130,'brokenAssets','<QQ',16))


def static_tables(blob):
    if len(blob)<0x178: raise ValueError('Short static-instance resource')
    cursor=0x178; tables={}
    for base,name,fmt,stride in SECTIONS:
        size=struct.unpack_from('<Q',blob,base+8)[0]
        count,used=struct.unpack_from('<2Q',blob,base+40)
        if count!=used or size!=count*stride or cursor+size>len(blob):
            raise ValueError('Unsupported static-instance table layout')
        tables[name]=[struct.unpack_from(fmt,blob,cursor+i*stride) for i in range(count)]
        cursor+=size
    if cursor!=len(blob): raise ValueError('Unexpected static-instance trailing data')
    return tables


def bind_static_shader(blob, model, shader):
    tables=static_tables(blob)
    matches=[(i,row) for i,row in enumerate(tables['assets']) if row[0]==model]
    if len(matches)!=1: raise ValueError('Carrier asset missing or duplicated')
    index,row=matches[0]
    start,count=row[1:3]
    if count!=1 or start>=len(tables['shaders']): raise ValueError('Unsupported carrier shader binding')
    if any(i!=index and r[1]<=start<r[1]+r[2] for i,r in enumerate(tables['assets'])):
        raise ValueError('Carrier shader override is shared')
    out=bytearray(blob)
    struct.pack_into('<I',out,0x178+index*24+16,0xffffffff)
    shader_offset=0x178+len(tables['assets'])*24+len(tables['instances'])*24+len(tables['meshes'])*16
    struct.pack_into('<QQ',out,shader_offset+start*16,*shader)
    static_tables(out)
    return bytes(out)


def place_static_world(transforms, instances, carrier=None):
    tables=static_tables(instances)
    scenery={row[0] for row in tables['instances']}
    if carrier is not None and carrier not in scenery: raise ValueError('Carrier is not a native static instance')
    if len(transforms)<56 or (len(transforms)-56)%176: raise ValueError('Unsupported transform resource')
    count=(len(transforms)-56)//176
    if struct.unpack_from('<2Q',transforms,40)!=(count,count): raise ValueError('Transform count mismatch')
    out=bytearray(transforms); hidden=[]; retained=[]; seen=set()
    for off in range(56,len(out),176):
        entity=struct.unpack_from('<Q',out,off+8)[0]
        if entity not in scenery: continue
        if entity in seen: raise ValueError('Duplicate static transform entity')
        seen.add(entity)
        values=struct.unpack_from('<10f',out,off+32)
        if not all(math.isfinite(v) for v in values) or abs(sum(v*v for v in values[:4])-1)>.02:
            raise ValueError('Invalid native static transform')
        if entity==carrier:
            # Vertices and collision were already authored in the same world frame.
            struct.pack_into('<10f',out,off+32,0,0,0,1,0,0,0,1,1,1)
            retained.append(entity)
        else:
            struct.pack_into('<3f',out,off+60,0,0,0)
            hidden.append(entity)
    if carrier is not None and retained!=[carrier]: raise ValueError('Missing carrier transform')
    return bytes(out),dict(hiddenStaticInstances=len(hidden),retainedCarriers=len(retained),
                           unmatchedStaticEntities=[f'{x:016x}' for x in sorted(scenery-seen)])
