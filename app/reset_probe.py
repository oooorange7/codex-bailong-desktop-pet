"""Isolated public-feed acceptance for source and frozen builds."""
import json,time,traceback,ssl
from pathlib import Path
from reset_watch import ResetTracker,fetch_feed

def run(output,cache):
    result=dict(passed=False,started=time.time(),stage='tls')
    try:
        context=ssl.create_default_context()
        result.update(certificateRequired=context.verify_mode==ssl.CERT_REQUIRED,
            hostnameChecked=context.check_hostname,stage='fetch')
        feed=fetch_feed()
        result.update(fetched=True,eventCount=len(feed['events']),stage='cache')
        snapshot,alerts=ResetTracker(cache).update(feed)
        result.update(passed=True,snapshot=snapshot,stage='complete')
    except Exception as error:
        result.update(errorType=type(error).__name__,
            origin=[dict(file=Path(f.filename).name,function=f.name,line=f.lineno)
                    for f in traceback.extract_tb(error.__traceback__)[-8:]])
        if isinstance(error,OSError):
            result['osError']=error.errno
            if error.filename:result['missingFile']=Path(error.filename).name
        reason=getattr(error,'reason',None)
        if reason is not None:result['reasonType']=type(reason).__name__
    result['finished']=time.time()
    path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['passed'] else 1
