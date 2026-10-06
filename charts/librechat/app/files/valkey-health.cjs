'use strict';

const { readFileSync, writeFileSync, rmSync, existsSync } = require('node:fs');

const runIdFile = '/tmp/librechat-valkey-run-id';
const outageFile = '/tmp/librechat-valkey-outage';

async function probe(mode, { httpOk, getRunId, files }) {
  if (!(await httpOk(mode === 'startup' ? '/api/admin/oauth/openid/check' : mode === 'readiness' ? '/readyz' : '/livez'))) {
    return false;
  }

  let runId;
  try {
    runId = await getRunId();
  } catch {
    if (mode === 'startup') return false;
    files.write(outageFile, '1');
    // Keep the process alive while Valkey is down; readiness removes traffic.
    return mode === 'liveness';
  }

  if (mode === 'startup') {
    files.write(runIdFile, runId);
    files.remove(outageFile);
    return true;
  }
  // An outage or a new Valkey process invalidates the app's Redis clients.
  return files.exists(runIdFile) && !files.exists(outageFile) && files.read(runIdFile) === runId;
}

async function main() {
  const mode = process.argv[2];
  if (!['startup', 'readiness', 'liveness'].includes(mode)) process.exit(1);
  const files = {
    exists: existsSync,
    read: (path) => readFileSync(path, 'utf8'),
    write: (path, value) => writeFileSync(path, value, { mode: 0o600 }),
    remove: (path) => rmSync(path, { force: true }),
  };
  const httpOk = async (path) => {
    const response = await fetch(`http://127.0.0.1:${process.env.PORT}${path}`, {
      signal: AbortSignal.timeout(1500),
    });
    return response.status === 200;
  };
  const getRunId = async () => {
    const Redis = require('ioredis');
    const redis = new Redis(process.env.REDIS_URI, {
      lazyConnect: true,
      connectTimeout: 1500,
      maxRetriesPerRequest: 0,
      retryStrategy: null,
      enableOfflineQueue: false,
    });
    redis.on('error', () => {}); // Do not log errors that might include the URI.
    try {
      await redis.connect();
      const info = await redis.info('server');
      const runId = /^run_id:([a-f0-9]+)\r?$/m.exec(info)?.[1];
      if (!runId) throw new Error('Missing Valkey run ID');
      return runId;
    } finally {
      redis.disconnect();
    }
  };
  try {
    if (!(await probe(mode, { files, httpOk, getRunId }))) process.exitCode = 1;
  } catch {
    // Never print a credential-bearing Redis or HTTP error.
    process.exitCode = 1;
  }
}

if (require.main === module) main();

module.exports = { probe };
