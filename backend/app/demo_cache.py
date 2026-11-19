"""Explicit demo mode only. Never silently relabel generated/cached results as live."""
import json
import os
from copy import deepcopy
from pathlib import Path

from . import config


def enabled():
    return os.getenv('WOMINMIN_DEMO', '').lower() in ('1', 'true')


def offline():
    return enabled() and os.getenv('WOMINMIN_OFFLINE_DEMO', '').lower() in ('1', 'true')


def lookup(brief, previous=None, include_nonvisual=False):
    if not enabled() or previous or include_nonvisual:
        return None
    path = Path(os.getenv('WOMINMIN_DEMO_CACHE', config.PROJECT_ROOT / 'eval/demo-recommendations.json'))
    try:
        result = deepcopy(json.loads(path.read_text(encoding='utf-8'))['responses'][brief.strip()])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    result['offline_demo'] = True
    result['notices'] = ['离线演示：显示已保存的推荐结果，本次未调用推荐模型。', *result.get('notices', [])]
    # Recorded token usage belongs to cache creation, not this request.
    result['cached_usage'] = result.pop('usage', None)
    result['usage'] = {'requests': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'latency_ms': 0, 'complete': True}
    return result
