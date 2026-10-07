"""Stage native full-size mesh resources. No writes to the game installation."""
import hashlib
import json
from pathlib import Path
import struct
import sys
from echo_package import Package,resource_type
from native_world_mesh import compile_mesh
from native_world_material import resident_texture, bind_atlas, validate_texture_parts
from native_world_placement import place_static_world, static_tables, bind_static_shader
from native_map_target import LEVELS, EMPTY_LEVEL, LEVEL_NAME, baseline_package
from native_world_spawn import relocate, spawn_meshes
from native_world_props import empty_single_draw,hide_instanced_props,collapse_model_actors
from native_world_environment import suppress_volume_lights,daylight_shell,hide_arena_canvases
from native_world_layers import composite_layers
from native_world_cutout import cutout_plants
from scene_geometry import Alignment
from echo_path import echo_game

ROOT=Path(__file__).resolve().parents[1]


def main():
    package=baseline_package(echo_game())
    source=ROOT/'runtime/world-replacement/world-input.json'
    world=json.loads(source.read_text())
    atlas=(source.parent/'atlas.png').read_bytes()
    if hashlib.sha256(atlas).hexdigest()!=world['atlasSha256']:
        raise ValueError('Atlas does not match the captured world input')
    batches=[b for c in world['chunks'] for b in c['render']]
    source_atlas=atlas
    batches,atlas,layer_report=composite_layers(batches,atlas)
    batches,cutout_report=cutout_plants(batches,atlas,Alignment(**world['alignment']))
    # The interim daylight shell occluded the live world (it writes depth at ~64 m).
    # The live world plugin now draws a Minecraft sky behind everything instead.
    carrier='9e2bc98a06a57256'
    entity=0x031a1cdb7d0c8f6d
    material='589d769988b44242'
    texture=0x510c1bcd316828ee
    r=package.get('CGInstancedModelResource',carrier)
    gr=package.get('CGInstancedModelResource',carrier,gpu=True)
    descriptor,gpu=compile_mesh(package.read(package.get('CGInstancedModelResource','9eafa673d5f7a3b8')),batches)
    bound={struct.unpack_from('<Q',descriptor,off)[0] for off in (1504,1520)}
    if len(bound)!=1: raise ValueError('Static carrier has mixed material bindings')
    if bound!={int(material,16)}:
        # Some community Echo builds bind the carrier to another material. Use theirs and carry on.
        material=f'{bound.pop():016x}'
        print(f'WARNING: this Echo build binds the arena carrier to material {material}, not the one EchoCraft was tested with. Trying anyway.',file=sys.stderr)
    changes=[(r,descriptor),(gr,gpu)]
    empty_model=0xb56a33f06ee43b41
    empty_resource=package.get('CGInstancedModelResource',empty_model)
    changes.append((empty_resource,empty_single_draw(package.read(empty_resource))))
    model_names={r.name for r in package.resources if r.type==resource_type('CGInstancedModelResource')}
    mr=package.get('CGMaterialResource',material)
    changes.append((mr,bind_atlas(package.read(mr),texture)))
    tr=package.by_key[(0x4a4c32c49300b8a0,texture)]
    mid=package.by_key[(0xbeac1969cb7b8861,texture)]
    if len(package.read(tr))!=256 or package.read(mid)[:4]!=b'DDS ':
        raise ValueError('Atlas donor no longer uses separate LOW descriptor / MID DDS resources')
    texture_data=resident_texture(atlas,texel_scale=4)
    validate_texture_parts(texture_data[:256],texture_data[256:])
    changes.extend(((tr,texture_data[:256]),(mid,texture_data[256:])))
    placement={}
    spawn_report=[]
    props={}
    environment={}
    model_actors={}
    empty_hull=package.read(package.get('CGMeshListResource',EMPTY_LEVEL))
    if len(empty_hull)!=56: raise ValueError('Empty native hull donor changed')
    for level in LEVELS:
        xr=package.get('CTransformCR',level)
        si=package.read(package.get('CGStaticInstanceResource',level))
        if level==LEVELS[0]:
            assets=[row for row in static_tables(si)['assets'] if row[0]==int(carrier,16)]
            if len(assets)!=1 or assets[0][2]!=1: raise ValueError('Unexpected static carrier draw binding')
        if level==LEVELS[0]:
            si=bind_static_shader(si,int(carrier,16),(12228134891314832642,19))
            changes.append((package.get('CGStaticInstanceResource',level),si))
        xf,stats=place_static_world(package.read(xr),si,entity if level==LEVELS[0] else None)
        if level==LEVELS[0]:
            ar=package.get('CActorDataResource',level)
            actors,xf,spawn_report=relocate(package.read(ar),xf,package.read(package.get('CR15SpawnPointCR',level)),
                                          spawn_meshes(source,world))
            scene_models={r.name for r in package.resources if r.type==resource_type('CGMeshListResource')}
            actors,xf,model_actors[level]=collapse_model_actors(package.read(package.get('CModelCR',level)),actors,xf,scene_models)
            changes.append((ar,actors))
        if level==LEVELS[0]:
            prop_resource=package.get('CInstanceModelCR',level)
            data,props[level]=hide_instanced_props(package.read(prop_resource),package.read(package.get('CActorDataResource',level)),model_names,empty_model)
            changes.append((prop_resource,data))
            scene_resource=package.get('CGSceneResource',level)
            scene,environment[level]=suppress_volume_lights(package.read(scene_resource))
            changes.append((scene_resource,scene))
            canvas_resource=package.get('CCanvasUICR',level)
            canvas,hidden_canvases=hide_arena_canvases(package.read(canvas_resource),{0xe13ccf2ac0c2c968,0xc38ba95f61643121})
            environment[level]['hiddenScoreCanvases']=hidden_canvases
            changes.append((canvas_resource,canvas))
        placement[level]=stats; changes.append((xr,xf))
        changes.append((package.get('CGMeshListResource',level),empty_hull))
    # Independently decode the serialized positions and indices before staging.
    count=struct.unpack_from('<I',descriptor,1152+0x13c)[0]
    s0size=struct.unpack_from('<I',descriptor,1152+0x130)[0]
    vertices=[v for b in batches for v in b['verts']]
    error=max(abs(x-y) for i,v in enumerate(vertices) for x,y in zip(v,struct.unpack_from('<3f',gpu,s0size+i*28)))
    indexstart,indexcount,stride,_=struct.unpack_from('<4I',descriptor,1488)
    if count!=len(vertices) or error>2e-5 or indexstart+indexcount*stride!=len(gpu):
        raise ValueError('Native mesh serialization failed validation')
    out=ROOT/'runtime/world-replacement/native-mesh'; out.mkdir(parents=True,exist_ok=True)
    (out/'compiled-atlas.png').write_bytes(atlas)
    digest=lambda b:hashlib.sha256(b).hexdigest()
    report=dict(targetLevel=LEVELS[0],targetName=LEVEL_NAME,schema=3,installable=False,status='Native terrain, atlas, static scenery replacement and spawn relocation staged; runtime integration pending',
                sourceSha256=digest(source.read_bytes()),atlasSha256=digest(source_atlas),compiledAtlasSha256=digest(atlas),layerComposition=layer_report,plantCutouts=cutout_report,manifestSha256=digest(package.manifest_bytes),
                carrier=carrier,carrierEntity=f'{entity:016x}',material=material,texture=f'{texture:016x}',
                vertices=count,triangles=indexcount//3,positionErrorMetres=error,placement=placement,spawnRelocations=spawn_report,actorProps=props,environment=environment,modelActors=model_actors,
                backdrop='Interim fixed daylight shell; no Minecraft sun, clouds, weather or dimension sky yet',atlasTexelScale=4,
                unresolved=['Verify atlas tint/alpha shader and native rendering in headset',
                            'Resolve remaining CModelCR scenery and sky/environment','Verify spawn semantics, boundary scripts and scatter culling',
                            'Integrate matching native collision and server behavior'],resources=[])
    for resource,data in changes:
        name=f'{resource.type:016x}_{resource.name:016x}.candidate'
        (out/name).write_bytes(data)
        report['resources'].append(dict(type=f'{resource.type:016x}',name=f'{resource.name:016x}',file=name,before=digest(package.read(resource)),after=digest(data),bytes=len(data)))
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','vertices','triangles','positionErrorMetres')}))


if __name__=='__main__': main()
