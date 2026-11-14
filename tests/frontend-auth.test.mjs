import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';

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

test('Repeated login requests preserve the existing form and typed password', async () => {
  let paints = 0, form = null;
  const sheet = { hidden: true, classList: { contains: () => !sheet.hidden } };
  const equipment = await load('equipment.js', {
    './state.js': { el: { sheet }, state: {} },
    './util.js': { $: selector => selector === '#loginForm' ? form : null },
    './ui.js': { openSheet: () => { paints++; sheet.hidden = false; form = { password: '' }; } },
  });
  equipment.openLogin();
  const original = form;
  form.password = '12';
  equipment.openLogin();
  equipment.openLogin();
  assert.equal(paints, 1);
  assert.equal(form, original);
  assert.equal(form.password, '12');
});

test('A stale 401 cannot reopen login after a successful sign-in', async () => {
  const state = { user: null };
  let showLogin = 0, resolve;
  const unauthorized = { ok: false, status: 401, json: async () => ({ detail: 'Sign in' }) };
  const net = await load('net.js', {
    './state.js': { state }, './equipment.js': { openLogin: () => showLogin++ },
  }, { fetch: () => new Promise(r => { resolve = r; }) });
  const pending = net.api('/api/detect');
  const rejection = assert.rejects(pending, /Sign in/);
  state.user = { username: 'engineer' };
  const signedIn = state.user;
  resolve(unauthorized);
  await rejection;
  assert.equal(showLogin, 0);
  assert.equal(state.user, signedIn);
  const expired = net.api('/api/equipment');
  const expiredRejection = assert.rejects(expired, /Sign in/);
  resolve(unauthorized);
  await expiredRejection;
  assert.equal(state.user, null);
  assert.equal(showLogin, 1);
});

test('Camera loops send no protected requests before sign-in', async () => {
  for (const name of ['qrLoop', 'detectLoop']) {
    let sleeps = 0, captures = 0;
    const stop = new Error('stop test loop');
    const live = await load('live.js', {
      './state.js': { state: { user: null, camera: true, status: { detector: { ready: true } } }, el: { sheet: { hidden: true } } },
      './camera.js': { captureBlob: () => { captures++; return Promise.resolve({}); } },
      './util.js': { sleep: async () => { if (++sleeps > 1) throw stop; } },
    }, { window: {}, document: { hidden: false } });
    await assert.rejects(live[name](), error => error === stop);
    assert.equal(captures, 0, name);
  }
});
