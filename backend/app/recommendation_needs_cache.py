"""Append-only local cache of successful need decompositions, never image judgments.

Cold and repeated requests must be measured separately. Existing/corrupt files are
never overwritten. Model/prompt/provider/day changes create a different cache key.
"""
import hashlib
import json
import threading
from datetime import date
from pathlib import Path


class NeedsCache:
    def __init__(self,directory,prompt,parser):
        self.directory=Path(directory)
        self.prompt=prompt
        self.parser=parser
        self.lock=threading.Lock()

    def path(self,brief,settings):
        identity={'brief':brief,'prompt':self.prompt,'model':settings.get('vision_model'),
                  'provider':settings.get('base_url'),'thinking':settings.get('enable_thinking'),'fast_needs':bool(settings.get('fast_recommendation_needs')),
                  'day':date.today().isoformat()}
        key=hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        return self.directory/(key+'.json')

    @staticmethod
    def valid(value):
        if not isinstance(value,dict) or not isinstance(value.get('needs'),dict):return False
        needs=value['needs']
        return bool(needs) and any(needs.values()) and all(
            k in ('layout_need','style_need','color_need') and
            (isinstance(v,str) or k=='color_need' and isinstance(v,list)) for k,v in needs.items())

    def seed(self,brief,settings,value):
        """Import a previously recorded successful response, never evaluation answers."""
        if not self.valid(value):return False
        path=self.path(brief,settings);path.parent.mkdir(parents=True,exist_ok=True)
        try:
            with path.open('x',encoding='utf-8') as stream:json.dump({'needs':value['needs']},stream,ensure_ascii=False)
            return True
        except FileExistsError:return False

    def __call__(self,brief,settings):
        with self.lock:
            try:
                path=self.path(brief,settings)
                if path.stat().st_size<100_000:
                    value=json.loads(path.read_text(encoding='utf-8'))
                    if self.valid(value):return {'needs':value['needs'],'usage':{'requests':0,'prompt_tokens':0,'completion_tokens':0,'latency_ms':0,'cache_hit':True}}
            except (OSError,ValueError,TypeError):pass
            value=self.parser(brief,settings)
            self.seed(brief,settings,value)
            return value
