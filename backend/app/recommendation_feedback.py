"""Project-local soft feedback over local CLIP; no model or sync calls."""
from . import db


def record(project_id,image_id,purpose,usable):
    if purpose not in ('layout','palette','style') or type(usable)is not bool:
        raise ValueError('反馈参数无效')
    conn=db.connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():raise KeyError('项目不存在')
        if not conn.execute('SELECT 1 FROM images WHERE id=?',(image_id,)).fetchone():raise KeyError('图片不存在')
        conn.execute('''INSERT INTO project_recommendation_feedback(project_id,image_id,purpose,usable)
            VALUES (?,?,?,?) ON CONFLICT(project_id,image_id,purpose) DO UPDATE SET
            usable=excluded.usable,updated_at=datetime('now')''',(project_id,image_id,purpose,int(usable)))
        conn.commit()
    except BaseException:
        if conn.in_transaction:conn.rollback()
        raise
    finally:conn.close()


def adjuster(conn,similar,column,project_id=None,negative_ids=()):
    positive=set();negative=set(negative_ids)
    if project_id is not None:
        if not conn.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():raise KeyError('项目不存在')
        positive={r[0]for r in conn.execute('SELECT image_id FROM pins WHERE project_id=?',(project_id,))}
        for row in conn.execute('SELECT image_id,usable FROM project_recommendation_feedback WHERE project_id=? AND purpose=?',(project_id,column)):
            (positive if row['usable'] else negative).add(row['image_id'])
    positive-=negative
    if not positive and not negative:return None
    if similar is None:return None
    try:
        import numpy as np
        cache=similar._cache();positions=cache['positions'];matrix=cache['matrix']
        def proximity(ids):
            indexes=[positions[i]for i in ids if i in positions]
            return np.maximum(0,(matrix@matrix[indexes].T).max(axis=1)) if indexes else np.zeros(len(matrix))
        delta=.03*proximity(positive)-.03*proximity(negative)
        return lambda i,score:score+float(delta[positions[i]]) if i in positions else score
    except Exception:
        # A missing local index must not break the ordinary recommendation route.
        return None


def local_neighbors(similar,image_id,eligible,excluded,limit=30):
    import numpy as np
    cache=similar._cache();position=cache['positions'].get(image_id)
    if position is None:raise ValueError('这张图的本地向量尚未就绪')
    scores=cache['matrix']@cache['matrix'][position]
    seed_sha=cache['by_id'][image_id]['sha256']
    order=np.lexsort((cache['ids'],-scores))
    result=[]
    for position in order:
        i=int(cache['ids'][position])
        if i in excluded or (eligible is not None and i not in eligible):continue
        if cache['by_id'][i]['sha256']==seed_sha:continue
        result.append((i,float(scores[position])))
        if len(result)==limit:break
    return result
