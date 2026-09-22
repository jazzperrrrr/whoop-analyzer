"""In-memory ASGI HTTP transport, with no socket or additional test dependency."""

import asyncio
import json
from urllib.parse import urlsplit


async def request(app,path,method='GET',headers=()):
    parsed=urlsplit(path)
    scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1',
           'method':method,'scheme':'http','path':parsed.path,'raw_path':parsed.path.encode(),
           'query_string':parsed.query.encode(),'root_path':'','headers':[(b'host',b'127.0.0.1:8000'),*headers],
           'client':('127.0.0.1',12345),'server':('127.0.0.1',8000)}
    messages=[]
    done=asyncio.Event()
    sent=False
    async def receive():
        nonlocal sent
        if not sent:
            sent=True
            return {'type':'http.request','body':b'','more_body':False}
        await done.wait()
        return {'type':'http.disconnect'}
    async def send(message):
        messages.append(message)
        if message['type']=='http.response.body' and not message.get('more_body',False):
            done.set()
    await app(scope,receive,send)
    start=next(m for m in messages if m['type']=='http.response.start')
    body=b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body')
    return start['status'],json.loads(body),dict(start['headers'])
