import unittest
from service_guard import ServiceGuard, split_text

class FakeAgent:
    operations={n:{} for n in ['members','order','shipments','reship','handoff']}
    def __init__(self): self.calls=[]
    async def request(self,client,name,args,state):
        self.calls.append((name,args))
        data={'members':[{'member_id':'M1','name':'Alice','phone':'01099991234'}],
              'order':{'member_id':'M1' if args.get('order_no')=='own' else 'M2','shipping_address':'SECRET','recipient_name':'OTHER'},
              'shipments':[{'shipment_id':'S1'}], 'reship':{'request_id':'R1'},'handoff':{'escalation_id':'E1'}}[name]
        return {'http_status':200,'data':data}

class GuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent=FakeAgent();self.state={}
        self.guard=ServiceGuard(dict(principal_pattern=r'Account: ([^|]+)\|([0-9*]+)',member_search='members',order_get='order',order_list='orders',escalate='handoff',private_fields=['shipping_address','recipient_name','phone'],security=dict(target='SECURITY',lane='REVIEW',priority='urgent',reason='ownership mismatch'),handoff_rules=[]))
        self.guard.capture(self.state,'Account: Alice|010****1234')
    async def execute(self,name,args): return await self.guard.execute(self.agent,None,name,args,self.state)
    async def test_other_order_no_leak_or_dependent_query_and_one_handoff(self):
        r=await self.execute('order',{'order_no':'foreign'})
        self.assertNotIn('SECRET',str(r));self.assertIn('ACCESS_DENIED',r['error'])
        r=await self.execute('shipments',{'order_no':'foreign'})
        self.assertIn('ACCESS_DENIED',r['error']);self.assertNotIn('shipments',[n for n,a in self.agent.calls])
        await self.guard.complete(self.agent,None,self.state);await self.guard.complete(self.agent,None,self.state)
        self.assertEqual([n for n,a in self.agent.calls].count('handoff'),1)
    async def test_guessed_member_blocked(self):
        r=await self.execute('order',{'member_id':'invented','order_no':'own'})
        self.assertEqual(r['verified_member_id'],'M1');self.assertEqual(len(self.agent.calls),1)
    async def test_resource_ownership_before_write(self):
        self.assertIn('error',await self.execute('reship',{'shipment_id':'S1'}))
        await self.execute('shipments',{'order_no':'own'})
        self.assertEqual((await self.execute('reship',{'shipment_id':'S1'}))['http_status'],200)
    async def test_missing_identity_blocks_access(self):
        self.state.pop('principal')
        self.assertIn('error',await self.execute('order',{'order_no':'own'}));self.assertEqual(self.agent.calls,[])
    async def test_wrong_phone_blocks_access(self):
        self.state['principal']['phone']='010****0000'
        self.assertIn('error',await self.execute('order',{'order_no':'own'}));self.assertEqual(len(self.agent.calls),1)
    def test_followup_cannot_replace_principal(self):
        self.guard.capture(self.state,'Account: Bob|010****4321')
        self.assertEqual(self.state['principal']['name'],'Alice')
    def test_bubbles_preserve_url_and_receipt(self):
        url='https://example.com/'+'a'*80
        parts=split_text('안내 사항입니다. '*55+'\n'+url+'\n접수번호 E1 기한 금요일')
        self.assertTrue(all(len(p)<=500 for p in parts));self.assertTrue(any(url in p for p in parts));self.assertIn('기한 금요일',parts[-1])
    async def test_same_handoff_with_different_wording_is_not_repeated(self):
        await self.execute('order',{'order_no':'foreign'})
        await self.guard.complete(self.agent,None,self.state)
        await self.execute('handoff',{'lane':'REVIEW','reason':'SECURITY: rewritten reason'})
        self.assertEqual([n for n,a in self.agent.calls].count('handoff'),1)
    async def test_customer_hold_blocks_write_even_after_previous_choice(self):
        self.guard.config['customer_choices']={'reship':'reship'}
        self.guard.config['hold_pattern']='hold'
        self.state['customer_texts']=['reship please','hold for now']
        r=await self.execute('reship',{'shipment_id':'S1'})
        self.assertIn('Customer',r['error']); self.assertEqual(self.agent.calls,[])
    async def test_no_choice_blocks_financial_action(self):
        self.guard.config['customer_choices']={'reship':'reship'}
        self.guard.config['hold_pattern']='hold'
        r=await self.execute('reship',{'shipment_id':'S1'})
        self.assertIn('Customer',r['error']); self.assertEqual(self.agent.calls,[])
    async def test_missing_status_lookup_blocks_action(self):
        self.guard.config['prerequisites']={'reship':[{'operation':'shipments','fields':['order_no']}]}
        r=await self.execute('reship',{'order_no':'own','shipment_id':'S1'})
        self.assertIn('Required status',r['error']);self.assertEqual(self.agent.calls,[])
    async def test_order_identifier_next_to_korean_particle(self):
        self.guard.config['order_pattern']=r'(?<![0-9])[0-9]{9}(?![0-9])'
        self.state['customer_texts'].append('주문번호 123456789이 대상 맞아요')
        r=await self.execute('order',{'order_no':'123456789'})
        self.assertIn('ACCESS_DENIED',r['error'])
        self.assertTrue(any(n=='order' for n,a in self.agent.calls))
    def test_required_condition_survives_final_rewrite(self):
        self.guard.config['required_notices']=[{'all_patterns':['medical'], 'remove_unsupported':['instant refund'], 'text':'Refund requires verified eligibility.'}]
        self.state['customer_texts'].append('medical claim')
        answer=self.guard.finalize('Receipt E1. instant refund',self.state)
        self.assertIn('Receipt E1',answer);self.assertNotIn('instant refund',answer)
        self.assertIn('Refund requires verified eligibility.',answer)
    def test_weekday_uses_intake_year(self):
        self.state['customer_texts'].append('상담 인입 일시: 2037-06-30')
        self.assertEqual(self.guard.finalize('회신 7월 3일(수)',self.state),'회신 7월 3일(금)')
    def test_delivery_window_calculation(self):
        self.guard.config['return_window_days']=7
        self.state['customer_texts'].append('상담 인입 일시: 2037-06-30')
        self.state['evidence']={'shipment':{'data':[{'order_no':'own','delivered_at':'2037-06-26T14:00:00+09:00'}]}}
        fact=self.guard.calendar_facts(self.state)[0]
        self.assertEqual(fact['elapsed_days'],4)
        self.assertEqual(fact['return_window_last_day'],'2037-07-03')
