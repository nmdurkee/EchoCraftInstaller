"""Suppress scene-local instanced prop rendering while preserving actor behavior."""
import struct
from native_world_spawn import actor_layout


def empty_single_draw(descriptor):
    if len(descriptor)!=1568 or [struct.unpack_from('<Q',descriptor,i*56+48)[0] for i in range(10)]!=[1,1,1,0,0,1,0,0,0,0]:
        raise ValueError('Empty visual donor is not the measured single-draw format')
    out=bytearray(descriptor)
    # Preserve buffers, material layout and vertex format, but issue zero indices.
    struct.pack_into('<I',out,1040+68+12,0)
    return bytes(out)


def hide_instanced_props(blob,actors,known_models,empty_model):
    layout=actor_layout(actors);ids=set(layout['ids']);out=bytearray(blob)
    poolstart=layout['transforms']+layout['count']*48
    pooled={layout['ids'][i] for i in range(layout['count']) if struct.unpack_from('<H',actors,poolstart+i*2)[0]!=0xffff}
    # Preserve descendants of pooled prefab roots, too.
    protected=set(pooled)
    for _ in range(layout['count']):
        changed=False
        for i,entity in enumerate(layout['ids']):
            parent=struct.unpack_from('<H',actors,layout['parents']+i*2)[0]
            if parent!=0xffff and parent>=layout['count']: raise ValueError('Invalid actor parent')
            if parent!=0xffff and layout['ids'][parent] in protected and entity not in protected:
                protected.add(entity);changed=True
        if not changed:break
    found=[];preserved=[]
    for off in range(0,len(blob)-72+1,8):
        kind,actor,term,slot,model,sentinel=struct.unpack_from('<6Q',blob,off)
        if actor not in ids or term!=0xffffffff or slot>32 or sentinel!=0xffffffffffffffff:continue
        if model not in known_models: raise ValueError('Instanced actor references an unknown native model')
        entry=dict(actor=f'{actor:016x}',model=f'{model:016x}',offset=off)
        if actor in protected:preserved.append(entry);continue
        struct.pack_into('<Q',out,off+32,empty_model);found.append(entry)
    return bytes(out),dict(hiddenBindings=found,preservedPooledBindings=preserved,
                          scope='Scene-local CInstanceModelCR only; CModelCR and shared player/UI levels preserved')


def collapse_model_actors(model_cr,actors,transforms,known_models):
    layout=actor_layout(actors);ids=layout['ids'];index={v:i for i,v in enumerate(ids)}
    parents=[struct.unpack_from('<H',actors,layout['parents']+i*2)[0] for i in range(len(ids))]
    if any(p!=0xffff and p>=len(ids) for p in parents):raise ValueError('Invalid actor hierarchy')
    poolstart=layout['transforms']+len(ids)*48
    protected={i for i in range(len(ids)) if struct.unpack_from('<H',actors,poolstart+i*2)[0]!=0xffff}
    for _ in ids:
        extra={i for i,p in enumerate(parents) if p in protected}
        if extra<=protected:break
        protected|=extra
    # An ancestor's zero scale would also hide a protected prefab.
    for i in list(protected):
        visited=set()
        while parents[i]!=0xffff:
            i=parents[i]
            if i in visited:raise ValueError('Cyclic actor hierarchy')
            visited.add(i);protected.add(i)
    selected=set();preserved=set()
    for off in range(32,len(model_cr)-8,8):
        kind,entity,flags,record,model=struct.unpack_from('<5Q',model_cr,off-32)
        if kind!=0x38ee951a26fb816a or record!=28 or flags!=0xffffffff or entity not in index:continue
        if model not in known_models:raise ValueError('Unknown actor scene model')
        (preserved if index[entity] in protected else selected).add(entity)
    if len(transforms)<56 or (len(transforms)-56)%176:raise ValueError('Invalid transform table')
    rows={struct.unpack_from('<Q',transforms,o+8)[0]:o for o in range(56,len(transforms),176)}
    if not selected<=rows.keys():raise ValueError('Model actor lacks a native transform')
    actor_out=bytearray(actors);xf_out=bytearray(transforms)
    for entity in selected:
        struct.pack_into('<3f',actor_out,layout['transforms']+index[entity]*48+28,0,0,0)
        struct.pack_into('<3f',xf_out,rows[entity]+60,0,0,0)
    return bytes(actor_out),bytes(xf_out),dict(hiddenModelActors=[f'{v:016x}' for v in sorted(selected)],
                                              preservedPooledActors=[f'{v:016x}' for v in sorted(preserved)])
