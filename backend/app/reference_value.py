"""Local image-level reference labels. No cloud client and no image deletion.

Reuses archived collection policy input validation/hash; its post-level categories
cannot represent text_card, so this layer deliberately owns a separate vocabulary.
Scores are heuristic evidence, not calibrated probabilities.
"""
import json
import os
import threading
from collections import Counter

from . import db
from .collection_classification import normalize_input, input_hash

CATEGORIES = ('visual_ref', 'text_card', 'life', 'uncertain')
VISUAL = ('visual_ref', 'uncertain')
VERSION = 'reference_value_v1'
PROMPTS = (
    '一张设计作品，海报排版，插画，艺术摄影，建筑设计展板',
    '一张纯文字截图，文字卡片，聊天记录，没有图案的文字笔记',
    '日常生活随手拍，购物订单，食物记录，生活用品，自拍',
)


def heuristic(row):
    def parse(key, default):
        try: return json.loads(row.get(key) or 'null') or default
        except (ValueError, TypeError): return default
    layout, style = parse('layout_json', {}), parse('style_json', {})
    payload = normalize_input({'images': [{
        'subject': parse('subject', []), 'layout': layout, 'style': style,
        'description': row.get('description') or '', 'visible_text': row.get('visible_text') or ''}]})
    fingerprint = input_hash(payload)
    text = row.get('visible_text') or ''
    ratio = layout.get('image_text_ratio', [])
    dominant = 'text_dominant' in ratio
    description = row.get('description') or ''
    # A typography poster is still a visual reference. Require combined evidence.
    if dominant and len(text) >= 180 and (style.get('medium') == 'typography' or
            any(x in description for x in ('纯文字', '聊天记录', '文字截图', '文字卡片'))):
        return 'text_card', '现有分析：文字占主导、长文字及文字媒介/卡片证据', fingerprint
    subject = ' '.join(parse('subject', [])) + ' ' + description
    design = any(x in subject for x in ('设计', '海报', '插画', '展板', '排版', '摄影作品', '建筑', '信息图'))
    if not design and any(x in description for x in ('生活记录', '购物订单', '聊天截图', '日常自拍', '随手拍')):
        return 'life', '现有分析：生活记录证据，缺少视觉作品证据', fingerprint
    if design:
        return 'visual_ref', '现有分析：视觉作品/设计证据', fingerprint
    return 'uncertain', '证据不足，默认保留在推荐中', fingerprint


def candidate_heuristic(row):
    def parse(key, default):
        try: return json.loads(row.get(key) or 'null') or default
        except (ValueError, TypeError): return default
    layout, style = parse('layout_json', {}), parse('style_json', {})
    payload = normalize_input({'images': [{
        'subject': parse('subject', []), 'layout': layout, 'style': style,
        'description': row.get('description') or '', 'visible_text': row.get('visible_text') or ''}]})
    fingerprint = input_hash(payload)
    text = row.get('visible_text') or ''
    ratio = layout.get('image_text_ratio', [])
    dominant = 'text_dominant' in ratio
    description = row.get('description') or ''
    # A typography poster is still a visual reference. Require combined evidence.
    if dominant and len(text) >= 180 and (style.get('medium') == 'typography' or
            any(x in description for x in ('纯文字', '聊天记录', '文字截图', '文字卡片'))):
        return 'text_card', '现有分析：文字占主导、长文字及文字媒介/卡片证据', fingerprint
    subject = ' '.join(parse('subject', [])) + ' ' + description
    evidence = subject + ' ' + str(style.get('style_note', ''))
    design = any(x in subject for x in ('设计', '海报', '插画', '展板', '排版', '摄影作品', '建筑', '信息图', 'Logo', '标志', '字体'))
    document = any(x in evidence for x in ('聊天', '对话界面', '技术文档', '学习笔记', '语法', '教材', '教程', '操作指南', '备忘录', '纯文字', '社交媒体帖子'))
    if document and ('text_dominant' in ratio or 'balanced' in ratio) and len(text) >= 60:
        return 'text_card', '现有分析：文档/对话/学习资料证据，文字占主导或均衡', fingerprint
    if style.get('medium') == 'photo' and any(x in subject for x in ('生活随拍', '生活细节', '生活记录', '购物订单', '日常自拍', '随手拍')) and not design:
        return 'life', '现有分析：生活随拍证据', fingerprint
    if 'image_dominant' in ratio and style.get('medium') in ('photo', 'illustration', '3d'):
        return 'visual_ref', '现有分析：图像为主的摄影/插画/三维作品', fingerprint
    if not design and any(x in description for x in ('生活记录', '购物订单', '聊天截图', '日常自拍', '随手拍')):
        return 'life', '现有分析：生活记录证据，缺少视觉作品证据', fingerprint
    if design:
        return 'visual_ref', '现有分析：视觉作品/设计证据', fingerprint
    return 'uncertain', '证据不足，默认保留在推荐中', fingerprint


def labels(conn, image_id=None):
    # Missing labels remain uncertain for retrieval, but are blocked from paid
    # analysis until local preclassification has actually completed.
    rows = conn.execute('''SELECT i.id,CASE WHEN m.is_reference=1 THEN 'visual_ref' ELSE COALESCE(r.manual_category,r.auto_category,'uncertain') END category,
        r.auto_category,r.manual_category,r.reason,r.version auto_version,COALESCE(r.confirmed,0) confirmed,m.is_reference manual_reference
        FROM images i LEFT JOIN image_reference_labels r ON r.image_id=i.id
        LEFT JOIN reference_manual_decisions m ON m.image_id=i.id''' + (' WHERE i.id=?' if image_id is not None else ''), (() if image_id is None else (image_id,)))
    return {r['id']: dict(r) for r in rows}


def eligible_ids(conn):
    return {i for i, r in labels(conn).items() if r['category'] in VISUAL and r['manual_reference'] != 0}


def allowed_analysis(conn, image_id, *, version=VERSION):
    r = labels(conn,image_id).get(image_id)
    if not r:return False
    if r['confirmed'] or r['manual_reference']==1:return True
    if r['manual_reference']==0:return False
    if r['manual_category']:return r['manual_category'] in VISUAL
    return r['auto_category'] in VISUAL and r['auto_version']==version



class ReferenceLabels:
    def __init__(self, similar):
        self.similar = similar
        self.candidate = os.getenv('WOMINMIN_REFERENCE_CANDIDATE','') == '1'
        self.version = 'reference_candidate_v2' if self.candidate else VERSION
        self.stop = threading.Event()
        self.thread = None
        self.error = None
        self._prompts = None

    def classify_pending(self, *, analysis_only=False):
        conn = db.connect()
        try:
            rows = [dict(r) for r in conn.execute('''SELECT i.id,i.file_path,i.sha256,a.* FROM images i
                LEFT JOIN analyses a ON a.image_id=i.id LEFT JOIN image_reference_labels r ON r.image_id=i.id
                WHERE r.version IS NULL OR r.version!=? OR (a.status='done' AND r.source!='analysis')''', (self.version,))]
            if not rows: return 0
            if analysis_only: rows = [r for r in rows if r.get('status') == 'done']
            pending = [r for r in rows if r.get('status') != 'done']
            clip = {}
            if pending:
                try:
                    import numpy as np
                    if not self.candidate:
                        if self._prompts is None:
                            self._prompts=np.asarray(self.similar.encoder.encode_texts(PROMPTS))
                            self._prompts/=np.linalg.norm(self._prompts,axis=1,keepdims=True)
                        cache=self.similar._cache()
                        for image_id,values in zip(cache['ids'],cache['matrix'] @ self._prompts.T):
                            order=np.argsort(-values);margin=float(values[order[0]]-values[order[1]])
                            clip[int(image_id)]=(CATEGORIES[int(order[0])] if margin>=.035 else 'uncertain',f'本地CLIP零样本，领先差值 {margin:.3f}')
                    else:
                        from .reference_calibration import PROMPT_GROUPS, prompt_scores, load_profile, cheap_features, decide
                        if self._prompts is None:
                            self._prompts = np.asarray(self.similar.encoder.encode_texts([p for group in PROMPT_GROUPS for p in group]))
                            self._prompts /= np.linalg.norm(self._prompts, axis=1, keepdims=True)
                        cache = self.similar._cache()
                        scores = prompt_scores(cache['matrix'], self._prompts)
                        profile = load_profile(getattr(self.similar.encoder, 'identity', None))
                        by_id = {r['id']:r for r in pending}
                        for image_id, values, image_vector in zip(cache['ids'], scores, cache['matrix']):
                            image_id=int(image_id)
                            if image_id not in by_id:continue
                            row=by_id[image_id]
                            saved=conn.execute('SELECT features_json,sha256 FROM reference_local_features WHERE image_id=?',(image_id,)).fetchone()
                            try:
                                if saved and saved['sha256']==row['sha256']:
                                    features=json.loads(saved['features_json'])
                                else:
                                    features=cheap_features(self.similar.database().parent/row['file_path'])
                                    conn.execute('INSERT INTO reference_local_features(image_id,sha256,features_json) VALUES (?,?,?) ON CONFLICT(image_id) DO UPDATE SET sha256=excluded.sha256,features_json=excluded.features_json',
                                                 (image_id,row['sha256'],json.dumps(features)))
                                clip[image_id] = decide(values,features,image_vector,profile)
                            except Exception:
                                clip[image_id] = ('uncertain','图片特征读取失败，等待人工确认')
                    self.error = None
                except Exception:
                    self.error = '本地模型/向量未就绪；未分类新图不会进入付费分析'
            count = 0
            for r in rows:
                if r.get('status') == 'done':
                    category, reason, fingerprint = (candidate_heuristic(r) if self.candidate else heuristic(r))
                    source = 'analysis'
                elif r['id'] in clip:
                    category, reason = clip[r['id']]
                    fingerprint, source = '', 'clip'
                else:
                    continue  # retry when the local image index becomes available
                conn.execute('''INSERT INTO image_reference_labels
                    (image_id,auto_category,reason,source,input_hash,version) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(image_id) DO UPDATE SET auto_category=excluded.auto_category,
                    reason=excluded.reason,source=excluded.source,input_hash=excluded.input_hash,version=excluded.version''',
                    (r['id'], category, reason, source, fingerprint, self.version))
                count += 1
            conn.commit()
            return count
        finally: conn.close()

    def next_analysis(self):
        conn = db.connect()
        try:
            for r in conn.execute("SELECT image_id FROM analyses WHERE status='pending' AND claimed_by IS NULL ORDER BY image_id"):
                if allowed_analysis(conn, r[0],version=self.version): return r[0]
            return None
        finally: conn.close()

    def start(self):
        def loop():
            while not self.stop.is_set():
                try: self.classify_pending()
                except Exception: self.error = '本地分类暂不可用，新图分析保持等待'
                self.stop.wait(5)
        self.thread = threading.Thread(target=loop, name='reference-labels', daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=5)

    def snapshot(self):
        conn = db.connect()
        try:
            items = labels(conn)
            counts = Counter(r['category'] for r in items.values())
            return {'items': list(items.values()), 'counts': {c: counts[c] for c in CATEGORIES},
                    'total': len(items), 'error': self.error}
        finally: conn.close()
