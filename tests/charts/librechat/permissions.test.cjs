'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { provisionPermissions } = require('../../../charts/librechat/app/files/provision-permissions.cjs');

function fixture({ fresh = false, fault } = {}) {
  const db = {};
  const cached = {};
  const seed = (name) => ({ name, permissions: {
    AGENTS: { USE: false, CREATE: true, SHARE: true, SHARE_PUBLIC: true, OTHER: false },
    MARKETPLACE: { USE: false, OTHER: true },
    PROMPTS: { USE: false, CREATE: true },
  } });
  if (!fresh) {
    for (const name of ['USER', 'ADMIN']) db[name] = seed(name);
  }
  const mongoose = { models: { Role: {
    findOne: ({ name }) => ({ lean: () => ({ exec: async () => structuredClone(db[name]) }) }),
  } } };
  const cache = { get: async (key) => structuredClone(cached[key]) };
  const schemas = {
    createModels: () => {}, getTenantId: () => undefined, scopedCacheKey: (name) => name,
    createMethods: (_, deps = {}) => ({
      getRoleByName: async (name) => {
        if (deps.getCache) return (await deps.getCache('roles').get(name)) ?? db[name];
        return structuredClone(db[name] ??= seed(name));
      },
      updateRoleByName: async (name, updates) => {
        assert.equal(Object.keys(updates).length, 5);
        assert.ok(Object.keys(updates).every((key) => /^permissions\.(AGENTS\.(USE|CREATE|SHARE|SHARE_PUBLIC)|MARKETPLACE\.USE)$/.test(key)));
        if (fault === 'throw') throw new Error('write failed');
        if (fault === 'swallow') return;
        for (const [key, value] of Object.entries(updates)) {
          const [, scope, field] = key.split('.');
          db[name].permissions[scope][field] = value;
        }
        if (fault !== 'cache') cached[name] = structuredClone(db[name]);
        if (fault === 'unrelated') db[name].permissions.PROMPTS.USE = true;
      },
    }),
  };
  const api = {
    cacheConfig: { USE_REDIS: true }, waitForKeyvRedisClient: async () => {},
    keyvRedisClient: { isReady: true }, standardCache: () => cache,
  };
  return { mongoose, schemas, api, CacheKeys: { ROLES: 'roles' }, db, cached };
}

test('fresh and existing roles converge, preserve other fields, and remain idempotent', async () => {
  for (const fresh of [false, true]) {
    const state = fixture({ fresh });
    await provisionPermissions(state);
    const first = structuredClone(state.db);
    await provisionPermissions(state);
    assert.deepEqual(state.db, first);
    assert.deepEqual(state.cached, state.db);
    for (const name of ['USER', 'ADMIN']) {
      assert.deepEqual(state.db[name].permissions, {
        AGENTS: { USE: true, CREATE: name === 'ADMIN', SHARE: name === 'ADMIN', SHARE_PUBLIC: name === 'ADMIN', OTHER: false },
        MARKETPLACE: { USE: true, OTHER: true }, PROMPTS: { USE: false, CREATE: true },
      });
    }
  }
});

test('thrown or swallowed database errors, stale cache, and unrelated changes fail', async () => {
  for (const fault of ['throw', 'swallow', 'cache', 'unrelated']) {
    await assert.rejects(provisionPermissions(fixture({ fault })));
  }
});

test('non-base tenant and missing shared Redis readiness fail closed', async () => {
  for (const field of ['tenant', 'memory', 'readiness']) {
    const state = fixture();
    if (field === 'tenant') state.schemas.getTenantId = () => 'other';
    if (field === 'memory') state.api.cacheConfig.USE_REDIS = false;
    if (field === 'readiness') state.api.keyvRedisClient.isReady = false;
    await assert.rejects(provisionPermissions(state));
  }
});

test('runner bounds retries, exits nonzero, and never logs credential-bearing errors', async () => {
  const { readFileSync } = require('node:fs');
  const { runInNewContext } = require('node:vm');
  const source = readFileSync(require.resolve('../../../charts/librechat/app/files/provision-permissions.cjs'), 'utf8');
  const exits = [];
  const messages = [];
  let attempts = 0;
  let deadline;
  const mongoose = {
    connection: { readyState: 0 },
    connect: async () => { attempts++; throw new Error('credential-bearing-URI'); },
  };
  await runInNewContext(`${source}\nmain();`, {
    module: { exports: {} },
    require: (name) => {
      if (name === 'mongoose') return mongoose;
      if (name === '@librechat/data-schemas') return { logger: {} };
      return {};
    },
    process: { env: {}, exit: (code) => exits.push(code) },
    console: { error: (message) => messages.push(message) },
    setTimeout: (callback, delay) => {
      if (delay === 240000) deadline = callback;
      else { assert.equal(delay, 5000); callback(); }
    },
    clearTimeout: () => {},
  });
  assert.equal(attempts, 12);
  assert.deepEqual(exits, [1]);
  assert.ok(messages.every((message) => !message.includes('credential-bearing-URI')));
  deadline();
  assert.deepEqual(exits, [1, 1]);
});
