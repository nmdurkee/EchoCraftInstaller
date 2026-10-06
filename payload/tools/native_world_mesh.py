"""Compile a Minecraft mesh into a measured single-draw Echo mesh layout.

Only descriptor/GPU buffers are authored here. The native mesh builder binds
the static wallpaper carrier, atlas material and world-space instance separately.
"""
import math
import struct
from native_world_collision import length


def compile_mesh(descriptor, batches):
    counts=[struct.unpack_from('<Q',descriptor,i*56+48)[0] for i in range(10)]
    if len(descriptor)!=1568 or counts!=[1,1,1,0,0,1,0,0,0,0]:
        raise ValueError('Unsupported native mesh carrier layout')
    vertices=[]; normals=[]; uvs=[]; colors=[]; faces=[]
    for batch in batches:
        count=len(batch['verts'])
        if any(len(batch[key])!=count for key in ('normals','uvs','colors')):
            raise ValueError('Vertex attribute count mismatch')
        if any(len(f)!=3 or any(type(i)!=int or not 0<=i<count for i in f) for f in batch['faces']):
            raise ValueError('Invalid mesh indices')
        faces.extend(tuple(i+len(vertices) for i in f) for f in batch['faces'])
        vertices.extend(batch['verts']); normals.extend(batch['normals'])
        uvs.extend(batch['uvs']); colors.extend(batch['colors'])
    if not vertices or not faces or len(vertices)>65535:
        raise ValueError('Carrier needs between 1 and 65535 vertices; split larger worlds')
    s0=bytearray(); s1=bytearray()
    snorm=lambda x:round(max(-1.,min(1.,x))*32767)
    for p,n,uv,color in zip(vertices,normals,uvs,colors):
        if len(p)!=3 or len(n)!=3 or len(uv)!=2 or len(color)!=4:
            raise ValueError('Invalid vertex attribute width')
        if not all(math.isfinite(x) for v in (p,n,uv,color) for x in v):
            raise ValueError('Nonfinite vertex attribute')
        if abs(length(n)-1)>1e-4 or any(not 0<=x<=1 for x in color):
            raise ValueError('Invalid normal or color')
        packed=sum(round(x*255)<<(8*i) for i,x in enumerate(color))
        u,v=uv
        # The donor declares two COLOR channels. Keep tint in both so its
        # vertex shader cannot select the old constant-white channel.
        s0.extend(struct.pack('<IIffHH',packed,packed,u,v,int((u-math.floor(u))*65535),int((v-math.floor(v))*65535)))
        axis=min(range(3),key=lambda a:abs(n[a]))
        tangent=[(1 if i==axis else 0)-n[axis]*n[i] for i in range(3)]
        size=length(tangent); tangent=[x/size for x in tangent]
        s1.extend(struct.pack('<3f8h',*p,*(snorm(x) for x in n),0,*(snorm(x) for x in tangent),32767))
    indices=b''.join(struct.pack('<3H',*f) for f in faces)
    gpu=bytes(s0+s1+indices); out=bytearray(descriptor)
    struct.pack_into('<I',out,568,len(gpu))
    lo=[min(v[a] for v in vertices) for a in range(3)]
    hi=[max(v[a] for v in vertices) for a in range(3)]
    center=[(a+b)/2 for a,b in zip(lo,hi)]
    for off,value in ((60,lo),(72,hi),(84,center)): struct.pack_into('<3f',out,888+off,*value)
    struct.pack_into('<f',out,888+96,max(length([x-y for x,y in zip(v,center)]) for v in vertices))
    struct.pack_into('<4I',out,1108,0,len(vertices),0,len(faces)*3)
    for off,value in ((0x128,0),(0x130,len(s0)),(0x13c,len(vertices)),(0x140,len(vertices)),(0x14c,len(vertices))):
        struct.pack_into('<I',out,1152+off,value)
    struct.pack_into('<4I',out,1488,len(s0)+len(s1),len(faces)*3,2,0)
    return bytes(out),gpu
