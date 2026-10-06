"""Disable measured arena volumetric light shafts without shifting scene tables."""
import math
import struct
from scene_geometry import box_triangles
from native_world_material import read_png

def daylight_shell(atlas, radius=128):
    """Interim inward-facing daylight backdrop; no collision or time/weather simulation."""
    width,height,rgba=read_png(atlas)
    pixel=next((i//4 for i in range(0,len(rgba),4) if rgba[i:i+4]==b'\xff\xff\xff\xff'),None)
    if pixel is None:raise ValueError('Atlas lacks a neutral white sample for the daylight backdrop')
    uv=[(pixel%width+.5)/width,(pixel//width+.5)/height]
    batch=dict(verts=[],faces=[],normals=[],uvs=[],colors=[])
    for triangle,normal in box_triangles((-radius,)*3,(radius,)*3):
        start=len(batch['verts'])
        for point in reversed(triangle):
            batch['verts'].append(list(point));batch['normals'].append([-v for v in normal]);batch['uvs'].append(uv[:])
            t=max(0,min(1,(point[1]+radius)/(2*radius)))
            batch['colors'].append([.65-.35*t,.8-.25*t,1,1])
        batch['faces'].append([start,start+1,start+2])
    return batch

def suppress_volume_lights(data):
    if len(data)<8: raise ValueError('Truncated scene resource')
    lights=struct.unpack_from('<I',data)[0]
    cursor=4+lights*360
    if cursor+4>len(data): raise ValueError('Invalid scene light table')
    for i in range(lights):
        if struct.unpack_from('<I',data,4+i*360+4)[0]>2:
            raise ValueError('Unknown scene light layout')
    count=struct.unpack_from('<I',data,cursor)[0]
    start=cursor+4
    if start+count*296>len(data): raise ValueError('Invalid scene volume table')
    out=bytearray(data)
    for i in range(count):
        off=start+i*296
        rgb=struct.unpack_from('<3f',data,off+28)
        magnitude=struct.unpack_from('<f',data,off+40)[0]
        if not all(math.isfinite(x) and 0<=x<=1 for x in rgb) or not math.isfinite(magnitude) or magnitude<0:
            raise ValueError('Unexpected scene volume parameters')
        struct.pack_into('<4f',out,off+28,0,0,0,0)
    return bytes(out),dict(suppressedVolumeLights=count,preservedSceneLights=lights)

def hide_arena_canvases(data, assets):
    """Zero the scale of identified arena score/readiness canvases only."""
    if len(data)<56 or (len(data)-56)%88:raise ValueError('Unknown canvas component layout')
    count=(len(data)-56)//88
    if struct.unpack_from('<Q',data,8)[0]!=count*88 or struct.unpack_from('<2Q',data,40)!=(count,count):
        raise ValueError('Canvas descriptor mismatch')
    out=bytearray(data);hidden=[]
    for off in range(56,len(data),88):
        kind,entity,term,slot,asset=struct.unpack_from('<5Q',data,off)
        if asset not in assets:continue
        if term!=0xffffffff or slot not in (4,5):raise ValueError('Unknown arena canvas record')
        lo,hi,pixels=struct.unpack_from('<3f',data,off+40)
        if not all(math.isfinite(v) for v in (lo,hi,pixels)) or not 0<=lo<=hi or pixels<=0:
            raise ValueError('Invalid arena canvas scale')
        struct.pack_into('<2f',out,off+40,0,0)
        struct.pack_into('<2I',out,off+52,1,0)
        hidden.append(dict(actor=f'{entity:016x}',asset=f'{asset:016x}'))
    return bytes(out),hidden
