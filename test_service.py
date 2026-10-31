import asyncio
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

    async def test_independent_reads_parallel_writes_ordered(self):
        active = set()
        both_started = asyncio.Event()
        completed = []
        async def execute(client, name, args, state):
            record = args['record_id']
            if name == 'read_record':
                active.add(record)
                if len(active) == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), 1)
            else:
                self.assertEqual(set(completed), {'1', '2'})
            completed.append(record)
            return {'ok': record}
        self.agent.execute = execute
        calls = [{'name': name, 'call_id': str(i), 'arguments': json.dumps({'record_id': str(i)})}
                 for i, name in [(1, 'read_record'), (2, 'read_record'), (3, 'submit_request')]]
        messages = list(calls)
        await self.agent.run_calls(None, calls, messages, self.state)
        self.assertEqual(completed[-1], '3')
        self.assertEqual([x['call_id'] for x in messages[3:]], ['1', '2', '3'])

    async def test_interruption_keeps_all_results_and_success(self):
        async def execute(client, name, args, state):
            if args['record_id'] == '2':
                raise asyncio.CancelledError()
            return {'request_id': 'confirmed'}
        self.agent.execute = execute
        calls = [{'name': 'submit_request', 'call_id': str(i), 'arguments': json.dumps({'record_id': str(i)})}
                 for i in range(1, 4)]
        messages = list(calls)
        with self.assertRaises(asyncio.CancelledError):
            await self.agent.run_calls(None, calls, messages, self.state)
        self.assertEqual(len(messages), 6)
        self.assertIn('confirmed', messages[3]['output'])
        self.assertIn('unconfirmed', messages[4]['output'])
        self.assertIn('Not executed', messages[5]['output'])

    async def test_identifier_requires_successful_evidence(self):
        self.agent.profile['verified_argument_fields'] = ['record_id']
        seen = []
        def handler(req):
            seen.append(req)
            return httpx.Response(200, json={'ok': True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await self.agent.execute(client, 'read_record', {'record_id': 'invented'}, self.state)
            self.assertIn('Unverified', result['error'])
            self.assertEqual(seen, [])
            self.agent.remember_verified({'items': [{'record_id': 'real'}]}, self.state)
            result = await self.agent.execute(client, 'read_record', {'record_id': 'real'}, self.state)
            self.assertEqual(result['http_status'], 200)
            self.assertEqual(len(seen), 1)

    async def test_final_review_has_receipt_evidence_and_no_action_tools(self):
        self.state['writes']={'request':{'http_status':200,'data':{'request_id':'R-verified','request_status':'SUBMITTED'}}}
        def handler(req):
            self.assertEqual(req.url.host,'api.openai.com')
            body=json.loads(req.content)
            self.assertNotIn('tools',body)
            evidence=json.loads(body['input'])
            self.assertEqual(evidence['confirmed_requests'][0]['data']['request_id'],'R-verified')
            return httpx.Response(200,json={'output':[{'type':'message','content':[{'type':'output_text','text':'접수번호 R-verified. 아직 처리 완료는 아닙니다.'}]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            answer=await self.agent.review_answer(client,'이미 처리가 완료됐습니다.',self.state)
        self.assertEqual(answer,'접수번호 R-verified. 아직 처리 완료는 아닙니다.')

    async def test_idempotent_write_retries_identical_body_after_network_loss(self):
        self.agent.profile['idempotent_requests']=True
        seen=[]
        def handler(req):
            seen.append(req.content)
            if len(seen)==1: raise httpx.ReadError('response lost',request=req)
            return httpx.Response(200,json={'request_id':'R1'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await self.agent.execute(client,'submit_request',{'record_id':'1'},self.state)
        self.assertEqual(result['http_status'],200)
        self.assertEqual(seen,[b'{}',b'{}'])
        self.assertEqual(len(self.state['writes']),1)

    async def test_non_idempotent_write_not_retried(self):
        seen=[]
        def handler(req):
            seen.append(req);raise httpx.ReadError('lost',request=req)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await self.agent.execute(client,'submit_request',{'record_id':'1'},self.state)
        self.assertIn('unconfirmed',result['error']);self.assertEqual(len(seen),1)

    async def test_long_rate_limit_is_not_retried(self):
        seen=[]
        def handler(req):
            seen.append(req);return httpx.Response(429,headers={'Retry-After':'60'},json={'code':'RATE_LIMIT'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await self.agent.execute(client,'read_record',{'record_id':'1'},self.state)
        self.assertEqual(result['retry_after_seconds'],60);self.assertEqual(len(seen),1)

    async def test_review_failure_keeps_confirmed_receipt(self):
        self.state['writes']={'x':{'http_status':200,'data':{'request_id':'R-confirmed'}}}
        def handler(req):return httpx.Response(503,json={'error':'temporary'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            answer=await self.agent.answer(client,{'botGroupKey':'a','botUserKey':'b','content':'follow up'})
        self.assertIn('R-confirmed',answer);self.assertIn('접수는 확인',answer)

    async def test_audio_transcript_survives_final_review(self):
        from service_guard import ServiceGuard
        self.agent.guard=ServiceGuard({'principal_pattern':'never matches','handoff_rules':[]})
        async def user_content(client,text):
            return [{'type':'input_text','text':'[고객 음성 전사, 불확실한 내용은 질문] 지난주 받은 수박 환불할 수 있나요?'}]
        self.agent.user_content=user_content
        def handler(req):
            return httpx.Response(200,json={'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'주문번호와 환불 사유를 알려주세요.'}]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            answer=await self.agent.answer(client,{'botGroupKey':'a','botUserKey':'b','content':'TC: sample'})
        self.assertIn('음성 문의는 “지난주 받은 수박 환불할 수 있나요?”로 들었습니다.',answer)
        self.assertIn('주문번호와 환불 사유',answer)
