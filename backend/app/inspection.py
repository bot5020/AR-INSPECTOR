"""Сводка осмотра: все кадры и решения сохраняются, покрытие узлов накапливается."""
from . import assess, db, store


def summary(session: dict, card: dict) -> dict:
    shots, findings, seen, categories = [], [], set(), {}
    for number, row in enumerate(store.session_shots(session['id']), 1):
        data = db.loads(row['result_json'], {})
        decisions = store.verdicts(session['equipment_id'], row['id'])
        items = []
        for raw in data.get('items', []):
            item = {**raw, 'analysis_id': row['id'], 'shot_number': number}
            cid = item.get('component_id')
            decision = decisions.get(cid, {})
            item['verdict'] = decision.get('verdict')
            items.append(item)
            if cid:
                if item.get('visibility') != 'occluded':
                    seen.add(cid)
                categories.setdefault(cid, set()).add(item['category'])
            findings.append({**item, 'note': decision.get('note'), 'author': decision.get('author')})
        shots.append({'analysis_id': row['id'], 'number': number, 'created_at': row['created_at'],
                      'image_url': f"/api/analyses/{row['id']}/image", 'image_path': row['image_path'],
                      'items': items, 'partial': bool(data.get('partial')), 'measurement': data.get('measurement')})
    attention = [f for f in findings if f['category'] in ('check', 'defect')]
    missing = assess.not_covered(card, [{'component_id': cid} for cid in seen])
    names = {c['id']: c['name'] for c in card['components']}
    conflicts = [{'component_id': cid, 'name': names.get(cid, cid)} for cid, values in categories.items()
                 if 'ok' in values and values.intersection({'check', 'defect'})]
    review = {'findings': findings, 'not_covered': missing,
              'open': sum(f.get('verdict') not in ('confirmed', 'rejected') for f in attention),
              'confirmed': sum(f.get('verdict') == 'confirmed' for f in attention),
              'rejected': sum(f.get('verdict') == 'rejected' for f in attention),
              'partial': any(s['partial'] for s in shots), 'conflicts': conflicts}
    return {**session, 'shots': shots, 'review': review, 'not_covered': missing,
            'covered': len(seen), 'total': len(seen) + len(missing), 'conflicts': conflicts}
