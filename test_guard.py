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
    def test_notice_once_when_already_explained_or_appended(self):
        self.guard.config['required_notices'] = [{'all_patterns':['issue'], 'text':'Policy.', 'satisfied_patterns':['refund', 'replacement'], 'once_key':'remedies'}]
        for initial in ['Choose refund or replacement.', 'Checking.']:
            state={'customer_texts':['issue']}
            first=self.guard.finalize(initial,state)
            self.assertEqual(first.count('Policy.'), 0 if 'refund' in initial else 1)
            self.assertEqual(self.guard.finalize('Request received.',state),'Request received.')
        self.assertIn('Policy.',self.guard.finalize('Checking.',{'customer_texts':['issue']}))

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
    async def test_history_not_used_to_guess_target_order(self):
        self.agent.operations={**self.agent.operations,'orders':{}}
        self.guard.config['history_purpose_pattern']='purchase history|bulk'
        self.state['customer_texts']=['received spoiled product']
        result=await self.execute('orders',{'member_id':'M1'})
        self.assertIn('target order number',result['error'])
        self.assertEqual(self.agent.calls,[])
    async def test_required_history_still_checks_account_identity(self):
        self.agent.operations={**self.agent.operations,'orders':{}}
        self.guard.config['history_purpose_pattern']='purchase history'
        self.guard.config['required_reads']=[{'operation':'orders','all_patterns':['50 units','again']}]
        self.state['customer_texts']=['50 units again']
        result=await self.execute('orders',{'member_id':'other'})
        self.assertIn('Unverified member',result['error'])
        self.assertEqual(len(self.agent.calls),1)
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
    def test_notice_preserves_missing_conditions_without_repeating_complete_notice(self):
        self.guard.config['required_notices']=[{'all_patterns':['claim'],
            'text':'Keep document A and document B.', 'satisfied_patterns':['document A','document B']}]
        state={'customer_texts':['claim']}
        complete='Preserve document A, plus document B.'
        self.assertEqual(self.guard.finalize(complete,state),complete)
        partial=self.guard.finalize('Keep document A.',state)
        self.assertIn('Keep document A and document B.',partial)
    def test_notice_corrections_run_even_if_required_topics_already_present(self):
        self.guard.config['required_notices']=[{'all_patterns':['claim'],
            'text':'Keep evidence.', 'satisfied_patterns':['evidence'],
            'remove_unsupported':['Guaranteed compensation!']}]
        result=self.guard.finalize('Keep evidence. Guaranteed compensation!',{'customer_texts':['claim']})
        self.assertNotIn('Guaranteed',result)
        self.assertEqual(result.count('evidence'),1)
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
        self.assertIn('Resource ownership',r['error'])
        self.assertNotIn('reship',[n for n,a in self.agent.calls])
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

    async def test_changed_choice_blocks_old_selection(self):
        self.guard.config['customer_choices']={'reship':'reship'}
        self.guard.config['choice_rejections']={'reship':['refund instead']}
        self.guard.config['hold_pattern']='hold'
        self.state['customer_texts']=['reship please','refund instead']
        self.assertIn('error',await self.execute('reship',{'shipment_id':'S1'}))
        self.assertEqual(self.agent.calls,[])

    def test_handoff_body_stable_across_restarts_and_wording(self):
        rule=self.guard.config['security']
        first={'case_id':'sample','denied_orders':{'O1'},'customer_texts':['first wording']}
        second={'case_id':'sample','denied_orders':{'O1'},'customer_texts':['different wording']}
        self.assertEqual(self.guard.handoff_reason(rule,first),self.guard.handoff_reason(rule,second))

    async def test_order_history_does_not_authorize_unselected_shipment(self):
        self.agent.operations['orders']={}
        original=self.agent.request
        async def request(client,name,args,state):
            if name=='orders':return {'http_status':200,'data':[{'member_id':'M1','order_no':'own','shipment_id':'S1','payment_refs':['P1']}]}
            return await original(client,name,args,state)
        self.agent.request=request
        await self.execute('orders',{'member_id':'M1'})
        self.assertNotIn('orders',self.state)
        self.assertIn('error',await self.execute('reship',{'shipment_id':'S1'}))

    async def test_required_lookup_runs_after_ownership_before_write(self):
        self.agent.operations['update']={}
        original=self.agent.request
        async def request(client,name,args,state):
            if name=='update':
                self.agent.calls.append((name,args))
                return {'http_status':200,'data':{'request_id':'R1'}}
            return await original(client,name,args,state)
        self.agent.request=request
        self.guard.config['prerequisites']={'update':[{'operation':'shipments','fields':['order_no']}]}
        result=await self.execute('update',{'order_no':'own'})
        self.assertEqual(result['http_status'],200)
        self.assertEqual([n for n,a in self.agent.calls],['members','order','shipments','update'])

    async def test_full_refund_uses_paid_total_not_unit_price(self):
        self.agent.operations['refund']={}
        self.guard.config['refund_validation']={'operation':'refund','full_patterns':['full refund']}
        self.state['orders']={'own':{'amount':35000,'items':[{'unit_price':28000}]}}
        self.state['customer_texts'].append('full refund please')
        self.state['member_id']='M1'
        r=await self.execute('refund',{'order_no':'own','amount':28000})
        self.assertEqual(r['required_amount'],35000)
        self.assertEqual(self.agent.calls,[])
        original=self.agent.request
        async def request(client,name,args,state):
            if name=='refund':return {'http_status':200,'data':{'refund_id':'RF1'}}
            return await original(client,name,args,state)
        self.agent.request=request
        self.assertEqual((await self.execute('refund',{'order_no':'own','amount':35000}))['http_status'],200)

    def test_slash_date_weekday_uses_intake_year(self):
        self.state['customer_texts'].append('상담 인입 일시: 2037-06-30')
        self.assertEqual(self.guard.finalize('회신 7/3(목요일)',self.state),'회신 7월 3일(금)')

    def test_dispatch_calendar_skips_holiday(self):
        self.guard.config['dispatch_calendar']={'weekdays':[0,2,4],'holidays':['2037-07-01']}
        self.state['customer_texts'].append('상담 인입 일시: 2037-06-30')
        facts=self.guard.calendar_facts(self.state)
        self.assertEqual(facts[0]['policy_next_dispatch_date'],'2037-07-03')
        self.assertEqual(facts[0]['usual_arrival_from'],'2037-07-04')

    async def test_remedy_selection_must_follow_options(self):
        self.guard.config['customer_choices']={'reship':'reship'}
        self.guard.config['hold_pattern']='hold'
        self.guard.config['remedy_options']={'all_patterns':['quality'],'operations':['reship'],'labels':['refund','reship']}
        self.state['customer_texts']=['quality complaint: reship please']
        self.assertIn('both available', (await self.execute('reship',{'shipment_id':'S1'}))['error'])
        self.guard.finalize('Choose refund or reship.',self.state)
        self.guard.capture(self.state,'order details only')
        self.assertIn('both available', (await self.execute('reship',{'shipment_id':'S1'}))['error'])
        await self.execute('shipments',{'order_no':'own'})
        self.guard.capture(self.state,'reship please')
        self.assertEqual((await self.execute('reship',{'shipment_id':'S1'}))['http_status'],200)

    def test_submission_notice_requires_confirmed_handoff(self):
        self.guard.config['required_notices']=[{'all_patterns':['delivery'], 'require_handoff':'LOGISTICS','text':'Schedule review submitted.'}]
        self.state['customer_texts'].append('delivery complaint')
        self.assertNotIn('submitted',self.guard.finalize('Checking.',self.state))
        self.state['completed_handoffs']={'LOGISTICS':{'http_status':200}}
        self.assertIn('Schedule review submitted.',self.guard.finalize('Checking.',self.state))

    def test_price_comparison_uses_verified_order_and_matching_product(self):
        self.guard.config['price_comparison']={'all_patterns':['price drop'],'product_lookup':'product'}
        self.state['customer_texts'].append('price drop compensation')
        self.state['orders']={'own':{'amount':32000,'items':[{'sku':'A','qty':1}]}}
        self.state['evidence']={'product:{"sku": "B"}':{'data':{'price':1}}}
        self.assertNotIn('차액',self.guard.finalize('Submitted.',self.state))
        self.state['evidence']['product:{"sku": "A"}']={'http_status':200,'data':{'price':27900}}
        result=self.guard.finalize('Submitted.',self.state)
        for value in ['32,000','27,900','4,100']:self.assertIn(value,result)

    def test_coupon_notice_requires_successful_request(self):
        self.guard.config['price_comparison']={'all_patterns':['price drop'],'product_lookup':'product','coupon_operation':'coupon','coupon_amounts':{'C5000':5000},'coupon_valid_days':90}
        self.state['customer_texts'].append('price drop')
        self.state['writes']={'["coupon", {"promo_code":"C5000"}]':{'http_status':400}}
        self.assertNotIn('90일',self.guard.finalize('Checking.',self.state))
        self.state['writes']['["coupon", {"promo_code":"C5000"}]']={'http_status':200}
        result=self.guard.finalize('Submitted.',self.state)
        self.assertIn('5,000원',result);self.assertIn('발급일부터 90일',result)

    async def test_customer_number_preserves_original_format(self):
        self.guard.config['customer_value_fields']={'reship':{'receipt_number':r'(?<![0-9])0[0-9]{2}-?[0-9]{4}-?[0-9]{4}(?![0-9])'}}
        self.state['customer_texts'].append('Use 010-1234-5678 for receipt.')
        r=await self.execute('reship',{'receipt_number':'01012345678'})
        self.assertEqual(r['http_status'],200)
        self.assertEqual(self.agent.calls[-1][1]['receipt_number'],'010-1234-5678')
        self.assertIn('error',await self.execute('reship',{'receipt_number':'01099999999'}))

    def test_return_notice_uses_delivery_date_not_payment_date(self):
        self.guard.config['return_notice']={'all_patterns':['change of mind']}
        self.guard.config['return_window_days']=7
        self.state['customer_texts'].append('상담 인입 일시: 2037-06-30 change of mind')
        self.state['orders']={'own':{'ordered_at':'2037-06-23'}}
        self.state['evidence']={'shipping':{'http_status':200,'data':[{'order_no':'own','status':'DELIVERED','delivered_at':'2037-06-26T14:00:00+09:00'}]}}
        answer=self.guard.finalize('No request submitted.',self.state)
        for text in ['2037-06-26','4일','7일','미개봉','왕복']:self.assertIn(text,answer)

    def test_public_policy_does_not_need_account_lookup(self):
        self.guard.config['order_pattern']=r'(?<![0-9])[0-9]{9}(?![0-9])'
        self.assertFalse(self.guard.needs_principal(self.state))
        self.state['customer_texts'].append('order 123456789')
        self.assertTrue(self.guard.needs_principal(self.state))

    def test_bulk_history_requires_account_even_without_order(self):
        self.guard.config['required_reads']=[{'all_patterns':['bulk'],'arguments':{'member_id':'$member_id'}}]
        self.state['customer_texts'].append('bulk history')
        self.assertTrue(self.guard.needs_principal(self.state))

    def test_promo_notice_requires_verified_reads(self):
        key='promo:{"code": "C1"}'
        self.guard.config['required_notices']=[{'all_patterns':['coupon'],'require_evidence':[key],'text':'Verified benefit.'}]
        self.state['customer_texts'].append('coupon')
        self.assertNotIn('Verified benefit',self.guard.finalize('Checking.',self.state))
        self.state['evidence']={key:{'http_status':200,'data':{'code':'C1'}}}
        self.assertIn('Verified benefit',self.guard.finalize('Checking.',self.state))
