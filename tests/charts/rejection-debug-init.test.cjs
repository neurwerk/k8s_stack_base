const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const test = require("node:test");
const { assertProject, databaseUrl, initialize, verifyMemberships } = require(
  "../../charts/langfuse/files/rejection-debug-init.cjs"
);

const id = "rejected-request-debug";
const env = {
  LANGFUSE_INIT_PROJECT_PUBLIC_KEY: "debug-public",
  LANGFUSE_INIT_PROJECT_SECRET_KEY: "debug-secret",
  NORMAL_PROJECT_PUBLIC_KEY: "normal-public",
  NORMAL_ORG_ID: "normal-org",
  LANGFUSE_INIT_USER_EMAIL: "operator@example.com",
  DATABASE_HOST: "database",
  DATABASE_NAME: "langfuse",
  DATABASE_USERNAME: "langfuse",
  DATABASE_PASSWORD: "synthetic-password",
};
const project = { data: [{ id, name: id, organization: { id, name: id } }] };

function database(overrides = {}) {
  const data = {
    user: { id: "operator" }, normalMember: { role: "OWNER" },
    project: { orgId: id, deletedAt: null, name: id }, org: { name: id },
    orgMembers: [{ userId: "operator", role: "OWNER" }], projectMembers: [],
    invitation: null, otherProject: null, key: { projectId: id, scope: "PROJECT" },
    ...overrides,
  };
  return {
    user: { findUnique: async () => data.user },
    organization: { findUnique: async () => data.org },
    organizationMembership: {
      findUnique: async () => data.normalMember, findMany: async () => data.orgMembers,
    },
    project: { findUnique: async () => data.project, findFirst: async () => data.otherProject },
    projectMembership: { findMany: async () => data.projectMembers },
    membershipInvitation: { findFirst: async () => data.invitation },
    apiKey: { findUnique: async () => data.key },
  };
}

test("identity verification rejects existing unrelated authority without mutation", async () => {
  await verifyMemberships(database({ project: null, orgMembers: [], key: null }), env, false);
  await verifyMemberships(database(), env, true);
  for (const override of [
    { user: null }, { normalMember: { role: "MEMBER" } },
    { org: { name: "unexpected" } },
    { project: { orgId: "normal-org" } }, { project: { orgId: id, deletedAt: new Date() } },
    { orgMembers: [{ userId: "other", role: "OWNER" }] },
    { projectMembers: [{ userId: "other", role: "VIEWER" }] },
    { key: { projectId: "normal-project", scope: "PROJECT" } },
    { key: { projectId: id, scope: "ORGANIZATION" } },
    { invitation: { id: "pending" } }, { otherProject: { id: "unrelated" } },
  ]) {
    await assert.rejects(verifyMemberships(database(override), env, false));
  }
  await assert.rejects(verifyMemberships(database({ orgMembers: [] }), env, true));
  await assert.rejects(verifyMemberships(database({ project: null }), env, true));
  await assert.rejects(verifyMemberships(database({ key: null }), env, true));
});

test("API project verification requires one exact project and organization", () => {
  assertProject(project);
  for (const body of [{}, { data: [] }, { data: [project.data[0], project.data[0]] },
                      { data: [{ id, organization: { id: "normal-org" } }] }]) {
    assert.throws(() => assertProject(body));
  }
});

test("preflight rejects collisions before child startup", async () => {
  let started = false;
  const options = {
    env, report: () => {}, start: () => { started = true; },
    verify: async () => verifyMemberships(database({ key: { projectId: "normal-project", scope: "PROJECT" } }), env, false),
    request: async () => assert.fail("must not authenticate before creation"),
  };
  await assert.rejects(initialize(options));
  await assert.rejects(initialize({ ...options, env: { ...env, LANGFUSE_INIT_PROJECT_PUBLIC_KEY: "normal-public" } }));
  await assert.rejects(initialize({ ...options, verify: async () => { throw new Error("bad membership"); } }));
  assert.equal(started, false);
});

test("supervisor verifies before and after startup then always stops child", async () => {
  for (const failAfter of [false, true]) {
    const calls = [];
    const child = new EventEmitter();
    child.kill = (signal) => { calls.push(signal); child.emit("exit", 0); };
    const run = initialize({
      env, report: () => {},
      verify: async (_, after) => { calls.push(after); if (after && failAfter) throw new Error("unexpected member"); },
      start: (_, __, options) => {
        assert.equal(options.env.HOSTNAME, "127.0.0.1");
        assert.equal(options.stdio, "ignore");
        return child;
      },
      request: async (url, options) => {
        assert.equal(url, "http://127.0.0.1:3000/api/public/projects");
        assert.equal(options.redirect, "error");
        return { status: 200, json: async () => project };
      },
    });
    if (failAfter) await assert.rejects(run); else await run;
    assert.deepEqual(calls, [false, true, "SIGTERM"]);
  }
});

test("supervisor deadline kills a child that never becomes ready", async () => {
  let clock = 0;
  const signals = [];
  const child = new EventEmitter();
  const reports = [];
  child.kill = (signal) => signals.push(signal);
  await assert.rejects(initialize({
    env, report: (entry) => reports.push(entry), verify: async () => {}, start: () => child,
    request: async () => ({ status: 401 }),
    now: () => clock, wait: async (duration) => { clock += duration; },
  }));
  assert.deepEqual(signals, ["SIGTERM", "SIGKILL"]);
  assert.ok(clock <= 250000);
  assert.deepEqual(reports, [{ event: "rejection_debug_init_failed", stage: "api_wait",
    httpStatus: 401, errorClass: "Error", errorCode: "INIT_DEADLINE" }]);
});

test("startup avoids shared negative-cache poisoning and bypasses existing misses", async () => {
  // Model pinned Langfuse: a cached miss wins over a newly created DB key.
  // Exercise both fresh startup and retries with an existing negative entry.
  for (const existingMiss of [false, true]) {
    let negativeCache = existingMiss;
    let created = false;
    let childEnv;
    let requests = 0;
    const child = new EventEmitter();
    child.kill = () => child.emit("exit", 0);
    await initialize({
      env: { ...env, LANGFUSE_CACHE_API_KEY_ENABLED: "true" },
      verify: async (_, after) => { if (after) assert.equal(created, true); },
      start: (_, __, options) => { childEnv = options.env; return child; },
      request: async (url) => {
        assert.ok(childEnv, "no authentication before child startup");
        assert.equal(url, "http://127.0.0.1:3000/api/public/projects");
        requests += 1;
        const cacheEnabled = childEnv.LANGFUSE_CACHE_API_KEY_ENABLED !== "false";
        if (cacheEnabled && negativeCache) throw new Error("cached miss blocks initialization");
        if (!created) {
          if (cacheEnabled) negativeCache = true;
          return { status: 401 };
        }
        return { status: 200, json: async () => project };
      },
      wait: async () => { created = true; },
    });
    assert.equal(requests, 2);
    assert.equal(negativeCache, existingMiss, "initialization must not write shared negative cache");
  }
});

test("diagnostics distinguish early child exit without leaking upstream errors", async () => {
  const reports = [];
  const child = new EventEmitter();
  child.kill = () => child.emit("exit", 0);
  await assert.rejects(initialize({
    env, report: (entry) => reports.push(entry), verify: async () => {}, start: () => child,
    request: async () => {
      child.emit("exit", 1, null);
      throw Object.assign(new TypeError("sensitive URL and credentials"), {
        cause: { code: "ECONNREFUSED", message: "sensitive cause" },
      });
    },
    wait: async () => {},
  }));
  assert.deepEqual(reports, [{ event: "rejection_debug_init_failed", stage: "api_wait",
    childExitCode: 1, childSignal: null,
    requestError: { errorClass: "TypeError", errorCode: "ECONNREFUSED" },
    errorClass: "Error", errorCode: "INIT_CHILD_EXIT" }]);
});

test("failure diagnostics allowlist database errors and identify API/postflight stages", async () => {
  for (const stage of ["database_preflight", "child_start", "api_identity", "database_postflight"]) {
    const reports = [];
    const child = new EventEmitter();
    child.kill = () => child.emit("exit", 0);
    const error = Object.assign(new Error("sensitive database credentials"), {
      name: "PrismaClientInitializationError", errorCode: "P1001",
    });
    await assert.rejects(initialize({
      env, report: (entry) => reports.push(entry),
      verify: async (_, after) => {
        if (stage === (after ? "database_postflight" : "database_preflight")) throw error;
      },
      start: () => {
        if (stage === "child_start") throw { name: "sensitive-name", code: "sensitive-code", message: "secret" };
        return child;
      },
      request: async () => ({ status: 200, json: async () => stage === "api_identity" ? {} : project }),
    }));
    assert.equal(reports.length, 1);
    assert.equal(reports[0].stage, stage);
    assert.equal(reports[0].errorCode, stage.startsWith("database_") ? "P1001" : "UNKNOWN");
    assert.equal(reports[0].errorClass, stage === "child_start" ? "Unknown" :
      stage === "api_identity" ? "Error" : "PrismaClientInitializationError");
    assert.doesNotMatch(JSON.stringify(reports), /sensitive|secret|credentials/);
  }
});

test("database URL encodes credentials and bounds connection setup", () => {
  const url = new URL(databaseUrl({ DATABASE_HOST: "database", DATABASE_NAME: "langfuse",
    DATABASE_USERNAME: "user", DATABASE_PASSWORD: "a@b:c/?#", DATABASE_ARGS: "sslmode=disable" }));
  assert.equal(decodeURIComponent(url.password), "a@b:c/?#");
  assert.equal(url.hostname, "database");
  assert.equal(url.searchParams.get("connect_timeout"), "5");
});
