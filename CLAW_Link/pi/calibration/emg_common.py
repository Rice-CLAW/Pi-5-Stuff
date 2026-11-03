"""Shared dataset loading, splitting, training and reporting."""
import argparse,csv,json,re
from pathlib import Path
import joblib,numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.svm import SVC
from emg_features import extract

ROOT=Path(__file__).resolve().parent

def records(dataset,path):
    result=[]
    if dataset=='mendeley':
        for f in sorted(path.glob('subject_*/*.csv')):
            m=re.fullmatch(r'S(\d+)_R(\d+)_G(\d+)\.csv',f.name)
            if not m:continue
            s,r,g=map(int,m.groups())
            result.append((f,f'subject_{s}',str(r),g))
    else:
        for item in json.loads((path/'manifest.json').read_text()):
            if item['gesture']==0:continue
            result.append((path/item['file'],f'subject_{item["subject"]:02d}/{item["date"]}',item['recording'],item['gesture']))
    if not result:raise ValueError(f'No recordings found at {path}')
    return result

def windows(record,dataset,n,step,features,threshold):
    path,session,trial,label=record
    with path.open() as f:rows=list(csv.DictReader(f))
    x=np.array([[float(r[f'Channel_{i}']) for i in range(1,9)] for r in rows])
    if not np.isfinite(x).all():raise ValueError(f'Nonfinite values: {path}')
    if dataset=='gestures':
        segments=np.array([r['Segment'] for r in rows]);times=np.array([float(r['Time_ms']) for r in rows])
        # Segment IDs preserve boundaries even though the CSV is gesture-specific.
        blocks=[x[segments==seg] for seg in dict.fromkeys(segments)]
    else:blocks=[x]
    out=[]
    for block in blocks:
        for start in range(0,len(block)-n+1,step):
            out.append(extract(block[start:start+n],features,threshold))
    if not out:raise ValueError(f'No full windows: {path}')
    return np.array(out)

def model(kind,seed):
    if kind=='rf':return RandomForestClassifier(n_estimators=200,min_samples_leaf=2,n_jobs=-1,random_state=seed)
    if kind=='lda':return make_pipeline(StandardScaler(),LinearDiscriminantAnalysis(solver='lsqr',shrinkage='auto'))
    return make_pipeline(StandardScaler(),SVC(C=1,gamma='scale'))

def run(kind):
    p=argparse.ArgumentParser(description='Per-session or pooled-session EMG training')
    p.add_argument('--dataset',choices=['mendeley','gestures'],default='mendeley')
    p.add_argument('--data-dir',type=Path)
    p.add_argument('--train-datasets',type=int,help='Pool N complete participant/date sessions; test on all remaining sessions')
    p.add_argument('--train-reps',type=int,help='Training recordings per session; default 4 for Mendeley, 1 for gestures')
    p.add_argument('--max-sessions',type=int,help='Limit how many sessions are processed, before splitting')
    p.add_argument('--feature-set',choices=['baseline','hudgins','tdar','relative_tdar'],default='hudgins')
    p.add_argument('--window',type=int,help='Rows per window: defaults 50 Mendeley, 250 gestures')
    p.add_argument('--step',type=int)
    p.add_argument('--threshold',type=float)
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--output-dir',type=Path)
    a=p.parse_args()
    if a.seed<0:p.error('seed must be nonnegative')
    if a.train_datasets is not None and a.train_reps is not None:p.error('Choose --train-datasets OR --train-reps')
    path=a.data_dir or (ROOT/'EMG_data/mendeley_data/EMG_DATA/data/csv_data' if a.dataset=='mendeley' else ROOT/'EMG_data/gestures_csv')
    n=a.window or (50 if a.dataset=='mendeley' else 250);step=a.step or n//2
    threshold=a.threshold if a.threshold is not None else (1.0 if a.dataset=='mendeley' else 1e-5)
    if n<6 or step<1 or threshold<0:p.error('Require window >=6, step >=1, threshold >=0')
    data=records(a.dataset,path);sessions=sorted({r[1] for r in data});rng=np.random.default_rng(a.seed)
    if a.max_sessions is not None:
        if not 1<=a.max_sessions<=len(sessions):p.error('Invalid --max-sessions')
        sessions=sorted(rng.choice(sessions,a.max_sessions,replace=False).tolist())
    data=[r for r in data if r[1] in sessions]
    out=a.output_dir or ROOT/({'svm':'svm','rf':'random_forest','lda':'lda'}[kind])/f'results_{a.dataset}'
    out.mkdir(parents=True,exist_ok=True)
    assignments={};models=[]
    if a.train_datasets is not None:
        if not 1<=a.train_datasets<len(sessions):p.error('--train-datasets must leave at least one test session')
        chosen=set(rng.choice(sessions,a.train_datasets,replace=False).tolist())
        for f,s,t,g in data:assignments[str(f)]='train' if s in chosen else 'test'
        models=[('pooled',data)]
    else:
        count=a.train_reps if a.train_reps is not None else (4 if a.dataset=='mendeley' else 1)
        for s in sessions:
            rs=[r for r in data if r[1]==s];trials=sorted({r[2] for r in rs})
            if not 1<=count<len(trials):p.error(f'{s}: --train-reps must be 1..{len(trials)-1}')
            chosen=set(rng.choice(trials,count,replace=False).tolist())
            for f,_,t,g in rs:assignments[str(f)]='train' if t in chosen else 'test'
            models.append((s,rs))
    report=[];manifest=[]
    for name,rs in models:
        print(f'Training {kind.upper()}: {name}',flush=True)
        xs=[];ys=[];ss=[];mask=[]
        for r in rs:
            f,s,t,g=r;v=windows(r,a.dataset,n,step,a.feature_set,threshold)
            xs.append(v);ys.extend([g]*len(v));ss.extend([s]*len(v));mask.extend([assignments[str(f)]=='train']*len(v))
            manifest.append({'file':str(f),'session':s,'trial':t,'gesture':g,'split':assignments[str(f)],'windows':len(v)})
        x=np.concatenate(xs);y=np.array(ys);ss=np.array(ss);train=np.array(mask);test=~train
        unseen=set(y[test])-set(y[train])
        if unseen:raise ValueError(f'{name}: gestures {unseen} missing from training. Choose a different split or more training recordings.')
        estimator=model(kind,a.seed);estimator.fit(x[train],y[train]);pred=estimator.predict(x[test]);yt=y[test];st=ss[test]
        dest=out/name;dest.mkdir(parents=True,exist_ok=True)
        settings={'dataset':a.dataset,'classifier':kind,'features':a.feature_set,'window':n,'step':step,'threshold':threshold,'seed':a.seed,'train_sessions':sorted(set(ss[train]))}
        joblib.dump({'pipeline':estimator,'settings':settings},dest/f'{kind}_model.joblib')
        for s in sorted(set(st)):
            take=st==s;row={'session':s,'overall':float(np.mean(pred[take]==yt[take]))}
            for g in sorted(set(yt[take])):
                hit=take&(yt==g);row[f'G{g}']=float(np.mean(pred[hit]==g))
            report.append(row)
    gestures=sorted({k for r in report for k in r if k.startswith('G')},key=lambda k:int(k[1:]));headers=['session',*gestures,'overall']
    display=[[r['session'],*[f'{r[g]:.1%}' if g in r else '-' for g in gestures],f'{r["overall"]:.1%}'] for r in report]
    display.append(['Mean',*[f'{np.mean([r[g] for r in report if g in r]):.1%}' for g in gestures],f'{np.mean([r["overall"] for r in report]):.1%}'])
    widths=[max(len(row[i]) for row in [headers]+display) for i in range(len(headers))]
    print('\nTest gesture recall (per-window); Overall = test accuracy')
    for row in [headers]+display:print(' | '.join(v.ljust(w) for v,w in zip(row,widths)))
    with (out/'summary.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=headers);w.writeheader();w.writerows(report)
    (out/'split_manifest.json').write_text(json.dumps(manifest,indent=2))
    (out/'settings.json').write_text(json.dumps({k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()},indent=2))
    print('Results:',out)
