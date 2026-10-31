"""Turn Responses URL annotations into clickable Kakao plain-text citations."""
import re
from urllib.parse import urlsplit


def render_response(payload):
    parts = []
    for item in payload.get('output', []):
        if item.get('type') != 'message':
            continue
        for part in item.get('content', []):
            if part.get('type') != 'output_text':
                continue
            text = part.get('text', '')
            edits = []
            for citation in part.get('annotations', []):
                url = citation.get('url', '')
                if citation.get('type') != 'url_citation' or urlsplit(url).scheme not in ('https', 'http'):
                    continue
                start, end = citation.get('start_index'), citation.get('end_index')
                if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(text):
                    edits.append((start, end, '\n출처: ' + url + '\n'))
            for start, end, replacement in sorted(edits, reverse=True):
                text = text[:start] + replacement + text[end:]
            parts.append(text)
    return '\n'.join(parts).strip()


def split_message(text):
    # Keep clickable URLs intact rather than slicing at the 1,000th character.
    chunks, current = [], ''
    for token in re.findall(r'https?://\S+|\s+|[^\s]+', text):
        if token.startswith(('http://', 'https://')) and len(token) > 1000:
            return ['출처 주소가 너무 길어 결과를 표시하지 못했어요. 다른 출처로 다시 검색해 달라고 요청해주세요.']
        while token:
            room = 1000 - len(current)
            if len(token) <= room:
                current += token
                break
            if current:
                chunks.append(current)
                current = ''
                continue
            chunks.append(token[:1000])
            token = token[1000:]
    if current:
        chunks.append(current)
    if len(chunks) > 3:
        return ['검색 결과와 출처가 길어 한 번에 표시하지 못했어요. 질문 범위를 좁히거나 한 가지 결과만 요청해주세요.']
    return chunks
