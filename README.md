# AI TOP 100 — Kakao bot connection scaffold

현재 기능은 **에코 응답**입니다. 실제 대회 문제 해결 AI는 아직 포함하지 않습니다.

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

실제 AI 처리 기능은 `answer()`를 교체하여 구현합니다. 장시간 처리에는 별도 작업 큐 등 추가 설계가 필요합니다.

공식 문서: https://developers.kakao.com/docs/ko/bot/gateway
