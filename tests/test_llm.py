import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import config, llm


class LocalModel(unittest.TestCase):
    def test_thinking_disabled_in_both_request_modes(self):
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            if body.get('stream'):
                chunk = {'choices': [{'delta': {'content': '{}'}}]}
                return httpx.Response(200, text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n')
            return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})

        async def check():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                with patch.object(llm, '_client', client), patch.object(config, 'VLM_REASONING_EFFORT', 'none'), patch.object(config, 'VLM_PROVIDER', 'local'):
                    await llm.ask('test', [], schema={})
                    async for _ in llm.ask_stream('test', [], schema={}):
                        pass

        asyncio.run(check())
        self.assertEqual(len(requests), 2)
        for body in requests:
            self.assertFalse(body['enable_thinking'])
            self.assertEqual(body['chat_template_kwargs'], {'enable_thinking': False})


    def test_engine_failure_before_answer_is_retried(self):
        calls = []

        def respond(request):
            calls.append(1)
            if len(calls) == 1:   # как «Engine protocol predict request failed» в LM Studio: ошибка сразу, без текста
                return httpx.Response(200, text='data: ' + json.dumps({'error': {'message': 'fetch failed'}}) + '\n\n')
            chunk = {'choices': [{'delta': {'content': '{"ok": true}'}}]}
            return httpx.Response(200, text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n')

        async def run():
            out = ''
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                with patch.object(llm, '_client', client), patch.object(config, 'VLM_PROVIDER', 'local'):
                    meta = {}
                    async for delta, m in llm.ask_stream('test', [], schema={}):
                        out += delta
                        meta = m or meta
            return out, meta

        out, meta = asyncio.run(run())
        self.assertEqual((out, meta['attempts'], len(calls)), ('{"ok": true}', 2, 2))

    def test_empty_answer_twice_is_reported_not_as_incomplete_json(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text='data: [DONE]\n\n'))) as client:
                with patch.object(llm, '_client', client), patch.object(config, 'VLM_PROVIDER', 'local'):
                    async for _ in llm.ask_stream('test', [], schema={}):
                        pass
        with self.assertRaises(llm.LLMUnavailable) as err:
            asyncio.run(run())
        self.assertIn('пустой ответ', str(err.exception))


if __name__ == '__main__':
    unittest.main()
