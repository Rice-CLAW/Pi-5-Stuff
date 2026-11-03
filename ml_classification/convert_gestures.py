#!/usr/bin/env python3
"""Convert the uploaded gesture ZIP without joining discontiguous segments."""
import argparse,csv,io,json,re,zipfile
from pathlib import Path
from datetime import datetime

def convert(source, output):
    if output.exists():
        raise ValueError('Output already exists; choose a new directory to avoid stale CSVs')
    output.mkdir(parents=True)
    manifest=[]; total=0; excluded=[]
    with zipfile.ZipFile(source) as z:
        for name in sorted(z.namelist()):
            m=re.search(r'/(\d+)/(.+raw_data.+_(\d{2}\.\d{2}\.\d{2}))\.txt$',name)
            if not m: continue
            person,recording,date=m.groups(); date=datetime.strptime(date,'%d.%m.%y').date().isoformat()
            folder=output/f'subject_{int(person):02d}'/date;folder.mkdir(parents=True,exist_ok=True)
            buckets={}; segment=0; previous=None
            with z.open(name) as raw:
                reader=csv.DictReader(io.TextIOWrapper(raw),delimiter='\t')
                for row in reader:
                    if row.get('class') is None:
                        excluded.append({'source':name,'time':row.get('time'),'reason':'missing class label'});segment+=1;previous=None;continue
                    label=int(row['class']);total+=1
                    if label!=previous: segment+=1
                    previous=label
                    buckets.setdefault(label,[]).append([row['time'],segment,recording,*[row[f'channel{i}'] for i in range(1,9)]])
            for label,rows in buckets.items():
                path=folder/f'{recording}_G{label}.csv'
                with path.open('w',newline='') as f:
                    w=csv.writer(f);w.writerow(['Time_ms','Segment','Recording',*[f'Channel_{i}' for i in range(1,9)]]);w.writerows(rows)
                manifest.append({'file':str(path.relative_to(output)),'subject':int(person),'date':date,'recording':recording,'gesture':label,'rows':len(rows),'segments':len({r[1] for r in rows})})
        readme=[n for n in z.namelist() if n.lower().endswith('readme.txt')]
        if readme:(output/'SOURCE_README.txt').write_bytes(z.read(readme[0]))
    if not manifest:raise ValueError('No source recordings matched')
    (output/'excluded_rows.json').write_text(json.dumps(excluded,indent=2))
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    assert sum(m['rows'] for m in manifest)==total
    print(f'Converted {total} rows into {len(manifest)} CSVs; {len({(m["subject"],m["date"]) for m in manifest})} participant/date sessions')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('zip',type=Path);p.add_argument('output',type=Path);a=p.parse_args();convert(a.zip,a.output)
