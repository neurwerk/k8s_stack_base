'use strict';

// This adapter is tested against the chart's exact upstream image, not a stable CLI.
async function provisionPermissions({ mongoose, schemas, api, CacheKeys }) {
  const { createModels, createMethods, scopedCacheKey, getTenantId } = schemas;
  if (getTenantId() !== undefined || !api.cacheConfig.USE_REDIS) {
    throw new Error('Expected the application base tenant and shared Redis cache');
  }
  await api.waitForKeyvRedisClient();
  if (!api.keyvRedisClient?.isReady) {
    throw new Error('Shared Redis is not ready');
  }
  createModels(mongoose);
  const cache = api.standardCache(CacheKeys.ROLES);
  const uncached = createMethods(mongoose);
  const methods = createMethods(mongoose, {
    getCache: (key) => {
      if (key !== CacheKeys.ROLES) {
        throw new Error('Unexpected cache namespace');
      }
      return cache;
    },
  });
  const { isDeepStrictEqual } = require('node:util');
  for (const name of ['USER', 'ADMIN']) {
    // Uncached upstream lookup seeds only missing roles; do not backfill or
    // migrate unrelated permission scopes on existing roles.
    const before = await uncached.getRoleByName(name);
    if (!before || before.tenantId != null) {
      throw new Error('Expected a base role');
    }
    const policy = {
      AGENTS: { USE: true, CREATE: name === 'ADMIN', SHARE: name === 'ADMIN', SHARE_PUBLIC: name === 'ADMIN' },
      MARKETPLACE: { USE: true },
    };
    const updates = {};
    const expected = structuredClone(before.permissions ?? {});
    for (const [scope, fields] of Object.entries(policy)) {
      expected[scope] = { ...expected[scope], ...fields };
      for (const [field, value] of Object.entries(fields)) {
        updates[`permissions.${scope}.${field}`] = value;
      }
    }
    // Unlike updateAccessPermissions, this upstream method propagates errors.
    // Dotted $set paths preserve every unrelated field and refresh the role cache.
    await methods.updateRoleByName(name, updates);
    const stored = await mongoose.models.Role.findOne({ name }).lean().exec();
    const cached = await cache.get(scopedCacheKey(name));
    const effective = await methods.getRoleByName(name);
    for (const role of [stored, cached, effective]) {
      if (role?.name !== name || role.tenantId != null ||
          !isDeepStrictEqual(role.permissions, expected)) {
        throw new Error('Role database/cache verification failed');
      }
    }
    console.log(`Verified ${name}: ${JSON.stringify(policy)}`);
  }
}

async function main() {
  const deadline = setTimeout(() => {
    console.error('Permission provisioning deadline exceeded');
    process.exit(1);
  }, 240000);
  try {
    const mongoose = require('mongoose');
    const schemas = require('@librechat/data-schemas');
    // Upstream connection errors can contain credential-bearing URIs.
    schemas.logger.silent = true;
    const api = require('@librechat/api');
    const { CacheKeys } = require('librechat-data-provider');
    for (let attempt = 1; attempt <= 12; attempt += 1) {
      try {
        if (mongoose.connection.readyState !== 1) {
          await mongoose.connect(process.env.MONGO_URI, {
            bufferCommands: false,
            autoIndex: false,
            autoCreate: false,
            maxPoolSize: 2,
            maxConnecting: 1,
            serverSelectionTimeoutMS: 5000,
            connectTimeoutMS: 5000,
            socketTimeoutMS: 10000,
          });
        }
        await provisionPermissions({ mongoose, schemas, api, CacheKeys });
        console.log('Permission provisioning completed');
        clearTimeout(deadline);
        process.exit(0);
      } catch {
        console.error(`Permission provisioning attempt ${attempt}/12 failed`);
        if (attempt < 12) {
          await new Promise((resolve) => setTimeout(resolve, 5000));
        }
      }
    }
  } catch {
    console.error('Permission provisioning module/configuration failure');
  }
  clearTimeout(deadline);
  process.exit(1);
}

module.exports = { provisionPermissions };
if (require.main === module) {
  void main();
}
