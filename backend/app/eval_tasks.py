"""F3 task schema shared by label API and evaluator. No model/database writes."""
KEYS=('find','layout','color','style')

def validate(payload):
    if not isinstance(payload,dict) or type(payload.get('version')) is not int or payload['version']!=1 or not isinstance(payload.get('tasks'),list):
        raise ValueError('tasks格式必须为 {version:1,tasks:[]}')
    out=[]; seen=set()
    for task in payload['tasks']:
        if not isinstance(task,dict): raise ValueError('题目必须是对象')
        id=task.get('id'); query=task.get('query'); kind=task.get('type'); split=task.get('split')
        if not isinstance(id,str) or not id.strip() or id.strip() in seen: raise ValueError('题目id为空或重复')
        if kind not in ('find','create') or split not in ('dev','test'): raise ValueError('type或split非法')
        if not isinstance(query,str) or not query.strip(): raise ValueError('query不能为空')
        expected=task.get('expected')
        if not isinstance(expected,dict) or set(expected)!=set(KEYS): raise ValueError('expected必须含find/layout/color/style四栏')
        clean={}
        for key in KEYS:
            ids=expected[key]
            if not isinstance(ids,list) or any(type(i) is not int or i<=0 for i in ids): raise ValueError('标注必须为正整数image_id数组')
            clean[key]=list(dict.fromkeys(ids))
        if kind=='find' and any(clean[k]for k in KEYS if k!='find'): raise ValueError('find题只标find；创作题按三栏分别标')
        if kind=='create' and clean['find']: raise ValueError('create题不标find')
        note=task.get('note','')
        if not isinstance(note,str): raise ValueError('note必须是字符串')
        seen.add(id.strip());out.append(dict(id=id.strip(),type=kind,query=query.strip(),split=split,expected=clean,note=note))
    return {'version':1,'tasks':out}
