import asyncio
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import briefing, llm

CARD = {'equipment': {'id': 'PUMP', 'name': 'Насос'}, 'components': [
    {'id': 'MOTOR', 'name': 'Двигатель', 'reasons': [
        {'source': 'maintenance', 'level': 2, 'text': 'Достигнут интервал обслуживания',
         'action': 'Выполнить обслуживание двигателя по регламенту'}],
     'recommendations': ['Рекомендация по другому основанию']},
    {'id': 'PUMP', 'name': 'Корпус', 'reasons': [
        {'source': 'visual', 'level': 1, 'text': 'Следы коррозии', 'action': 'Проверить коррозию'}]}], 'guidance': []}
GOOD = {'steps': [0, 1]}


class PlanValidation(unittest.TestCase):
    def test_thinking_and_missing_final(self):
        self.assertEqual(llm.final_content('<think>внутренний текст</think> {"steps": []}'), '{"steps": []}')
        with self.assertRaises(llm.LLMUnavailable):
            llm.final_content('<think>незаконченный ответ')

    def test_actions_remain_attached_to_their_own_evidence(self):
        card = copy.deepcopy(CARD)
        card['components'][0]['reasons'].append({'source': 'resource', 'level': 1,
            'text': 'Осталось 70 ч', 'action': 'Включить в план замены: осталось 70 ч.'})
        facts = briefing.context(card)
        self.assertEqual(briefing.validate({'steps': [0]}, facts)[0]['action'],
                         'Выполнить обслуживание двигателя по регламенту')
        resource = next(f for f in facts if f['source'] == 'resource')
        self.assertEqual(resource['suggestion'], 'Включить в план замены: осталось 70 ч.')
        self.assertNotIn('Рекомендация по другому основанию', [f['suggestion'] for f in facts])

    def test_original_verified_dates_are_preserved_without_allowing_generated_text(self):
        card = copy.deepcopy(CARD)
        card['components'][0]['reasons'][0]['action'] = 'Запланировать смазку через 9 дн.'
        facts = briefing.context(card)
        self.assertEqual(briefing.validate(GOOD, facts)[0]['action'], 'Запланировать смазку через 9 дн.')
        for bad in [{'steps': [{'choice': 0, 'action': 'Заменить через 999 часов'}]},
                    {'steps': [0], 'action': 'Заменить через 999 часов'}]:
            with self.assertRaises(ValueError): briefing.validate(bad, facts)

    def test_unknown_duplicate_boolean_and_low_priority_selections_are_rejected(self):
        facts = briefing.context(CARD)
        for steps in [[9], [-1], [True], ['0'], [0, 0], [1], [1, 0], []]:
            with self.assertRaises(ValueError, msg=str(steps)):
                briefing.validate({'steps': steps}, facts)

    def test_schema_contains_only_available_choices(self):
        schema = briefing.schema(briefing.context(CARD))
        self.assertEqual(schema['properties']['steps']['items']['enum'], [0, 1])
        self.assertEqual(schema['properties']['steps']['maxItems'], 2)
        self.assertFalse(schema['additionalProperties'])

    def test_candidates_are_unique_per_node_and_keep_other_evidence(self):
        card = copy.deepcopy(CARD)
        card['components'][0]['reasons'].append({'source': 'parameter', 'level': 2,
            'text': 'Температура 95 °C', 'action': 'Проверить нагрев по месту'})
        choices = briefing.candidates(briefing.context(card))
        self.assertEqual(len(choices), 2)
        self.assertEqual(choices[0]['source'], 'parameter')
        self.assertEqual(choices[0]['related_bases'][0]['source'], 'maintenance')


class PlanRequests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        briefing._cache.clear(); briefing._pending.clear()

    async def test_same_context_is_coalesced_and_cached_but_new_fact_recalculates(self):
        async def answer(*args, **kw):
            await asyncio.sleep(0)
            return copy.deepcopy(GOOD), {'seconds': .1}
        with patch.object(llm, 'ask', side_effect=answer) as call:
            a, b = await asyncio.gather(briefing.get(CARD), briefing.get(CARD))
            self.assertEqual((a['source'], b['source']), ('ai', 'ai'))
            await briefing.get(CARD)
            self.assertEqual(call.call_count, 1)
            changed = copy.deepcopy(CARD)
            changed['components'][0]['reasons'][0]['text'] = 'Другое основание'
            await briefing.get(changed)
            self.assertEqual(call.call_count, 2)
            self.assertFalse(briefing._pending)

    async def test_invalid_thinking_answer_retries_then_uses_marked_rules(self):
        with patch.object(llm, 'ask', new=AsyncMock(return_value=('Thinking Process:', {}))) as call:
            out = await briefing.get(CARD)
            self.assertEqual(call.call_count, 2)
            self.assertEqual(out['source'], 'rules')
            self.assertNotIn('Thinking', str(out['steps']))
            self.assertFalse(briefing._cache)

    async def test_unavailable_engine_is_not_queried_again_by_the_planner(self):
        with patch.object(llm, 'ask', new=AsyncMock(side_effect=llm.LLMUnavailable('engine unavailable'))) as call:
            out = await briefing.get(CARD)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(out['source'], 'rules')
            self.assertTrue(out['error'])

    async def test_vision_cancels_background_generation(self):
        started = asyncio.Event()
        async def answer(*args, **kw):
            started.set()
            await asyncio.Event().wait()
        with patch.object(llm, 'ask', side_effect=answer):
            request = asyncio.create_task(briefing.get(CARD))
            await started.wait()
            briefing.cancel_pending()
            out = await request
            self.assertTrue(out['deferred'])
            self.assertFalse(briefing._pending)

    async def test_disconnected_browser_does_not_leak_the_shared_task_or_lose_its_result(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def answer(*args, **kw):
            started.set(); await release.wait()
            return copy.deepcopy(GOOD), {}
        with patch.object(llm, 'ask', side_effect=answer) as call:
            request = asyncio.create_task(briefing.get(CARD))
            await started.wait()
            model_task = next(iter(briefing._pending.values()))
            request.cancel()
            with self.assertRaises(asyncio.CancelledError): await request
            self.assertFalse(model_task.cancelled())
            release.set(); await model_task; await asyncio.sleep(0)
            self.assertFalse(briefing._pending)
            self.assertEqual((await briefing.get(CARD))['source'], 'ai')
            self.assertEqual(call.call_count, 1)
