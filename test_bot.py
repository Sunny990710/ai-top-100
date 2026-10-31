import asyncio, os, json, unittest
from unittest.mock import patch
import httpx
from ai import Assistant, outputs
from bot import Worker

class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_context(self):
        requests=[]
        def handler(r):
            requests.append(json.loads(r.content))
            return httpx.Response(200,json={'output':[{'type':'message','content':[{'type':'output_text','text':'안녕'}]}]})
        with patch.dict(os.environ, {'OPENAI_API_KEY':'fake'}): ai=Assistant()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            d={'content':'hi','botGroupKey':'r','botUserKey':'u'}
            self.assertEqual(await ai.answer(client,d),'안녕')
            await ai.answer(client,d)
            self.assertEqual(len(requests[-1]['input']),3)
            await ai.answer(client,{**d,'botUserKey':'other'})
            self.assertEqual(len(requests[-1]['input']),1)
            await ai.answer(client,{**d,'content':'대화 초기화'})
            await ai.answer(client,d)
            self.assertEqual(len(requests[-1]['input']),1)
        self.assertFalse(requests[0]['store'])
        self.assertEqual([len(x['simpleText']['text']) for x in outputs('가'*2800)],[1000,1000,800])
    async def test_quota(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY':'fake'}): ai=Assistant()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(429,json={}))) as c:
            self.assertIn('잔액',await ai.answer(c,{'content':'hi'}))
        self.assertFalse(ai.history)
    async def test_nonblocking(self):
        w=Worker('fake'); gate=asyncio.Event()
        async def slow(c,d):
            await gate.wait()
            return 'done'
        async def reply(c,d,t): pass
        w.ai.answer=slow; w.reply=reply
        await asyncio.wait_for(w.dispatch(None,{'callbackToken':'fake','botGroupKey':'r','botUserKey':'u'}),0.5)
        self.assertEqual(len(w.jobs),1)
        gate.set()
        await asyncio.gather(*w.jobs)
        self.assertFalse(w.busy)
if __name__=='__main__': unittest.main()
