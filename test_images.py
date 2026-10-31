import base64
import json
import os
import unittest
from unittest.mock import patch
import httpx
from ai import Assistant, outputs
from image_service import ImageService, parse_storage_url

ENV = {'OPENAI_API_KEY': 'fake-openai',
       'CLOUDINARY_URL': 'cloudinary://fake-key:fake-secret@test-cloud'}

class ImageTests(unittest.IsolatedAsyncioTestCase):
    def test_copied_environment_formats(self):
        for value in (ENV['CLOUDINARY_URL'], ' CLOUDINARY_URL="' + ENV['CLOUDINARY_URL'] + '"\n',
                      "export CLOUDINARY_URL='" + ENV['CLOUDINARY_URL'] + "'"):
            self.assertEqual(parse_storage_url(value).hostname, 'test-cloud')
            self.assertEqual(parse_storage_url(value).password, 'fake-secret')

    async def test_generation_upload_and_bubble(self):
        calls = []
        def handler(request):
            calls.append(request.url.host)
            if request.url.path == '/v1/responses':
                body = json.loads(request.content)
                self.assertTrue(any(t.get('name') == 'generate_image' for t in body['tools']))
                return httpx.Response(200, json={'output': [{'type': 'function_call',
                    'name': 'generate_image', 'arguments': json.dumps({'prompt': '크리스마스 트리 일러스트'})}]})
            if request.url.path == '/v1/images/generations':
                body = json.loads(request.content)
                self.assertEqual(body['n'], 1)
                self.assertEqual(body['output_format'], 'jpeg')
                return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(b'\xff\xd8\xfftest').decode()}]})
            self.assertEqual(request.url.host, 'api.cloudinary.com')
            self.assertTrue(request.headers['authorization'].startswith('Basic '))
            self.assertNotIn(b'fake-openai',request.content)
            self.assertIn(b'\xff\xd8\xfftest',request.content)
            return httpx.Response(200,json={'secure_url':'https://res.cloudinary.com/test-cloud/image/upload/random.jpg'})
        with patch.dict(os.environ, ENV): ai=Assistant()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await ai.answer(client,{'content':'크리스마스 그림 그려줘'})
        self.assertIn('simpleImage',result)
        self.assertEqual(outputs(result),[result])
        self.assertEqual(calls,['api.openai.com','api.openai.com','api.cloudinary.com'])

    async def test_no_generation_without_storage(self):
        with patch.dict(os.environ, {'CLOUDINARY_URL':''}): service=ImageService()
        def handler(request): self.fail('Must not incur image costs without storage')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.assertIn('저장소 연결',await service.generate(client,'트리'))

    async def test_permission_and_upload_failure(self):
        with patch.dict(os.environ, ENV): service=ImageService()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as client:
            self.assertIn('접근 권한',await service.generate(client,'트리'))
        def handler(request):
            if request.url.host=='api.openai.com':
                return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(b'\xff\xd8\xfftest').decode()}]})
            return httpx.Response(401)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.assertIn('업로드하지 못',await service.generate(client,'트리'))

if __name__=='__main__': unittest.main()
