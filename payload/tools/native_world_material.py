"""Resident Minecraft atlas and measured Echo material containers, stdlib only."""
import struct
import zlib
from echo_package import symbol


def read_png(blob):
    if blob[:8]!=b'\x89PNG\r\n\x1a\n': raise ValueError('Invalid PNG signature')
    cursor=8; compressed=bytearray(); dimensions=None; ended=False
    while cursor<len(blob):
        if cursor+12>len(blob): raise ValueError('Truncated PNG chunk')
        size=struct.unpack_from('>I',blob,cursor)[0]; kind=blob[cursor+4:cursor+8]
        end=cursor+12+size
        if end>len(blob): raise ValueError('PNG chunk exceeds file')
        data=blob[cursor+8:end-4]
        if zlib.crc32(kind+data)!=struct.unpack_from('>I',blob,end-4)[0]: raise ValueError('PNG CRC mismatch')
        if kind==b'IHDR':
            if dimensions is not None or cursor!=8 or size!=13: raise ValueError('Invalid PNG header')
            w,h,depth,color,compression,filtering,interlace=struct.unpack('>IIBBBBB',data)
            if depth!=8 or color!=6 or compression or filtering or interlace or not 0<w<=4096 or not 0<h<=4096:
                raise ValueError('Expected bounded, noninterlaced RGBA8 atlas')
            dimensions=w,h
        elif kind==b'IDAT': compressed.extend(data)
        elif kind==b'IEND':
            if size or end!=len(blob): raise ValueError('Invalid PNG ending')
            ended=True; break
        elif not kind[0]&32: raise ValueError('Unsupported critical PNG chunk')
        cursor=end
    if dimensions is None or not ended: raise ValueError('Incomplete PNG')
    w,h=dimensions; stride=w*4; expected=(stride+1)*h
    decoder=zlib.decompressobj(); raw=decoder.decompress(compressed,expected+1)
    if len(raw)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('PNG decompressed size mismatch')
    pixels=bytearray(); previous=bytearray(stride)
    for y in range(h):
        at=y*(stride+1); mode=raw[at]; row=bytearray(raw[at+1:at+1+stride])
        if mode>4: raise ValueError('Invalid PNG filter')
        for i in range(stride):
            a=row[i-4] if i>=4 else 0; b=previous[i]; c=previous[i-4] if i>=4 else 0
            if mode==1: prediction=a
            elif mode==2: prediction=b
            elif mode==3: prediction=(a+b)//2
            elif mode==4:
                p=a+b-c; errors=(abs(p-a),abs(p-b),abs(p-c))
                prediction=(a,b,c)[errors.index(min(errors))]
            else: prediction=0
            row[i]=(row[i]+prediction)&255
        pixels.extend(row); previous=row
    return w,h,bytes(pixels)


def resident_texture(png, texel_scale=1):
    """Return descriptor + DDS for staging; store them in LOW and MID separately.

    This installed texture uses a 256-byte LOW descriptor and a separate MID
    DDS resource. Descriptor word 14 is row pitch, not a DDS byte offset.
    """
    w,h,rgba=read_png(png)
    if type(texel_scale) is not int or not 1<=texel_scale<=4 or max(w,h)*texel_scale>4096:
        raise ValueError('Unsupported atlas texel scale')
    if texel_scale!=1:
        # Preserve Minecraft's nearest-neighbor texels while reducing the
        # donor shader's linear-filter transition width in source pixels.
        rows=[]
        for y in range(h):
            row=rgba[y*w*4:(y+1)*w*4]
            rows.append(b''.join(row[x:x+4]*texel_scale for x in range(0,len(row),4))*texel_scale)
        rgba=b''.join(rows);w*=texel_scale;h*=texel_scale
    # Native Backrooms resident DDS layout: DXGI B8G8R8A8_UNORM_SRGB (91).
    pixels=bytearray(rgba); pixels[0::4]=rgba[2::4]; pixels[2::4]=rgba[0::4]
    dds=bytearray(148); dds[:4]=b'DDS '
    struct.pack_into('<7I',dds,4,124,0x100f,h,w,w*4,0,1)
    struct.pack_into('<II4s',dds,76,32,4,b'DX10')
    struct.pack_into('<I',dds,108,0x1000); struct.pack_into('<5I',dds,128,91,3,0,1,0)
    dds.extend(pixels)
    descriptor=bytearray(b'\xff'*256)
    struct.pack_into('<16I',descriptor,192,1,w,h,1,1,0,91,1,0,0,w,h,1,len(dds),w*4,0)
    return bytes(descriptor+dds)


def validate_texture_parts(low,mid):
    """Validate the measured split descriptor/DDS contract, not just PNG pixels."""
    if len(low)!=256 or len(mid)<148 or mid[:4]!=b'DDS ':
        raise ValueError('Texture requires a 256-byte LOW descriptor and a MID DDS')
    fields=struct.unpack_from('<16I',low,192)
    _,width,height,mips,_,_,fmt,_,_,_,resident_w,resident_h,resident_mips,dds_size,pitch,_=fields
    if not 0<width<=4096 or not 0<height<=4096 or (width,height,mips)!=(resident_w,resident_h,resident_mips):
        raise ValueError('Resident texture dimensions disagree')
    if mips!=1 or fmt!=91 or pitch!=width*4 or dds_size!=len(mid):
        raise ValueError('Invalid resident texture format, pitch or payload length')
    if len(mid)!=148+width*height*4:raise ValueError('DDS pixel payload length mismatch')
    if struct.unpack_from('<I',mid,4)[0]!=124 or struct.unpack_from('<II4s',mid,76)!=(32,4,b'DX10'):
        raise ValueError('Invalid DDS header')
    if struct.unpack_from('<3I',mid,12)!=(height,width,pitch) or struct.unpack_from('<I',mid,28)[0]!=1:
        raise ValueError('DDS dimensions disagree with the native descriptor')
    if struct.unpack_from('<5I',mid,128)!=(91,3,0,1,0):raise ValueError('Unexpected DDS format or array layout')
    return width,height


def containers(data):
    cursor=424; arrays=[]
    for off,stride in ((0x28,1),(0x60,16),(0xa0,8),(0xd8,8),(0x118,32),(0x150,16)):
        if len(data)<off+56: raise ValueError('Truncated material header')
        pointer,size,reserved=struct.unpack_from('<3Q',data,off)
        reserved2=struct.unpack_from('<I',data,off+24)[0]
        capacity,count=struct.unpack_from('<2Q',data,off+40)
        if pointer or reserved or reserved2 or count>capacity or (capacity and size!=capacity*stride):
            raise ValueError('Unsupported material container')
        cursor=(cursor+7)&~7; end=cursor+count*stride
        if end>len(data): raise ValueError('Truncated material payload')
        arrays.append((cursor,count,stride)); cursor=end
    if cursor!=len(data): raise ValueError('Unexpected material trailing data')
    return arrays


def bind_atlas(data, texture):
    arrays=containers(data); out=bytearray(data)
    props,propbytes,_=arrays[0]; handles,count,_=arrays[1]
    slots={}
    for i in range(count):
        key,offset=struct.unpack_from('<QI',data,handles+i*16)
        if offset%4 or offset>=propbytes: raise ValueError('Unsupported material property offset')
        slots[key]=offset
    def put(name,fmt,*values):
        key=symbol(name)
        if key not in slots: raise ValueError('Missing material property '+name)
        off=props+slots[key]
        if off+struct.calcsize(fmt)>props+propbytes: raise ValueError('Property exceeds array')
        struct.pack_into(fmt,out,off,*values)
    for name in ('enablebakedlighting','enabledynamiclighting','enablesglighting','enablefog'):
        put(name,'<I',0)
    put('layer0.emissive.enabled','<I',1)
    put('layer0.emissive.applyvtxcolor','<I',1)
    put('layer0.basecolor.intensity','<f',0.)
    put('layer0.emissive.intensity','<f',1.)
    put('layer0.emissive.tint','<3f',1.,1.,1.)
    put('emissivescale','<f',1.)
    start,count,_=arrays[5]; changed=set()
    wanted={symbol('layer0_basecolor_map'),symbol('layer0_emissive_map')}
    for i in range(count):
        off=start+i*16; key=struct.unpack_from('<Q',data,off)[0]
        if key in wanted: struct.pack_into('<Q',out,off+8,texture); changed.add(key)
    if changed!=wanted: raise ValueError('Material lacks the required atlas texture slots')
    return bytes(out)
