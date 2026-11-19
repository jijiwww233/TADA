"""Cheap local features plus a locally generated linear CLIP calibration profile.

No weights are shipped in Git. A missing/mismatched profile falls back safely to
zero-shot classification; local calibration coefficients stay in the data folder.
"""
import json
import os
from pathlib import Path
from . import config

PROMPT_GROUPS = (
    ('专业平面设计作品，海报设计，排版展板', '建筑设计展板，建筑分析图，设计作品',
     '绘画插画，艺术创作，图案纹样设计', '精心构图的摄影作品，风景人像艺术摄影',
     '信息可视化设计，数据图表，图文设计排版'),
    ('纯文字卡片，白底黑字笔记，没有插图', '聊天记录截图，手机软件界面截图',
     '文字截图，阅读文章，长篇文字说明', '备忘录，清单，文字段落，文档页面',
     '语录卡片，文字转图片，阅读笔记'),
    ('日常生活随手拍，食物记录，没有设计排版', '生活自拍，购物商品记录，普通生活照片',
     '生活用品，购物订单，日常聊天生活记录'),
)
FEATURE_NAMES = ('log_aspect','color_entropy','color_count','background_share','white_share',
                 'black_share','luminance_std','saturation_mean','saturation_std',
                 'edge_density','text_row_density_std','edge_column_std','low_saturation_share')


def cheap_features(path):
    import numpy as np
    from PIL import Image, ImageOps
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert('RGB')
        aspect = np.log(max(.05, min(20, image.width / image.height)))
        image.thumbnail((256, 256))
        rgb = np.asarray(image, dtype=float) / 255
    q = (rgb * 7).astype(int)
    hist = np.bincount((q[:,:,0]*64 + q[:,:,1]*8 + q[:,:,2]).ravel(), minlength=512) / q.shape[0] / q.shape[1]
    nz = hist[hist > 0]
    gray, saturation = rgb.mean(2), rgb.max(2)-rgb.min(2)
    gx = np.abs(np.diff(gray, axis=1, prepend=gray[:,:1]))
    gy = np.abs(np.diff(gray, axis=0, prepend=gray[:1]))
    edge = np.maximum(gx, gy) > .12
    # Row edge variation is a text-density proxy; this is not OCR.
    return [float(aspect), float(-(nz*np.log(nz)).sum()/np.log(512)), float((hist>.001).sum()/512),
            float(hist.max()), float((gray>.9).mean()), float((gray<.1).mean()), float(gray.std()),
            float(saturation.mean()), float(saturation.std()), float(edge.mean()),
            float(edge.mean(1).std()), float(edge.mean(0).std()), float((saturation<.06).mean())]


def prompt_scores(matrix, prompt_vectors):
    import numpy as np
    values = matrix @ np.asarray(prompt_vectors).T
    result, start = [], 0
    for group in PROMPT_GROUPS:
        result.append(np.sort(values[:, start:start+len(group)], axis=1)[:, -2:].mean(axis=1))
        start += len(group)
    return np.stack(result, axis=1)


def profile_path():
    return Path(os.getenv('WOMINMIN_REFERENCE_CALIBRATION', config.data_dir()/'reference-calibration.json'))


def load_profile(identity):
    try:
        value = json.loads(profile_path().read_text(encoding='utf-8'))
        if value.get('clip_identity') != identity or value.get('feature_names') != list(FEATURE_NAMES):
            return None
        return value
    except (OSError, ValueError, TypeError):
        return None


def decide(scores, features, image_vector, profile):
    import numpy as np
    visual, text, life = scores
    if profile:
        x = np.asarray([*scores, *features, *image_vector])
        mean, scale, coefficients = (np.asarray(profile[k]) for k in ('mean','scale','coefficients'))
        if x.shape != mean.shape or x.shape != coefficients.shape or np.any(scale<=0):
            return 'uncertain', '本地校准配置无效，等待确认'
        z = float(((x-mean)/scale) @ coefficients + profile['intercept'])
        probability = float(1/(1+np.exp(-np.clip(z,-40,40))))
        category = ('text_card' if probability >= profile['text_threshold'] else
                    'visual_ref' if probability <= profile['visual_threshold'] else 'uncertain')
        if category == 'visual_ref' and life-visual > profile['life_margin'] and life-text > profile['life_margin']:
            category = 'life'
        return category, f'本地CLIP与图像特征校准；文字分数{probability:.3f}（不是概率保证）'
    order = np.argsort(-np.asarray(scores))
    margin = float(scores[order[0]]-scores[order[1]])
    return (('visual_ref','text_card','life')[int(order[0])] if margin>=.035 else 'uncertain',
            '本地图片预判，未安装校准配置，建议人工确认')
