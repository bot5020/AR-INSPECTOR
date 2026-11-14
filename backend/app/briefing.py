"""План проверки: код рассчитывает основания и действия, LLM выбирает их порядок."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import OrderedDict

from . import llm

log = logging.getLogger('briefing')
SYSTEM = """Выбери приоритетный порядок проверок оборудования из готовых вариантов.
Верни JSON {"steps": [номера вариантов]}: от 1 до 4 разных узлов, не больше количества вариантов.
Номер — целое число из поля choice. Не придумывай текст, сроки, значения или новые варианты.
Сначала обязательные проверки с priority=2: включи все такие узлы, если их не больше четырёх;
если их больше, выбери четыре наиболее важных. Затем проверки priority=1, затем плановые.
Для каждого узла выбери одно наиболее важное основание и соответствующее ему действие.
Не требуй осмотра скрытых деталей обычной камерой. Только итоговый JSON без рассуждений."""
_cache: OrderedDict[str, dict] = OrderedDict()
_pending: dict[str, asyncio.Task] = {}


def context(card: dict, session: dict | None = None) -> list[dict]:
    out = []
    for c in card['components']:
        for i, reason in enumerate(c.get('reasons', [])):
            out.append({'basis_id': f"{c['id']}:{i}", 'component_id': c['id'], 'name': c['name'],
                        'source': reason['source'], 'reason': reason['text'], 'level': reason['level'],
                        'suggestion': reason.get('action') or 'Проверить основание предупреждения и зафиксировать результат.'})
    for f in (session or {}).get('review', {}).get('findings', []):
        if f.get('component_id') and f['category'] != 'ok' and f.get('verdict') not in ('rejected', 'confirmed'):
            out.append({'basis_id': f"shot:{f['analysis_id']}:{f['key']}", 'component_id': f['component_id'],
                        'name': f['name'], 'source': 'visual', 'reason': f.get('reason') or 'Находка требует решения',
                        'level': 2 if f['category'] == 'defect' else 1,
                        'suggestion': f.get('recommendation') or 'Проверить находку на снимке'})
    if not out:
        for g in card.get('guidance', [])[:4]:
            out.append({'basis_id': f"guide:{g['component_id']}", 'component_id': g['component_id'],
                        'name': g['name'], 'source': 'inspection', 'reason': g['reason'], 'level': g['level'],
                        'suggestion': g['hint']})
    unique = {}
    for b in sorted(out, key=lambda b: -b['level']):
        key = (b['component_id'], b['source'], b['reason'], b['suggestion'])
        unique.setdefault(key, b)
    return list(unique.values())


def rules(facts: list[dict], **extra) -> dict:
    steps, seen = [], set()
    for b in facts:
        if b['component_id'] in seen:
            continue
        seen.add(b['component_id'])
        steps.append({**b, 'action': b['suggestion']})
        if len(steps) == 4:
            break
    return {'steps': steps, 'source': 'rules', **extra}


def schema(facts: list[dict]) -> dict:
    return {'type': 'object', 'properties': {'steps': {'type': 'array', 'minItems': 1, 'maxItems': min(4, len(facts)),
            'items': {'type': 'integer', 'enum': list(range(len(facts)))}}},
            'required': ['steps'], 'additionalProperties': False}


def candidates(facts: list[dict]) -> list[dict]:
    """Один вариант на узел: модель ранжирует узлы, а не дубли их предупреждений."""
    order = {'parameter': 0, 'resource': 1, 'maintenance': 2, 'defect': 3, 'visual': 4, 'combined': 5}
    groups = {}
    for basis in sorted(facts, key=lambda b: (-b['level'], order.get(b['source'], 6))):
        groups.setdefault(basis['component_id'], []).append(basis)
    return [{**group[0], 'related_bases': group[1:]} for group in groups.values()]


def validate(data: dict, facts: list[dict]) -> list[dict]:
    if not isinstance(data, dict) or set(data) != {'steps'} or not isinstance(data['steps'], list) or not 1 <= len(data['steps']) <= 4:
        raise ValueError('Ожидается список из 1–4 номеров проверок')
    result, seen = [], set()
    for index in data['steps']:
        if type(index) is not int or not 0 <= index < len(facts):
            raise ValueError('Выбран неизвестный вариант проверки')
        basis = facts[index]
        if basis['component_id'] in seen:
            raise ValueError('Один узел выбран повторно')
        seen.add(basis['component_id'])
        result.append({**basis, 'action': basis['suggestion']})
    urgent = {b['component_id'] for b in facts if b['level'] >= 2}
    if len(seen & urgent) < min(4, len(urgent)):
        raise ValueError('Пропущен узел с обязательной проверкой')
    if [b['level'] for b in result] != sorted((b['level'] for b in result), reverse=True):
        raise ValueError('Срочные проверки должны идти первыми')
    return result


def cancel_pending() -> None:
    """Снимок имеет приоритет над фоновым текстовым запросом к той же локальной модели."""
    for task in list(_pending.values()):
        task.cancel()


async def generate(facts: list[dict], name: str) -> dict:
    payload = {'equipment': name, 'choices': [
        {'choice': i, 'component': b['component_id'], 'name': b['name'], 'priority': b['level'],
         'basis': b['reason'], 'action': b['suggestion'], 'other_bases': b.get('related_bases', [])}
        for i, b in enumerate(facts)]}
    for attempt in range(2):
        try:
            data, meta = await asyncio.wait_for(llm.ask(
                SYSTEM, [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}],
                schema=schema(facts), max_tokens=180), timeout=30)
            return {'steps': validate(data, facts), 'source': 'ai', 'model': meta}
        except TimeoutError:
            log.warning('План: модель не ответила за 30 секунд')
            break
        except llm.LLMUnavailable as error:
            log.warning('Модель плана недоступна: %s', error)
            break  # транспорт уже повторяет запрос; не удваиваем очередь на неисправном движке
        except (ValueError, TypeError) as error:
            log.warning('План отклонён, попытка %d: %s', attempt + 1, error)
            payload['previous_error'] = str(error)
    return rules(facts, error='Не удалось получить корректный порядок проверок от ИИ')


def _finished(key: str, task: asyncio.Task) -> None:
    if _pending.get(key) is task:
        _pending.pop(key, None)
    if task.cancelled():
        return
    try:
        result = task.result()
    except Exception:
        log.exception('Не удалось подготовить план')
        return
    if result['source'] == 'ai':
        _cache[key] = result
        while len(_cache) > 64:
            _cache.popitem(last=False)


async def get(card: dict, session: dict | None = None) -> dict:
    facts = candidates(context(card, session))
    key = hashlib.sha256(json.dumps([card['equipment']['id'], facts], sort_keys=True,
                                   ensure_ascii=False).encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    if not facts:
        return {'steps': [], 'source': 'rules'}
    task = _pending.get(key)
    if task is None or task.cancelled():
        task = _pending[key] = asyncio.create_task(generate(facts, card['equipment']['name']))
        task.add_done_callback(lambda done: _finished(key, done))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.cancelled():
            raise
        return rules(facts, deferred=True)
