# AI TOP 100 — Kakao bot connection scaffold

현재 기능은 **OpenAI 기반 일반 대화**입니다. 대회 업무에 특화된 기능은 아직 없습니다.

## Render 배포

1. New → Background Worker에서 이 저장소를 연결합니다.
2. Language: Python / Build Command: `pip install -r requirements.txt`
3. Start Command: `python -u bot.py`
4. Environment에 `BOT_TOKEN`을 등록합니다. 토큰은 저장소에 올리지 않습니다.
5. 인스턴스 1개로 실행하고, 같은 봇을 실행하던 로컬 터미널은 먼저 종료합니다.
6. 유료 요금을 확인하고 배포한 후 로그의 `Gateway connected`와 카카오톡 응답을 확인합니다.
7. 최종 버전 검증 후 자동 배포를 끄고 심사 종료까지 유지합니다.

게이트웨이 방식이므로 카카오 봇 설정의 웹훅 URL은 비어 있어야 합니다.

## 연결 복구

일시적인 연결 종료는 최대 60초의 지수 백오프로 재시도하며, 세션과 마지막 처리 시퀀스로 RESUME합니다.
세션 만료(4009) 시 새로 인증합니다. 프로세스 자체가 재시작되면 새 세션으로 시작합니다.
인증 실패(4001), 잘못된 페이로드(4003), 중복 연결(4004)은 로그를 남기고 대기합니다.
해당 설정을 해결한 후 서비스를 재시작하세요. 콜백 실패는 중복 발송 방지를 위해 무조건 재전송하지 않습니다.

AI 처리 기능은 `ai.py`에서 구현합니다.

공식 문서: https://developers.kakao.com/docs/ko/bot/gateway

## OpenAI 연결 (현재 버전)

현재 버전은 에코 대신 **일반 AI 대화**를 제공합니다. 대회 업무에 특화된 도구·데이터는 아직 없습니다.
Render Environment에 `OPENAI_API_KEY`를 추가합니다. 기본 모델은 `gpt-5-mini`입니다.
`OPENAI_MODEL`로 모델 변경이 가능하나 현재 요청의 reasoning minimal 지원이 필요합니다.
`SYSTEM_PROMPT`로 기본 지시문을 설정할 수 있습니다.

최근 4회 왕복 대화를 채팅방+사용자별로 메모리에 유지하며 재배포/재시작하면 초기화됩니다. 최대 128개 대화입니다.
`대화 초기화` 또는 `/reset`으로 지울 수 있습니다. 질문과 최근 맥락은 OpenAI API에 전송합니다(`store=false`).
동시 처리 최대 8개, 사용자당 1개이며 AI 요청 제한시간은 60초입니다.
응답은 카카오 말풍선 1,000자씩 최대 3개로 나눕니다. API 오류 시 안내하며 토큰과 대화 내용은 로그에 기록하지 않습니다.

## 카드와 버튼

`도움말`(`/help`, `메뉴`) → 사용법 카드, `사용 예시` → 예시 카드,
`대화 초기화`(`/reset`) → 해당 사용자 기록 초기화와 완료 카드.
카드의 버튼을 누르면 같은 명령이 메시지로 전달됩니다. 이 명령들은 OpenAI를 호출하지 않습니다.
일반 질문은 기존 텍스트 답변을 유지합니다. 카카오 전역 추천 명령어·메뉴 도움말 API 등록과는 별개입니다.

## 웹 검색

Responses API의 `web_search`를 사용합니다. 일반 질문은 모델이 검색 필요 여부를 판단하며,
`검색: 질문` 또는 `/search 질문`은 도구 사용을 필수로 설정합니다.
검색 호출 비용이 추가될 수 있습니다. 기본 모델은 그대로 `gpt-5-mini`, reasoning은 `low`로 변경했습니다.
한국 현재 시각을 전달하고 최신 수치의 출처·기준 시각을 명시하도록 안내합니다. 검색이 실시간 금융 피드를 보장하지는 않습니다.
API 인용 annotation을 답변 중 클릭 가능한 URL로 변환하며 URL을 말풍선 경계에서 자르지 않습니다.
최대 3개 말풍선에 출처를 포함할 수 없으면 짧은 질문으로 재요청하도록 안내합니다.
AI 요청 제한시간은 웹 검색을 위해 120초로 변경했습니다.

## 이미지 생성

그림 요청은 Responses의 `generate_image` 함수 호출로 분류하고 OpenAI Images API에서 실제 이미지를 생성합니다.
기본 모델은 `gpt-image-1.5`, medium 품질, 1024×1024 JPEG 한 장입니다.
`OPENAI_IMAGE_MODEL`로 모델을 변경할 수 있습니다. API 계정의 이미지 모델 접근 권한/조직 인증과 잔액이 필요합니다.

Render Environment에 `CLOUDINARY_URL=cloudinary://API_KEY:API_SECRET@CLOUD_NAME`을 설정합니다.
Cloudinary Console의 API Keys에서 값을 복사하고 저장소나 채팅에는 붙여넣지 않습니다.
이미지는 서버에서 인증된 업로드로 저장하고 HTTPS URL을 Kakao SimpleImage로 보냅니다.
생성된 이미지는 URL을 아는 사람이 열 수 있으며 Cloudinary에 유지됩니다. 개인정보가 담긴 그림은 요청하지 마세요.
저장소 미설정 상태에서는 유료 이미지 생성 요청을 보내지 않습니다. 업로드 실패 후 자동 재생성하지 않습니다.
이미지 생성은 동시 2개, 전체 메시지 처리는 최대 260초로 제한해 5분 콜백 만료 전에 응답합니다.
현재 텍스트 기반 새 이미지 생성만 지원하며 입력 사진 편집은 지원하지 않습니다.

검증: `python -m unittest -v test_bot.py test_web.py test_images.py` (외부 호출은 mock).
실제 배포 검증은 `크리스마스 그림 그려줘`를 보내 이미지 수신과 generation/upload/callback 로그를 확인합니다.
