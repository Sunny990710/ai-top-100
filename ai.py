"""Bounded, per-user conversation memory and OpenAI Responses integration."""
import os
from collections import OrderedDict

import httpx
from cards import help_card, examples_card, reset_card


class Assistant:
    def __init__(self):
        self.history = OrderedDict()
        self.key = os.environ.get('OPENAI_API_KEY', '').strip()
        self.model = os.environ.get('OPENAI_MODEL', 'gpt-5-mini')

    async def answer(self, client, data):
        text = data.get('content', '').strip()
        identity = (data.get('botGroupKey'), data.get('botUserKey'))
        # Missing identifiers must never share conversation history.
        history = self.history.get(identity, []) if all(identity) else []
        if text in ('도움말', '/help', '메뉴'):
            return help_card()
        if text in ('사용 예시', '/examples'):
            return examples_card()
        if text in ('/reset', '대화 초기화'):
            self.history.pop(identity, None)
            return reset_card()
        if not self.key:
            return 'AI 연결 설정이 필요합니다. 운영자는 OPENAI_API_KEY를 확인해 주세요.'
        if not text or len(text) > 6000:
            return '질문을 6,000자 이내의 텍스트로 보내주세요.'
        inputs = history + [{'role': 'user', 'content': text}]
        try:
            response = await client.post('https://api.openai.com/v1/responses',
                headers={'Authorization': f'Bearer {self.key}'},
                json={'model': self.model, 'input': inputs, 'store': False,
                      'instructions': os.environ.get('SYSTEM_PROMPT',
                          '너는 그리니라는 한국어 AI 도우미다. 질문에 정확하고 간결하게 답하라. '
                          '답변은 800자 이내로 작성하라. 모르면 모른다고 말하라. '
                          '외부 검색이나 업무 실행 도구가 없으므로 검색·예약·저장 등을 했다고 주장하지 마라.'),
                      'reasoning': {'effort': 'minimal'}, 'max_output_tokens': 1200},
                timeout=60)
            print(f'OpenAI HTTP {response.status_code}', flush=True)
            if response.status_code == 429:
                return 'AI 사용 한도 또는 잔액 문제로 응답하지 못했어요. 운영자는 OpenAI 사용량·결제 상태를 확인해 주세요.'
            if response.status_code in (401, 403):
                return 'AI 인증 또는 접근 권한 오류입니다. 운영자는 API 키와 권한을 확인해 주세요.'
            if response.status_code in (400, 404):
                return 'AI 요청 설정 오류입니다. 운영자는 모델과 요청 설정을 확인해 주세요.'
            response.raise_for_status()
            payload = response.json()
            result = '\n'.join(part.get('text', '') for item in payload.get('output', [])
                if item.get('type') == 'message' for part in item.get('content', [])
                if part.get('type') == 'output_text').strip()
            if not result:
                return '답변을 완성하지 못했어요. 질문을 짧게 바꿔 다시 보내주세요.'
            if len(result) > 2800:
                result = result[:2750] + '\n(답변이 길어 일부 생략했어요. 범위를 좁혀 질문해주세요.)'
            if all(identity):
                self.history[identity] = (inputs + [{'role': 'assistant', 'content': result}])[-8:]
                self.history.move_to_end(identity)
                while len(self.history) > 128:
                    self.history.popitem(last=False)
            return result
        except httpx.TimeoutException:
            return 'AI 응답 시간이 길어 요청을 종료했어요. 잠시 후 다시 보내주세요.'
        except (httpx.HTTPError, ValueError):
            return 'AI 서비스에 일시적인 오류가 있어요. 잠시 후 다시 시도해주세요.'


def outputs(text):
    if isinstance(text, dict) and 'textCard' in text:
        return [text]
    return [{'simpleText': {'text': text[i:i + 1000]}}
            for i in range(0, min(len(text), 3000), 1000)]
