import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';
import * as geometry from '../frontend/view-geometry.mjs';

async function load(file, imports, globals = {}) {
  const source = await fs.readFile(new URL(`../frontend/js/${file}`, import.meta.url), 'utf8');
  const context = vm.createContext(globals);
  const module = new vm.SourceTextModule(source, { context });
  await module.link(specifier => {
    const match = [...source.matchAll(/import\s*\{([^}]+)\}\s*from\s*['"]([^'"]+)['"]/g)].find(m => m[2] === specifier);
    const names = match[1].split(',').map(n => n.trim().split(/\s+as\s+/)[0]);
    return new vm.SyntheticModule(names, function () {
      for (const name of names) this.setExport(name, imports[specifier]?.[name] ?? (() => {}));
    }, { context });
  });
  await module.evaluate();
  return module.namespace;
}

test('Visual identification offers a low-confidence card with reasons and explicit confirmation', async () => {
  const util = await load('util.js', {});
  const equipment = await load('equipment.js', { './util.js': util });
  const res = { equipment_id:'PUMP-001', method:'visual', confidence:.15, what_is_it:'Электродвигатель',
    evidence:'Похож на узел насосного агрегата; шильдик не виден', equipment:{name:'Насос НА-1',model:'К 100',location:'Цех'} };
  const spec = equipment.identifySpec(res);
  assert.equal(spec.title, 'Предположительно');
  assert.match(spec.subtitle, /Электродвигатель/);
  assert.ok(spec.html.includes(res.evidence));
  assert.match(spec.html, /Низкая уверенность/);
  assert.match(spec.foot, /data-id="PUMP-001"/);
  assert.match(spec.foot, /Выбрать эту карточку/);
  assert.equal(equipment.identifySpec({...res,method:'qr',confidence:1}).title, 'Оборудование распознано');
  assert.equal(equipment.identifySpec({...res,equipment_id:null}).title, 'Не удалось распознать');
});

test('Labels avoid collisions, stay inside the viewport and use numbers when crowded', () => {
  const bounds = { left: 8, top: 100, right: 360, bottom: 650 };
  const labels = Array.from({ length: 12 }, (_, n) => ({ number: n+1, w: 190, box: [100, 250, 230, 360] }));
  const result = geometry.layoutLabels(labels, bounds);
  assert.equal(result.length, labels.length);
  assert.ok(result.some(l => l.numbered));
  for (const a of result) {
    assert.ok(a.x >= bounds.left && a.x+a.w <= bounds.right && a.y >= bounds.top && a.y+a.h <= bounds.bottom);
    for (const b of result.filter(b => b !== a))
      assert.ok(a.x+a.w <= b.x || b.x+b.w <= a.x || a.y+a.h <= b.y || b.y+b.h <= a.y);
  }
  assert.deepEqual(result, geometry.layoutLabels(labels, bounds));
});

test('Focus restores the original card, scroll, list stack and photo view', async () => {
  const card = { kind: 'component', title: 'Насос', rebuild: () => ({ title: 'Обновленный насос' }) };
  const stack = [{ title: 'Осмотр' }];
  const state = { analysis: { id: 42, items: [{ key: 'p', box: [.4,.2,.6,.5] }] }, view: { scale: 2, x: 15, y: 30 },
    stillSize: { w: 1000, h: 1000 }, frozen: true, sheetSpec: card, sheetStack: stack,
    marksVisible: false, hintHidden: false };
  const originalView = { ...state.view };
  const el = { stage: { clientWidth: 600, clientHeight: 700, dataset: {}, addEventListener() {} }, still: { style: {} },
    photoTools: { querySelector: () => ({}) }, zoomReset: {}, sheetBody: { scrollTop: 185 } };
  let restored;
  const camera = await load('camera.js', { '../view-geometry.mjs': geometry,
    './state.js': { state, el }, './ui.js': {
      closeSheet: () => { state.sheetStack = []; state.sheetSpec = null; },
      openSheet: () => { state.sheetStack = []; }, paintSheet: spec => { restored = spec; },
    } }, { window: { addEventListener() {} } });
  camera.focusItem('p');
  assert.equal(state.focusedKey, 'p');
  assert.equal(state.focusReturn.spec.scroll, 185);
  await camera.returnFromFocus();
  assert.equal(restored.title, 'Обновленный насос');
  assert.equal(restored.scroll, 185);
  assert.deepEqual(state.view, originalView);
  assert.equal(state.sheetStack[0], stack[0]);
  assert.equal(state.focusedKey, null);
  assert.equal(state.marksVisible, false);
});

test('A stale frame response cannot replace a more recently selected frame', async () => {
  const state = { eqId: 'PUMP', frameRevision: 0, inspection: { status: 'draft' } };
  const resolvers = {};
  const system = await load('system.js', {
    './state.js': { state, el: { still: {} } },
    './net.js': { api: path => new Promise(resolve => { resolvers[path] = resolve; }) },
    './camera.js': { freeze: async () => { state.frameLoading = false; return true; } },
  }, { fetch: async () => ({ ok: true, blob: async () => 'image' }) });
  const first = system.reopenAnalysis(1), second = system.reopenAnalysis(2);
  state.shownKeys = new Set();
  resolvers['/api/analyses/2']({ id: 2, image_url: '/image/2', result: { items: [{ key: 'new' }] } });
  await second;
  resolvers['/api/analyses/1']({ id: 1, image_url: '/image/1', result: { items: [{ key: 'old' }] } });
  await first;
  assert.equal(state.analysis.id, 2);
  assert.equal(state.analysis.items[0].key, 'new');
});

test('A photo label and node list share one component card and preserve its return stack', async () => {
  const fixture = JSON.parse(await fs.readFile(new URL('../experiments/ml/baseline/multiframe-live.json', import.meta.url)));
  const done = fixture.runs[0].result;
  const item = { ...done.items.find(i => i.category !== 'ok' && i.component_id), analysis_id: done.analysis_id, shot_number: 1 };
  const state = { card: done.card, analysis: { id: item.analysis_id, items: [item] }, sheetStack: [{ title: 'Осмотр' }],
    inspection: { status: 'draft', conflicts: [], review: { findings: [item] } }, user: { role: 'engineer' } };
  const el = { sheet: { hidden: false }, sheetBody: { scrollTop: 0 } };
  const util = await load('util.js', {});
  let last;
  const component = await load('component.js', {
    './state.js': { state, el }, './util.js': util,
    './inspection.js': { observations: () => state.inspection.review.findings, pendingObservations: () => [item], reviewNodes: () => done.card.components.filter(c => c.id === item.component_id) },
    './ui.js': { disc: (title,icon,tone,body) => body, statusPill: () => '',
      openSheet: spec => { last = state.sheetSpec = spec; }, paintSheet: spec => { last = state.sheetSpec = spec; } },
  });
  component.openComponent(item.component_id);
  const first = last;
  component.openItem(item);
  assert.equal(last.kind, 'component');
  assert.equal(first.title, last.title);
  assert.equal(first.html, last.html);
  assert.match(last.html, /focus-observation/);
  assert.ok(last.html.includes(`/api/analyses/${item.analysis_id}/crop/${encodeURIComponent(item.key)}`));
  assert.match(last.foot, /Не подтверждаю/);
  assert.equal(state.sheetStack.length, 1);
  state.inspection.status = 'closed';
  assert.doesNotMatch(component.componentSpec(item.component_id).foot, /observation-verdict/);
});

test('Callout names avoid all object boxes, including neighboring nodes', () => {
  const boxes = [[20,230,500,430],[410,260,510,370],[470,200,700,440],[510,350,580,430],[330,200,405,245],[670,275,720,360]];
  const labels = boxes.map((box,i)=>({box,number:i+1,w:[135,110,100,160,135,110][i]}));
  const out = geometry.layoutLabels(labels,{left:8,top:80,right:728,bottom:650});
  assert.equal(out.length, labels.length);
  for (const l of out) {
    assert.equal(l.numbered,false,'Available free space should preserve names');
    for (const [x1,y1,x2,y2] of boxes) assert.ok(l.x+l.w <= x1 || l.x>=x2 || l.y+l.h<=y1 || l.y>=y2);
    const [sx,sy,x,y] = geometry.calloutLeader(l);
    assert.ok(Number.isFinite(sx+sy));
    assert.ok(x===l.box[0] || x===l.box[2] || y===l.box[1] || y===l.box[3]);
  }
});

test('Finishing a multiframe inspection sends its session ID and opens the saved report', async () => {
  const state = { eqId: 'PUMP-001', author: 'Инженер', analysis: { id: 42 },
    inspection: { id: 4, status: 'draft', shots: [{ analysis_id: 41 }, { analysis_id: 42 }] } };
  let request, report, error, removed;
  const component = await load('component.js', {
    './state.js': { state, el: {} },
    './net.js': { postJson: async (url, body) => { request = { url, body }; return { saved_inspection_id: 73 }; } },
    './equipment.js': { openReports: id => { report = id; } },
    './ui.js': { toast: (message, kind) => { if (kind === 'warn') error = message; } },
  }, { localStorage: { getItem: () => 'PUMP-001', removeItem: key => { removed = key; } } });
  await component.saveInspection();
  assert.equal(error, undefined);
  assert.equal(request.url, '/api/equipment/PUMP-001/inspections');
  assert.equal(request.body.inspection_id, 4);
  assert.equal(request.body.analysis_id, 42);
  assert.equal(state.inspection.status, 'closed');
  assert.equal(state.inspection.shots.length, 2);
  assert.equal(state.analysis.saved, true);
  assert.equal(removed, 'inspectionEquipment');
  assert.equal(report, 73);
});

test('Failed completion preserves the draft and frames for retry', async () => {
  const state = { eqId: 'PUMP-001', analysis: { id: 42 },
    inspection: { id: 4, status: 'draft', shots: [{ analysis_id: 42 }] } };
  let warning;
  const component = await load('component.js', {
    './state.js': { state, el: {} },
    './net.js': { postJson: async () => { throw new Error('Offline'); } },
    './util.js': { humanError: e => e.message },
    './ui.js': { toast: (message, kind) => { warning = [message, kind]; } },
  });
  await component.saveInspection();
  assert.equal(state.inspection.status, 'draft');
  assert.equal(state.inspection.shots.length, 1);
  assert.equal(state.analysis.saved, undefined);
  assert.deepEqual(warning, ['Offline', 'warn']);
});

test('Invalid or absent label space never produces an unbounded grid', () => {
  const labels = [{ box: [10, 10, 100, 100], number: 1, w: 120 }];
  for (const bounds of [
    { left: 8, right: 390, top: -Infinity, bottom: Infinity },
    { left: 8, right: 390, top: 600, bottom: 100 },
    { left: 8, right: 390, top: NaN, bottom: 700 },
  ]) assert.deepEqual(geometry.layoutLabels(labels, bounds), []);
  const result = geometry.layoutLabels(labels, { left: 8, right: 390, top: 8, bottom: 700 });
  assert.equal(result.length, 1);
  assert.deepEqual(geometry.layoutLabels([{ ...labels[0], box: [0,0,NaN,20] }], { left:8,right:390,top:8,bottom:700 }), []);
});

test('Review moves through observed nodes once and keeps deferred findings in the inspection', async () => {
  const findings = [
    { component_id: 'motor', category: 'defect', verdict: 'confirmed' },
    { component_id: 'pump', category: 'check', verdict: 'deferred' },
    { component_id: 'valve', category: 'ok' },
  ];
  const state = { card: { components: [{id:'motor'},{id:'pump'},{id:'valve'},{id:'internal'}] },
    inspection: { review: { findings } }, reviewedNodes: new Set(['motor']) };
  const inspection = await load('inspection.js', { './state.js': { state } });
  assert.equal(inspection.nextReviewTarget('motor'), 'pump');
  state.reviewedNodes.add('pump');
  assert.equal(inspection.nextReviewTarget('pump'), 'valve');
  state.reviewedNodes.add('valve');
  assert.equal(inspection.nextReviewTarget('valve'), null);
  assert.equal(inspection.pendingObservations().length, 1);
});

test('Recording a finding advances only after the event and session refresh succeed', async () => {
  const item = { key: 'motor', component_id: 'motor', analysis_id: 42, category: 'defect', reason: 'Коррозия' };
  const state = { eqId:'PUMP', analysis:{id:42,items:[item]}, inspection:{status:'draft',review:{findings:[item]}} };
  const calls = [];
  const component = await load('component.js', {
    './state.js': { state, el:{} },
    './net.js': { postJson: async () => { calls.push('saved'); return { components: [] }; } },
    './inspection.js': { observations:()=>[item],refreshInspection:async()=>{calls.push('refreshed')},advanceReview:async id=>{calls.push(id)} },
  });
  await component.decideObservation(42,'motor','confirmed');
  assert.deepEqual(calls,['saved','refreshed','motor']);
});

test('Returning to the start clears the viewer but keeps the server draft untouched', async () => {
  const state = { eqId:'PUMP', analysis:{id:42}, inspection:{id:4,status:'draft'}, frozen:true, camera:false,
    selectedObservation:{key:'motor'}, auto:true, frameRevision:2 };
  let resetPhoto=false;
  const equipment = await load('equipment.js', {
    './state.js':{state,el:{}},
    './camera.js':{unfreeze:()=>{state.frozen=false;resetPhoto=true}},
    './net.js':{postJson:()=>{throw new Error('Reset must not write or delete the draft')}},
  }, {localStorage:{removeItem(){}}});
  equipment.resetEquipment();
  assert.equal(state.eqId,null);assert.equal(state.inspection,null);
  assert.equal(state.selectedObservation,null);assert.equal(state.auto,false);
  assert.equal(resetPhoto,true);assert.equal(state.frozen,false);
});

test('Starting a new inspection saves the previous draft before creating the next one', async () => {
  const state = { eqId:'PUMP', author:'Инженер', inspection:{id:4,status:'draft',shots:[{analysis_id:42}]} };
  const calls=[];
  const equipment = await load('equipment.js', {
    './state.js':{state,el:{}},
    './net.js':{postJson:async(url,payload)=>{calls.push(['save',payload.inspection_id]);return {saved_inspection_id:73}}},
    './inspection.js':{ensureInspection:async()=>{assert.equal(state.inspection,null);calls.push(['new']);state.inspection={id:5,status:'draft',shots:[]}}},
    './camera.js':{unfreeze:()=>{calls.push(['clear-photo'])}},
  });
  await equipment.newInspection();
  assert.deepEqual(calls,[['save',4],['clear-photo'],['new']]);
  assert.equal(state.inspection.id,5);
});

test('Failed save while starting another inspection keeps the existing draft and viewer', async () => {
  const draft={id:4,status:'draft',shots:[{analysis_id:42}]};
  const state={eqId:'PUMP',inspection:draft};
  const equipment=await load('equipment.js',{
    './state.js':{state,el:{}},
    './net.js':{postJson:async()=>{throw new Error('Offline')}},
    './camera.js':{unfreeze:()=>assert.fail('Must preserve the photo')},
    './inspection.js':{ensureInspection:()=>assert.fail('Must not create a new draft')},
  });
  await assert.rejects(equipment.newInspection(),/Offline/);
  assert.equal(state.inspection,draft);
});

test('Component header keeps navigation above the body and current photo evidence precedes unique historical notes', async () => {
  const fixture = JSON.parse(await fs.readFile(new URL('../experiments/ml/baseline/multiframe-live.json', import.meta.url)));
  const done = structuredClone(fixture.runs[0].result);
  const item = { ...done.items.find(i => i.category !== 'ok' && i.component_id), analysis_id: done.analysis_id, shot_number: 1,
    reason: 'На текущем кадре видна коррозия' };
  const c = done.card.components.find(c => c.id === item.component_id);
  const old = { source:'defect',level:2,text:'Торцевое уплотнение вала: Открытое замечание: Следы влаги',component_id:'SEAL' };
  c.reasons = [old, {...old}]; c.rollup_reasons = c.reasons;
  const state = {card:done.card,analysis:{id:item.analysis_id,items:[item]},
    inspection:{status:'draft',conflicts:[],review:{findings:[item]}},user:{role:'engineer'}};
  const util = await load('util.js',{});
  const component = await load('component.js',{
    './state.js':{state,el:{}},'./util.js':util,
    './inspection.js':{observations:()=>[item],pendingObservations:()=>[item],reviewNodes:()=>[c]},
    './ui.js':{statusPill:()=>'<span>Требует действий</span>',disc:(title,icon,tone,body)=>`<details><summary>${title}</summary>${body}</details>`},
  });
  const spec = component.componentSpec(c.id,item);
  assert.equal(spec.subtitle,'');
  assert.match(spec.headerHtml,/Требует действий/);
  assert.match(spec.headerActionsHtml,/Узлы осмотра/);
  assert.match(spec.headerHtml,/Ждёт решения/);
  assert.doesNotMatch(spec.html,/Ждёт решения|node-verdict/);
  assert.doesNotMatch(spec.html,/node-top|сводный статус|Открытое замечание:/);
  assert.equal((spec.html.match(/Следы влаги/g)||[]).length,1);
  assert.ok(spec.html.indexOf('На текущем кадре видна коррозия')<spec.html.indexOf('Следы влаги'));
});
