from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import defects
from app.vision import analyze


def param(key, level, value=1.0, name='Канал'):
    return {'id': f'ORG-{key}', 'name': name, 'value': value, 'unit': 'мм/с', 'level': level,
            'status_text': 'выше предупредительной уставки'}


class Taxonomy(unittest.TestCase):
    def test_only_known_signs_in_russian(self):
        self.assertEqual(defects.normalize_signs(['corrosion', 'paint_damage', 'rust everywhere', 'коррозия', 'Течь, подтёки']),
                         ['corrosion', 'paint_damage', 'leak'])
        item = analyze.normalize_item({'component_id': 'X', 'bbox': [0, 0, 500, 500], 'visibility': 'visible',
                                       'category': 'defect', 'signs': ['corrosion', 'made_up'], 'reason': 'r',
                                       'recommendation': '', 'defect_bbox': [], 'confidence': 0.9}, 0,
                                      {'X': {'id': 'X', 'name': 'Узел'}})
        self.assertEqual(item['signs'], ['коррозия'])

    def test_schema_restricts_signs(self):
        items = analyze.SCHEMA_INSPECT['properties']['components']['items']['properties']['signs']['items']
        self.assertEqual(set(items['enum']), set(defects.CLASSES))
        self.assertIn('seal_leak —', analyze.SYSTEM_INSPECT)


class Combined(unittest.TestCase):
    names = {'PUMP-001-SEAL': 'Торцевое уплотнение вала', 'PUMP-001-COUP': 'Муфта'}

    def test_needs_both_sensor_and_visual(self):
        seal = {'PUMP-001-SEAL': {'category': 'defect', 'signs': ['течь уплотнения вала']}}
        self.assertEqual(defects.combined([param('hf', 0)], seal, self.names), [])
        self.assertEqual(defects.combined([param('hf', 1)], {}, self.names), [])
        hit = defects.combined([param('hf', 1, 3.1, 'Высокочастотный шум')], seal, self.names)
        self.assertEqual([d['id'] for d in hit], ['C2'])
        self.assertEqual(hit[0]['sources'], 'камера + датчики')
        self.assertIn('Торцевое уплотнение вала: течь уплотнения вала', hit[0]['visual'])

    def test_ok_visual_and_sensor_only_rule(self):
        coup = {'PUMP-001-COUP': {'category': 'ok', 'signs': ['нет или ослаблен крепёж']}}
        self.assertEqual(defects.combined([param('v2', 2)], coup, self.names), [])
        both = defects.combined([param('hf', 1), param('bearing_temp', 2)], {}, self.names)
        self.assertEqual([d['id'] for d in both], ['C1'])
        self.assertEqual(both[0]['sources'], 'датчики')


if __name__ == '__main__':
    unittest.main()
