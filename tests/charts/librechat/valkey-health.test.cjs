'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { probe } = require('../../../charts/librechat/app/files/valkey-health.cjs');

function fixture() {
  const storage = new Map();
  let runId = 'first';
  let available = true;
  let httpHealthy = true;
  const dependencies = {
    httpOk: async () => httpHealthy,
    getRunId: async () => {
      if (!available) throw new Error('credential-bearing-URI');
      return runId;
    },
    files: {
      exists: (key) => storage.has(key),
      read: (key) => storage.get(key),
      write: (key, value) => storage.set(key, value),
      remove: (key) => storage.delete(key),
    },
  };
  return {
    check: (mode) => probe(mode, dependencies),
    setAvailable: (value) => { available = value; },
    setRunId: (value) => { runId = value; },
    setHttpHealthy: (value) => { httpHealthy = value; },
  };
}

test('startup waits for Valkey and keeps ordinary probes healthy', async () => {
  const state = fixture();
  state.setAvailable(false);
  assert.equal(await state.check('startup'), false);
  state.setAvailable(true);
  assert.equal(await state.check('startup'), true);
  assert.equal(await state.check('readiness'), true);
  assert.equal(await state.check('liveness'), true);
  state.setHttpHealthy(false);
  assert.equal(await state.check('readiness'), false);
  assert.equal(await state.check('liveness'), false);
});

test('outage removes traffic, then restarts only after Valkey returns', async () => {
  const state = fixture();
  assert.equal(await state.check('startup'), true);
  state.setAvailable(false);
  assert.equal(await state.check('readiness'), false);
  assert.equal(await state.check('liveness'), true);
  state.setAvailable(true);
  assert.equal(await state.check('readiness'), false);
  assert.equal(await state.check('liveness'), false);
  // Startup runs again after the container restart and clears the marker.
  assert.equal(await state.check('startup'), true);
  assert.equal(await state.check('readiness'), true);
  assert.equal(await state.check('liveness'), true);
});

test('even an outage between probes is detected by Valkey run ID', async () => {
  const state = fixture();
  assert.equal(await state.check('startup'), true);
  state.setRunId('restarted');
  assert.equal(await state.check('readiness'), false);
  assert.equal(await state.check('liveness'), false);
  assert.equal(await state.check('startup'), true);
  assert.equal(await state.check('readiness'), true);
});
