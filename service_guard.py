"""Evidence-based access control and required workflow completion.

Tenant-specific identifiers, rules and routing live in the private profile.
"""
import json
import re
from datetime import date, timedelta


class ServiceGuard:
    def __init__(self, config):
        self.config = config

    def capture(self, state, text):
        if 'principal' not in state:
            match = re.search(self.config['principal_pattern'], text)
            if match:
                state['principal'] = {'name': match.group(1).strip(), 'phone': match.group(2).strip()}
        state.setdefault('customer_texts', []).append(text)

    async def principal(self, agent, client, state):
        if state.get('member_id'):
            return state['member_id']
        principal = state.get('principal')
        if not principal:
            return None
        result = await agent.request(client, self.config['member_search'], {'name': principal['name']}, state)
        if result.get('http_status') != 200 or not isinstance(result.get('data'), list):
            return None
        pattern = re.sub(r'[^0-9*]', '', principal['phone'])
        matches = [m for m in result['data'] if m.get('name') == principal['name'] and
                   re.fullmatch(pattern.replace('*', '[0-9*]'), re.sub(r'[^0-9*]', '', m.get('phone', '')))]
        if len(matches) != 1:
            return None
        state['member_id'] = matches[0]['member_id']
        state['member'] = self.redact(matches[0])
        return state['member_id']

    def redact(self, value):
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, dict):
            return {k: self.redact(v) for k, v in value.items() if k not in self.config.get('private_fields', [])}
        return value

    async def execute(self, agent, client, name, args, state):
        cfg = self.config
        op = agent.operations[name]
        if name == cfg['escalate']:
            pending = self.requirements(state)
            for target, completed in state.get('completed_handoffs', {}).items():
                if target in args.get('reason', ''):
                    return completed
            candidates = [r for r in pending.values() if r['target'] in args.get('reason', '')]
            if not candidates and len(pending) == 1:
                candidates = list(pending.values())
            if len(candidates) == 1:
                rule = candidates[0]
                args = {'lane': rule['lane'], 'reason': self.handoff_reason(rule, state)}
            for rule in cfg.get('handoff_rules', []):
                if rule.get('defer_until_order') and rule['target'] in args.get('reason', '') and not state.get('orders'):
                    return {'error': 'Confirm the target order before filing this review, and include the order and unconfirmed facts in the reason.'}
        choice = cfg.get('customer_choices', {}).get(name)
        if choice:
            texts = state.get('customer_texts', [])
            options = cfg.get('remedy_options', {})
            if name in options.get('operations', []) and all(re.search(p, '\n'.join(texts)) for p in options['all_patterns']):
                offered = state.get('remedy_offer_turn')
                if offered is None or not any(re.search(choice, t) for t in texts[offered+1:]):
                    return {'error': 'Explain both available remedies first, then wait for the customer to select one. The initial complaint is not a selection after disclosure.'}
            decisions = [t for t in texts if re.search(choice, t) or any(
                re.search(p, t) for p in cfg.get('choice_rejections', {}).get(name, []))]
            latest = decisions[-1] if decisions else ''
            rejected = any(re.search(p, latest) for p in cfg.get('choice_rejections', {}).get(name, []))
            if not latest or rejected or (texts and re.search(cfg['hold_pattern'], texts[-1])):
                return {'error': 'Customer has not selected this action, or asked to hold it. Explain options; do not submit or claim submission.'}
        # All account queries are scoped to the supplied authenticated intake account.
        protected = any(k in args for k in ('member_id', 'order_no', 'approval_no', 'shipment_id', 'subscription_id')) or name == cfg['member_search']
        if protected:
            member = await self.principal(agent, client, state)
            if not member:
                return {'error': 'Account identity could not be verified from authenticated intake context. Ask for official account verification; do not guess identifiers or say there are no orders.'}
            if name == cfg['member_search']:
                if args.get('name') != state['principal']['name']:
                    return {'error': 'Other account lookup denied.'}
                return {'http_status': 200, 'data': [state['member']]}
            if 'member_id' in args and args['member_id'] != member:
                return {'error': 'Unverified member identifier. Use verified member_id.', 'verified_member_id': member}
            if 'order_no' in args:
                order = args['order_no']
                pattern = cfg.get('order_pattern')
                if pattern and order not in re.findall(pattern, '\n'.join(state.get('customer_texts', []))):
                    return {'error': 'Target order is not confirmed by customer. Ask for the specific order number; do not choose an order from history.'}
                if order in state.get('denied_orders', set()):
                    return {'error': 'ACCESS_DENIED: order is not owned by this account. Disclose no status, recipient or address, even partially.'}
                if order not in state.setdefault('orders', {}):
                    result = await agent.request(client, cfg['order_get'], {'order_no': order}, state)
                    if result.get('http_status') != 200:
                        return result
                    row = result.get('data') or {}
                    if row.get('member_id') != member:
                        state.setdefault('denied_orders', set()).add(order)
                        state.setdefault('required_handoffs', {})[cfg['security']['target']] = cfg['security']
                        return {'error': 'ACCESS_DENIED: order is not owned by this account. Disclose no status, recipient or address, even partially.'}
                    state['orders'][order] = self.redact(row)
                    self.remember_resources(row, state)
                if name == cfg['order_get']:
                    return {'http_status': 200, 'data': state['orders'][order]}
            for field in ('approval_no', 'shipment_id', 'subscription_id'):
                if field in args and args[field] not in state.get('owned_resources', {}).get(field, set()):
                    return {'error': 'Resource ownership not established. Query the verified own order first.', 'field': field}
        refund = cfg.get('refund_validation', {})
        if name == refund.get('operation'):
            order = state.get('orders', {}).get(args.get('order_no'), {})
            total = order.get('amount')
            amount = args.get('amount')
            if not isinstance(total, int) or not isinstance(amount, int) or not 0 < amount <= total:
                return {'error': 'Refund must be positive and within verified order payment total.', 'verified_total': total}
            context = '\n'.join(state.get('customer_texts', [])) + '\n' + args.get('reason', '')
            single_unit = sum(item.get('qty', 1) for item in order.get('items', [])) == 1
            full = single_unit or any(re.search(p, context) for p in refund.get('explicit_full_patterns', []))
            if full and any(re.search(p, context) for p in refund.get('full_patterns', [])) and amount != total:
                return {'error': 'Full refund must use verified order total, not item unit price. Correct the amount before submission.', 'required_amount': total}
        for requirement in cfg.get('prerequisites', {}).get(name, []):
            lookup_args = {field: args[field] for field in requirement['fields'] if field in args}
            if len(lookup_args) != len(requirement['fields']):
                return {'error': 'Missing target for required status lookup.'}
            key = requirement['operation'] + ':' + json.dumps(lookup_args, sort_keys=True)
            if state.get('evidence', {}).get(key, {}).get('http_status') != 200:
                lookup = await self.execute(agent, client, requirement['operation'], lookup_args, state)
                if lookup.get('http_status') != 200:
                    return {'error': 'Required status lookup failed; no action submitted.', 'lookup': lookup}
        result = await agent.request(client, name, args, state)
        if result.get('http_status') == 200:
            data = result.get('data')
            # Order collections are scoped and stripped before entering model context.
            if name == cfg.get('order_list'):
                data = [r for r in data if r.get('member_id') == state.get('member_id')]
                # History establishes purchase patterns, not the customer's target.
                # Dependent writes require an explicit order selection and lookup.
                state['order_history'] = self.redact(data)
            if protected and name != cfg.get('order_list'):
                self.remember_resources(data, state)
            result = {**result, 'data': self.redact(data)}
            if name == cfg['escalate']:
                for target in list(state.get('required_handoffs', {})):
                    if target in args.get('reason', ''):
                        state.setdefault('completed_handoffs', {})[target] = result
        state.setdefault('evidence', {})[name + ':' + json.dumps(args, sort_keys=True)] = result
        return result

    def remember_resources(self, data, state):
        if isinstance(data, list):
            for row in data:
                self.remember_resources(row, state)
        elif isinstance(data, dict):
            for field in ('approval_no', 'shipment_id', 'subscription_id'):
                if isinstance(data.get(field), str):
                    state.setdefault('owned_resources', {}).setdefault(field, set()).add(data[field])
            for approval in data.get('payment_refs', []):
                state.setdefault('owned_resources', {}).setdefault('approval_no', set()).add(approval)
            for value in data.values():
                if isinstance(value, (dict, list)):
                    self.remember_resources(value, state)

    def requirements(self, state):
        text = '\n'.join(state.get('customer_texts', []))
        pending = state.setdefault('required_handoffs', {})
        for rule in self.config.get('handoff_rules', []):
            if (not rule.get('defer_until_order') or state.get('orders')) and all(re.search(pattern, text, re.I) for pattern in rule['all_patterns']):
                pending[rule['target']] = rule
        # Confirmed repeated complaints + no investigation require a separate investigation.
        evidence = state.get('evidence', {})
        for key, result in evidence.items():
            if key.startswith(self.config.get('lot_tickets', '!') + ':') and result.get('http_status') == 200 and result.get('data'):
                args = json.loads(key.split(':', 1)[1])
                investigation = evidence.get(self.config['lot_investigation'] + ':' + json.dumps(args, sort_keys=True), {})
                if investigation.get('http_status') == 404:
                    rule = self.config['supply']
                    pending[rule['target']] = rule
        return {k: v for k, v in pending.items() if k not in state.get('completed_handoffs', {})}

    async def complete(self, agent, client, state):
        results = []
        for target, rule in self.requirements(state).items():
            reason = self.handoff_reason(rule, state)
            result = await self.execute(agent, client, self.config['escalate'], {'lane': rule['lane'], 'reason': reason[:3900]}, state)
            results.append({'target': target, 'result': result})
        return results

    def handoff_reason(self, rule, state):
        # Stable across wording changes/restarts; the API deduplicates exact bodies.
        return json.dumps({'case_id': state.get('case_id'), 'target': rule['target'],
            'priority': rule['priority'], 'reason': rule['reason'],
            'orders': sorted(state.get('orders', {})),
            'denied_orders': sorted(state.get('denied_orders', set())),
            'lots': sorted(state.get('verified_values', {}).get('lot_id', set()))},
            ensure_ascii=False, sort_keys=True)

    async def prepare(self, agent, client, state):
        results = []
        text = '\n'.join(state.get('customer_texts', []))
        for rule in self.config.get('required_reads', []):
            if not all(re.search(p, text) for p in rule['all_patterns']):
                continue
            args = {k: state.get(v[1:]) if v.startswith('$') else v for k, v in rule['arguments'].items()}
            if not all(args.values()):
                continue
            key = rule['operation'] + ':' + json.dumps(args, sort_keys=True)
            if key not in state.get('evidence', {}):
                result = await self.execute(agent, client, rule['operation'], args, state)
                results.append({'operation': rule['operation'], 'result': result, 'instruction': rule['instruction']})
        return results

    def guidance(self, state):
        text = '\n'.join(state.get('customer_texts', []))
        return '\n'.join(rule['instruction'] for rule in self.config.get('guidance', [])
                         if all(re.search(p, text) for p in rule['all_patterns']))

    def finalize(self, reply, state):
        context = '\n'.join(state.get('customer_texts', []))
        for rule in self.config.get('required_notices', []):
            if rule.get('require_order') and not state.get('orders'):
                continue
            if rule.get('require_handoff') and rule['require_handoff'] not in state.get('completed_handoffs', {}):
                continue
            if not all(re.search(p, context) for p in rule['all_patterns']):
                continue
            if any(re.search(p, context) for p in rule.get('exclude_patterns', [])):
                continue
            for pattern in rule.get('remove_unsupported', []):
                reply = re.sub(pattern, '', reply)
            if rule['text'] not in reply:
                reply = reply.rstrip() + '\n\n' + rule['text']
        options = self.config.get('remedy_options', {})
        if options and all(re.search(p, context) for p in options['all_patterns']) and all(label in reply for label in options['labels']):
            state['remedy_offer_turn'] = len(state.get('customer_texts', []))-1
        intake = re.search(r'상담\s*인입\s*일시:\s*(\d{4})-', context)
        if intake:
            def weekday(match):
                try:
                    day = date(int(intake.group(1)), int(match.group(1)), int(match.group(2)))
                    return f'{day.month}월 {day.day}일({"월화수목금토일"[day.weekday()]})'
                except ValueError:
                    return match.group(0)
            reply = re.sub(r'(\d{1,2})월\s*(\d{1,2})일\s*\([월화수목금토일]\)', weekday, reply)
            reply = re.sub(r'(?<![\d/])(\d{1,2})/(\d{1,2})\s*\([월화수목금토일](?:요일)?\)', weekday, reply)
        return reply.strip()

    def calendar_facts(self, state):
        context = '\n'.join(state.get('customer_texts', []))
        intake = re.search(r'상담\s*인입\s*일시:\s*(\d{4}-\d{2}-\d{2})', context)
        if not intake:
            return []
        today = date.fromisoformat(intake.group(1))
        facts = []
        shipping = self.config.get('dispatch_calendar')
        if shipping and all(re.search(p, context) for p in shipping.get('all_patterns', [])):
            next_day = today + timedelta(days=1)
            holidays = set(shipping.get('holidays', []))
            for _ in range(30):
                if next_day.weekday() in shipping['weekdays'] and str(next_day) not in holidays:
                    facts.append({'policy_next_dispatch_date': str(next_day),
                        'usual_arrival_from': str(next_day+timedelta(days=1)),
                        'usual_arrival_to': str(next_day+timedelta(days=2)),
                        'note': 'General calendar estimate only; individual dispatch and arrival are not guaranteed.'})
                    break
                next_day += timedelta(days=1)
        for result in state.get('evidence', {}).values():
            data = result.get('data')
            for row in data if isinstance(data, list) else [data]:
                if not isinstance(row, dict) or not row.get('delivered_at'):
                    continue
                received = date.fromisoformat(row['delivered_at'][:10])
                fact = {'order_no': row.get('order_no'), 'received_date': str(received),
                        'today': str(today), 'elapsed_days': (today-received).days}
                window = self.config.get('return_window_days')
                if window:
                    fact['return_window_days'] = window
                    fact['return_window_last_day'] = str(received+timedelta(days=window))
                if fact not in facts:
                    facts.append(fact)
        return facts


def split_text(text, limit=500):
    """Keep all content and whole URLs; return at most three Kakao text bubbles."""
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', r'\1: \2', text.strip())
    parts = []
    while len(text) > limit and len(parts) < 2:
        cuts = [m.end() for m in re.finditer(r'\s+', text[:limit])]
        cut = cuts[-1] if cuts else limit
        # Do not cut through a URL even if it follows text without whitespace.
        for match in re.finditer(r'https?://\S+', text):
            if match.start() < cut < match.end():
                cut = match.start() or min(match.end(), limit)
                break
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    if any(len(part) > limit for part in parts):
        return ['안내 내용이 길어 한 번에 전송하지 못했습니다. 필요한 항목을 나누어 요청해 주세요. 이미 접수된 요청은 다시 실행하지 않습니다.']
    return parts
