"""Kakao gateway AI worker. No credentials or chat contents are logged."""
import asyncio
import contextlib
import json
import os
import random
import time

from ai import Assistant, outputs

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

GATEWAY = 'wss://bot-gateway.kakao.com/gateway'
CALLBACK = 'https://kapi.kakao.com/v1/bot/callback'



class Worker:
    def __init__(self, token):
        self.token = token
        self.session = None
        self.seq = 0
        self.connected_at = None
        self.ai = Assistant()
        self.jobs = set()
        self.busy = set()

    def auth_frame(self):
        if self.session:
            return {'op': 6, 'd': {'token': self.token,
                    'session_id': self.session, 'seq': self.seq}}
        return {'op': 2, 'd': {'token': self.token}}

    async def heartbeat(self, ws, interval, state):
        while True:
            await asyncio.sleep(interval)
            if state['pending']:
                await ws.close(code=1000, reason='Heartbeat acknowledgement missing')
                return
            state['pending'] = True
            await ws.send(json.dumps({'op': 1}))

    async def reply(self, client, data, text):
        try:
            response = await client.post(CALLBACK, headers={
                'Authorization': f'KakaoAK {self.token}',
                'X-Bot-Callback-Token': data['callbackToken'],
            }, json={'skillResponse': {'version': '2.0', 'template': {
                'outputs': outputs(text)
            }}})
            print(f'Callback HTTP {response.status_code}', flush=True)
            if response.is_success:
                print(f"Callback errors: {response.json().get('errorCount', 'unknown')}", flush=True)
        except (httpx.HTTPError, ValueError) as exc:
            print(f'Callback failed: {type(exc).__name__}', flush=True)

    async def process(self, data, identity):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                try:
                    async with asyncio.timeout(260):
                        text = await self.ai.answer(client, data)
                except TimeoutError:
                    text = '처리 시간이 길어 요청을 종료했어요. 잠시 후 다시 요청해 주세요.'
                await self.reply(client, data, text)
        except Exception as exc:
            print(f'Message failed: {type(exc).__name__}', flush=True)
        finally:
            self.busy.discard(identity)

    async def dispatch(self, client, data):
        if not data.get('callbackToken'):
            return
        identity = (data.get('botGroupKey'), data.get('botUserKey'))
        if identity in self.busy or len(self.jobs) >= 8:
            await self.reply(client, data, '질문을 처리 중이에요. 답변이 온 후 다시 보내주세요.')
            return
        self.busy.add(identity)
        job = asyncio.create_task(self.process(data, identity))
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)

    async def connection(self):
        async with websockets.connect(GATEWAY, open_timeout=20) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if hello.get('op') != 10:
                raise RuntimeError('Expected HELLO')
            await ws.send(json.dumps(self.auth_frame()))
            ready = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if ready.get('op') != 3:
                raise RuntimeError('Expected READY')
            self.session = ready['d']['session_id']
            self.connected_at = time.monotonic()
            print('Gateway connected / 게이트웨이 연결 성공', flush=True)
            state = {'pending': False}
            pulse = asyncio.create_task(self.heartbeat(
                ws, hello['d']['heartbeat_interval'] / 1000 * 0.9, state))
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    async for raw in ws:
                        event = json.loads(raw)
                        if event.get('op') == 11:
                            state['pending'] = False
                        elif event.get('op') == 0:
                            if event.get('t') == 'MESSAGE_CREATE':
                                await self.dispatch(client, event['d'])
                            self.seq = event.get('s', self.seq)
            finally:
                pulse.cancel()
                with contextlib.suppress(asyncio.CancelledError, ConnectionClosed):
                    await pulse

    async def run(self):
        delay = 1
        while True:
            self.connected_at = None
            try:
                await self.connection()
            except ConnectionClosed as exc:
                code = exc.rcvd.code if exc.rcvd else None
                print(f'Gateway closed: {code}', flush=True)
                if code in (4001, 4003, 4004):
                    print('Stopped: check token/payload or another running instance.', flush=True)
                    # Remain idle to avoid a platform restart loop fighting another worker.
                    await asyncio.Event().wait()
                if code == 4009:
                    self.session, self.seq = None, 0
            except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as exc:
                print(f'Connection interrupted: {type(exc).__name__}', flush=True)
            if self.connected_at and time.monotonic() - self.connected_at > 60:
                delay = 1
            print(f'Reconnecting in about {delay}s', flush=True)
            await asyncio.sleep(delay + random.random())
            delay = min(delay * 2, 60)


if __name__ == '__main__':
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        raise SystemExit('Set BOT_TOKEN in the service environment variables.')
    try:
        asyncio.run(Worker(token).run())
    except KeyboardInterrupt:
        pass
