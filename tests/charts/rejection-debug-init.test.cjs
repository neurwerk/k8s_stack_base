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
  DEBUG_LANGFUSE_BASE_URL: "http://langfuse",
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
    env, start: () => { started = true; }, verify: async () => {},
    request: async () => ({ status: 200, json: async () => ({ data: [{ id: "normal-project" }] }) }),
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
      env,
      verify: async (_, after) => { calls.push(after); if (after && failAfter) throw new Error("unexpected member"); },
      start: (_, __, options) => {
        assert.equal(options.env.HOSTNAME, "127.0.0.1");
        assert.equal(options.stdio, "ignore");
        return child;
      },
      request: async (url, options) => {
        assert.equal(options.redirect, "error");
        return { status: url.startsWith("http://127.") ? 200 : 401, json: async () => project };
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
  child.kill = (signal) => signals.push(signal);
  await assert.rejects(initialize({
    env, verify: async () => {}, start: () => child,
    request: async () => ({ status: 401 }),
    now: () => clock, wait: async (duration) => { clock += duration; },
  }));
  assert.deepEqual(signals, ["SIGTERM", "SIGKILL"]);
  assert.ok(clock <= 250000);
});

test("database URL encodes credentials and bounds connection setup", () => {
  const url = new URL(databaseUrl({ DATABASE_HOST: "database", DATABASE_NAME: "langfuse",
    DATABASE_USERNAME: "user", DATABASE_PASSWORD: "a@b:c/?#", DATABASE_ARGS: "sslmode=disable" }));
  assert.equal(decodeURIComponent(url.password), "a@b:c/?#");
  assert.equal(url.hostname, "database");
  assert.equal(url.searchParams.get("connect_timeout"), "5");
});
