"""Assemble the current native map candidates into a non-installable append plan."""
import json
from pathlib import Path
from native_map_target import LEVELS, LEVEL_NAME, baseline_package
from native_map_package import append_plan, sha

ROOT=Path(__file__).resolve().parents[1]


def main():
    game=Path(r'C:\Program Files\Meta Horizon\Software\Software\ready-at-dawn-echo-arena')
    package=baseline_package(game); source=ROOT/'runtime/world-replacement'
    changes=[]; reports=[]
    for folder in ('native-collision','native-mesh'):
        report=json.loads((source/folder/'report.json').read_text());reports.append(report)
        if report['manifestSha256']!=sha(package.manifest_bytes): raise ValueError('Installed manifest changed; rebuild candidates')
        for item in report['resources']:
            filename=item['file']
            if Path(filename).name!=filename: raise ValueError('Invalid candidate filename')
            data=(source/folder/filename).read_bytes()
            r=package.by_key[(int(item['type'],16),int(item['name'],16))]
            if sha(data)!=item['after'] or sha(package.read(r))!=item['before']:
                raise ValueError('Candidate or installed baseline changed')
            changes.append((r,data))
    if any(r['targetLevel']!=LEVELS[0] for r in reports): raise ValueError('Wrong level target')
    if len({r['sourceSha256'] for r in reports})!=1: raise ValueError('Geometry and collision snapshots differ')
    target=package.root/'packages'/f'{package.name}_2'
    manifest,delta,report=append_plan(package,changes,2,target.stat().st_size)
    report.update(targetLevel=LEVELS[0],targetName=LEVEL_NAME,installable=False,sourceSha256=reports[0]['sourceSha256'],
                  status='Verified offline package plan; runtime gates remain open',
                  unresolved=sorted({s for r in reports for s in r['unresolved']}))
    out=source/'package-plan';out.mkdir(parents=True,exist_ok=True)
    (out/'manifest.candidate').write_bytes(manifest)
    (out/'package-2.append').write_bytes(delta)
    (out/'manifest.original').write_bytes(package.manifest_bytes)
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(changedResources=len(report['resources']),appendBytes=len(delta),installable=False)))


if __name__=='__main__':main()
