"""Create a reversible package append plan; never writes into a game install.

Original resources and package bytes remain intact. Only a new manifest points
at appended frames. Runtime readiness is separate from package correctness.
"""
from compression import zstd
import hashlib
import struct
from echo_package import decompress


def sha(data): return hashlib.sha256(data).hexdigest()


def append_plan(package, changes, target_package, original_size):
    if not changes: raise ValueError('Empty package change set')
    if not 0<=original_size<=0xffffffff or not 0<=target_package<=0xffffffff:
        raise ValueError('Invalid package address')
    path=package.root/'packages'/f'{package.name}_{target_package}'
    if path.stat().st_size!=original_size:raise ValueError('Package size differs from append baseline')
    keys=set(); raw=bytearray(package.raw); delta=bytearray(); records=[]; newframes=[]
    for resource,data in changes:
        key=(resource.type,resource.name)
        if key in keys or package.by_key.get(key)!=resource: raise ValueError('Duplicate or foreign resource')
        keys.add(key)
        if not data or len(data)>512*1024*1024: raise ValueError('Unsupported candidate resource size')
        before=package.read(resource)
        if before==data: continue
        compressed=zstd.compress(data)
        if decompress(compressed,len(data))!=data: raise ValueError('Candidate compression failed readback')
        offset=original_size+len(delta)
        if offset+len(compressed)>0xffffffff: raise ValueError('Package exceeds 32-bit frame offsets')
        frame=len(package.frames)+len(newframes)
        struct.pack_into('<QI',raw,192+resource.index*32+16,frame,len(data))
        newframes.append((target_package,offset,len(compressed),len(data)))
        delta.extend(compressed)
        records.append(dict(type=f'{resource.type:016x}',name=f'{resource.name:016x}',
                            before=sha(before),after=sha(data),frame=frame,bytes=len(data)))
    if not records: raise ValueError('No resource bytes changed')
    # The installed end markers have usize=0 and can retain nonzero csize.
    # A restored manifest can point at an earlier EOF: rollback intentionally
    # retains unreferenced appended bytes. The old marker must still enclose all
    # original data frames and may never point beyond the measured file EOF.
    referenced={r.location&0xffffffff for r in package.resources}
    markers=[]
    for i,(pkg,offset,csize,usize) in enumerate(package.frames):
        if pkg==target_package and not usize and i not in referenced:
            last=max((start+size for fp,start,size,rawsize in package.frames if fp==target_package and rawsize),default=0)
            if not last<=offset<=original_size: raise ValueError('Package end marker does not enclose its data frames')
            struct.pack_into('<I',raw,package.c_offset+i*16+4,original_size+len(delta));markers.append(i)
    raw.extend(b''.join(struct.pack('<4I',*f) for f in newframes))
    count=len(package.frames)+len(newframes)
    for off,value in ((144,count*16),(176,count),(184,count)): struct.pack_into('<Q',raw,off,value)
    compressed=zstd.compress(bytes(raw))
    manifest=package.manifest_bytes[:8]+struct.pack('<QQ',len(raw),len(compressed))+compressed
    # Independent readback of every relocated entry, including the locator.
    decoded=decompress(manifest[24:],len(raw))
    for record in records:
        resource=package.by_key[(int(record['type'],16),int(record['name'],16))]
        loc,size=struct.unpack_from('<QI',decoded,192+resource.index*32+16)
        pkg,offset,csize,usize=struct.unpack_from('<4I',decoded,package.c_offset+loc*16)
        if pkg!=target_package or size!=usize: raise ValueError('Relocated manifest entry mismatch')
        at=offset-original_size
        if sha(decompress(delta[at:at+csize],usize))!=record['after']: raise ValueError('Relocated resource readback mismatch')
    # B contains opaque engine hashes/build stamps: preserve byte-for-byte, as
    # existing native map tools do. They are not recomputed or described as valid.
    bstart=192+package.counts[0]*32
    if decoded[bstart:package.c_offset]!=package.raw[bstart:package.c_offset]: raise ValueError('Opaque metadata changed')
    report=dict(schema=1,manifestBefore=sha(package.manifest_bytes),manifestAfter=sha(manifest),
                package=target_package,packageSizeBefore=original_size,packageSizeAfter=original_size+len(delta),
                appendSha256=sha(delta),endMarkersUpdated=markers,resources=records,
                rollback='Restore the saved baseline manifest; leave unreferenced appended bytes intact')
    return manifest,bytes(delta),report
