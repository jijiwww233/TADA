"""F3 per-column evaluation. Default: DEV + mock, no network or model calls.
--responses consumes previously saved product responses keyed by task id.
Actual live adapters may be injected into evaluate(); this CLI never invokes cloud models.
Legacy paid runner stays in eval_suite.py and is not the F3 canonical-schema entrypoint.
"""
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.eval_tasks import validate, KEYS


def usage(raw):
    if isinstance(raw.get('usage'),dict): buckets=[raw['usage']]
    else: buckets=[raw.get('needs_usage')or{}]+[x.get('usage')or{}for x in raw.get('columns',[])]
    complete = isinstance(raw.get('usage'),dict) and raw['usage'].get('complete',True)
    return {'requests':sum(int(b.get('requests')or 0)for b in buckets) if complete else None,
            'reported_requests':sum(int(b.get('requests')or 0)for b in buckets), 'complete':complete,
            'prompt_tokens':sum(int(b.get('prompt_tokens')or 0)for b in buckets) if any(b.get('prompt_tokens')is not None for b in buckets)else None,
            'completion_tokens':sum(int(b.get('completion_tokens')or 0)for b in buckets)if any(b.get('completion_tokens')is not None for b in buckets)else None}


def evaluate(payload, search, recommend, *, final=False, mock=False):
    tasks=validate(payload)['tasks']; selected=[t for t in tasks if t['split']==('test'if final else'dev')]; rows=[]
    for task in selected:
        start=time.perf_counter();error=None
        try: raw=(search if task['type']=='find'else recommend)(task['id'],task['query'])
        except Exception as exc: raw={};error=type(exc).__name__  # never leak provider key/URL
        raw=raw if isinstance(raw,dict)else{};stats=usage(raw);columns={}
        error=error or raw.get("evaluation_error")
        if task['type']=='find': predictions={'find':raw.get('items',[])}
        else: predictions={('color'if c.get('purpose')=='palette'else c.get('purpose')):c.get('items',[]) for c in raw.get('columns',[])}
        for key in (['find']if task['type']=='find'else['layout','color','style']):
            top=[]
            for item in predictions.get(key,[])[:5]:
                id=item.get('image_id')if isinstance(item,dict)else item
                if type(id)is int and id not in top:top.append(id)
            expected=task['expected'][key];hits=[i for i in expected if i in top];missing=[i for i in expected if i not in top]
            columns[key]={'top5':top,'expected':expected,'hit_count':len(hits),'hits':hits,'missing':missing,
                          'hit':bool(hits)if expected and not error else None,
                          'recall':len(hits)/len(expected)if expected and not error else None,
                          'status':'error'if error else'mock_unscored'if mock else'unlabeled'if not expected else'scored'}
        if task['type']=='find':
            expected=set(task['expected']['find'])
            ordered=[item.get('image_id') if isinstance(item,dict) else item for item in predictions['find']]
            columns['find']['rank']=next((i for i,image_id in enumerate(ordered,1) if image_id in expected),None)
        rows.append({'id':task['id'],'query':task['query'],'type':task['type'],'split':task['split'],'columns':columns,
                     'usage':stats,'elapsed_ms':round((time.perf_counter()-start)*1000,3),'error':error})
    total={'requests':sum(r['usage']['requests']for r in rows) if all(r['usage']['requests']is not None for r in rows)else None, 'reported_requests':sum(r['usage']['reported_requests']for r in rows),'elapsed_ms':round(sum(r['elapsed_ms']for r in rows),3)}
    for key in ('prompt_tokens','completion_tokens'):
        values=[r['usage'][key]for r in rows];total[key]=sum(x for x in values if x is not None)if all(x is not None for x in values)else None
    total['complete']=all(r['usage']['complete']for r in rows)
    total['tokens']=total['prompt_tokens']+total['completion_tokens']if total['complete'] and total['prompt_tokens']is not None and total['completion_tokens']is not None else None
    scored_find=[row['columns']['find'] for row in rows
                 if row['type']=='find' and row['columns']['find']['status']=='scored']
    find_summary={'scored':len(scored_find), **{
        f'top{limit}':sum(col['rank'] is not None and col['rank']<=limit for col in scored_find)
        for limit in (5,20)}}
    return {'find_summary':find_summary,'format':'f3-evaluation-v1' ,'mode':'MOCK (not a quality score)'if mock else'recorded_or_injected_responses',
            'split':'test'if final else'dev','tasks_total':len(rows),'unlabeled_columns':sum(c['status']=='unlabeled'for r in rows for c in r['columns'].values()),'per_task':rows,'usage':total}


def markdown(report):
    lines=['# F3 评测结果', '',f"模式：{report['mode']}；split={report['split']}；{report['tasks_total']}题",'',
            '空标注不算通过；MOCK仅验证链路，不能当质量成绩。','',
           f"找图前 5 名命中 {report['find_summary']['top5']} 题，前 20 名命中 {report['find_summary']['top20']} 题。",
           '', '|题目|栏|前5|答案名次|命中标注数|漏掉的图|状态|','|---|---|---|---|---|---|---|']
    for row in report['per_task']:
        for key,c in row['columns'].items():lines.append(f"|{row['id']}|{key}|{c['top5']}|{c.get('rank') or '—'}|{c['hit_count']}/{len(c['expected'])}|{c['missing']}|{c['status']}|")
    lines+=['','请求/token/时间：`'+json.dumps(report['usage'],ensure_ascii=False)+'`','']
    return '\n'.join(lines)


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--tasks',type=Path,default=Path(__file__).resolve().parents[2]/'eval/tasks.json')
    ap.add_argument('--responses',type=Path);ap.add_argument('--mock',action='store_true');ap.add_argument('--final',action='store_true',help='only with this flag evaluate test; never for tuning')
    ap.add_argument('--split',choices=['dev','test']);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args(argv)
    if args.split=='test' and not args.final:ap.error('test必须加 --final')
    if args.split=='dev' and args.final:ap.error('--final只跑test，不能同时指定dev')
    if args.output.exists()or args.output.with_suffix('.md').exists():ap.error('输出已存在，请选择新文件，避免覆盖历史评测')
    if args.mock and args.responses:ap.error('选择mock或responses其中之一')
    try:
        payload=json.loads(args.tasks.read_text(encoding='utf8'));mock=not args.responses
        responses=json.loads(args.responses.read_text(encoding='utf8'))if args.responses else{}
        if isinstance(responses,dict)and isinstance(responses.get('responses'),dict):responses=responses['responses']  # f4_live package wrapper
        def respond(id,query):
            if not mock:
                if id not in responses:raise ValueError('missing_fixture')
                return responses[id]
            return {'items':[],'columns':[],'usage':{'requests':0,'prompt_tokens':0,'completion_tokens':0}}
        report=evaluate(payload,respond,respond,final=args.final,mock=mock)
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');args.output.with_suffix('.md').write_text(markdown(report),encoding='utf8');print(json.dumps({'tasks':report['tasks_total'],'split':report['split'],'usage':report['usage'],'mode':report['mode']},ensure_ascii=False));return 0
    except (ValueError,OSError) as exc:print(str(exc),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
