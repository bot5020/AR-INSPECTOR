import asyncio
import copy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='inspector-test-init-')
# Тесты не зависят от .env стенда и от папки организаторов: пароли — случайные из demo-accounts.txt,
# карточки — только демо-набор (load_dotenv не перезаписывает уже заданные переменные).
for _name in ('ENGINEER', 'MASTER', 'ADMIN'):
    os.environ[f'DEMO_PASSWORD_{_name}'] = ''
os.environ['ORG_DATA_DIR'] = str(Path(tempfile.gettempdir()) / 'inspector-no-org-data')

from fastapi.testclient import TestClient
from PIL import Image
from app import assess, config, db, main, resource, store, telemetry
from app.vision import analyze


class Workflow(unittest.TestCase):
    def setUp(self):
        self.demo_password_env = patch.dict(os.environ, {f'DEMO_PASSWORD_{name}': '' for name in ('ENGINEER', 'MASTER', 'ADMIN')})
        self.demo_password_env.start()
        self.folder = tempfile.TemporaryDirectory(prefix='inspector-test-')
        if db._conn is not None:
            db._conn.close()
        db._conn = None
        config.DATA_DIR = Path(self.folder.name)
        config.IMAGES_DIR = config.DATA_DIR / 'images'
        config.IMAGES_DIR.mkdir()
        config.DB_PATH = config.DATA_DIR / 'app.db'
        main._started = False
        self.detector = patch.object(main.detector, 'start')
        self.detector.start()
        self.client = TestClient(main.app).__enter__()
        self.passwords = dict(line.split(': ', 1) for line in (config.DATA_DIR / 'demo-accounts.txt').read_text(encoding='utf-8').splitlines()[1:])
        self.login('engineer')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.detector.stop()
        db._conn.close(); db._conn = None
        self.folder.cleanup()
        self.demo_password_env.stop()

    def login(self, name):
        r = self.client.post('/api/auth/login', json={'username': name, 'password': self.passwords[name]})
        self.assertEqual(r.status_code, 200, r.text)
        return r

    def card(self):
        r = self.client.get('/api/equipment/PUMP-001')
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_node_preview_uses_the_requested_observation_and_requires_auth(self):
        done = self.analyze_once([self.finding()])
        aid = done['analysis_id']
        key = done['items'][0]['key']
        response = self.client.get(f'/api/analyses/{aid}/crop/{key}')
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
        crop = Image.open(io.BytesIO(response.content))
        self.assertGreater(crop.width, 0)
        self.assertEqual(crop.format, 'JPEG')
        self.assertEqual(self.client.get(f'/api/analyses/{aid}/crop/not-a-node').status_code, 404)
        self.client.post('/api/auth/logout')
        self.assertEqual(self.client.get(f'/api/analyses/{aid}/crop/{key}').status_code, 401)

    def test_roles_and_authenticated_author(self):
        self.client.post('/api/auth/logout')
        self.assertEqual(self.client.get('/api/equipment').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={'username': 'engineer', 'password': 'wrong'}).status_code, 401)
        self.login('engineer')
        r = self.client.post('/api/equipment/PUMP-001/events', json={'type': 'checked', 'component_id': 'PUMP-001-PUMP', 'author': 'forged'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['history'][0]['author'], 'Инженер')
        norm = {'normative_hours': 21000, 'source': 'Принято командой в тесте'}
        url = '/api/equipment/PUMP-001/components/PUMP-001-MOTOR-BRG/normative'
        self.assertEqual(self.client.post(url, json=norm).status_code, 403)
        self.login('admin')
        self.assertEqual(self.client.post(url, json=norm).status_code, 200)
        self.assertEqual(db.query_one("SELECT normative_hours FROM components WHERE id='PUMP-001-MOTOR-BRG'")['normative_hours'], 21000)
        self.assertEqual(self.client.post(url, json={**norm, 'normative_hours': -1}).status_code, 400)

    def test_replay_and_reference_is_not_an_input(self):
        card = self.card()
        self.assertEqual(card['measurement']['count'], 500)
        self.assertTrue(all(p['source'].startswith('Описание') for p in card['parameters']))
        preset = telemetry.presets()[1]
        r = self.client.post('/api/equipment/PUMP-001/scenario', json={'index': preset['index']})
        self.assertEqual(r.status_code, 200)
        params = {p['id']: p for p in r.json()['parameters']}
        self.assertGreater(params['ORG-v1']['level'], 0)
        self.assertEqual(params['ORG-bearing_temp']['level'], 0)
        row = copy.deepcopy(telemetry.dataset()['rows'][preset['index']])
        before = telemetry.parameters(row)
        row['reference'] = {'vibration': 'Made up', 'temperature': 'Made up'}
        self.assertEqual(before, telemetry.parameters(row))
        self.assertEqual(self.client.post('/api/equipment/PUMP-001/scenario', json={'index': 500}).status_code, 400)
        self.assertEqual(self.client.post('/api/equipment/PUMP-001/scenario', json={'index': True}).status_code, 400)

    def test_missing_readings_and_hidden_components(self):
        self.assertIsNone(assess.assess_parameter({'id': 'none', 'name': 'none', 'value': None})['level'])
        card = self.card()
        known = {c['id']: c for c in card['components']}
        observation = self.observation('PUMP-001-IMP')
        self.assertIsNone(analyze.normalize_item(observation, 0, known))
        self.assertNotIn('PUMP-001-IMP:', analyze._component_lines(card))
        observation = self.observation('PUMP-001-PUMP'); observation['visibility'] = 'not_visible'
        self.assertIsNone(analyze.normalize_item(observation, 0, known))

    def test_operation_and_maintenance_hours(self):
        initial = db.query_one("SELECT operating_hours FROM components WHERE id='PUMP-001-MOTOR'")['operating_hours']
        self.assertEqual(self.client.post('/api/equipment/PUMP-001/operation', json={'state': 'running'}).status_code, 200)
        since = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with db.tx() as c:
            c.execute("UPDATE equipment SET run_started_at=? WHERE id='PUMP-001'", (since,))
        motor = next(c for c in self.card()['components'] if c['id'] == 'PUMP-001-MOTOR')
        self.assertAlmostEqual(motor['operating_hours'], initial + 2, places=2)
        self.client.post('/api/equipment/PUMP-001/operation', json={'state': 'stopped'})
        motor = next(c for c in self.card()['components'] if c['id'] == 'PUMP-001-MOTOR')
        self.assertAlmostEqual(motor['operating_hours'], initial + 2, places=2)
        with db.tx() as c:
            c.execute("UPDATE components SET maintenance_interval_days=NULL,maintenance_interval_hours=100,last_service_hours=? WHERE id='PUMP-001-MOTOR'", (initial - 99,))
        motor = next(c for c in self.card()['components'] if c['id'] == 'PUMP-001-MOTOR')
        self.assertEqual(motor['maintenance']['status'], 'overdue')
        r = self.client.post('/api/equipment/PUMP-001/events', json={'type': 'maintenance', 'component_id': motor['id']})
        self.assertEqual(r.status_code, 200, r.text)
        motor = next(c for c in r.json()['components'] if c['id'] == motor['id'])
        self.assertEqual(motor['maintenance']['status'], 'ok')
        self.assertAlmostEqual(motor['maintenance']['hours_left'], 100)

    @staticmethod
    def observation(cid='PUMP-001-PUMP'):
        return dict(component_id=cid, bbox=[100, 100, 600, 600], visibility='visible', category='check', signs=['коррозия'],
                    reason='Видимый признак', recommendation='Проверить', defect_bbox=[200, 200, 300, 300], confidence=.7)

    def test_inspection_validation(self):
        card, image = self.card(), Image.new('RGB', (100, 100))
        base = dict(scene='pump', matches_equipment=True, image_quality='good', components=[self.observation()], next_view='Другой ракурс')
        async def attempt(data):
            async def stream(*args, **kwargs):
                yield data, {'model': 'mock'}
            with patch('app.llm.ask_stream', stream):
                return await analyze.inspect(card, image)
        self.assertEqual(len(asyncio.run(attempt(json.dumps(base)))['items']), 1)
        for invalid in ['{"incomplete":', json.dumps({**base, 'matches_equipment': False}),
                        json.dumps({**base, 'image_quality': 'poor'}), json.dumps({**base, 'components': []}),
                        json.dumps({**base, 'components': [self.observation('PUMP-001-IMP')]})]:
            with self.assertRaises(main.llm.LLMUnavailable):
                asyncio.run(attempt(invalid))

    def test_repeated_model_items_are_removed_but_more_serious_findings_remain(self):
        card, image = self.card(), Image.new('RGB', (100, 100))
        first = self.observation()
        repeated = {**first, 'confidence': .95}
        worse = {**first, 'category': 'defect'}
        response = dict(scene='pump', matches_equipment=True, image_quality='good',
                        components=[first, repeated, worse], next_view='Другой ракурс')

        async def attempt(text):
            async def stream(*args, **kwargs):
                yield text, {'model': 'mock'}
            with patch('app.llm.ask_stream', stream):
                return [event async for event in analyze.inspect_stream(card, image)]

        for text in [json.dumps(response), json.dumps(response).rsplit(']', 1)[0]]:
            events = asyncio.run(attempt(text))
            self.assertEqual([data['category'] for kind, data in events if kind == 'item'], ['check', 'defect'])
            self.assertEqual(len(events[-1][1]['items']), 2)

    def test_open_notes_are_unique_without_removing_the_history(self):
        cid = 'PUMP-001-SEAL'
        for note in ['Следы влаги', '  Следы   влаги ', 'Коррозия']:
            self.assertEqual(self.client.post('/api/equipment/PUMP-001/events',
                json={'type': 'defect', 'component_id': cid, 'note': note}).status_code, 200)
        comp = next(c for c in self.card()['components'] if c['id'] == cid)
        self.assertEqual(len(comp['open_defects']), 2)
        self.assertEqual(sum(e['type'] == 'defect' and e['note'].strip().startswith('Следы') for e in comp['history']), 2)
        self.client.post('/api/equipment/PUMP-001/events', json={'type': 'resolved', 'component_id': cid})
        self.client.post('/api/equipment/PUMP-001/events', json={'type': 'defect', 'component_id': cid, 'note': 'Следы влаги'})
        comp = next(c for c in self.card()['components'] if c['id'] == cid)
        self.assertEqual(len(comp['open_defects']), 1)

    def test_demo_passwords_from_env_and_setup_page(self):
        from app import auth
        with patch.dict(os.environ, {'DEMO_PASSWORD_MASTER': 'master-from-env'}):
            auth.bootstrap()
            self.assertEqual(self.client.post('/api/auth/login', json={'username': 'master', 'password': 'master-from-env'}).status_code, 200)
            self.assertIn('master: задан в .env', auth.accounts_hint())
        # по сети (не localhost) страница подключения учётки не показывает
        self.assertNotIn('engineer:', self.client.get('/setup').text)

    def test_identification_offers_low_confidence_candidate(self):
        async def reply(*args, **kwargs):
            self.assertIn('PUMP-001', kwargs['schema']['properties']['equipment_id']['enum'])
            return dict(equipment_id='PUMP-001', confidence=.15, nameplate_text='',
                        what_is_it='Электродвигатель', evidence='Узел насосного агрегата, шильдик не виден'), {}
        photo = io.BytesIO(); Image.new('RGB', (50, 50)).save(photo, format='PNG')
        with patch.object(analyze, 'decode_qr', return_value=None), patch.object(analyze.llm, 'ask', side_effect=reply):
            r = self.client.post('/api/identify', files={'file': ('frame.png', photo.getvalue(), 'image/png')})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()['equipment_id'], r.json()['confidence']), ('PUMP-001', .15))
        self.assertEqual(r.json()['equipment']['id'], 'PUMP-001')
        self.assertIn('шильдик', r.json()['evidence'])

    def test_identification_keeps_unknown_and_prefers_nameplate_over_visual_guess(self):
        image = Image.new('RGB', (50, 50))
        async def reply(*args, **kwargs):
            return dict(equipment_id='unknown', confidence=.1, nameplate_text=plate,
                        what_is_it='Неясно', evidence='Недостаточно данных'), {}
        for plate, expected, method in [('', None, 'visual'), ('Зав. № 2019-0471', 'PUMP-001', 'nameplate')]:
            with self.subTest(plate=plate), patch.object(analyze, 'decode_qr', return_value=None), patch.object(analyze.llm, 'ask', side_effect=reply):
                result = asyncio.run(analyze.identify(image))
            self.assertEqual((result['equipment_id'], result['method']), (expected, method))

    def test_identification_qr_does_not_call_model(self):
        with patch.object(analyze, 'decode_qr', return_value='KST:EQ:CAB-003'), patch.object(analyze.llm, 'ask') as model:
            result = asyncio.run(analyze.identify(Image.new('RGB', (50, 50))))
        self.assertEqual((result['equipment_id'], result['method']), ('CAB-003', 'qr'))
        model.assert_not_called()

    def test_streaming_occluded_and_partial_answers(self):
        card, image = self.card(), Image.new('RGB', (100, 100))
        known = {c['id']: c for c in card['components']}
        occluded = self.observation('PUMP-001-COUP'); occluded.update(visibility='occluded', category='ok')
        item = analyze.normalize_item(occluded, 0, known)
        self.assertEqual((item['category'], item['visibility']), ('check', 'occluded'))
        self.assertTrue(item['reason'].startswith('Узел частично перекрыт'))

        head = '{"scene":"pump","matches_equipment":true,"image_quality":"good","components":['
        first, second = json.dumps(self.observation()), json.dumps(occluded)

        async def run(chunks):
            events = []
            async def stream(*args, **kwargs):
                for chunk in chunks:
                    events.append(('chunk', chunk))
                    yield chunk, None
                yield '', {'model': 'mock'}
            with patch('app.llm.ask_stream', stream):
                async for kind, payload in analyze.inspect_stream(card, image):
                    events.append((kind, payload))
            return events

        # первая метка уходит сразу после того, как узел дописан, — до конца ответа
        events = asyncio.run(run([head, first, ',', second, '],"next_view":"ракурс"}']))
        kinds = [k for k, _ in events]
        self.assertLess(kinds.index('item'), kinds.index('chunk', kinds.index('item')))
        self.assertEqual([k for k in kinds if k == 'item'], ['item', 'item'])
        self.assertFalse(events[-1][1]['partial'])

        # ответ оборвался после первого узла — осмотр не теряется, помечен как неполный
        events = asyncio.run(run([head, first, ',{"component_id":"PUMP-']))
        done = events[-1][1]
        self.assertTrue(done['partial'])
        self.assertEqual(len(done['items']), 1)

        # кадр не того оборудования — меток нет, сразу ошибка
        with self.assertRaises(main.llm.LLMUnavailable):
            asyncio.run(run([head.replace('true', 'false'), first, ']}']))

    def test_saved_snapshot_approval_and_reports(self):
        r = self.client.post('/api/equipment/PUMP-001/inspections', json={'author': 'forged'})
        self.assertEqual(r.status_code, 200, r.text)
        iid = r.json()['saved_inspection_id']
        saved = db.loads(db.query_one('SELECT data_json FROM events WHERE id=?', (iid,))['data_json'], {})
        self.assertNotIn('data_json', saved['card']['history'][0])
        self.client.post('/api/equipment/PUMP-001/scenario', json={'index': 100})
        url = f'/api/equipment/PUMP-001/inspections/{iid}'
        self.assertEqual(self.client.post(url + '/approve').status_code, 403)
        self.login('master')
        self.assertEqual(self.client.post(url + '/approve').status_code, 200)
        self.assertEqual(self.client.post(url + '/approve').status_code, 409)
        approvals = db.query("SELECT id FROM events WHERE type='approval' AND equipment_id='PUMP-001'")
        self.assertEqual(len(approvals), 1)
        book = self.client.get(url + '/report.xlsx')
        self.assertEqual(book.status_code, 200, book.text[:300] if book.status_code != 200 else '')
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(book.content))
        self.assertEqual(wb.sheetnames[:4], ['Осмотр', 'Измерения', 'Узлы', 'История'])
        self.assertEqual(wb['Осмотр']['A1'].value, 'Поле')
        from app.reports import _cell_value
        self.assertEqual(_cell_value('=HYPERLINK("x")'), '\'=HYPERLINK("x")')
        pdf = self.client.get(url + '/report.pdf')
        self.assertEqual(pdf.status_code, 200, pdf.text[:300] if pdf.status_code != 200 else '')
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        from app.reports import sections
        _, tables = sections(db.query_one('SELECT * FROM events WHERE id=?', (iid,)))
        self.assertEqual(tables['Осмотр'][10][1], '2026-11-10 08:00')
        self.assertIn('Мастер', str(tables))

    def test_camera_and_selected_photo_can_be_analyzed(self):
        image = io.BytesIO()
        Image.new('RGB', (100, 100)).save(image, format='JPEG')

        async def inspect(*args):
            yield 'done', {'items': [], 'scene': 'test', 'model': {'model': 'mock'}}

        with patch.object(analyze, 'inspect_stream', inspect), patch.object(main.detector, 'ready', False):
            for source in ('camera', 'file'):
                response = self.client.post('/api/equipment/PUMP-001/analyze', data={'source': source},
                                            files={'file': ('frame.jpg', image.getvalue(), 'image/jpeg')})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn('event: done', response.text)
                saved = db.query_one('SELECT result_json FROM analyses ORDER BY id DESC LIMIT 1')
                self.assertEqual(db.loads(saved['result_json'], {})['image_source'], source)
            response = self.client.post('/api/equipment/PUMP-001/analyze', data={'source': 'invalid'},
                                        files={'file': ('frame.jpg', image.getvalue(), 'image/jpeg')})
            self.assertEqual(response.status_code, 400)

    def test_events_same_timestamp_keep_order(self):
        now = '2026-11-14T00:00:00+00:00'
        with patch('app.db.now_iso', return_value=now):
            for kind in ('defect', 'resolved'):
                r = self.client.post('/api/equipment/PUMP-001/events', json={'type': kind, 'component_id': 'PUMP-001-FLANGE'})
                self.assertEqual(r.status_code, 200)
        flange = next(c for c in self.card()['components'] if c['id'] == 'PUMP-001-FLANGE')
        self.assertEqual(flange['open_defects'], [])

    # ------------------------------------------------------------ архитектура

    def test_org_passports_load_cards_when_folder_present(self):
        from app import orgdata
        with tempfile.TemporaryDirectory() as org:
            passports = Path(org) / orgdata.PASSPORTS
            passports.mkdir(parents=True)
            for n in (1, 3):
                (passports / f'1) Насос №{n} ПС.pdf').write_bytes(b'%PDF-')
            with patch.dict(os.environ, {'ORG_DATA_DIR': org}):
                self.assertEqual(orgdata.sync(), 2)
                self.client.post('/api/equipment/AMS-1/events', json={'type': 'checked', 'component_id': 'AMS-1-SEAL', 'note': 'Подтёков нет'})
                self.assertEqual(orgdata.sync(), 0)  # повторный запуск ничего не дублирует и не трогает записи
        ids = {e['id'] for e in self.client.get('/api/equipment').json()}
        self.assertTrue({'AMS-1', 'AMS-3'} <= ids)
        self.assertNotIn('AMS-2', ids)  # нет паспорта — нет карточки
        card = self.client.get('/api/equipment/AMS-1').json()
        self.assertEqual(card['equipment']['serial_number'], '21031004-1')
        self.assertIn('Паспорта организаторов', card['equipment']['data_origin'])
        motor = next(c for c in card['components'] if c['id'] == 'AMS-1-MOTOR')
        self.assertEqual(motor['normative_hours'], 60000)
        self.assertIn('Нет данных о наработке — оценка по моточасам невозможна.', motor['resource']['limitations'])
        self.assertTrue(any(h['note'] == 'Подтёков нет' for h in card['history']))
        self.assertEqual(self.client.post('/api/resolve', json={'code': 'KST:EQ:AMS-3', 'source': 'qr'}).json()['equipment_id'], 'AMS-3')

    def test_status_by_source_shows_measurements_separately(self):
        presets = {p['label']: p['index'] for p in telemetry.presets()}
        card = self.client.post('/api/equipment/PUMP-001/scenario', json={'index': presets['Норма']}).json()
        sources = {s['key']: s for s in card['by_source']}
        self.assertEqual(card['level'], 2)                       # общий статус красный из‑за ресурса
        self.assertEqual(sources['resource']['level'], 2)
        self.assertEqual(sources['measurements']['level'], 0)    # а измерения в норме — это видно отдельно
        card = self.client.post('/api/equipment/PUMP-001/scenario', json={'index': presets['Нагрев обмоток']}).json()
        meas = next(s for s in card['by_source'] if s['key'] == 'measurements')
        self.assertEqual(meas['level'], 2)
        self.assertTrue(any('обмоток' in t for t in meas['top']))
        self.assertIsNone(next(s for s in card['by_source'] if s['key'] == 'visual')['level'])

    def analyze_once(self, items, inspection_id=None):
        image = io.BytesIO()
        Image.new('RGB', (100, 100)).save(image, format='JPEG')

        async def inspect(*args):
            for it in items:
                yield 'item', dict(it)
            yield 'done', {'items': [dict(it) for it in items], 'scene': 'test', 'model': {'model': 'mock', 'provider': 'local'},
                           'trace': {'prompt_version': 'inspect-test', 'raw': '{"raw": true}'}}

        with patch.object(analyze, 'inspect_stream', inspect), patch.object(main.detector, 'ready', False):
            data = {'source': 'file', **({'inspection_id': inspection_id} if inspection_id else {})}
            r = self.client.post('/api/equipment/PUMP-001/analyze', data=data,
                                 files={'file': ('frame.jpg', image.getvalue(), 'image/jpeg')})
        self.assertEqual(r.status_code, 200, r.text)
        done = json.loads(r.text.split('event: done\ndata: ')[1].split('\n')[0])
        return done

    @staticmethod
    def finding(cid='PUMP-001-PUMP', category='defect'):
        return dict(key='vlm-0', component_id=cid, name=cid, box=[.1, .1, .5, .5], defect_box=None,
                    category=category, signs=['test'], visibility='visible', reason='Видимый признак',
                    recommendation='Проверить', confidence=.8)

    def begin_session(self):
        response = self.client.post('/api/equipment/PUMP-001/inspections/session')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['session']['id']

    def session(self, sid):
        response = self.client.get(f'/api/equipment/PUMP-001/inspections/session/{sid}')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['session']

    def test_two_shots_keep_decisions_coverage_and_report_images(self):
        sid = self.begin_session()
        self.assertEqual(self.begin_session(), sid)
        first = self.analyze_once([self.finding()], sid)
        decision = self.client.post('/api/equipment/PUMP-001/events', json={
            'type': 'review', 'component_id': 'PUMP-001-PUMP', 'analysis_id': first['analysis_id'], 'verdict': 'deferred'})
        self.assertEqual(decision.status_code, 200, decision.text)
        second = self.analyze_once([self.finding('PUMP-001-MOTOR')], sid)
        restored = self.client.get('/api/equipment/PUMP-001/inspections/session').json()['session']
        self.assertEqual(restored['id'], sid)
        self.assertEqual(restored['covered'], 2)
        self.assertEqual(restored['shots'][0]['items'][0]['verdict'], 'deferred')
        self.assertNotIn('PUMP-001-PUMP', [c['id'] for c in restored['not_covered']])
        saved = self.client.post('/api/equipment/PUMP-001/inspections', json={'inspection_id': sid}).json()
        iid = saved['saved_inspection_id']
        snapshot = db.loads(store.inspection('PUMP-001', iid)['data_json'], {})
        self.assertEqual(snapshot['analysis_ids'], [first['analysis_id'], second['analysis_id']])
        self.assertEqual(len(snapshot['image_paths']), 2)
        self.assertEqual(snapshot['review']['open'], 2)
        pdf = self.client.get(f'/api/equipment/PUMP-001/inspections/{iid}/report.pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        import openpyxl
        xlsx = self.client.get(f'/api/equipment/PUMP-001/inspections/{iid}/report.xlsx')
        book = openpyxl.load_workbook(io.BytesIO(xlsx.content))
        self.assertIn('Кадр 1', book.sheetnames)
        self.assertIn('Кадр 2', book.sheetnames)
        self.assertEqual(book['Находки ИИ'].max_row, 3)
        book.close()

    def test_conflicting_new_frame_does_not_remove_earlier_finding(self):
        sid = self.begin_session()
        first = self.analyze_once([self.finding()], sid)
        self.analyze_once([self.finding(category='ok')], sid)
        view = self.session(sid)
        self.assertEqual(view['review']['open'], 1)
        self.assertEqual(len(view['review']['findings']), 2)
        self.assertEqual(view['conflicts'][0]['component_id'], 'PUMP-001-PUMP')
        self.assertEqual(view['shots'][0]['analysis_id'], first['analysis_id'])

    def test_failed_followup_preserves_session_and_first_frame(self):
        sid = self.begin_session()
        first = self.analyze_once([self.finding()], sid)
        async def fail(*args):
            raise main.llm.LLMUnavailable('Плохой второй кадр')
            yield
        image = io.BytesIO(); Image.new('RGB', (100, 100)).save(image, format='JPEG')
        with patch.object(analyze, 'inspect_stream', fail), patch.object(main.detector, 'ready', False):
            response = self.client.post('/api/equipment/PUMP-001/analyze', data={'source': 'file', 'inspection_id': sid},
                                        files={'file': ('frame.jpg', image.getvalue(), 'image/jpeg')})
        self.assertIn('event: error', response.text)
        view = self.session(sid)
        self.assertEqual([s['analysis_id'] for s in view['shots']], [first['analysis_id']])
        self.assertEqual(view['review']['open'], 1)

    def test_session_owner_finalization_and_retry_are_safe(self):
        sid = self.begin_session()
        first = self.analyze_once([self.finding()], sid)
        self.login('master')
        self.assertEqual(self.client.get(f'/api/equipment/PUMP-001/inspections/session/{sid}').status_code, 403)
        forbidden = self.client.post('/api/equipment/PUMP-001/events', json={'type': 'checked',
            'component_id': 'PUMP-001-PUMP', 'analysis_id': first['analysis_id'], 'verdict': 'rejected'})
        self.assertEqual(forbidden.status_code, 403)
        self.login('engineer')
        response = self.client.post('/api/equipment/PUMP-001/inspections', json={'inspection_id': sid})
        iid = response.json()['saved_inspection_id']
        before = store.inspection('PUMP-001', iid)['data_json']
        repeated = self.client.post('/api/equipment/PUMP-001/inspections', json={'inspection_id': sid})
        self.assertEqual(repeated.json()['saved_inspection_id'], iid)
        self.assertEqual(self.session(sid)['status'], 'closed')
        image = io.BytesIO(); Image.new('RGB', (100, 100)).save(image, format='JPEG')
        response = self.client.post('/api/equipment/PUMP-001/analyze', data={'source': 'file', 'inspection_id': sid},
                                    files={'file': ('frame.jpg', image.getvalue(), 'image/jpeg')})
        self.assertEqual(response.status_code, 409)
        decision = self.client.post('/api/equipment/PUMP-001/events', json={'type': 'checked',
            'component_id': 'PUMP-001-PUMP', 'analysis_id': first['analysis_id'], 'verdict': 'rejected'})
        self.assertEqual(decision.status_code, 409)
        self.assertEqual(store.inspection('PUMP-001', iid)['data_json'], before)
        self.assertNotEqual(self.begin_session(), sid)

    def test_verdicts_trace_and_review_in_report(self):
        item = dict(key='vlm-0', component_id='PUMP-001-PUMP', name='Насос', box=[.1, .1, .5, .5], defect_box=None,
                    category='defect', signs=['подтёки'], visibility='visible', reason='Подтёки под корпусом',
                    recommendation='Проверить уплотнение', confidence=.8)
        done = self.analyze_once([item])
        aid = done['analysis_id']
        self.assertIn('PUMP-001-GAUGE', [n['id'] for n in done['not_covered']])
        self.assertNotIn('PUMP-001-BRG', [n['id'] for n in done['not_covered']])     # внутренний узел камере не нужен
        row = db.query_one('SELECT prompt_version, raw_response, provider FROM analyses WHERE id=?', (aid,))
        self.assertEqual((row['prompt_version'], row['provider']), ('inspect-test', 'local'))
        # решение без анализа или по несуществующему вердикту — отказ
        bad = {'type': 'defect', 'component_id': 'PUMP-001-PUMP', 'verdict': 'confirmed'}
        self.assertEqual(self.client.post('/api/equipment/PUMP-001/events', json=bad).status_code, 400)
        self.assertEqual(self.client.post('/api/equipment/PUMP-001/events',
                                          json={**bad, 'analysis_id': aid, 'verdict': 'maybe'}).status_code, 400)
        r = self.client.post('/api/equipment/PUMP-001/events', json={**bad, 'analysis_id': aid, 'note': 'Течь подтверждена'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get(f'/api/analyses/{aid}').json()['result']['items'][0]['verdict'], 'confirmed')
        saved = self.client.post('/api/equipment/PUMP-001/inspections', json={'analysis_id': aid}).json()
        iid = saved['saved_inspection_id']
        snap = db.loads(db.query_one('SELECT data_json FROM events WHERE id=?', (iid,))['data_json'], {})
        self.assertEqual((snap['review']['confirmed'], snap['review']['open']), (1, 0))
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(self.client.get(f'/api/equipment/PUMP-001/inspections/{iid}/report.xlsx').content))
        self.assertIn('Находки ИИ', book.sheetnames)
        self.assertEqual(book['Находки ИИ']['D2'].value, 'Подтверждено')

    def test_login_rate_limit(self):
        from app import auth
        try:
            for _ in range(auth.LoginLimiter.MAX):
                self.assertEqual(self.client.post('/api/auth/login', json={'username': 'master', 'password': 'x'}).status_code, 401)
            r = self.client.post('/api/auth/login', json={'username': 'master', 'password': self.passwords['master']})
            self.assertEqual(r.status_code, 429)
            self.login('engineer')   # другая учётка не заблокирована
        finally:
            auth.limiter._fails.clear()

    def test_config_validation(self):
        with patch.object(config, 'VLM_PROVIDER', 'cloud'), patch.object(config, 'HTTP_PORT', config.PORT):
            with self.assertRaises(config.ConfigError) as err:
                config.validate()
        self.assertIn('VLM_PROVIDER', str(err.exception))
        self.assertIn('совпадают', str(err.exception))
        config.validate()

    def test_migration_from_database_before_numbering(self):
        import sqlite3
        path = Path(self.folder.name) / 'old.db'
        old = sqlite3.connect(path)
        old.executescript(db.SCHEMA)
        old.execute("INSERT INTO equipment (id, name, type) VALUES ('PUMP-001', 'Насосный агрегат НА-1', 'насос')")
        old.execute("INSERT INTO components (id, equipment_id, name, kind) VALUES ('PUMP-001-BRG', 'PUMP-001', 'Подшипник', 'part')")
        old.execute("INSERT INTO events (equipment_id, type, created_at) VALUES ('PUMP-001', 'checked', '2026-11-01T10:00:00+03:00')")
        old.commit()
        db.migrate(old)
        self.assertEqual(old.execute('PRAGMA user_version').fetchone()[0], len(db.MIGRATIONS))
        self.assertEqual(old.execute("SELECT created_at FROM events").fetchone()[0], '2026-11-01T07:00:00.000000+00:00')
        self.assertEqual(old.execute("SELECT name FROM equipment").fetchone()[0], 'Центробежный насос НА-1')
        self.assertEqual(old.execute("SELECT visual_access FROM components").fetchone()[0], 'internal')
        self.assertIn('prompt_version', [r[1] for r in old.execute('PRAGMA table_info(analyses)')])
        db.migrate(old)   # повторный запуск ничего не ломает
        old.close()


if __name__ == '__main__':
    unittest.main()
