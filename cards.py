"""Static Kakao cards; button messages are handled locally without an AI call."""
HELP = ('그리니에게 질문을 보내면 AI가 답해요.\n\n'
        '• 글 요약·문장 다듬기·아이디어 정리를 도와드려요.\n'
        '• 그림 요청은 실제 AI 이미지로 보내드려요(저장소 연결 필요).\n'
        '• 최근 대화 맥락을 기억해요. 서버가 재시작되면 초기화돼요.\n'
        '• 새 주제로 시작하려면 대화 초기화를 눌러주세요.\n'
        '• 최신 정보는 웹 검색으로 확인하고 출처를 알려드려요.\n• 검색: 질문 형식으로 검색을 요청할 수 있어요.')


def card(title, description, buttons):
    return {'textCard': {'title': title, 'description': description,
                        'buttonLayout': 'horizontal',
                        'buttons': [{'action': 'message', 'label': label,
                                     'messageText': message}
                                    for label, message in buttons]}}


def help_card():
    return card('그리니 사용 안내', HELP,
                [('사용 예시', '사용 예시'), ('대화 초기화', '대화 초기화')])


def examples_card():
    return card('이렇게 질문해 보세요',
                '아래 문장을 직접 입력해 보세요.\n\n'
                '• 이 문장을 더 공손하게 바꿔줘: …\n'
                '• 다음 글을 세 줄로 요약해줘: …\n'
                '• 검색: 오늘 USD/KRW 환율과 기준 시각 알려줘',
                [('도움말', '도움말'), ('대화 초기화', '대화 초기화')])


def reset_card():
    return card('새 대화를 시작해요', '이 채팅방에서 나눈 사용자님의 대화 기록을 초기화했어요. 새 질문을 보내주세요.',
                [('도움말', '도움말'), ('사용 예시', '사용 예시')])
