import json
import unittest
import httpx
from service_agent import ServiceAgent, validate


def profile():
    schema={'type':'object','properties':{'record_id':{'type':'string'}},'required':['record_id'],'additionalProperties':False}
    return {'api_base':'https://service.example','case_header':'X-Case-ID', 'case_pattern':r'^TC:\s*([A-Za-z0-9._-]+)',
      'instructions':'Answer briefly.','documents':[],
      'operations':[{'name':'read_record','description':'Read','method':'GET','path':'/records/{record_id}','parameters':schema},
                    {'name':'submit_request','description':'Submit','method':'POST','path':'/records/{record_id}/requests','parameters':schema}]}

class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent=ServiceAgent(profile());self.agent.key='test';self.agent.token='test'
        self.state=self.agent.state_for(('a','b'),'TC: sample')

    async def test_missing_case_and_injection_block_network(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:self.fail('network called'))) as client:
            for args in ({'record_id':'../secrets'},{'record_id':'1','extra':'x'}):
                self.assertIn('error',await self.agent.execute(client,'read_record',args,self.state))
            self.state['case_id']=None
            self.assertIn('error',await self.agent.execute(client,'read_record',{'record_id':'1'},self.state))

    async def test_write_dedup_and_header(self):
        seen=[]
        def handler(req):
            seen.append(req);return httpx.Response(200,json={'request_id':'R1'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first=await self.agent.execute(client,'submit_request',{'record_id':'1'},self.state)
            second=await self.agent.execute(client,'submit_request',{'record_id':'1'},self.state)
        self.assertEqual(first,second);self.assertEqual(len(seen),1)
        self.assertEqual(seen[0].headers['X-Case-ID'],'sample')

    def test_isolation_and_case_switch(self):
        self.state['messages']=[{'role':'user','content':'private'}]
        self.assertEqual(self.agent.state_for(('a','b'),'follow up')['messages'],self.state['messages'])
        self.assertEqual(self.agent.state_for(('a','b'),'TC: next')['messages'],[])
        self.assertEqual(self.agent.state_for(('x','y'),'hello')['messages'],[])

    async def test_tool_loop_retains_evidence(self):
        requests=[]
        def handler(req):
            requests.append(req)
            if req.url.host=='service.example':return httpx.Response(200,json={'status':'ready'})
            body=json.loads(req.content)
            if body['input'][-1].get('type')=='function_call_output':
                self.assertIn('ready',body['input'][-1]['output'])
                return httpx.Response(200,json={'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Ready.'}]}]})
            return httpx.Response(200,json={'output':[{'type':'function_call','name':'read_record','call_id':'call1','arguments':'{"record_id":"1"}'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await self.agent.answer(client,{'botGroupKey':'a','botUserKey':'b','content':'TC: sample'})
        self.assertEqual(result,'Ready.');self.assertEqual(len(requests),3)
        self.assertTrue(any(x.get('type')=='function_call_output' for x in self.state['messages']))

    def test_integer_validation(self):
        self.assertFalse(validate(True,{'type':'integer'}));self.assertFalse(validate(-1,{'type':'integer'}))
