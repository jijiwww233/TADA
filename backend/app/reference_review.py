"""Append-only manual decision history and atomic, conflict-aware undo."""
import uuid
from . import db
from .reference_value import labels


def review_rows():
    from .importer import list_images
    conn=db.connect()
    try: by_id=labels(conn)
    finally: conn.close()
    result=[]
    for image in list_images():
        row=by_id.get(image['id'],{'id':image['id'],'category':'uncertain','auto_category':None,
            'manual_category':None,'manual_reference':None,'confirmed':0,'reason':None})
        result.append({**image,**row,'needs_confirmation':not row['manual_category'] and
            row['manual_reference'] is None and row['category'] in ('text_card','life','uncertain')})
    return result



def apply(ids, is_reference=None, *, category=None):
    ids=list(dict.fromkeys(ids))
    if not ids or len(ids)>2000 or any(type(i)is not int or i<=0 for i in ids):
        raise ValueError('请选择1至2000张图片')
    operation=uuid.uuid4().hex
    conn=db.connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        current=labels(conn)
        if any(i not in current for i in ids): raise KeyError('图片不存在')
        for i in ids:
            row=current[i]
            target=category
            reference=None if category else int(bool(is_reference))
            conn.execute('INSERT INTO reference_manual_decisions(image_id,is_reference) VALUES (?,?) ON CONFLICT(image_id) DO UPDATE SET is_reference=excluded.is_reference',(i,reference))
            conn.execute("""INSERT INTO image_reference_labels(image_id,manual_category,confirmed) VALUES (?,?,0)
                ON CONFLICT(image_id) DO UPDATE SET manual_category=excluded.manual_category,confirmed=0""",(i,target))
            conn.execute("""INSERT INTO reference_manual_events
                (operation,image_id,before_category,after_category,before_confirmed,after_confirmed,before_reference,after_reference)
                VALUES (?,?,?,?,?,0,?,?)""",(operation,i,row['manual_category'],target,row['confirmed'],row['manual_reference'],reference))
        conn.execute('COMMIT')
        return {'operation':operation,'updated':len(ids)}
    except BaseException:
        if conn.in_transaction:conn.rollback()
        raise
    finally: conn.close()


def undo(operation):
    conn=db.connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        events=conn.execute('SELECT * FROM reference_manual_events WHERE operation=? ORDER BY id',(operation,)).fetchall()
        if not events:raise KeyError('操作不存在')
        current=labels(conn);restored=[];conflicts=[];undo_id=uuid.uuid4().hex
        for event in events:
            i=event['image_id'];row=current.get(i)
            last=conn.execute('SELECT MAX(id) FROM reference_manual_events WHERE image_id=?',(i,)).fetchone()[0]
            if not row or last!=event['id'] or row['manual_category']!=event['after_category'] or row['confirmed']!=event['after_confirmed'] or row['manual_reference']!=event['after_reference']:
                conflicts.append(i);continue
            conn.execute('INSERT INTO reference_manual_decisions(image_id,is_reference) VALUES (?,?) ON CONFLICT(image_id) DO UPDATE SET is_reference=excluded.is_reference',(i,event['before_reference']))
            conn.execute('UPDATE image_reference_labels SET manual_category=?,confirmed=? WHERE image_id=?',
                         (event['before_category'],event['before_confirmed'],i))
            conn.execute('''INSERT INTO reference_manual_events(operation,image_id,before_category,after_category,
                before_confirmed,after_confirmed,before_reference,after_reference) VALUES (?,?,?,?,?,?,?,?)''',
                (undo_id,i,event['after_category'],event['before_category'],event['after_confirmed'],event['before_confirmed'],event['after_reference'],event['before_reference']))
            restored.append(i)
        conn.execute('COMMIT')
        return {'restored':restored,'conflicts':conflicts}
    except BaseException:
        if conn.in_transaction:conn.rollback()
        raise
    finally:conn.close()
