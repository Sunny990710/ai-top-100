import json, os, unittest
from unittest.mock import patch
import httpx
from ai import Assistant
from web_results import render_response, split_message

class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_and_citations(self):
        raw='환율 기준 정보입니다. [source]'
        url='https://example.org/exchange'
        def handler(request):
            body=json.loads(request.content)
            self.assertIn({'type':'web_search'},body['tools'])
            self.assertEqual(body['tool_choice'],{'type':'web_search'})
            self.assertEqual(body['reasoning']['effort'],'low')
            return httpx.Response(200,json={'output':[
                {'type':'web_search_call','status':'completed'},
                {'type':'message','content':[{'type':'output_text','text':raw,'annotations':[
                    {'type':'url_citation','start_index':raw.index('[source]'),'end_index':len(raw),'url':url}]}]}]})
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake'}): ai=Assistant()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await ai.answer(client,{'content':'검색: 오늘 환율'})
        self.assertIn(url,result)
        self.assertNotIn('[source]',result)
    def test_url_not_split(self):
        url='https://example.org/'+'x'*150
        chunks=split_message('가'*950+'\n출처: '+url)
        self.assertTrue(any(url in c for c in chunks))
        self.assertTrue(all(len(c)<=1000 for c in chunks))
    def test_long_response_fails_without_partial_uncited_answer(self):
        self.assertIn('한 번에 표시하지 못했어요',split_message('가'*3100)[0])
if __name__=='__main__': unittest.main()
