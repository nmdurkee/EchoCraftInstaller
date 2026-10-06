"""Composite coplanar Minecraft cutout overlays for Echo's opaque carrier."""
import copy
import math
import struct
import zlib
from native_world_material import read_png

def png_rgba(w,h,data):
    if len(data)!=w*h*4:raise ValueError('Pixel size mismatch')
    def chunk(kind,payload):
        return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload))
    scan=b''.join(b'\0'+data[y*w*4:(y+1)*w*4] for y in range(h))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(scan))+chunk(b'IEND',b'')

def composite_layers(batches,png):
    w,h,pixels=read_png(png);batches=[copy.deepcopy(batch) for batch in batches];tiles=[];cache={};merged=0
    def bounds(uvs):
        xs=[v[0]*w for v in uvs];ys=[v[1]*h for v in uvs]
        return math.floor(min(xs)),math.floor(min(ys)),math.ceil(max(xs)),math.ceil(max(ys))
    def crop(rect):
        x0,y0,x1,y1=rect
        if not 0<=x0<x1<=w or not 0<=y0<y1<=h:raise ValueError('Invalid overlay sprite bounds')
        return b''.join(pixels[(y*w+x0)*4:(y*w+x1)*4] for y in range(y0,y1))
    def tint(colors):
        if any(any(abs(a-b)>1e-6 for a,b in zip(c,colors[0])) for c in colors):
            raise ValueError('Overlay with varying vertex tint needs a shader blend')
        return colors[0]
    for batch in batches:
        n=len(batch['verts'])
        if n%4 or batch['faces']!=[f for i in range(0,n,4) for f in ([i,i+1,i+2],[i,i+2,i+3])]:
            raise ValueError('Expected source baked quads before native mesh packing')
        groups={};skip=set()
        for i in range(0,n,4):
            key=(tuple(sorted(tuple(round(x,5) for x in v) for v in batch['verts'][i:i+4])),tuple(round(x,5) for x in batch['normals'][i]))
            groups.setdefault(key,[]).append(i)
        for group in groups.values():
            if len(group)==1:continue
            if len(group)!=2:raise ValueError('Unsupported multi-layer coplanar surface')
            a,b=group
            if batch['verts'][a:a+4]!=batch['verts'][b:b+4]:raise ValueError('Overlay corner ordering differs')
            auv,buv=batch['uvs'][a:a+4],batch['uvs'][b:b+4];ar,br=bounds(auv),bounds(buv)
            aw,ah=ar[2]-ar[0],ar[3]-ar[1]
            if (aw,ah)!=(br[2]-br[0],br[3]-br[1]):raise ValueError('Overlay dimensions differ')
            for u,v in zip(auv,buv):
                if abs((v[0]-u[0])*w-(br[0]-ar[0]))>.001 or abs((v[1]-u[1])*h-(br[1]-ar[1]))>.001:
                    raise ValueError('Overlay UV mapping differs')
            ac,bc=tint(batch['colors'][a:a+4]),tint(batch['colors'][b:b+4]);base,over=crop(ar),crop(br)
            if any(base[i]!=255 for i in range(3,len(base),4)) or ac[3]!=1:
                raise ValueError('Overlay base is not opaque')
            key=(ar,br,tuple(ac),tuple(bc))
            if key not in cache:
                result=bytearray(len(base))
                for p in range(0,len(base),4):
                    alpha=over[p+3]/255*bc[3]
                    for c in range(3):result[p+c]=round(base[p+c]*ac[c]*(1-alpha)+over[p+c]*bc[c]*alpha)
                    result[p+3]=255
                cache[key]=len(tiles);tiles.append(dict(width=aw,height=ah,pixels=bytes(result)))
            tile=cache[key]
            for j in range(4):
                batch['uvs'][a+j]=[tile,(auv[j][0]*w-ar[0]),(auv[j][1]*h-ar[1])]
                batch['colors'][a+j]=[1,1,1,1]
            skip.add(b);merged+=1
        out={k:[] for k in ('verts','uvs','colors','normals','faces')}
        for i in range(0,n,4):
            if i in skip:continue
            start=len(out['verts'])
            for k in ('verts','uvs','colors','normals'):out[k].extend(batch[k][i:i+4])
            out['faces'].extend(([start,start+1,start+2],[start,start+2,start+3]))
        batch.clear();batch.update(out)
    if not tiles:return batches,png,dict(compositedQuads=0,tiles=0)
    # Pack generated tiles below the original atlas, with replicated borders.
    x=1;y=h+1;rowheight=0
    for tile in tiles:
        if tile['width']+2>w:raise ValueError('Overlay tile exceeds atlas width')
        if x+tile['width']+1>w:x=1;y+=rowheight;rowheight=0
        tile.update(x=x,y=y);x+=tile['width']+2;rowheight=max(rowheight,tile['height']+2)
    newh=1<<(y+rowheight-1).bit_length()
    if newh>1024:raise ValueError('Composited atlas exceeds current native texture budget')
    output=bytearray(w*newh*4);output[:len(pixels)]=pixels
    for tile in tiles:
        tw,th=tile['width'],tile['height']
        for dy in range(-1,th+1):
            for dx in range(-1,tw+1):
                p=(min(th-1,max(0,dy))*tw+min(tw-1,max(0,dx)))*4
                q=((tile['y']+dy)*w+tile['x']+dx)*4
                output[q:q+4]=tile['pixels'][p:p+4]
    for batch in batches:
        for i,uv in enumerate(batch['uvs']):
            if len(uv)==3:
                tile=tiles[uv[0]];batch['uvs'][i]=[(tile['x']+uv[1])/w,(tile['y']+uv[2])/newh]
            else:batch['uvs'][i]=[uv[0],uv[1]*h/newh]
    return batches,png_rgba(w,newh,output),dict(compositedQuads=merged,tiles=len(tiles),atlasWidth=w,atlasHeight=newh)
