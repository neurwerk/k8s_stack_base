// Langfuse 4.28.1 has no standalone initializer. Supervise its normal startup,
// verify the project-scoped API response, then stop the private child server.
const { spawn } = require("node:child_process");
const { setTimeout: sleep } = require("node:timers/promises");
const { createRequire } = require("node:module");

function databaseUrl(env) {
  const url = new URL(`postgresql://${env.DATABASE_HOST}:${env.DATABASE_PORT || "5432"}/${env.DATABASE_NAME}`);
  url.username = env.DATABASE_USERNAME;
  url.password = env.DATABASE_PASSWORD;
  url.search = env.DATABASE_ARGS || "";
  url.searchParams.set("connect_timeout", "5");
  url.searchParams.set("connection_limit", "1");
  return url.toString();
}

// Pinned Prisma 6 client shipped with Langfuse; read-only, bounded transactions.
// Do not import Langfuse's server module, which initializes other services/loggers.
async function verifyDatabase(env, after = false) {
  const { PrismaClient } = createRequire("/app/packages/shared/package.json")("@prisma/client");
  const db = new PrismaClient({ datasourceUrl: databaseUrl(env), log: [] });
  try {
    await db.$transaction(async (tx) => {
      await tx.$executeRaw`SET TRANSACTION READ ONLY`;
      await tx.$executeRaw`SET LOCAL statement_timeout = '5s'`;
      await verifyMemberships(tx, env, after);
    }, { maxWait: 5000, timeout: 15000 });
  } finally {
    await db.$disconnect();
  }
}

async function verifyMemberships(tx, env, after) {
  const id = "rejected-request-debug";
  const user = await tx.user.findUnique({
    where: { email: env.LANGFUSE_INIT_USER_EMAIL.toLowerCase() }, select: { id: true },
  });
  if (!user || !env.NORMAL_ORG_ID || env.NORMAL_ORG_ID === id) {
    throw new Error("Existing normal organization administrator required");
  }
  const normalMember = await tx.organizationMembership.findUnique({
    where: { orgId_userId: { orgId: env.NORMAL_ORG_ID, userId: user.id } },
    select: { role: true },
  });
  if (normalMember?.role !== "OWNER") throw new Error("Existing administrator must own the normal organization");
  const project = await tx.project.findUnique({
    where: { id }, select: { orgId: true, deletedAt: true, name: true },
  });
  const org = await tx.organization.findUnique({ where: { id }, select: { name: true } });
  if ((project && (project.orgId !== id || project.name !== id || project.deletedAt)) ||
      (org && org.name !== id) || (after && (!project || !org))) {
    throw new Error("Debug project identity mismatch");
  }
  const orgMembers = await tx.organizationMembership.findMany({
    where: { orgId: id }, select: { userId: true, role: true }, take: 2,
  });
  const projectMembers = await tx.projectMembership.findMany({
    where: { projectId: id }, select: { userId: true, role: true }, take: 2,
  });
  const intended = (row) => row.userId === user.id && row.role === "OWNER";
  if (orgMembers.some((row) => !intended(row)) || projectMembers.some((row) => !intended(row)) ||
      (after && orgMembers.length !== 1)) {
    throw new Error("Unexpected debug membership; no automatic repair");
  }
  if (await tx.membershipInvitation.findFirst({ where: { orgId: id }, select: { id: true } })) {
    throw new Error("Unexpected debug organization invitation");
  }
  if (await tx.project.findFirst({ where: { orgId: id, id: { not: id } }, select: { id: true } })) {
    throw new Error("Debug organization contains another project");
  }
  const key = await tx.apiKey.findUnique({
    where: { publicKey: env.LANGFUSE_INIT_PROJECT_PUBLIC_KEY },
    select: { projectId: true, scope: true },
  });
  if ((key && (key.projectId !== id || key.scope !== "PROJECT")) || (after && !key)) {
    throw new Error("Debug public key collision");
  }
}

function assertProject(body) {
  if (!Array.isArray(body?.data) || body.data.length !== 1 ||
      body.data[0].id !== "rejected-request-debug" ||
      body.data[0].name !== "rejected-request-debug" ||
      body.data[0].organization?.id !== "rejected-request-debug" ||
      body.data[0].organization?.name !== "rejected-request-debug") {
    throw new Error("Debug project or organization mismatch");
  }
}

async function initialize({ env = process.env, start = spawn, request = fetch,
                            wait = sleep, now = Date.now, verify = verifyDatabase } = {}) {
  const publicKey = env.LANGFUSE_INIT_PROJECT_PUBLIC_KEY;
  const secretKey = env.LANGFUSE_INIT_PROJECT_SECRET_KEY;
  if (!publicKey || !secretKey || !env.NORMAL_PROJECT_PUBLIC_KEY ||
      publicKey === env.NORMAL_PROJECT_PUBLIC_KEY) {
    throw new Error("Debug key must be present and distinct from the normal project key");
  }
  const authorization = "Basic " + Buffer.from(`${publicKey}:${secretKey}`).toString("base64");
  await verify(env, false);
  const options = () => ({ headers: { authorization }, redirect: "error",
                          signal: AbortSignal.timeout(5000) });
  // Detect a valid key assigned to another project before upstream initialization
  // can move it. Trusted setup additionally guarantees newly generated unique keys.
  const preflight = await request(env.DEBUG_LANGFUSE_BASE_URL + "/api/public/projects", options());
  if (preflight.status === 200) {
    assertProject(await preflight.json());
  } else if (preflight.status !== 401) {
    throw new Error("Debug project preflight failed");
  }
  const child = start("./web/entrypoint.sh", ["node", "./web/server.js"], {
    cwd: "/app", env: { ...env, DATABASE_URL: databaseUrl(env), HOSTNAME: "127.0.0.1", PORT: "3000" },
    // Upstream errors may include connection strings; report bounded status only.
    stdio: "ignore",
  });
  let exited = false;
  child.on("exit", () => { exited = true; });
  child.on("error", () => { exited = true; });
  const abort = () => child.kill("SIGTERM");
  process.once("SIGTERM", abort);
  process.once("SIGINT", abort);
  try {
    const deadline = now() + 240000;
    while (now() < deadline && !exited) {
      let response;
      try {
        response = await request("http://127.0.0.1:3000/api/public/projects", options());
      } catch {
        await wait(1000);
        continue;
      }
      if (response.status === 200) {
        assertProject(await response.json());
        await verify(env, true);
        return;
      }
      await wait(1000);
    }
    throw new Error("Debug project initialization did not become ready");
  } finally {
    child.kill("SIGTERM");
    const deadline = now() + 10000;
    while (!exited && now() < deadline) await wait(100);
    if (!exited) child.kill("SIGKILL");
    process.removeListener("SIGTERM", abort);
    process.removeListener("SIGINT", abort);
  }
}

module.exports = { assertProject, databaseUrl, initialize, verifyMemberships };
if (require.main === module) {
  initialize().then(() => console.log("Debug project initialized and verified"))
    .catch(() => {
      console.error("Debug project initialization failed");
      process.exitCode = 1;
    });
}
