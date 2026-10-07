"""Transactional paired native terrain TEST installer, with manifest-only rollback.

No process is launched or killed. Invoke through Install-NativeMap.ps1 after Echo
is closed. This does not claim full Minecraft gameplay or a complete world.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import uuid
from echo_package import Package,DATA,MANIFEST
from native_map_package import sha
from echo_path import echo_game

ROOT=Path(__file__).resolve().parents[1]


def ensure_echo_closed():
    if os.name!='nt': raise RuntimeError('The Echo installer requires Windows')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
                           'if (Get-Process -Name echovr -ErrorAction SilentlyContinue) { exit 3 }'],
                          capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode: raise RuntimeError('Close both the Echo client and dedicated server before installing or restoring.')


def save_json(path,value):
    temporary=path.with_name(path.name+'.tmp')
    with temporary.open('w',encoding='utf-8') as f:
        json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)


def atomic_manifest(path,data):
    temporary=path.with_name(path.name+'.echocraft-'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()


def verify_resources(package,report,field):
    seen=set()
    for item in report['resources']:
        key=int(item['type'],16),int(item['name'],16)
        if key in seen:raise ValueError('Duplicate resource in install report')
        seen.add(key)
        r=package.by_key[key]
        if sha(package.read(r))!=item[field]:raise ValueError(f'Resource differs from expected {field} state: {key}')


def prepare(game,plan):
    game=Path(game).resolve();plan=Path(plan).resolve()
    report=json.loads((plan/'report.json').read_text())
    if report['schema']!=1 or report['package']!=2:raise ValueError('Unsupported package plan')
    manifest=(plan/'manifest.candidate').read_bytes();original=(plan/'manifest.original').read_bytes()
    delta=(plan/'package-2.append').read_bytes()
    if sha(manifest)!=report['manifestAfter'] or sha(original)!=report['manifestBefore'] or sha(delta)!=report['appendSha256']:
        raise ValueError('Staged package hashes do not match the report')
    if len(delta)+report['packageSizeBefore']!=report['packageSizeAfter']:raise ValueError('Invalid append length')
    package=Package(game)
    if package.manifest_bytes!=original:raise ValueError(f'Installed manifest changed; rebuild the candidate: {game}')
    target=package.root/'packages'/f'{MANIFEST}_2'
    if target.stat().st_size!=report['packageSizeBefore']:raise ValueError(f'Package size changed; rebuild the candidate: {game}')
    verify_resources(package,report,'before')
    return dict(game=game,plan=plan,report=report,manifest=manifest,original=original,delta=delta,
                target=target,manifestPath=package.root/'manifests'/MANIFEST)


def install(targets,backup_root,process_guard=ensure_echo_closed,commit_hook=None):
    process_guard()
    prepared=[prepare(game,plan) for game,plan in targets]
    if len({p['game'] for p in prepared})!=len(prepared):raise ValueError('Duplicate install target')
    if len({p['report'].get('sourceSha256') for p in prepared})!=1:raise ValueError('Paired plans use different world captures')
    backup_root=Path(backup_root);backup_root.mkdir(parents=True,exist_ok=True)
    lock=backup_root/'install.lock'
    with lock.open('x'):pass
    transaction=backup_root/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    state=dict(schema=1,status='preparing',testOnly=True,targets=[])
    try:
        transaction.mkdir()
        for i,p in enumerate(prepared):
            with (transaction/f'{i}.manifest.original').open('xb') as f:
                f.write(p['original']);f.flush();os.fsync(f.fileno())
            state['targets'].append(dict(game=str(p['game']),plan=str(p['plan']),original=f'{i}.manifest.original',
                                         manifestBefore=p['report']['manifestBefore'],manifestAfter=p['report']['manifestAfter'],
                                         report=p['report']))
        save_json(transaction/'transaction.json',state)
        # Append all data first. Original manifests remain active until every
        # append verifies; restore never truncates package files.
        for p in prepared:
            process_guard()
            if p['manifestPath'].read_bytes()!=p['original']:raise ValueError('Manifest changed during install')
            with p['target'].open('r+b') as f:
                f.seek(0,2)
                if f.tell()!=p['report']['packageSizeBefore']:raise ValueError('Package changed during install')
                f.write(p['delta']);f.flush();os.fsync(f.fileno())
                f.seek(p['report']['packageSizeBefore'])
                if sha(f.read())!=p['report']['appendSha256']:raise ValueError('Appended data failed readback')
        state['status']='appended';save_json(transaction/'transaction.json',state)
        for i,p in enumerate(prepared):
            process_guard()
            if p['manifestPath'].read_bytes()!=p['original']:raise ValueError('Manifest changed before activation')
            atomic_manifest(p['manifestPath'],p['manifest'])
            if commit_hook:commit_hook(i)
            changed=Package(p['game']);verify_resources(changed,p['report'],'after')
        state['status']='installed';save_json(transaction/'transaction.json',state)
        save_json(backup_root/'latest.json',dict(transaction=transaction.name))
        return transaction
    except BaseException:
        errors=[]
        for p in prepared:
            try:
                current=p['manifestPath'].read_bytes()
                if current==p['manifest']:atomic_manifest(p['manifestPath'],p['original'])
                elif current!=p['original']:errors.append(str(p['game'])+': manifest changed externally; preserved')
            except Exception as error:errors.append(str(error))
        state['status']='rollback-incomplete' if errors else 'failed-manifests-restored'
        state['rollbackErrors']=errors
        if transaction.exists():save_json(transaction/'transaction.json',state)
        raise
    finally:lock.unlink(missing_ok=True)


def restore(transaction,process_guard=ensure_echo_closed):
    process_guard();transaction=Path(transaction).resolve()
    state=json.loads((transaction/'transaction.json').read_text())
    if state['schema']!=1:raise ValueError('Unsupported transaction')
    targets=[]
    for item in state['targets']:
        name=item['original']
        if Path(name).name!=name:raise ValueError('Invalid backup filename')
        original=(transaction/name).read_bytes()
        if sha(original)!=item['manifestBefore']:raise ValueError('Backup manifest changed')
        path=Path(item['game'])/DATA/'manifests'/MANIFEST
        current=sha(path.read_bytes())
        if current not in (item['manifestBefore'],item['manifestAfter']):raise ValueError('Other map changes detected; refusing to overwrite them')
        if current==item['manifestAfter']:verify_resources(Package(item['game']),item['report'],'after')
        targets.append((path,original,item))
    for path,original,item in targets:
        process_guard();atomic_manifest(path,original)
        verify_resources(Package(item['game']),item['report'],'before')
    state['status']='restored';save_json(transaction/'transaction.json',state)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--restore',action='store_true');p.add_argument('--check',action='store_true')
    args=p.parse_args();base=ROOT/'runtime/world-replacement';backups=base/'install-backups'
    if args.restore:
        latest=json.loads((backups/'latest.json').read_text())['transaction']
        if Path(latest).name!=latest:raise ValueError('Invalid transaction directory')
        restore(backups/latest);print('Original client and server manifests restored; appended data left unreferenced.');return
    release=json.loads((base/'terrain-test-release.json').read_text())
    if not release.get('readyForTerrainTest') or not release.get('testOnly'):raise ValueError('No verified terrain test release')
    for folder in ('package-plan','native-server'):
        if sha((base/folder/'report.json').read_bytes())!=release['reportHashes'][folder]:raise ValueError('Release plans changed; rebuild release')
    targets=[(echo_game(),base/'package-plan'),
             (ROOT/'EchoVR - SERVER/ready-at-dawn-echo-arena',base/'native-server')]
    if args.check:
        for game,plan in targets:prepare(game,plan)
        print('Both baseline checks passed. No files changed.');return
    transaction=install(targets,backups)
    print('Installed experimental native terrain test on client and local server.')
    print('Backup: '+str(transaction))
    print('Launch your server normally, then join it through Discord/Spark. Live blocks and full Minecraft gameplay are not enabled.')


if __name__=='__main__':main()
