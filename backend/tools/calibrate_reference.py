"""Offline local calibration. Weak labels from existing fields; never calls a cloud API.

Install optional requirements-calibration.txt, select a private DATA_DIR, then run
  python backend/tools/calibrate_reference.py --out <new-profile.json> --report <new-report.json>
Profile coefficients are local-only and must not be committed. Grouped folds are
used to select the conservative threshold: reported selection-set scores are NOT
an independent test score or a guarantee of zero visual false exclusions.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import config
from app.embedding.search_application import readonly_connection
from app.reference_value import candidate_heuristic as heuristic
from app.reference_calibration import FEATURE_NAMES,PROMPT_GROUPS,cheap_features,prompt_scores,decide
from app.similarity import ImageSimilarity


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.out.exists() or args.report.exists():parser.error('输出已存在；请选新路径，保留原配置和记录')
    import numpy as np
    from sklearn.model_selection import StratifiedGroupKFold
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.metrics import confusion_matrix
    engine=ImageSimilarity()
    try:
        engine.warmup(); cache=engine._cache()
        with readonly_connection(config.db_path()) as conn:
            rows={r['id']:dict(r) for r in conn.execute('SELECT i.id,i.post_id,i.file_path,a.* FROM images i LEFT JOIN analyses a ON a.image_id=i.id')}
        vectors=engine.encoder.encode_texts([p for g in PROMPT_GROUPS for p in g])
        scores=prompt_scores(cache['matrix'],vectors)
        examples=[];features={};failures=[]
        for i,s,v in zip(cache['ids'],scores,cache['matrix']):
            i=int(i)
            try:cheap=cheap_features(config.data_dir()/rows[i]['file_path'])
            except Exception:failures.append(i);continue
            features[i]=(s,cheap,v)
            if rows[i].get('status')=='done':
                category=heuristic(rows[i])[0]
                if category in ('visual_ref','text_card'):
                    examples.append((i,[*s,*cheap,*v],int(category=='text_card'),rows[i]['post_id']))
        if len(examples)<30:raise ValueError('至少需要30张有明确字段弱标签的图片')
        ids,X,y,groups=zip(*examples);X=np.asarray(X);y=np.asarray(y);groups=np.asarray(groups)
        cv=StratifiedGroupKFold(n_splits=4,shuffle=True,random_state=42);prob=np.zeros(len(y))
        for train,valid in cv.split(X,y,groups):
            if len(set(y[train]))<2:raise ValueError('校准分组缺类别，不能可靠拟合')
            model=make_pipeline(StandardScaler(),LogisticRegression(C=.03,class_weight='balanced',max_iter=1000))
            model.fit(X[train],y[train]);prob[valid]=model.predict_proba(X[valid])[:,1]
        threshold=max(.65,float(prob[y==0].max())+.001)
        model=make_pipeline(StandardScaler(),LogisticRegression(C=.03,class_weight='balanced',max_iter=1000));model.fit(X,y)
        profile={'version':'reference_calibrated_v2','clip_identity':engine.encoder.identity,
                 'feature_names':list(FEATURE_NAMES),'prompts':PROMPT_GROUPS,
                 'mean':model[0].mean_.tolist(),'scale':model[0].scale_.tolist(),
                 'coefficients':model[1].coef_[0].tolist(),'intercept':float(model[1].intercept_[0]),
                 'visual_threshold':min(.5,threshold-.1),'text_threshold':threshold,'life_margin':.025}
        counts=Counter();details=[]
        for i,(s,f,v) in features.items():
            category,reason=decide(s,f,v,profile)
            if rows[i].get('status')=='done':category,reason,_=heuristic(rows[i])
            counts[category]+=1;details.append({'image_id':i,'category':category,'reason':reason})
        mask=(prob<=profile['visual_threshold'])|(prob>=threshold)
        report={'weak_label_count':len(y),'post_groups':len(set(groups)),
                'note':'字段弱标签，不是人工真值；阈值用OOF选择，非独立检验。life弱样本太少，不报告life泛化准确率。',
                'confusion_at_half':confusion_matrix(y,prob>=.5).tolist(),
                'confusion_at_exclusion_threshold':confusion_matrix(y,prob>=threshold).tolist(),
                'decided_count':int(mask.sum()),'decided_correct':int(((prob>=threshold)==y)[mask].sum()),
                'counts':dict(counts),'feature_failures':failures,'items':details,
                'oof':[{'image_id':i,'expected':int(t),'score':float(p)}for i,t,p in zip(ids,y,prob)],
                'paid_calls':0}
        for path,value in ((args.out,profile),(args.report,report)):
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=1),encoding='utf-8')
        print(json.dumps({'counts':dict(counts),'weak_labels':len(y),'paid_calls':0}))
    finally:engine.shutdown()


if __name__=='__main__':main()
