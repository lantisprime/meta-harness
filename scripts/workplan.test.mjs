import test from "node:test";
import assert from "node:assert/strict";
import { spawn, execFile } from "node:child_process";
import {
  link,
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  rm,
  stat,
  unlink,
  writeFile,
} from "node:fs/promises";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import { tmpdir } from "node:os";
import { hostname } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  add,
  acquireAddLock,
  releaseAddLock,
  ready,
  block,
  computeDefinitionHash,
  computeAcceptanceReceiptHash,
} from "./workplan.mjs";

const cli = fileURLToPath(new URL("./workplan.mjs", import.meta.url));

function card(overrides = {}) {
  return {
    id: "TASK-20260711-001",
    title: "Default card",
    status: "backlog",
    owner: "",
    paths: [],
    ...overrides,
  };
}

function state(cards, overrides = {}) {
  return {
    schemaVersion: 1,
    revision: 1,
    cards,
    ...overrides,
  };
}

function execGit(args, options = {}) {
  return new Promise((resolve, reject) => {
    execFile("git", args, options, (error, stdout, stderr) => {
      if (error) {
        reject(
          new Error(
            `git ${args.join(" ")} failed: ${stderr || error.message}`,
          ),
        );
      } else {
        resolve({ stdout, stderr });
      }
    });
  });
}

async function repository(initialState, name = "primary") {
  const parent = await mkdtemp(join(tmpdir(), "meta-harness-workplan-"));
  const root = join(parent, name);
  await mkdir(root, { recursive: true });
  await execGit(["init"], { cwd: root });
  await mkdir(join(root, ".workplan"));
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(initialState, null, 2)}\n`,
  );
  await writeFile(join(root, "WORKPLAN.md"), "# existing projection\n");
  return { root, parent };
}

function run(args, environment = {}) {
  return new Promise((resolveRun) => {
    const child = spawn(process.execPath, [cli, ...args], {
      env: { ...process.env, ...environment },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("close", (code) => resolveRun({ code, stdout, stderr }));
  });
}

function baseDefinition(callerWorktree = "/tmp/worktree") {
  return {
    sourceRevision: "abc123",
    worktreePath: callerWorktree,
    branch: "main",
    baseCommit: "abc123",
    currentHead: "def456",
    plane: "development",
    frozenAxes: ["H", "E", "W"],
    budget: "10",
    stopCondition: "tests pass",
    evaluatorAuthority: "local",
    acceptanceCommands: ["npm test"],
    nextCheckpoint: "ready",
    allowedOwnerNamespaces: ["codex"],
  };
}

function makeDefinition(overrides = {}, callerWorktree = "/tmp/worktree") {
  const definition = { ...baseDefinition(callerWorktree), ...overrides };
  definition.definitionHash = computeDefinitionHash(definition);
  return definition;
}

async function writeDefinition(path, overrides = {}, callerWorktree = "/tmp/worktree") {
  const definition = makeDefinition(overrides, callerWorktree);
  await writeFile(path, `${JSON.stringify(definition, null, 2)}\n`);
  return definition;
}

function coordinatorOwner(session = "session-1") {
  return `coordinator:${hostname()}:${session}`;
}

function codexOwner(session = "session-1") {
  return `codex:${hostname()}:${session}`;
}

function claudeOwner(session = "session-1") {
  return `claude:${hostname()}:${session}`;
}

async function readyCard(root, id, overrides = {}, expectedRevision = "1") {
  const definitionPath = join(root, "definition.json");
  await writeDefinition(definitionPath, overrides, root);
  const result = await run([
    "ready",
    id,
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    expectedRevision,
    "--owner",
    coordinatorOwner(),
    "--definition-json",
    definitionPath,
  ]);
  if (result.code !== 0) {
    throw new Error(`ready failed: ${result.stderr}`);
  }
  return JSON.parse(result.stdout);
}

async function claimCard(root, id, owner, expectedRevision = "2") {
  const result = await run([
    "claim",
    id,
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    expectedRevision,
    "--owner",
    owner,
  ]);
  if (result.code !== 0) {
    throw new Error(`claim failed: ${result.stderr}`);
  }
  return JSON.parse(result.stdout);
}

async function startCard(root, id, owner, expectedRevision = "3") {
  const result = await run([
    "start",
    id,
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    expectedRevision,
    "--owner",
    owner,
  ]);
  if (result.code !== 0) {
    throw new Error(`start failed: ${result.stderr}`);
  }
  return JSON.parse(result.stdout);
}

async function currentHead(dir) {
  const { stdout } = await execGit(["-C", dir, "rev-parse", "HEAD"]);
  return stdout.trim();
}

async function seedCommit(root) {
  await execGit([
    "-C",
    root,
    "-c",
    "user.name=Test User",
    "-c",
    "user.email=test@example.com",
    "commit",
    "--allow-empty",
    "-m",
    "seed",
  ]);
}

async function ensureMainBranch(root) {
  const current = (
    await execGit(["-C", root, "rev-parse", "--abbrev-ref", "HEAD"])
  ).stdout.trim();
  if (current !== "main") {
    await execGit(["-C", root, "branch", "-m", "main"]);
  }
}

async function createBranch(root, branch, startPoint = "HEAD") {
  await execGit(["-C", root, "branch", branch, startPoint]);
}

async function commitOnBranch(root, branch, message = "commit") {
  const current = (
    await execGit(["-C", root, "rev-parse", "--abbrev-ref", "HEAD"])
  ).stdout.trim();
  await execGit(["-C", root, "checkout", branch]);
  await execGit([
    "-C",
    root,
    "-c",
    "user.name=Test User",
    "-c",
    "user.email=test@example.com",
    "commit",
    "--allow-empty",
    "-m",
    message,
  ]);
  const hash = (await execGit(["-C", root, "rev-parse", "HEAD"])).stdout.trim();
  await execGit(["-C", root, "checkout", current]);
  return hash;
}

async function mergeBranch(root, branch, message = "merge") {
  await execGit(["-C", root, "checkout", "main"]);
  await execGit([
    "-C",
    root,
    "-c",
    "user.name=Test User",
    "-c",
    "user.email=test@example.com",
    "merge",
    "--no-ff",
    "-m",
    message,
    branch,
  ]);
  return (await execGit(["-C", root, "rev-parse", "HEAD"])).stdout.trim();
}

async function integrateCard(
  root,
  id,
  actor,
  integrationCommit,
  expectedRevision = "5",
) {
  const result = await run([
    "integrate",
    id,
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    expectedRevision,
    "--actor",
    actor,
    "--integration-commit",
    integrationCommit,
  ]);
  if (result.code !== 0) {
    throw new Error(`integrate failed: ${result.stderr}`);
  }
  return JSON.parse(result.stdout);
}

async function reachClaimed(root, id, owner = null) {
  const actualOwner = owner || codexOwner("worker-1");
  await readyCard(root, id);
  await claimCard(root, id, actualOwner);
  return { owner: actualOwner };
}

async function reachInProgress(root, id, owner = null) {
  const actualOwner = owner || codexOwner("worker-1");
  await reachClaimed(root, id, actualOwner);
  await startCard(root, id, actualOwner);
  return { owner: actualOwner };
}

async function reachReview(root, id, owner = null) {
  const actualOwner = owner || codexOwner("worker-1");
  await reachInProgress(root, id, actualOwner);
  await createBranch(root, "review");
  const reviewHead = await commitOnBranch(root, "review", "review commit");
  await execGit(["-C", root, "checkout", "review"]);
  const submitResult = await run([
    "submit",
    id,
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    actualOwner,
    "--expected-head",
    reviewHead,
  ]);
  if (submitResult.code !== 0) {
    throw new Error(`submit failed: ${submitResult.stderr}`);
  }
  await execGit(["-C", root, "checkout", "main"]);
  return { owner: actualOwner, reviewHead };
}

async function reachVerifying(
  root,
  id,
  owner = null,
  actor = null,
) {
  const actualOwner = owner || codexOwner("worker-1");
  const actualActor = actor || coordinatorOwner("coordinator-1");
  const { owner: returnedOwner, reviewHead } = await reachReview(root, id, actualOwner);
  const integrationCommit = await mergeBranch(
    root,
    "review",
    "integrate review",
  );
  const integrateResult = await run([
    "integrate",
    id,
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "5",
    "--actor",
    actualActor,
    "--integration-commit",
    integrationCommit,
  ]);
  if (integrateResult.code !== 0) {
    throw new Error(`integrate failed: ${integrateResult.stderr}`);
  }
  return { owner: returnedOwner, actor: actualActor, reviewHead, integrationCommit };
}

async function blockCard(
  root,
  id,
  owner,
  expectedRevision,
  reason = "blocked",
  retainPaths = true,
) {
  const result = await run([
    "block",
    id,
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    expectedRevision,
    "--owner",
    owner,
    "--reason",
    reason,
    "--retain-paths",
    String(retainPaths),
  ]);
  if (result.code !== 0) {
    throw new Error(`block failed: ${result.stderr}`);
  }
  return JSON.parse(result.stdout);
}

async function resumeCard(root, id, owner, expectedRevision) {
  const result = await run([
    "resume",
    id,
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    expectedRevision,
    "--owner",
    owner,
  ]);
  if (result.code !== 0) {
    throw new Error(`resume failed: ${result.stderr}`);
  }
  return JSON.parse(result.stdout);
}

async function writeAcceptance(path, card, actor, overrides = {}) {
  const acceptance = {
    schemaVersion: 1,
    verdict: "pass",
    evaluator: actor,
    definitionHash: card.definition.definitionHash,
    integrationCommit: card.integrationReceipt.integrationCommit,
    commands: [...card.definition.acceptanceCommands],
    evidenceRefs: ["evidence-1"],
  };
  Object.assign(acceptance, overrides);
  if (!("receiptHash" in overrides)) {
    acceptance.receiptHash = computeAcceptanceReceiptHash(acceptance);
  }
  await writeFile(path, `${JSON.stringify(acceptance, null, 2)}\n`);
  return acceptance;
}

test("exact deterministic ordering", async (context) => {
  const cards = [
    card({ id: "TASK-20260711-003", title: "Gamma", status: "done" }),
    card({ id: "TASK-20260711-001", title: "Alpha", status: "backlog" }),
    card({
      id: "TASK-20260711-002",
      title: "Beta",
      status: "ready",
      owner: "alice",
      paths: ["src/b"],
    }),
    card({
      id: "TASK-20260711-004",
      title: "Delta",
      status: "in_progress",
      owner: "bob",
      paths: ["src/d"],
    }),
  ];
  const { root, parent } = await repository(state(cards));
  context.after(() => rm(parent, { recursive: true, force: true }));

  const result = await run(["sync", "--control-root", root]);
  assert.equal(result.code, 0, result.stderr);

  const markdown = await readFile(join(root, "WORKPLAN.md"), "utf8");
  const backlog = markdown.indexOf("TASK-20260711-001");
  const ready = markdown.indexOf("TASK-20260711-002");
  const inProgress = markdown.indexOf("TASK-20260711-004");
  const done = markdown.indexOf("TASK-20260711-003");
  assert.ok(backlog < ready, "backlog should come before ready");
  assert.ok(ready < inProgress, "ready should come before in_progress");
  assert.ok(inProgress < done, "in_progress should come before done");
});

test("byte-identical second sync", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Example",
        status: "ready",
        owner: "alice",
        paths: ["src/example"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const first = await run(["sync", "--control-root", root]);
  assert.equal(first.code, 0, first.stderr);
  const firstBytes = await readFile(join(root, "WORKPLAN.md"));

  const second = await run(["sync", "--control-root", root]);
  assert.equal(second.code, 0, second.stderr);
  const secondBytes = await readFile(join(root, "WORKPLAN.md"));

  assert.deepEqual(firstBytes, secondBytes);
});

test("explicit-root requirement", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));
  const existing = await readFile(join(root, "WORKPLAN.md"), "utf8");

  const result = await run(["sync"]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /--control-root is required/);
  const after = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.equal(after, existing);
});

test("relative-root rejection", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));
  const existing = await readFile(join(root, "WORKPLAN.md"), "utf8");

  const result = await run(["sync", "--control-root", "relative/path"]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /--control-root must be an absolute path/);
  const after = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.equal(after, existing);
});

test("malformed state preserving existing WORKPLAN.md", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));
  await writeFile(
    join(root, ".workplan", "state.json"),
    JSON.stringify({ schemaVersion: 2, revision: 0, cards: [] }),
  );
  const existing = await readFile(join(root, "WORKPLAN.md"), "utf8");

  const result = await run(["sync", "--control-root", root]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /schemaVersion must be 1/);
  const after = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.equal(after, existing);
});

test("repair of deliberately corrupted projection", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Fix me",
        status: "blocked",
        owner: "alice",
        paths: ["src/fix"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));
  await writeFile(join(root, "WORKPLAN.md"), "this projection is corrupted\n");

  const result = await run(["sync", "--control-root", root]);
  assert.equal(result.code, 0, result.stderr);

  const markdown = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(markdown, /TASK-20260711-001/);
  assert.match(markdown, /Fix me/);
  assert.match(markdown, /blocked/);
  assert.match(markdown, /alice/);
  assert.match(markdown, /src\/fix/);
});

test("add success increments revision and renders card", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Existing",
        status: "backlog",
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Second card",
    "--paths",
    "src/second, tests/second",
  ]);
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 2);
  assert.equal(output.added, true);

  const newState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(newState.revision, 2);
  assert.equal(newState.cards.length, 2);
  const added = newState.cards.find((c) => c.id === "TASK-20260711-002");
  assert.ok(added);
  assert.equal(added.title, "Second card");
  assert.equal(added.status, "backlog");
  assert.equal(added.owner, "workplan:add");
  assert.deepEqual(added.paths, ["src/second", "tests/second"]);

  const markdown = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(markdown, /Revision: 2/);
  assert.match(markdown, /TASK-20260711-002/);
  assert.match(markdown, /Second card/);
});

test("stale revision leaves both canonical files unchanged", async (context) => {
  const { root, parent } = await repository(
    state(
      [
        card({
          id: "TASK-20260711-001",
          title: "Existing",
          status: "backlog",
        }),
      ],
      { revision: 2 },
    ),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Stale attempt",
    "--paths",
    "src/x",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /expected revision 1 but found 2/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
});

test("simultaneous adds yield one winner and exactly one increment", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-000",
        title: "Seed",
        status: "backlog",
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const a = run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-001",
    "--outcome",
    "Winner A",
    "--paths",
    "src/a",
  ]);
  const b = run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Winner B",
    "--paths",
    "src/b",
  ]);

  const [resultA, resultB] = await Promise.all([a, b]);
  assert.notEqual(resultA.code, resultB.code);

  const finalState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(finalState.revision, 2);
  assert.equal(finalState.cards.length, 2);

  const ids = finalState.cards.map((c) => c.id);
  assert.ok(ids.includes("TASK-20260711-000"));
  const winner = ids.includes("TASK-20260711-001")
    ? "TASK-20260711-001"
    : "TASK-20260711-002";
  const loser = winner === "TASK-20260711-001"
    ? "TASK-20260711-002"
    : "TASK-20260711-001";
  assert.ok(ids.includes(winner));
  assert.ok(!ids.includes(loser));
});

test("duplicate id leaves canonical bytes unchanged", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Existing",
        status: "backlog",
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-001",
    "--outcome",
    "Duplicate",
    "--paths",
    "src/dup",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /duplicate id TASK-20260711-001/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
});

test("validateOwnedPath rejects invalid and accepts valid paths", async () => {
  const invalidCases = [
    { path: "", description: "empty string" },
    { path: "   ", description: "whitespace-only" },
    { path: "a\0b", description: "NUL byte" },
    { path: "/absolute", description: "leading slash" },
    { path: "back\\slash", description: "backslash" },
    { path: "C:drive", description: "Windows drive letter without slash" },
    { path: "C:/path", description: "Windows drive letter with slash" },
    { path: ".", description: "single dot segment" },
    { path: "..", description: "double dot segment" },
    { path: "src/.", description: "dot segment inside path" },
    { path: "src/..", description: "double dot segment inside path" },
    { path: "src//double", description: "repeated separator" },
    { path: "src/trailing/", description: "trailing slash" },
    { path: "src/../a", description: "normalization changes value" },
    { path: "./src", description: "leading dot segment" },
    { path: "src/./a", description: "embedded dot segment" },
  ];

  for (const { path, description } of invalidCases) {
    const { root, parent } = await repository(state([card()], { revision: 1 }));
    try {
      const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
      const stateBefore = await readFile(join(root, ".workplan", "state.json"));

      await assert.rejects(
        add({
          controlRoot: root,
          callerWorktree: root,
          expectedRevision: 1,
          id: "TASK-20260711-002",
          outcome: "Invalid path",
          paths: [path],
        }),
        /path/,
        `expected rejection for ${description}: ${JSON.stringify(path)}`,
      );

      assert.deepEqual(
        await readFile(join(root, "WORKPLAN.md")),
        workplanBefore,
        `WORKPLAN.md changed for ${description}: ${JSON.stringify(path)}`,
      );
      assert.deepEqual(
        await readFile(join(root, ".workplan", "state.json")),
        stateBefore,
        `state.json changed for ${description}: ${JSON.stringify(path)}`,
      );
    } finally {
      await rm(parent, { recursive: true, force: true });
    }
  }

  const validPaths = ["src", "src2", "src/a.js"];
  for (const path of validPaths) {
    const { root, parent } = await repository(state([card()], { revision: 1 }));
    try {
      const result = await add({
        controlRoot: root,
        callerWorktree: root,
        expectedRevision: 1,
        id: "TASK-20260711-002",
        outcome: "Valid path",
        paths: [path],
      });
      assert.equal(result.revision, 2);
      assert.equal(result.added, true);

      const newState = JSON.parse(
        await readFile(join(root, ".workplan", "state.json"), "utf8"),
      );
      const added = newState.cards.find((c) => c.id === "TASK-20260711-002");
      assert.ok(added);
      assert.deepEqual(added.paths, [path]);
    } finally {
      await rm(parent, { recursive: true, force: true });
    }
  }
});

test("injected fault leaves canonical state and sync repairs projection", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Original",
        status: "backlog",
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run(
    [
      "add",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "1",
      "--id",
      "TASK-20260711-002",
      "--outcome",
      "Faulty card",
      "--paths",
      "src/fault",
    ],
    { WORKPLAN_FAULT_AFTER_PROJECTION: "1" },
  );
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /injected fault after projection/);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );

  const projectionAfterFault = await readFile(
    join(root, "WORKPLAN.md"),
    "utf8",
  );
  assert.match(projectionAfterFault, /TASK-20260711-002/);
  assert.match(projectionAfterFault, /Faulty card/);

  const repair = await run(["sync", "--control-root", root]);
  assert.equal(repair.code, 0, repair.stderr);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  const projectionAfterRepair = await readFile(
    join(root, "WORKPLAN.md"),
    "utf8",
  );
  assert.match(projectionAfterRepair, /TASK-20260711-001/);
  assert.match(projectionAfterRepair, /Original/);
  assert.doesNotMatch(projectionAfterRepair, /TASK-20260711-002/);
  assert.doesNotMatch(projectionAfterRepair, /Faulty card/);
});

test("duplicate owned paths keep canonical files byte-identical", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Existing",
        status: "backlog",
        paths: ["src/a"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Duplicate paths",
    "--paths",
    "src/b,src/b",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /overlap/i);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
});

test("intra-card ancestor path keeps canonical files byte-identical", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Existing",
        status: "backlog",
        paths: ["src/x"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Ancestor paths",
    "--paths",
    "src,src/a",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /overlap/i);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
});

test("active-card path overlap keeps canonical files byte-identical", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Active",
        status: "ready",
        owner: "alice",
        paths: ["src"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Overlaps active",
    "--paths",
    "src/a",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /overlap/i);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
});

test("blocked retained path overlap keeps canonical files byte-identical", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Blocked retained",
        status: "blocked",
        owner: "alice",
        paths: ["src"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Overlaps blocked retained",
    "--paths",
    "src/a",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /overlap/i);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
});

test("src and src2 do not overlap and add succeeds", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Existing",
        status: "backlog",
        paths: ["src2"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Sibling prefix",
    "--paths",
    "src",
  ]);
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 2);
  assert.equal(output.added, true);

  const newState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const added = newState.cards.find((c) => c.id === "TASK-20260711-002");
  assert.ok(added);
  assert.deepEqual(added.paths, ["src"]);
});

test("overlap with backlog card succeeds", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Backlog",
        status: "backlog",
        paths: ["src"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Overlaps backlog",
    "--paths",
    "src/a",
  ]);
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 2);
  assert.equal(output.added, true);
});

test("overlap with blocked retainPaths false succeeds", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Blocked not retained",
        status: "blocked",
        owner: "alice",
        retainPaths: false,
        paths: ["src"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Overlaps non-retained blocked",
    "--paths",
    "src/a",
  ]);
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 2);
  assert.equal(output.added, true);
});

test("sync fails when path-reserving cards overlap", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Active one",
        status: "ready",
        owner: "alice",
        paths: ["src"],
      }),
      card({
        id: "TASK-20260711-002",
        title: "Active two",
        status: "in_progress",
        owner: "bob",
        paths: ["src/a"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));
  const existing = await readFile(join(root, "WORKPLAN.md"), "utf8");

  const result = await run(["sync", "--control-root", root]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /overlap/i);

  const after = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.equal(after, existing);
});

test("sync rejects stored absolute path and preserves projection", async (context) => {
  const { root, parent } = await repository(state([card({ paths: ["src"] })]));
  context.after(() => rm(parent, { recursive: true, force: true }));
  const existing = await readFile(join(root, "WORKPLAN.md"), "utf8");

  await writeFile(
    join(root, ".workplan", "state.json"),
    JSON.stringify(
      state([card({ id: "TASK-20260711-001", title: "Bad", status: "backlog", paths: ["/absolute"] })]),
    ),
  );

  const result = await run(["sync", "--control-root", root]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /path must not start with a slash/);
  assert.equal(await readFile(join(root, "WORKPLAN.md"), "utf8"), existing);
});

test("sync rejects stored traversal path and preserves projection", async (context) => {
  const { root, parent } = await repository(state([card({ paths: ["src"] })]));
  context.after(() => rm(parent, { recursive: true, force: true }));
  const existing = await readFile(join(root, "WORKPLAN.md"), "utf8");

  await writeFile(
    join(root, ".workplan", "state.json"),
    JSON.stringify(
      state([card({ id: "TASK-20260711-001", title: "Bad", status: "backlog", paths: ["../escape"] })]),
    ),
  );

  const result = await run(["sync", "--control-root", root]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /path must not contain \. or \.\./);
  assert.equal(await readFile(join(root, "WORKPLAN.md"), "utf8"), existing);
});

test("sync rejects stored backslash path and preserves projection", async (context) => {
  const { root, parent } = await repository(state([card({ paths: ["src"] })]));
  context.after(() => rm(parent, { recursive: true, force: true }));
  const existing = await readFile(join(root, "WORKPLAN.md"), "utf8");

  await writeFile(
    join(root, ".workplan", "state.json"),
    JSON.stringify(
      state([card({ id: "TASK-20260711-001", title: "Bad", status: "backlog", paths: ["src\\\\bad"] })]),
    ),
  );

  const result = await run(["sync", "--control-root", root]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /path must not contain backslash/);
  assert.equal(await readFile(join(root, "WORKPLAN.md"), "utf8"), existing);
});

test("sync rejects duplicate card ids and preserves projection", async (context) => {
  const { root, parent } = await repository(state([card({ paths: ["src"] })]));
  context.after(() => rm(parent, { recursive: true, force: true }));
  const existing = await readFile(join(root, "WORKPLAN.md"), "utf8");

  await writeFile(
    join(root, ".workplan", "state.json"),
    JSON.stringify(
      state([
        card({ id: "TASK-20260711-001", title: "One", status: "backlog", paths: ["src/a"] }),
        card({ id: "TASK-20260711-001", title: "Two", status: "backlog", paths: ["src/b"] }),
      ]),
    ),
  );

  const result = await run(["sync", "--control-root", root]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /duplicate card id/);
  assert.equal(await readFile(join(root, "WORKPLAN.md"), "utf8"), existing);
});

test("linked worktree of same temporary primary succeeds", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));

  const worktreePath = join(parent, "linked");
  await execGit(["-C", root, "worktree", "add", worktreePath]);

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    worktreePath,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Linked worktree card",
    "--paths",
    "src/linked",
  ]);
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 2);
  assert.equal(output.added, true);

  const newState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(newState.cards.length, 2);
  assert.ok(newState.cards.find((c) => c.id === "TASK-20260711-002"));
});

test("unrelated temporary clone fails before lock and leaves canonical bytes unchanged", async (context) => {
  const { root, parent } = await repository(state([card()]));
  const cloneRoot = join(parent, "clone");
  context.after(() => rm(parent, { recursive: true, force: true }));

  await execGit(["clone", root, cloneRoot]);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    cloneRoot,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Clone attempt",
    "--paths",
    "src/x",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(
    result.stderr,
    /caller-worktree is not part of the same repository|git metadata unavailable/i,
  );

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("empty/malformed/ownerless/foreign/live/EPERM fail-closed without canonical changes", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));

  const lockPath = join(root, ".workplan", "lock");
  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const cases = [
    { name: "empty", content: "" },
    { name: "malformed", content: "not-json" },
    {
      name: "ownerless missing",
      content: JSON.stringify({ schemaVersion: 1, host: hostname(), pid: process.pid }),
    },
    {
      name: "ownerless empty",
      content: JSON.stringify({ schemaVersion: 1, host: hostname(), pid: process.pid, owner: "" }),
    },
    {
      name: "foreign host",
      content: JSON.stringify({ schemaVersion: 1, host: "other-host", pid: process.pid, owner: "workplan:add" }),
    },
    {
      name: "live process",
      content: JSON.stringify({ schemaVersion: 1, host: hostname(), pid: process.pid, owner: "workplan:add" }),
    },
  ];

  for (const c of cases) {
    await writeFile(lockPath, c.content);
    await assert.rejects(
      acquireAddLock(root),
      /lock exists|recovery in progress|another acquirer/i,
      `expected rejection for ${c.name}`,
    );
    assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
    assert.deepEqual(
      await readFile(join(root, ".workplan", "state.json")),
      stateBefore,
    );
    await unlink(lockPath).catch(() => {});
  }

  await writeFile(
    lockPath,
    JSON.stringify({ schemaVersion: 1, host: hostname(), pid: 999999, owner: "workplan:add" }),
  );
  const originalKill = process.kill;
  process.kill = (pid, signal) => {
    const error = new Error("permission denied");
    error.code = "EPERM";
    throw error;
  };
  try {
    await assert.rejects(
      acquireAddLock(root),
      /lock exists|recovery in progress|another acquirer/i,
    );
    assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
    assert.deepEqual(
      await readFile(join(root, ".workplan", "state.json")),
      stateBefore,
    );
  } finally {
    process.kill = originalKill;
  }
});

test("missing-Git caller fails before lock and leaves canonical bytes unchanged", async (context) => {
  const { root, parent } = await repository(state([card()]));
  const callerRoot = join(parent, "not-a-repo");
  await mkdir(callerRoot, { recursive: true });
  context.after(() => rm(parent, { recursive: true, force: true }));

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    callerRoot,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Missing git",
    "--paths",
    "src/x",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /git metadata unavailable/i);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("omitted and relative caller-worktree fail before lock", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const omitted = await run([
    "add",
    "--control-root",
    root,
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Omitted caller",
    "--paths",
    "src/x",
  ]);
  assert.notEqual(omitted.code, 0);
  assert.match(omitted.stderr, /--caller-worktree is required/i);

  const relative = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    "relative/path",
    "--expected-revision",
    "1",
    "--id",
    "TASK-20260711-003",
    "--outcome",
    "Relative caller",
    "--paths",
    "src/y",
  ]);
  assert.notEqual(relative.code, 0);
  assert.match(relative.stderr, /--caller-worktree must be an absolute path/i);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("release not deleting replacement", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));

  const acquired = await acquireAddLock(root);
  const lockPath = acquired.path;

  const replacementContent = JSON.stringify({
    schemaVersion: 1,
    host: "other-host",
    pid: 12345,
    owner: "workplan:add",
  });
  const replacementTemp = join(
    root,
    ".workplan",
    `.tmp-replacement.${randomBytes(4).toString("hex")}`,
  );
  await writeFile(replacementTemp, replacementContent);
  await unlink(lockPath);
  await link(replacementTemp, lockPath);
  await unlink(replacementTemp).catch(() => {});

  await releaseAddLock(acquired);

  const st = await stat(lockPath);
  assert.ok(st.isFile());
  assert.equal(await readFile(lockPath, "utf8"), replacementContent);

  await unlink(lockPath);
});

test("five concurrent acquireAddLock calls yield exactly one winner and release removes lock", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));

  const attempts = Array.from({ length: 5 }, () => acquireAddLock(root));
  const results = await Promise.allSettled(attempts);
  const winners = results.filter((r) => r.status === "fulfilled");
  assert.equal(winners.length, 1);
  const acquired = winners[0].value;
  assert.equal(acquired.metadata.owner, "workplan:add");
  assert.equal(acquired.metadata.pid, process.pid);
  assert.equal(acquired.metadata.host, hostname());

  await releaseAddLock(acquired);
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("acquired lock is complete JSON with exact schemaVersion host pid owner", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));

  const acquired = await acquireAddLock(root);
  const content = await readFile(join(root, ".workplan", "lock"), "utf8");
  const metadata = JSON.parse(content);
  assert.equal(metadata.schemaVersion, 1);
  assert.equal(typeof metadata.host, "string");
  assert.ok(metadata.host.length > 0);
  assert.equal(metadata.pid, process.pid);
  assert.equal(metadata.owner, "workplan:add");

  await releaseAddLock(acquired);
});

test("stale lock from exited child PID is quarantined and same-host acquisition succeeds with no recovery guard", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));

  const child = spawn(process.execPath, ["-e", "process.exit(0)"]);
  await once(child, "exit");
  const stalePid = child.pid;

  const lockPath = join(root, ".workplan", "lock");
  await writeFile(
    lockPath,
    JSON.stringify({ schemaVersion: 1, host: hostname(), pid: stalePid, owner: "workplan:add" }),
  );

  const acquired = await acquireAddLock(root);
  const content = await readFile(lockPath, "utf8");
  const metadata = JSON.parse(content);
  assert.equal(metadata.pid, process.pid);

  const entries = await readdir(join(root, ".workplan"));
  assert.ok(entries.includes("lock"));
  assert.ok(!entries.includes("lock.recovery"), "recovery guard should be released");
  const quarantine = entries.find((e) => e.startsWith("lock.quarantine-"));
  assert.ok(quarantine, "stale lock should be quarantined");

  await releaseAddLock(acquired);
});

test("five concurrent acquisitions against stale lock yield exactly one winner", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));

  const child = spawn(process.execPath, ["-e", "process.exit(0)"]);
  await once(child, "exit");
  const stalePid = child.pid;

  const lockPath = join(root, ".workplan", "lock");
  await writeFile(
    lockPath,
    JSON.stringify({ schemaVersion: 1, host: hostname(), pid: stalePid, owner: "workplan:add" }),
  );

  const attempts = Array.from({ length: 5 }, () => acquireAddLock(root));
  const results = await Promise.allSettled(attempts);
  const winners = results.filter((r) => r.status === "fulfilled");
  assert.equal(winners.length, 1);
  const acquired = winners[0].value;
  assert.equal(acquired.metadata.pid, process.pid);

  await releaseAddLock(acquired);
});

test("cross-command stale lock recovery lets add recover a dead ready owner", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));

  const child = spawn(process.execPath, ["-e", "process.exit(0)"]);
  await once(child, "exit");
  const stalePid = child.pid;

  const lockPath = join(root, ".workplan", "lock");
  await writeFile(
    lockPath,
    JSON.stringify({ schemaVersion: 1, host: hostname(), pid: stalePid, owner: "workplan:ready" }),
  );

  const acquired = await acquireAddLock(root);
  const content = await readFile(lockPath, "utf8");
  const metadata = JSON.parse(content);
  assert.equal(metadata.owner, "workplan:add");
  assert.equal(metadata.pid, process.pid);

  await releaseAddLock(acquired);
});

test("ready success increments revision, sets status ready, stores definition and authority grant", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Prepare",
        status: "backlog",
        owner: "",
        paths: ["src/ready"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  const definition = await writeDefinition(definitionPath, {}, root);

  const result = await run([
    "ready",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    `coordinator:${hostname()}:session-1`,
    "--definition-json",
    definitionPath,
  ]);
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 2);
  assert.equal(output.ready, true);

  const newState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(newState.revision, 2);
  const readyCard = newState.cards.find((c) => c.id === "TASK-20260711-001");
  assert.ok(readyCard);
  assert.equal(readyCard.status, "ready");
  assert.equal(readyCard.owner, "");
  assert.deepEqual(readyCard.definition, definition);
  assert.deepEqual(readyCard.authorityGrant, {
    executionCoordination: true,
    evaluator: false,
    merge: false,
    promotion: false,
    deployment: false,
    credentials: false,
    runtime: false,
  });
});

test("ready appends transition receipt with expected fields", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Prepare",
        status: "backlog",
        owner: "",
        paths: ["src/ready"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  await writeDefinition(definitionPath, {}, root);
  const actor = `coordinator:${hostname()}:session-1`;

  const before = Date.now();
  const result = await run([
    "ready",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    actor,
    "--definition-json",
    definitionPath,
  ]);
  const after = Date.now();
  assert.equal(result.code, 0, result.stderr);

  const newState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const readyCard = newState.cards.find((c) => c.id === "TASK-20260711-001");
  assert.ok(readyCard.receipts);
  assert.equal(readyCard.receipts.length, 1);
  const receipt = readyCard.receipts[0];
  assert.equal(receipt.from, "backlog");
  assert.equal(receipt.to, "ready");
  assert.equal(receipt.actor, actor);
  assert.equal(receipt.revisionFrom, 1);
  assert.equal(receipt.revisionTo, 2);
  const at = new Date(receipt.at).getTime();
  assert.ok(at >= before && at <= after, "receipt timestamp out of range");
});

test("ready rejects non-coordinator owner before lock", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Prepare",
        status: "backlog",
        paths: ["src/ready"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  await writeDefinition(definitionPath, {}, root);
  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "ready",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    "worker:host:session-1",
    "--definition-json",
    definitionPath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /owner must be coordinator/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("ready rejects wrong coordinator host before lock and leaves canonical bytes unchanged", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Prepare",
        status: "backlog",
        paths: ["src/ready"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  await writeDefinition(definitionPath, {}, root);
  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "ready",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    "coordinator:wrong-host:session-1",
    "--definition-json",
    definitionPath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /owner host must match this host/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("ready rejects stale revision and leaves canonical bytes unchanged", async (context) => {
  const { root, parent } = await repository(
    state(
      [
        card({
          id: "TASK-20260711-001",
          title: "Prepare",
          status: "backlog",
          paths: ["src/ready"],
        }),
      ],
      { revision: 2 },
    ),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  await writeDefinition(definitionPath, {}, root);
  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "ready",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    `coordinator:${hostname()}:session-1`,
    "--definition-json",
    definitionPath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /expected revision 1 but found 2/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
});

test("ready rejects incomplete definition", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Prepare",
        status: "backlog",
        paths: ["src/ready"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  const badDefinition = { ...baseDefinition(root) };
  delete badDefinition.budget;
  badDefinition.definitionHash = computeDefinitionHash(badDefinition);
  await writeFile(definitionPath, `${JSON.stringify(badDefinition, null, 2)}\n`);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "ready",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    `coordinator:${hostname()}:session-1`,
    "--definition-json",
    definitionPath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /definition has incorrect fields/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
});

test("ready rejects mismatched definitionHash", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Prepare",
        status: "backlog",
        paths: ["src/ready"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  const definition = makeDefinition({}, root);
  definition.definitionHash =
    "sha256:0000000000000000000000000000000000000000000000000000000000000000";
  await writeFile(definitionPath, `${JSON.stringify(definition, null, 2)}\n`);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "ready",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    `coordinator:${hostname()}:session-1`,
    "--definition-json",
    definitionPath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /definitionHash mismatch/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
});

test("ready rejects unmet dependency", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Dependency",
        status: "ready",
        owner: "alice",
        paths: ["src/dep"],
      }),
      card({
        id: "TASK-20260711-002",
        title: "Prepare",
        status: "backlog",
        paths: ["src/ready"],
        dependencies: ["TASK-20260711-001"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  await writeDefinition(definitionPath, {}, root);

  const result = await run([
    "ready",
    "TASK-20260711-002",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    `coordinator:${hostname()}:session-1`,
    "--definition-json",
    definitionPath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /dependency TASK-20260711-001 is not done/);
});

test("ready rejects wrong frozenAxes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Prepare",
        status: "backlog",
        paths: ["src/ready"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  await writeDefinition(definitionPath, { frozenAxes: ["H", "W", "E"] }, root);

  const result = await run([
    "ready",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    `coordinator:${hostname()}:session-1`,
    "--definition-json",
    definitionPath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /frozenAxes must be exactly/);
});

test("ready rejects wrong plane", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Prepare",
        status: "backlog",
        paths: ["src/ready"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  await writeDefinition(definitionPath, { plane: "production" }, root);

  const result = await run([
    "ready",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    `coordinator:${hostname()}:session-1`,
    "--definition-json",
    definitionPath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /plane must be development/);
});

test("ready rejects worktreePath mismatch", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Prepare",
        status: "backlog",
        paths: ["src/ready"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  await writeDefinition(definitionPath, {}, "/other/worktree");

  const result = await run([
    "ready",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    `coordinator:${hostname()}:session-1`,
    "--definition-json",
    definitionPath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /worktreePath must match callerWorktree/);
});

test("ready rejects reserved-path conflict and leaves canonical bytes unchanged", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Active",
        status: "ready",
        owner: "alice",
        paths: ["src/active"],
      }),
      card({
        id: "TASK-20260711-002",
        title: "Prepare",
        status: "backlog",
        paths: ["src/active/sub"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  await writeDefinition(definitionPath, {}, root);
  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "ready",
    "TASK-20260711-002",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    `coordinator:${hostname()}:session-1`,
    "--definition-json",
    definitionPath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /overlap/i);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
});

test("ready injected fault leaves canonical state and sync repairs projection", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Prepare",
        status: "backlog",
        owner: "",
        paths: ["src/ready"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const definitionPath = join(parent, "definition.json");
  await writeDefinition(definitionPath, {}, root);
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run(
    [
      "ready",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "1",
      "--owner",
      `coordinator:${hostname()}:session-1`,
      "--definition-json",
      definitionPath,
    ],
    { WORKPLAN_FAULT_AFTER_PROJECTION: "1" },
  );
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /injected fault after projection/);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );

  const projectionAfterFault = await readFile(
    join(root, "WORKPLAN.md"),
    "utf8",
  );
  assert.match(projectionAfterFault, /TASK-20260711-001/);
  assert.match(projectionAfterFault, /ready/);

  const repair = await run(["sync", "--control-root", root]);
  assert.equal(repair.code, 0, repair.stderr);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  const projectionAfterRepair = await readFile(
    join(root, "WORKPLAN.md"),
    "utf8",
  );
  assert.match(projectionAfterRepair, /TASK-20260711-001/);
  assert.match(projectionAfterRepair, /Prepare/);
  assert.doesNotMatch(projectionAfterRepair, /\| ready \|/);
});

test("claim stores immutable snapshot and transition receipt", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Claimable",
        status: "backlog",
        owner: "",
        paths: ["src/claim"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001");

  const owner = codexOwner("snapshot-session");
  const before = Date.now();
  const result = await run([
    "claim",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    owner,
  ]);
  const after = Date.now();
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 3);
  assert.equal(output.claimed, true);

  const newState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(newState.revision, 3);
  const claimedCard = newState.cards.find((c) => c.id === "TASK-20260711-001");
  assert.equal(claimedCard.status, "claimed");
  assert.equal(claimedCard.owner, owner);
  assert.ok(claimedCard.claimSnapshot);
  assert.equal(claimedCard.claimSnapshot.owner, owner);
  assert.match(claimedCard.claimSnapshot.definitionHash, /^sha256:[0-9a-f]{64}$/);
  assert.equal(claimedCard.claimSnapshot.sourceRevision, "abc123");
  assert.equal(claimedCard.claimSnapshot.worktreePath, root);
  assert.equal(claimedCard.claimSnapshot.branch, "main");
  assert.equal(claimedCard.claimSnapshot.baseCommit, "abc123");
  assert.equal(claimedCard.claimSnapshot.currentHead, "def456");
  assert.deepEqual(claimedCard.claimSnapshot.paths, ["src/claim"]);
  assert.equal(claimedCard.claimSnapshot.plane, "development");
  assert.deepEqual(claimedCard.claimSnapshot.frozenAxes, ["H", "E", "W"]);
  assert.equal(claimedCard.claimSnapshot.budget, "10");
  assert.deepEqual(claimedCard.claimSnapshot.acceptanceCommands, ["npm test"]);
  assert.equal(claimedCard.claimSnapshot.nextCheckpoint, "ready");
  assert.equal(claimedCard.claimSnapshot.revisionFrom, 2);
  assert.equal(claimedCard.claimSnapshot.revisionTo, 3);
  const snapAt = new Date(claimedCard.claimSnapshot.at).getTime();
  assert.ok(snapAt >= before && snapAt <= after);

  assert.ok(claimedCard.receipts);
  const claimReceipt = claimedCard.receipts[claimedCard.receipts.length - 1];
  assert.equal(claimReceipt.from, "ready");
  assert.equal(claimReceipt.to, "claimed");
  assert.equal(claimReceipt.actor, owner);
  assert.equal(claimReceipt.revisionFrom, 2);
  assert.equal(claimReceipt.revisionTo, 3);
  const receiptAt = new Date(claimReceipt.at).getTime();
  assert.ok(receiptAt >= before && receiptAt <= after);

  const projection = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projection, /TASK-20260711-001/);
  assert.match(projection, /claimed/);
  assert.match(projection, /snapshot-session/);
});

test("simultaneous same-card claims yield one winner and exactly one increment", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Contested",
        status: "backlog",
        owner: "",
        paths: ["src/contest"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001", {
    allowedOwnerNamespaces: ["codex", "claude"],
  });

  const ownerA = codexOwner("session-a");
  const ownerB = claudeOwner("session-b");
  const a = run([
    "claim",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    ownerA,
  ]);
  const b = run([
    "claim",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    ownerB,
  ]);

  const [resultA, resultB] = await Promise.all([a, b]);
  assert.notEqual(resultA.code, resultB.code);

  const finalState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(finalState.revision, 3);
  const claimedCard = finalState.cards.find((c) => c.id === "TASK-20260711-001");
  assert.equal(claimedCard.status, "claimed");
  assert.ok([ownerA, ownerB].includes(claimedCard.owner));
});

test("sequential different-owner claims on different cards both succeed", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "First",
        status: "backlog",
        owner: "",
        paths: ["src/first"],
      }),
      card({
        id: "TASK-20260711-002",
        title: "Second",
        status: "backlog",
        owner: "",
        paths: ["src/second"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001");
  await readyCard(
    root,
    "TASK-20260711-002",
    { allowedOwnerNamespaces: ["codex", "claude"] },
    "2",
  );

  const ownerA = codexOwner("session-a");
  const ownerB = claudeOwner("session-b");

  const first = await run([
    "claim",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "3",
    "--owner",
    ownerA,
  ]);
  assert.equal(first.code, 0, first.stderr);

  const second = await run([
    "claim",
    "TASK-20260711-002",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    ownerB,
  ]);
  assert.equal(second.code, 0, second.stderr);

  const finalState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(finalState.revision, 5);
  const cardA = finalState.cards.find((c) => c.id === "TASK-20260711-001");
  const cardB = finalState.cards.find((c) => c.id === "TASK-20260711-002");
  assert.equal(cardA.status, "claimed");
  assert.equal(cardA.owner, ownerA);
  assert.equal(cardB.status, "claimed");
  assert.equal(cardB.owner, ownerB);
});

test("claim rejects same exact owner already having WIP card and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "First",
        status: "backlog",
        owner: "",
        paths: ["src/first"],
      }),
      card({
        id: "TASK-20260711-002",
        title: "Second",
        status: "backlog",
        owner: "",
        paths: ["src/second"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001");
  await readyCard(
    root,
    "TASK-20260711-002",
    { allowedOwnerNamespaces: ["codex", "claude"] },
    "2",
  );

  const owner = codexOwner("wip-session");
  const first = await run([
    "claim",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "3",
    "--owner",
    owner,
  ]);
  assert.equal(first.code, 0, first.stderr);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const second = await run([
    "claim",
    "TASK-20260711-002",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
  ]);
  assert.notEqual(second.code, 0);
  assert.match(second.stderr, /owner already has WIP card/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("claim rejects disallowed owner namespace and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Namespace",
        status: "backlog",
        owner: "",
        paths: ["src/ns"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001", { allowedOwnerNamespaces: ["claude"] });

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "claim",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    codexOwner("session-1"),
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /owner namespace codex is not allowed/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("claim rejects wrong owner host before lock and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Host",
        status: "backlog",
        owner: "",
        paths: ["src/host"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001");

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "claim",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    "codex:wrong-host:session-1",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /owner host must match this host/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("claim accepts an owner host that is a configured alias", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Alias",
        status: "backlog",
        owner: "",
        paths: ["src/alias"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001");

  const aliasedHost = `${hostname()}.alias.example`;
  const result = await run(
    [
      "claim",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "2",
      "--owner",
      `codex:${aliasedHost}:session-1`,
    ],
    { WORKPLAN_HOST_ALIASES: aliasedHost },
  );
  assert.equal(result.code, 0, result.stderr);
  assert.match(result.stdout, /"claimed":true/);

  const claimed = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(claimed.cards[0].owner, `codex:${aliasedHost}:session-1`);
});

test("submit accepts an owner host alias that differs from the current hostname", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Alias submit",
        status: "backlog",
        owner: "",
        paths: ["src/alias-submit"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001");

  const aliasedHost = `${hostname()}.submit-alias.example`;
  const aliasEnv = { WORKPLAN_HOST_ALIASES: aliasedHost };

  const claimResult = await run(
    [
      "claim",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "2",
      "--owner",
      `codex:${aliasedHost}:session-1`,
    ],
    aliasEnv,
  );
  assert.equal(claimResult.code, 0, claimResult.stderr);

  const startResult = await run(
    [
      "start",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "3",
      "--owner",
      `codex:${aliasedHost}:session-1`,
    ],
    aliasEnv,
  );
  assert.equal(startResult.code, 0, startResult.stderr);

  // submit while the configured alias is the frozen owner host, accepted even
  // though it is not the current os.hostname().
  const fakeHead = "a".repeat(40);
  await writeFile(join(root, ".git", "HEAD"), `${fakeHead}\n`);
  const submitResult = await run(
    [
      "submit",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "4",
      "--owner",
      `codex:${aliasedHost}:session-1`,
      "--expected-head",
      fakeHead,
    ],
    aliasEnv,
  );
  assert.equal(submitResult.code, 0, submitResult.stderr);
  assert.match(submitResult.stdout, /"revision":5/);
});

test("named claim of non-ready card fails without falling through and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Backlog only",
        status: "backlog",
        owner: "",
        paths: ["src/backlog"],
      }),
      card({
        id: "TASK-20260711-002",
        title: "Ready",
        status: "backlog",
        owner: "",
        paths: ["src/ready"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-002");

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "claim",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    codexOwner("session-1"),
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /card TASK-20260711-001 is not in ready status/);

  const finalState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(finalState.cards.find((c) => c.id === "TASK-20260711-002").status, "ready");

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("claim fails when dependency regressed from done and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Dependency",
        status: "done",
        owner: "",
        paths: ["src/dep"],
      }),
      card({
        id: "TASK-20260711-002",
        title: "Dependent",
        status: "backlog",
        owner: "",
        paths: ["src/dependent"],
        dependencies: ["TASK-20260711-001"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-002");

  const stateBefore = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  stateBefore.cards.find((c) => c.id === "TASK-20260711-001").status = "ready";
  stateBefore.revision = 2;
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(stateBefore, null, 2)}\n`,
  );

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "claim",
    "TASK-20260711-002",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    codexOwner("session-1"),
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /dependency TASK-20260711-001 is not done/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("claim fails closed on active path-retaining overlap and preserves bytes", async (context) => {
  const { root, parent } = await repository(state([card()]));
  context.after(() => rm(parent, { recursive: true, force: true }));

  const corrupt = state([
    card({
      id: "TASK-20260711-001",
      title: "Active",
      status: "blocked",
      owner: "alice",
      retainPaths: true,
      paths: ["src/overlap"],
    }),
    card({
      id: "TASK-20260711-002",
      title: "Ready",
      status: "ready",
      owner: "",
      paths: ["src/overlap/sub"],
      definition: makeDefinition({}, root),
    }),
  ]);
  corrupt.revision = 2;
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(corrupt, null, 2)}\n`,
  );

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "claim",
    "TASK-20260711-002",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    codexOwner("session-1"),
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /overlap/i);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("claim-next sorts priority then id, skips ineligible, claims first eligible", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-A",
        title: "Ineligible high priority",
        status: "backlog",
        owner: "",
        paths: ["src/a"],
        priority: 2,
      }),
      card({
        id: "TASK-B",
        title: "Eligible medium priority",
        status: "backlog",
        owner: "",
        paths: ["src/b"],
        priority: 5,
      }),
      card({
        id: "TASK-C",
        title: "Ineligible higher priority",
        status: "backlog",
        owner: "",
        paths: ["src/c"],
        priority: 1,
      }),
      card({
        id: "TASK-D",
        title: "Eligible no priority",
        status: "backlog",
        owner: "",
        paths: ["src/d"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-A", { allowedOwnerNamespaces: ["claude"] });
  await readyCard(root, "TASK-B", { allowedOwnerNamespaces: ["codex"] }, "2");
  await readyCard(root, "TASK-C", { allowedOwnerNamespaces: ["claude"] }, "3");
  await readyCard(root, "TASK-D", { allowedOwnerNamespaces: ["codex"] }, "4");

  const result = await run([
    "claim-next",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "5",
    "--owner",
    codexOwner("session-1"),
  ]);
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 6);
  assert.equal(output.claimed, true);
  assert.equal(output.id, "TASK-B");

  const finalState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(finalState.revision, 6);
  assert.equal(finalState.cards.find((c) => c.id === "TASK-B").status, "claimed");
  assert.equal(finalState.cards.find((c) => c.id === "TASK-B").owner, codexOwner("session-1"));
  assert.equal(finalState.cards.find((c) => c.id === "TASK-A").status, "ready");
  assert.equal(finalState.cards.find((c) => c.id === "TASK-C").status, "ready");
  assert.equal(finalState.cards.find((c) => c.id === "TASK-D").status, "ready");

  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("claim rejects stale expected revision and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Stale",
        status: "backlog",
        owner: "",
        paths: ["src/stale"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001");

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "claim",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "1",
    "--owner",
    codexOwner("session-1"),
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /expected revision 1 but found 2/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("claim-next fails closed on later corrupt definition hash and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-ELIGIBLE",
        title: "Eligible",
        status: "backlog",
        owner: "",
        paths: ["src/eligible"],
      }),
      card({
        id: "TASK-CORRUPT",
        title: "Corrupt hash",
        status: "backlog",
        owner: "",
        paths: ["src/corrupt"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-ELIGIBLE");
  await readyCard(root, "TASK-CORRUPT", {}, "2");

  const corruptState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  corruptState.cards.find((c) => c.id === "TASK-CORRUPT").definition
    .definitionHash =
    "sha256:0000000000000000000000000000000000000000000000000000000000000000";
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(corruptState, null, 2)}\n`,
  );

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "claim-next",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "3",
    "--owner",
    codexOwner("session-1"),
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /definitionHash mismatch/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("claim-next fails closed on later invalid priority and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-ELIGIBLE",
        title: "Eligible",
        status: "backlog",
        owner: "",
        paths: ["src/eligible"],
      }),
      card({
        id: "TASK-BADPRIO",
        title: "Bad priority",
        status: "backlog",
        owner: "",
        paths: ["src/badprio"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-ELIGIBLE");
  await readyCard(root, "TASK-BADPRIO", {}, "2");

  const corruptState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  corruptState.cards.find((c) => c.id === "TASK-BADPRIO").priority = -1;
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(corruptState, null, 2)}\n`,
  );

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "claim-next",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "3",
    "--owner",
    codexOwner("session-1"),
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /priority must be a nonnegative integer/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("claim-next explicit priority sorts before missing priority and id breaks ties", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-A",
        title: "No priority A",
        status: "backlog",
        owner: "",
        paths: ["src/a"],
      }),
      card({
        id: "TASK-B",
        title: "Priority one id B",
        status: "backlog",
        owner: "",
        paths: ["src/b"],
        priority: 1,
      }),
      card({
        id: "TASK-C",
        title: "Priority one id C",
        status: "backlog",
        owner: "",
        paths: ["src/c"],
        priority: 1,
      }),
      card({
        id: "TASK-D",
        title: "No priority D",
        status: "backlog",
        owner: "",
        paths: ["src/d"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-A");
  await readyCard(root, "TASK-B", {}, "2");
  await readyCard(root, "TASK-C", {}, "3");
  await readyCard(root, "TASK-D", {}, "4");

  const result = await run([
    "claim-next",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "5",
    "--owner",
    codexOwner("session-1"),
  ]);
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 6);
  assert.equal(output.claimed, true);
  assert.equal(output.id, "TASK-B");

  const finalState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(finalState.cards.find((c) => c.id === "TASK-B").status, "claimed");
  assert.equal(finalState.cards.find((c) => c.id === "TASK-C").status, "ready");
  assert.equal(finalState.cards.find((c) => c.id === "TASK-A").status, "ready");
  assert.equal(finalState.cards.find((c) => c.id === "TASK-D").status, "ready");

  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("claim injected fault after projection leaves canonical state and sync repairs projection", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Faulty",
        status: "backlog",
        owner: "",
        paths: ["src/faulty"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001");
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run(
    [
      "claim",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "2",
      "--owner",
      codexOwner("session-1"),
    ],
    { WORKPLAN_FAULT_AFTER_PROJECTION: "1" },
  );
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /injected fault after projection/);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );

  const projectionAfterFault = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterFault, /TASK-20260711-001/);
  assert.match(projectionAfterFault, /claimed/);

  const repair = await run(["sync", "--control-root", root]);
  assert.equal(repair.code, 0, repair.stderr);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  const projectionAfterRepair = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterRepair, /TASK-20260711-001/);
  assert.match(projectionAfterRepair, /Faulty/);
  assert.doesNotMatch(projectionAfterRepair, /\| claimed \|/);
});

test("concurrent claim-next calls against one eligible card yield one winner and one increment", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Solo eligible",
        status: "backlog",
        owner: "",
        paths: ["src/solo"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001", {
    allowedOwnerNamespaces: ["codex", "claude"],
  });

  const ownerA = codexOwner("session-a");
  const ownerB = claudeOwner("session-b");
  const a = run([
    "claim-next",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    ownerA,
  ]);
  const b = run([
    "claim-next",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    ownerB,
  ]);

  const [resultA, resultB] = await Promise.all([a, b]);
  assert.notEqual(resultA.code, resultB.code);

  const finalState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(finalState.revision, 3);
  const claimedCard = finalState.cards.find((c) => c.id === "TASK-20260711-001");
  assert.equal(claimedCard.status, "claimed");
  assert.ok([ownerA, ownerB].includes(claimedCard.owner));

  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("claim-next injected fault after projection leaves canonical state and sync repairs projection", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Faulty next",
        status: "backlog",
        owner: "",
        paths: ["src/faultynext"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001");
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run(
    [
      "claim-next",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "2",
      "--owner",
      codexOwner("session-1"),
    ],
    { WORKPLAN_FAULT_AFTER_PROJECTION: "1" },
  );
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /injected fault after projection/);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );

  const projectionAfterFault = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterFault, /TASK-20260711-001/);
  assert.match(projectionAfterFault, /claimed/);

  const repair = await run(["sync", "--control-root", root]);
  assert.equal(repair.code, 0, repair.stderr);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  const projectionAfterRepair = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterRepair, /TASK-20260711-001/);
  assert.match(projectionAfterRepair, /Faulty next/);
  assert.doesNotMatch(projectionAfterRepair, /\| claimed \|/);
});

test("claim-next with all candidates namespace-ineligible fails and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Claude only",
        status: "backlog",
        owner: "",
        paths: ["src/claude"],
      }),
      card({
        id: "TASK-20260711-002",
        title: "Also claude only",
        status: "backlog",
        owner: "",
        paths: ["src/claude2"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await readyCard(root, "TASK-20260711-001", { allowedOwnerNamespaces: ["claude"] });
  await readyCard(root, "TASK-20260711-002", { allowedOwnerNamespaces: ["claude"] }, "2");

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "claim-next",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "3",
    "--owner",
    codexOwner("session-1"),
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /no eligible ready card/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("start transitions claimed card to in_progress and appends receipt", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Startable",
        status: "backlog",
        owner: "",
        paths: ["src/start"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("start-session");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);

  const before = Date.now();
  const result = await run([
    "start",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "3",
    "--owner",
    owner,
  ]);
  const after = Date.now();
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 4);
  assert.equal(output.started, true);

  const finalState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(finalState.revision, 4);
  const startedCard = finalState.cards.find((c) => c.id === "TASK-20260711-001");
  assert.equal(startedCard.status, "in_progress");
  assert.equal(startedCard.owner, owner);
  assert.ok(startedCard.claimSnapshot);
  assert.ok(startedCard.definition);
  assert.deepEqual(startedCard.paths, ["src/start"]);

  const startReceipt = startedCard.receipts[startedCard.receipts.length - 1];
  assert.equal(startReceipt.from, "claimed");
  assert.equal(startReceipt.to, "in_progress");
  assert.equal(startReceipt.actor, owner);
  assert.equal(startReceipt.revisionFrom, 3);
  assert.equal(startReceipt.revisionTo, 4);
  const at = new Date(startReceipt.at).getTime();
  assert.ok(at >= before && at <= after);

  const projection = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projection, /TASK-20260711-001/);
  assert.match(projection, /in_progress/);
  assert.match(projection, /start-session/);
});

test("submit transitions in_progress card to review and stores reviewFreeze with evidence", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Submittable",
        status: "backlog",
        owner: "",
        paths: ["src/submit"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("submit-session");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await startCard(root, "TASK-20260711-001", owner);
  await seedCommit(root);

  const head = await currentHead(root);
  const before = Date.now();
  const result = await run([
    "submit",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
    "--expected-head",
    head,
  ]);
  const after = Date.now();
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 5);
  assert.equal(output.submitted, true);

  const finalState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(finalState.revision, 5);
  const submittedCard = finalState.cards.find((c) => c.id === "TASK-20260711-001");
  assert.equal(submittedCard.status, "review");
  assert.equal(submittedCard.owner, owner);
  assert.ok(submittedCard.claimSnapshot);
  assert.ok(submittedCard.definition);
  assert.ok(submittedCard.reviewFreeze);
  assert.equal(submittedCard.reviewFreeze.head, head);
  assert.equal(submittedCard.reviewFreeze.branch, "main");
  assert.equal(submittedCard.reviewFreeze.revisionFrom, 4);
  assert.equal(submittedCard.reviewFreeze.revisionTo, 5);
  const freezeAt = new Date(submittedCard.reviewFreeze.at).getTime();
  assert.ok(freezeAt >= before && freezeAt <= after);

  const submitReceipt = submittedCard.receipts[submittedCard.receipts.length - 1];
  assert.equal(submitReceipt.from, "in_progress");
  assert.equal(submitReceipt.to, "review");
  assert.equal(submitReceipt.actor, owner);
  assert.equal(submitReceipt.revisionFrom, 4);
  assert.equal(submitReceipt.revisionTo, 5);
  assert.equal(submitReceipt.evidence.head, head);
  assert.equal(submitReceipt.evidence.branch, "main");

  const projection = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projection, /TASK-20260711-001/);
  assert.match(projection, /review/);
  assert.match(projection, /submit-session/);
});

test("start rejects wrong owner and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Owned",
        status: "backlog",
        owner: "",
        paths: ["src/owned"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const ownerA = codexOwner("session-a");
  const ownerB = codexOwner("session-b");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", ownerA);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "start",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "3",
    "--owner",
    ownerB,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /is not owned by/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("start rejects non-claimed status and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Ready only",
        status: "backlog",
        owner: "",
        paths: ["src/readyonly"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("session-1");
  await readyCard(root, "TASK-20260711-001");

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "start",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    owner,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /is not in claimed status/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("start rejects stale expected revision and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Stale start",
        status: "backlog",
        owner: "",
        paths: ["src/stalestart"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("session-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "start",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    owner,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /expected revision 2 but found 3/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("start rejects caller-worktree mismatch and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Worktree bound",
        status: "backlog",
        owner: "",
        paths: ["src/bound"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("session-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);

  const otherWorktree = join(parent, "other-worktree");
  await execGit(["-C", root, "worktree", "add", otherWorktree]);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "start",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    otherWorktree,
    "--expected-revision",
    "3",
    "--owner",
    owner,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /worktreePath must match callerWorktree/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("submit rejects HEAD mismatch and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Head bound",
        status: "backlog",
        owner: "",
        paths: ["src/headbound"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("session-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await startCard(root, "TASK-20260711-001", owner);
  await seedCommit(root);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const wrongHead =
    "0000000000000000000000000000000000000000";
  const result = await run([
    "submit",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
    "--expected-head",
    wrongHead,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /HEAD .* does not match expected/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("submit injected fault after projection leaves canonical state and sync repairs projection", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Faulty submit",
        status: "backlog",
        owner: "",
        paths: ["src/faultysubmit"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("session-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await startCard(root, "TASK-20260711-001", owner);
  await seedCommit(root);

  const head = await currentHead(root);
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run(
    [
      "submit",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "4",
      "--owner",
      owner,
      "--expected-head",
      head,
    ],
    { WORKPLAN_FAULT_AFTER_PROJECTION: "1" },
  );
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /injected fault after projection/);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );

  const projectionAfterFault = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterFault, /TASK-20260711-001/);
  assert.match(projectionAfterFault, /review/);

  const repair = await run(["sync", "--control-root", root]);
  assert.equal(repair.code, 0, repair.stderr);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  const projectionAfterRepair = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterRepair, /TASK-20260711-001/);
  assert.match(projectionAfterRepair, /Faulty submit/);
  assert.doesNotMatch(projectionAfterRepair, /\| review \|/);
});

test("start rejects tampered claim snapshot and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Tampered snapshot",
        status: "backlog",
        owner: "",
        paths: ["src/tampered"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("session-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);

  const tamperedState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  tamperedState.cards.find((c) => c.id === "TASK-20260711-001").claimSnapshot
    .budget = "999";
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(tamperedState, null, 2)}\n`,
  );

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "start",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "3",
    "--owner",
    owner,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /claim snapshot budget mismatch/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("start rejects extra authority grant key and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Extra authority",
        status: "backlog",
        owner: "",
        paths: ["src/extraauth"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("session-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);

  const tamperedState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  tamperedState.cards.find((c) => c.id === "TASK-20260711-001").authorityGrant
    .extra = true;
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(tamperedState, null, 2)}\n`,
  );

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "start",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "3",
    "--owner",
    owner,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /authorityGrant has incorrect fields/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("integrate transitions review to verifying with descendant commit and full receipt", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Integrable",
        status: "backlog",
        owner: "",
        paths: ["src/integrate"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const owner = codexOwner("worker-1");
  const actor = coordinatorOwner("coordinator-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await startCard(root, "TASK-20260711-001", owner);

  await createBranch(root, "review");
  const reviewHead = await commitOnBranch(root, "review", "review commit");
  await execGit(["-C", root, "checkout", "review"]);
  const submitResult = await run([
    "submit",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
    "--expected-head",
    reviewHead,
  ]);
  assert.equal(submitResult.code, 0, submitResult.stderr);

  await execGit(["-C", root, "checkout", "main"]);
  const integrationCommit = await mergeBranch(root, "review", "integrate review");

  const before = Date.now();
  const result = await run([
    "integrate",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "5",
    "--actor",
    actor,
    "--integration-commit",
    integrationCommit,
  ]);
  const after = Date.now();
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 6);
  assert.equal(output.integrated, true);

  const finalState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(finalState.revision, 6);
  const integratedCard = finalState.cards.find((c) => c.id === "TASK-20260711-001");
  assert.equal(integratedCard.status, "verifying");
  assert.equal(integratedCard.owner, owner);
  assert.deepEqual(integratedCard.paths, ["src/integrate"]);
  assert.ok(integratedCard.integrationReceipt);
  assert.equal(integratedCard.integrationReceipt.actor, actor);
  assert.equal(integratedCard.integrationReceipt.reviewBranch, "review");
  assert.equal(integratedCard.integrationReceipt.reviewHead, reviewHead);
  assert.equal(integratedCard.integrationReceipt.integrationCommit, integrationCommit);
  assert.equal(
    integratedCard.integrationReceipt.definitionHash,
    integratedCard.definition.definitionHash,
  );
  assert.equal(integratedCard.integrationReceipt.revisionFrom, 5);
  assert.equal(integratedCard.integrationReceipt.revisionTo, 6);
  const receiptAt = new Date(integratedCard.integrationReceipt.at).getTime();
  assert.ok(receiptAt >= before && receiptAt <= after);

  const transitionReceipt = integratedCard.receipts[integratedCard.receipts.length - 1];
  assert.equal(transitionReceipt.from, "review");
  assert.equal(transitionReceipt.to, "verifying");
  assert.equal(transitionReceipt.actor, actor);
  assert.equal(transitionReceipt.revisionFrom, 5);
  assert.equal(transitionReceipt.revisionTo, 6);
  assert.equal(transitionReceipt.evidence.reviewBranch, "review");
  assert.equal(transitionReceipt.evidence.reviewHead, reviewHead);
  assert.equal(transitionReceipt.evidence.integrationCommit, integrationCommit);

  const projection = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projection, /TASK-20260711-001/);
  assert.match(projection, /verifying/);
  assert.match(projection, /worker-1/);

  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("integrate rejects non-descendant integration commit and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Non descendant",
        status: "backlog",
        owner: "",
        paths: ["src/nondesc"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const owner = codexOwner("worker-1");
  const actor = coordinatorOwner("coordinator-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await startCard(root, "TASK-20260711-001", owner);

  await createBranch(root, "review");
  const reviewHead = await commitOnBranch(root, "review", "review commit");
  await execGit(["-C", root, "checkout", "review"]);
  const submitResult = await run([
    "submit",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
    "--expected-head",
    reviewHead,
  ]);
  assert.equal(submitResult.code, 0, submitResult.stderr);

  await execGit(["-C", root, "checkout", "main"]);
  const nonDescendantCommit = await commitOnBranch(root, "main", "parallel main commit");

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "integrate",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "5",
    "--actor",
    actor,
    "--integration-commit",
    nonDescendantCommit,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /review head is not an ancestor of integration commit/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("integrate rejects non-coordinator actor before lock and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Actor check",
        status: "backlog",
        owner: "",
        paths: ["src/actorcheck"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const owner = codexOwner("worker-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await startCard(root, "TASK-20260711-001", owner);

  await createBranch(root, "review");
  const reviewHead = await commitOnBranch(root, "review", "review commit");
  await execGit(["-C", root, "checkout", "review"]);
  const submitResult = await run([
    "submit",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
    "--expected-head",
    reviewHead,
  ]);
  assert.equal(submitResult.code, 0, submitResult.stderr);
  await execGit(["-C", root, "checkout", "main"]);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "integrate",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "5",
    "--actor",
    "worker:host:session-1",
    "--integration-commit",
    reviewHead,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /actor must be coordinator/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("integrate rejects non-review status and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Not review",
        status: "backlog",
        owner: "",
        paths: ["src/notreview"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const owner = codexOwner("worker-1");
  const actor = coordinatorOwner("coordinator-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await startCard(root, "TASK-20260711-001", owner);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "integrate",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--actor",
    actor,
    "--integration-commit",
    "0000000000000000000000000000000000000000",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /is not in review status/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("integrate rejects stale expected revision and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Stale integrate",
        status: "backlog",
        owner: "",
        paths: ["src/staleint"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const owner = codexOwner("worker-1");
  const actor = coordinatorOwner("coordinator-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await startCard(root, "TASK-20260711-001", owner);

  await createBranch(root, "review");
  const reviewHead = await commitOnBranch(root, "review", "review commit");
  await execGit(["-C", root, "checkout", "review"]);
  const submitResult = await run([
    "submit",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
    "--expected-head",
    reviewHead,
  ]);
  assert.equal(submitResult.code, 0, submitResult.stderr);
  await execGit(["-C", root, "checkout", "main"]);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "integrate",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--actor",
    actor,
    "--integration-commit",
    reviewHead,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /expected revision 4 but found 5/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("integrate rejects missing integration commit and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Missing commit",
        status: "backlog",
        owner: "",
        paths: ["src/missingcommit"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const owner = codexOwner("worker-1");
  const actor = coordinatorOwner("coordinator-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await startCard(root, "TASK-20260711-001", owner);

  await createBranch(root, "review");
  const reviewHead = await commitOnBranch(root, "review", "review commit");
  await execGit(["-C", root, "checkout", "review"]);
  const submitResult = await run([
    "submit",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
    "--expected-head",
    reviewHead,
  ]);
  assert.equal(submitResult.code, 0, submitResult.stderr);
  await execGit(["-C", root, "checkout", "main"]);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "integrate",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "5",
    "--actor",
    actor,
    "--integration-commit",
    "0000000000000000000000000000000000000000",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /integration commit not found/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("integrate rejects tampered reviewFreeze and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Tampered freeze",
        status: "backlog",
        owner: "",
        paths: ["src/tamperedfreeze"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const owner = codexOwner("worker-1");
  const actor = coordinatorOwner("coordinator-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await startCard(root, "TASK-20260711-001", owner);

  await createBranch(root, "review");
  const reviewHead = await commitOnBranch(root, "review", "review commit");
  await execGit(["-C", root, "checkout", "review"]);
  const submitResult = await run([
    "submit",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
    "--expected-head",
    reviewHead,
  ]);
  assert.equal(submitResult.code, 0, submitResult.stderr);
  await execGit(["-C", root, "checkout", "main"]);

  const tamperedState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  tamperedState.cards.find((c) => c.id === "TASK-20260711-001").reviewFreeze
    .revisionTo = 99;
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(tamperedState, null, 2)}\n`,
  );

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "integrate",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "5",
    "--actor",
    actor,
    "--integration-commit",
    reviewHead,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /reviewFreeze.revisionTo must be revisionFrom \+ 1/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("integrate injected fault after projection leaves canonical state and sync repairs projection", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Faulty integrate",
        status: "backlog",
        owner: "",
        paths: ["src/faultyintegrate"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const owner = codexOwner("worker-1");
  const actor = coordinatorOwner("coordinator-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await startCard(root, "TASK-20260711-001", owner);

  await createBranch(root, "review");
  const reviewHead = await commitOnBranch(root, "review", "review commit");
  await execGit(["-C", root, "checkout", "review"]);
  const submitResult = await run([
    "submit",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
    "--expected-head",
    reviewHead,
  ]);
  assert.equal(submitResult.code, 0, submitResult.stderr);

  await execGit(["-C", root, "checkout", "main"]);
  const integrationCommit = await mergeBranch(root, "review", "integrate review");

  const stateBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run(
    [
      "integrate",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "5",
      "--actor",
      actor,
      "--integration-commit",
      integrationCommit,
    ],
    { WORKPLAN_FAULT_AFTER_PROJECTION: "1" },
  );
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /injected fault after projection/);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );

  const projectionAfterFault = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterFault, /TASK-20260711-001/);
  assert.match(projectionAfterFault, /verifying/);

  const repair = await run(["sync", "--control-root", root]);
  assert.equal(repair.code, 0, repair.stderr);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBefore,
  );
  const projectionAfterRepair = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterRepair, /TASK-20260711-001/);
  assert.match(projectionAfterRepair, /Faulty integrate/);
  assert.doesNotMatch(projectionAfterRepair, /\| verifying \|/);
});

test("accept transitions verifying card to done with full receipt and releases paths", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Acceptable",
        status: "backlog",
        owner: "",
        paths: ["src/accept"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { owner, actor } = await reachVerifying(root, "TASK-20260711-001");

  const beforeState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const cardBefore = beforeState.cards.find((c) => c.id === "TASK-20260711-001");

  const acceptancePath = join(parent, "acceptance.json");
  await writeAcceptance(acceptancePath, cardBefore, actor);

  const before = Date.now();
  const result = await run([
    "accept",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "6",
    "--actor",
    actor,
    "--acceptance-json",
    acceptancePath,
  ]);
  const after = Date.now();
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.revision, 7);
  assert.equal(output.accepted, true);

  const finalState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(finalState.revision, 7);
  const acceptedCard = finalState.cards.find((c) => c.id === "TASK-20260711-001");
  assert.equal(acceptedCard.status, "done");
  assert.equal(acceptedCard.owner, owner);
  assert.deepEqual(acceptedCard.paths, []);
  assert.deepEqual(acceptedCard.releasedPaths, ["src/accept"]);
  assert.ok(acceptedCard.claimSnapshot);
  assert.ok(acceptedCard.integrationReceipt);
  assert.ok(acceptedCard.acceptanceReceipt);
  assert.equal(acceptedCard.acceptanceReceipt.schemaVersion, 1);
  assert.equal(acceptedCard.acceptanceReceipt.verdict, "pass");
  assert.equal(acceptedCard.acceptanceReceipt.evaluator, actor);
  assert.equal(
    acceptedCard.acceptanceReceipt.definitionHash,
    acceptedCard.definition.definitionHash,
  );
  assert.equal(
    acceptedCard.acceptanceReceipt.integrationCommit,
    acceptedCard.integrationReceipt.integrationCommit,
  );
  assert.deepEqual(acceptedCard.acceptanceReceipt.commands, ["npm test"]);
  assert.deepEqual(acceptedCard.acceptanceReceipt.evidenceRefs, ["evidence-1"]);
  assert.match(acceptedCard.acceptanceReceipt.receiptHash, /^sha256:[0-9a-f]{64}$/);
  assert.equal(acceptedCard.acceptanceReceipt.actor, actor);
  assert.deepEqual(acceptedCard.acceptanceReceipt.releasedPaths, ["src/accept"]);
  assert.equal(acceptedCard.acceptanceReceipt.revisionFrom, 6);
  assert.equal(acceptedCard.acceptanceReceipt.revisionTo, 7);
  const receiptAt = new Date(acceptedCard.acceptanceReceipt.at).getTime();
  assert.ok(receiptAt >= before && receiptAt <= after);

  const transitionReceipt = acceptedCard.receipts[acceptedCard.receipts.length - 1];
  assert.equal(transitionReceipt.from, "verifying");
  assert.equal(transitionReceipt.to, "done");
  assert.equal(transitionReceipt.actor, actor);
  assert.equal(transitionReceipt.revisionFrom, 6);
  assert.equal(transitionReceipt.revisionTo, 7);
  assert.equal(transitionReceipt.evidence.evaluator, actor);
  assert.equal(transitionReceipt.evidence.schemaVersion, 1);

  const projection = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projection, /TASK-20260711-001/);
  assert.match(projection, /done/);
  assert.doesNotMatch(projection, /src\/accept/);
});

test("accept rejects non-coordinator actor before lock and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Actor gate",
        status: "backlog",
        owner: "",
        paths: ["src/actorgate"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { actor } = await reachVerifying(root, "TASK-20260711-001");
  const stateBefore = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const cardBefore = stateBefore.cards.find((c) => c.id === "TASK-20260711-001");
  const acceptancePath = join(parent, "acceptance.json");
  await writeAcceptance(acceptancePath, cardBefore, actor);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "accept",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "6",
    "--actor",
    "worker:host:session-1",
    "--acceptance-json",
    acceptancePath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /actor must be coordinator/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("accept rejects non-verifying status and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Not verifying",
        status: "backlog",
        owner: "",
        paths: ["src/notverifying"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const owner = codexOwner("worker-1");
  const actor = coordinatorOwner("coordinator-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await startCard(root, "TASK-20260711-001", owner);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const acceptancePath = join(parent, "acceptance.json");
  await writeFile(
    acceptancePath,
    JSON.stringify({
      schemaVersion: 1,
      verdict: "pass",
      evaluator: actor,
      definitionHash: "sha256:0000000000000000000000000000000000000000000000000000000000000000",
      integrationCommit: "0000000000000000000000000000000000000000",
      commands: ["npm test"],
      evidenceRefs: ["evidence-1"],
      receiptHash: "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    }),
  );

  const result = await run([
    "accept",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--actor",
    actor,
    "--acceptance-json",
    acceptancePath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /is not in verifying status/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("accept rejects stale expected revision and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Stale accept",
        status: "backlog",
        owner: "",
        paths: ["src/staleaccept"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { actor } = await reachVerifying(root, "TASK-20260711-001");
  const stateBefore = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const cardBefore = stateBefore.cards.find((c) => c.id === "TASK-20260711-001");
  const acceptancePath = join(parent, "acceptance.json");
  await writeAcceptance(acceptancePath, cardBefore, actor);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "accept",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "5",
    "--actor",
    actor,
    "--acceptance-json",
    acceptancePath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /expected revision 5 but found 6/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("accept rejects tampered integrationReceipt and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Tampered receipt",
        status: "backlog",
        owner: "",
        paths: ["src/tamperedreceipt"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { actor } = await reachVerifying(root, "TASK-20260711-001");
  const tamperedState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  tamperedState.cards.find((c) => c.id === "TASK-20260711-001").integrationReceipt
    .definitionHash =
    "sha256:0000000000000000000000000000000000000000000000000000000000000000";
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(tamperedState, null, 2)}\n`,
  );

  const cardBefore = tamperedState.cards.find((c) => c.id === "TASK-20260711-001");
  const acceptancePath = join(parent, "acceptance.json");
  await writeAcceptance(acceptancePath, cardBefore, actor);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "accept",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "6",
    "--actor",
    actor,
    "--acceptance-json",
    acceptancePath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /integrationReceipt definitionHash mismatch/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("accept rejects tampered integrationReceipt actor and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Bad actor receipt",
        status: "backlog",
        owner: "",
        paths: ["src/badactorreceipt"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { actor } = await reachVerifying(root, "TASK-20260711-001");
  const tamperedState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  tamperedState.cards.find((c) => c.id === "TASK-20260711-001").integrationReceipt
    .actor = "worker:host:session-1";
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(tamperedState, null, 2)}\n`,
  );

  const cardBefore = tamperedState.cards.find((c) => c.id === "TASK-20260711-001");
  const acceptancePath = join(parent, "acceptance.json");
  await writeAcceptance(acceptancePath, cardBefore, actor);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "accept",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "6",
    "--actor",
    actor,
    "--acceptance-json",
    acceptancePath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /actor must be coordinator/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("accept rejects tampered integrationReceipt commit shape and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Bad commit receipt",
        status: "backlog",
        owner: "",
        paths: ["src/badcommitreceipt"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { actor } = await reachVerifying(root, "TASK-20260711-001");
  const tamperedState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  tamperedState.cards.find((c) => c.id === "TASK-20260711-001").integrationReceipt
    .integrationCommit = "not-a-hash";
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(tamperedState, null, 2)}\n`,
  );

  const cardBefore = tamperedState.cards.find((c) => c.id === "TASK-20260711-001");
  const acceptancePath = join(parent, "acceptance.json");
  await writeAcceptance(acceptancePath, cardBefore, actor, {
    integrationCommit: "not-a-hash",
  });

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "accept",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "6",
    "--actor",
    actor,
    "--acceptance-json",
    acceptancePath,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(
    result.stderr,
    /integrationReceipt integrationCommit must be 40 or 64 lowercase hex characters/,
  );

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("accept rejects invalid acceptance variants and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Variant gate",
        status: "backlog",
        owner: "",
        paths: ["src/variantgate"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { actor } = await reachVerifying(root, "TASK-20260711-001");
  const stateBefore = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const cardBefore = stateBefore.cards.find((c) => c.id === "TASK-20260711-001");

  const cases = [
    {
      name: "evaluator mismatch",
      overrides: { evaluator: coordinatorOwner("other-session") },
      expected: /acceptance.evaluator must equal actor/,
    },
    {
      name: "definitionHash mismatch",
      overrides: {
        definitionHash:
          "sha256:0000000000000000000000000000000000000000000000000000000000000000",
      },
      expected: /acceptance definitionHash mismatch/,
    },
    {
      name: "integrationCommit mismatch",
      overrides: { integrationCommit: "0000000000000000000000000000000000000000" },
      expected: /acceptance integrationCommit mismatch/,
    },
    {
      name: "commands mismatch",
      overrides: { commands: ["npm test", "extra"] },
      expected: /acceptance commands mismatch/,
    },
    {
      name: "commands non-array",
      overrides: { commands: "npm test" },
      expected: /acceptance commands must be a string array/,
    },
    {
      name: "commands non-string element",
      overrides: { commands: ["npm test", 123] },
      expected: /acceptance commands must be a string array/,
    },
    {
      name: "empty evidenceRefs",
      overrides: { evidenceRefs: [] },
      expected: /acceptance evidenceRefs must be a nonempty array of nonempty strings/,
    },
    {
      name: "receiptHash mismatch",
      overrides: {
        receiptHash:
          "sha256:0000000000000000000000000000000000000000000000000000000000000000",
      },
      expected: /acceptance receiptHash mismatch/,
    },
  ];

  for (const c of cases) {
    const acceptancePath = join(parent, `acceptance-${c.name}.json`);
    await writeAcceptance(acceptancePath, cardBefore, actor, c.overrides);

    const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
    const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

    const result = await run([
      "accept",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "6",
      "--actor",
      actor,
      "--acceptance-json",
      acceptancePath,
    ]);
    assert.notEqual(result.code, 0, `case ${c.name} should fail`);
    assert.match(result.stderr, c.expected, `case ${c.name} message mismatch`);

    assert.deepEqual(
      await readFile(join(root, "WORKPLAN.md")),
      workplanBefore,
      `case ${c.name} changed WORKPLAN.md`,
    );
    assert.deepEqual(
      await readFile(join(root, ".workplan", "state.json")),
      stateBytesBefore,
      `case ${c.name} changed state.json`,
    );
    await assert.rejects(stat(join(root, ".workplan", "lock")), {
      code: "ENOENT",
    });
  }
});

test("accept injected fault after projection leaves canonical state and sync repairs projection", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Faulty accept",
        status: "backlog",
        owner: "",
        paths: ["src/faultyaccept"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { actor } = await reachVerifying(root, "TASK-20260711-001");
  const stateBefore = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const cardBefore = stateBefore.cards.find((c) => c.id === "TASK-20260711-001");
  const acceptancePath = join(parent, "acceptance.json");
  await writeAcceptance(acceptancePath, cardBefore, actor);

  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run(
    [
      "accept",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "6",
      "--actor",
      actor,
      "--acceptance-json",
      acceptancePath,
    ],
    { WORKPLAN_FAULT_AFTER_PROJECTION: "1" },
  );
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /injected fault after projection/);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );

  const projectionAfterFault = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterFault, /TASK-20260711-001/);
  assert.match(projectionAfterFault, /done/);

  const repair = await run(["sync", "--control-root", root]);
  assert.equal(repair.code, 0, repair.stderr);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  const projectionAfterRepair = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterRepair, /TASK-20260711-001/);
  assert.match(projectionAfterRepair, /Faulty accept/);
  assert.match(projectionAfterRepair, /verifying/);
  assert.doesNotMatch(projectionAfterRepair, /\| done \|/);
});

test("block from claimed retainPaths=true records blockedFrom and preserves snapshots", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Blocked from claimed",
        status: "backlog",
        owner: "",
        paths: ["src/blockclaimed"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const { owner } = await reachClaimed(root, "TASK-20260711-001");
  const reason = "blocked from claimed";

  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));
  const stateBefore = JSON.parse(stateBytesBefore);
  const cardBefore = stateBefore.cards.find((c) => c.id === "TASK-20260711-001");
  const pathsBefore = [...cardBefore.paths];
  const claimSnapshotBefore = cardBefore.claimSnapshot;
  const definitionBefore = cardBefore.definition;
  const authorityGrantBefore = cardBefore.authorityGrant;

  const result = await blockCard(root, "TASK-20260711-001", owner, "3", reason, true);
  assert.equal(result.revision, 4);
  assert.equal(result.blocked, true);

  const stateBytesAfter = await readFile(join(root, ".workplan", "state.json"));
  const stateAfter = JSON.parse(stateBytesAfter);
  assert.equal(stateAfter.revision, 4);
  const blockedCard = stateAfter.cards.find((c) => c.id === "TASK-20260711-001");
  assert.equal(blockedCard.status, "blocked");
  assert.equal(blockedCard.blockedFrom, "claimed");
  assert.equal(blockedCard.blockReason, reason);
  assert.equal(blockedCard.retainPaths, true);
  assert.equal(blockedCard.owner, owner);
  assert.deepEqual(blockedCard.paths, pathsBefore);
  assert.deepEqual(blockedCard.claimSnapshot, claimSnapshotBefore);
  assert.deepEqual(blockedCard.definition, definitionBefore);
  assert.deepEqual(blockedCard.authorityGrant, authorityGrantBefore);
  assert.equal(blockedCard.reviewFreeze, undefined);
  assert.equal(blockedCard.integrationReceipt, undefined);

  const lastReceipt = blockedCard.receipts[blockedCard.receipts.length - 1];
  assert.equal(lastReceipt.from, "claimed");
  assert.equal(lastReceipt.to, "blocked");
  assert.equal(lastReceipt.actor, owner);
  assert.equal(lastReceipt.revisionFrom, 3);
  assert.equal(lastReceipt.revisionTo, 4);
  assert.equal(lastReceipt.evidence.reason, reason);
  assert.equal(lastReceipt.evidence.retainPaths, true);
  assert.match(lastReceipt.at, /^\d{4}-\d{2}-\d{2}T/);

  const projection = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projection, /TASK-20260711-001/);
  assert.match(projection, /\| blocked \|/);

  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("block from in_progress retainPaths=true preserves paths and snapshots", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Blocked from in_progress",
        status: "backlog",
        owner: "",
        paths: ["src/blockinprogress"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const { owner } = await reachInProgress(root, "TASK-20260711-001");
  const reason = "blocked from in_progress";

  const stateBefore = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const cardBefore = stateBefore.cards.find((c) => c.id === "TASK-20260711-001");
  const pathsBefore = [...cardBefore.paths];
  const claimSnapshotBefore = cardBefore.claimSnapshot;

  const result = await blockCard(root, "TASK-20260711-001", owner, "4", reason, true);
  assert.equal(result.revision, 5);
  assert.equal(result.blocked, true);

  const stateAfter = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const blockedCard = stateAfter.cards.find((c) => c.id === "TASK-20260711-001");
  assert.equal(blockedCard.status, "blocked");
  assert.equal(blockedCard.blockedFrom, "in_progress");
  assert.equal(blockedCard.blockReason, reason);
  assert.equal(blockedCard.retainPaths, true);
  assert.deepEqual(blockedCard.paths, pathsBefore);
  assert.deepEqual(blockedCard.claimSnapshot, claimSnapshotBefore);
  assert.equal(blockedCard.reviewFreeze, undefined);
  assert.equal(blockedCard.integrationReceipt, undefined);

  const lastReceipt = blockedCard.receipts[blockedCard.receipts.length - 1];
  assert.equal(lastReceipt.from, "in_progress");
  assert.equal(lastReceipt.to, "blocked");
  assert.equal(lastReceipt.actor, owner);
  assert.equal(lastReceipt.revisionFrom, 4);
  assert.equal(lastReceipt.revisionTo, 5);
  assert.equal(lastReceipt.evidence.retainPaths, true);

  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("block from review retainPaths=true preserves reviewFreeze", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Blocked from review",
        status: "backlog",
        owner: "",
        paths: ["src/blockreview"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { owner } = await reachReview(root, "TASK-20260711-001");
  const stateBefore = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const cardBefore = stateBefore.cards.find((c) => c.id === "TASK-20260711-001");
  const reviewFreezeBefore = cardBefore.reviewFreeze;
  const pathsBefore = [...cardBefore.paths];
  const claimSnapshotBefore = cardBefore.claimSnapshot;

  const reason = "blocked from review";
  const result = await blockCard(root, "TASK-20260711-001", owner, "5", reason, true);
  assert.equal(result.revision, 6);
  assert.equal(result.blocked, true);

  const stateAfter = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const blockedCard = stateAfter.cards.find((c) => c.id === "TASK-20260711-001");
  assert.equal(blockedCard.status, "blocked");
  assert.equal(blockedCard.blockedFrom, "review");
  assert.equal(blockedCard.blockReason, reason);
  assert.equal(blockedCard.retainPaths, true);
  assert.deepEqual(blockedCard.paths, pathsBefore);
  assert.deepEqual(blockedCard.claimSnapshot, claimSnapshotBefore);
  assert.deepEqual(blockedCard.reviewFreeze, reviewFreezeBefore);
  assert.equal(blockedCard.integrationReceipt, undefined);

  const lastReceipt = blockedCard.receipts[blockedCard.receipts.length - 1];
  assert.equal(lastReceipt.from, "review");
  assert.equal(lastReceipt.to, "blocked");
  assert.equal(lastReceipt.revisionFrom, 5);
  assert.equal(lastReceipt.revisionTo, 6);
  assert.equal(lastReceipt.evidence.retainPaths, true);

  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("block from verifying retainPaths=true preserves reviewFreeze and integrationReceipt", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Blocked from verifying",
        status: "backlog",
        owner: "",
        paths: ["src/blockverifying"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { owner } = await reachVerifying(root, "TASK-20260711-001");
  const stateBefore = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const cardBefore = stateBefore.cards.find((c) => c.id === "TASK-20260711-001");
  const reviewFreezeBefore = cardBefore.reviewFreeze;
  const integrationReceiptBefore = cardBefore.integrationReceipt;
  const pathsBefore = [...cardBefore.paths];
  const claimSnapshotBefore = cardBefore.claimSnapshot;

  const reason = "blocked from verifying";
  const result = await blockCard(root, "TASK-20260711-001", owner, "6", reason, true);
  assert.equal(result.revision, 7);
  assert.equal(result.blocked, true);

  const stateAfter = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const blockedCard = stateAfter.cards.find((c) => c.id === "TASK-20260711-001");
  assert.equal(blockedCard.status, "blocked");
  assert.equal(blockedCard.blockedFrom, "verifying");
  assert.equal(blockedCard.blockReason, reason);
  assert.equal(blockedCard.retainPaths, true);
  assert.deepEqual(blockedCard.paths, pathsBefore);
  assert.deepEqual(blockedCard.claimSnapshot, claimSnapshotBefore);
  assert.deepEqual(blockedCard.reviewFreeze, reviewFreezeBefore);
  assert.deepEqual(blockedCard.integrationReceipt, integrationReceiptBefore);

  const lastReceipt = blockedCard.receipts[blockedCard.receipts.length - 1];
  assert.equal(lastReceipt.from, "verifying");
  assert.equal(lastReceipt.to, "blocked");
  assert.equal(lastReceipt.actor, owner);
  assert.equal(lastReceipt.revisionFrom, 6);
  assert.equal(lastReceipt.revisionTo, 7);
  assert.equal(lastReceipt.evidence.reason, reason);
  assert.equal(lastReceipt.evidence.retainPaths, true);

  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("block from verifying retainPaths=false releases path so peer card can overlap", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Released block",
        status: "backlog",
        owner: "",
        paths: ["src/blockreleased"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { owner } = await reachVerifying(root, "TASK-20260711-001");
  const reason = "blocked and released";

  const blockResult = await blockCard(
    root,
    "TASK-20260711-001",
    owner,
    "6",
    reason,
    false,
  );
  assert.equal(blockResult.revision, 7);
  assert.equal(blockResult.blocked, true);

  const stateAfterBlock = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const blockedCard = stateAfterBlock.cards.find(
    (c) => c.id === "TASK-20260711-001",
  );
  assert.equal(blockedCard.status, "blocked");
  assert.equal(blockedCard.retainPaths, false);
  assert.deepEqual(blockedCard.paths, ["src/blockreleased"]);

  const peerAdd = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "7",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Peer overlap",
    "--paths",
    "src/blockreleased",
  ]);
  assert.equal(peerAdd.code, 0, peerAdd.stderr);
  const peerOutput = JSON.parse(peerAdd.stdout);
  assert.equal(peerOutput.revision, 8);
  assert.equal(peerOutput.added, true);

  const finalState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(finalState.revision, 8);
  assert.equal(
    finalState.cards.find((c) => c.id === "TASK-20260711-002").status,
    "backlog",
  );

  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("block rejects wrong owner, stale revision, whitespace-only reason, and invalid retain flag with bytes and lock preserved", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Reject block",
        status: "backlog",
        owner: "",
        paths: ["src/rejectblock"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const ownerA = codexOwner("session-a");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", ownerA);

  const cases = [
    {
      name: "wrong owner",
      args: [
        "block",
        "TASK-20260711-001",
        "--control-root",
        root,
        "--caller-worktree",
        root,
        "--expected-revision",
        "3",
        "--owner",
        codexOwner("session-other"),
        "--reason",
        "x",
        "--retain-paths",
        "true",
      ],
      expected: /is not owned by/,
    },
    {
      name: "stale revision",
      args: [
        "block",
        "TASK-20260711-001",
        "--control-root",
        root,
        "--caller-worktree",
        root,
        "--expected-revision",
        "2",
        "--owner",
        ownerA,
        "--reason",
        "x",
        "--retain-paths",
        "true",
      ],
      expected: /expected revision 2 but found 3/,
    },
    {
      name: "whitespace-only reason",
      args: [
        "block",
        "TASK-20260711-001",
        "--control-root",
        root,
        "--caller-worktree",
        root,
        "--expected-revision",
        "3",
        "--owner",
        ownerA,
        "--reason",
        "   ",
        "--retain-paths",
        "true",
      ],
      expected: /--reason must be a non-empty string/,
    },
    {
      name: "invalid retain flag",
      args: [
        "block",
        "TASK-20260711-001",
        "--control-root",
        root,
        "--caller-worktree",
        root,
        "--expected-revision",
        "3",
        "--owner",
        ownerA,
        "--reason",
        "x",
        "--retain-paths",
        "yes",
      ],
      expected: /--retain-paths must be true or false/,
    },
  ];

  for (const c of cases) {
    const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
    const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

    const result = await run(c.args);
    assert.notEqual(result.code, 0, `case ${c.name} should fail`);
    assert.match(result.stderr, c.expected, `case ${c.name} message mismatch`);

    assert.deepEqual(
      await readFile(join(root, "WORKPLAN.md")),
      workplanBefore,
      `case ${c.name} changed WORKPLAN.md`,
    );
    assert.deepEqual(
      await readFile(join(root, ".workplan", "state.json")),
      stateBytesBefore,
      `case ${c.name} changed state.json`,
    );
    await assert.rejects(stat(join(root, ".workplan", "lock")), {
      code: "ENOENT",
    });
  }
});

test("block rejects invalid source status and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Ready only",
        status: "backlog",
        owner: "",
        paths: ["src/notblockable"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("session-1");
  await readyCard(root, "TASK-20260711-001");

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "block",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "2",
    "--owner",
    owner,
    "--reason",
    "trying too early",
    "--retain-paths",
    "true",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /cannot be blocked from ready status/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("block rejects exact wrong worktree from same repository and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Wrong worktree",
        status: "backlog",
        owner: "",
        paths: ["src/wrongworktree"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("worker-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);

  const worktreePath = join(parent, "linked");
  await execGit(["-C", root, "worktree", "add", worktreePath]);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "block",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    worktreePath,
    "--expected-revision",
    "3",
    "--owner",
    owner,
    "--reason",
    "wrong tree",
    "--retain-paths",
    "true",
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /worktreePath must match callerWorktree/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("resume from blocked retainPaths=true restores in_progress and clears transient block fields", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Resume kept",
        status: "backlog",
        owner: "",
        paths: ["src/resumekeep"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const { owner } = await reachClaimed(root, "TASK-20260711-001");
  const reason = "need help";
  await blockCard(root, "TASK-20260711-001", owner, "3", reason, true);

  const stateBefore = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const cardBefore = stateBefore.cards.find((c) => c.id === "TASK-20260711-001");
  const pathsBefore = [...cardBefore.paths];
  const claimSnapshotBefore = cardBefore.claimSnapshot;
  const definitionBefore = cardBefore.definition;
  const authorityGrantBefore = cardBefore.authorityGrant;
  const receiptsBefore = cardBefore.receipts;

  const result = await resumeCard(root, "TASK-20260711-001", owner, "4");
  assert.equal(result.revision, 5);
  assert.equal(result.resumed, true);

  const stateAfter = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  assert.equal(stateAfter.revision, 5);
  const resumedCard = stateAfter.cards.find(
    (c) => c.id === "TASK-20260711-001",
  );
  assert.equal(resumedCard.status, "in_progress");
  assert.equal(resumedCard.owner, owner);
  assert.deepEqual(resumedCard.paths, pathsBefore);
  assert.deepEqual(resumedCard.claimSnapshot, claimSnapshotBefore);
  assert.deepEqual(resumedCard.definition, definitionBefore);
  assert.deepEqual(resumedCard.authorityGrant, authorityGrantBefore);
  assert.equal(resumedCard.blockedFrom, undefined);
  assert.equal(resumedCard.blockReason, undefined);
  assert.equal(resumedCard.retainPaths, undefined);
  assert.equal(resumedCard.receipts.length, receiptsBefore.length + 1);
  for (let i = 0; i < receiptsBefore.length; i += 1) {
    assert.deepEqual(resumedCard.receipts[i], receiptsBefore[i]);
  }
  const resumeReceipt = resumedCard.receipts[resumedCard.receipts.length - 1];
  assert.equal(resumeReceipt.from, "blocked");
  assert.equal(resumeReceipt.to, "in_progress");
  assert.equal(resumeReceipt.actor, owner);
  assert.equal(resumeReceipt.revisionFrom, 4);
  assert.equal(resumeReceipt.revisionTo, 5);
  assert.equal(resumeReceipt.evidence.blockedFrom, "claimed");
  assert.equal(resumeReceipt.evidence.reason, reason);
  assert.equal(resumeReceipt.evidence.retainPaths, true);
  assert.match(resumeReceipt.at, /^\d{4}-\d{2}-\d{2}T/);

  const projection = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projection, /TASK-20260711-001/);
  assert.match(projection, /\| in_progress \|/);

  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("resume from blocked retainPaths=false clears transient fields and reclaims the path", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Resume released",
        status: "backlog",
        owner: "",
        paths: ["src/resumerelease"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const { owner } = await reachClaimed(root, "TASK-20260711-001");
  const reason = "let others run";
  await blockCard(root, "TASK-20260711-001", owner, "3", reason, false);

  const stateBefore = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const cardBefore = stateBefore.cards.find((c) => c.id === "TASK-20260711-001");
  const pathsBefore = [...cardBefore.paths];
  const claimSnapshotBefore = cardBefore.claimSnapshot;

  const result = await resumeCard(root, "TASK-20260711-001", owner, "4");
  assert.equal(result.revision, 5);
  assert.equal(result.resumed, true);

  const stateAfter = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const resumedCard = stateAfter.cards.find(
    (c) => c.id === "TASK-20260711-001",
  );
  assert.equal(resumedCard.status, "in_progress");
  assert.equal(resumedCard.owner, owner);
  assert.deepEqual(resumedCard.paths, pathsBefore);
  assert.deepEqual(resumedCard.claimSnapshot, claimSnapshotBefore);
  assert.equal(resumedCard.blockedFrom, undefined);
  assert.equal(resumedCard.blockReason, undefined);
  assert.equal(resumedCard.retainPaths, undefined);

  const resumeReceipt = resumedCard.receipts[resumedCard.receipts.length - 1];
  assert.equal(resumeReceipt.from, "blocked");
  assert.equal(resumeReceipt.to, "in_progress");
  assert.equal(resumeReceipt.actor, owner);
  assert.equal(resumeReceipt.evidence.blockedFrom, "claimed");
  assert.equal(resumeReceipt.evidence.reason, reason);
  assert.equal(resumeReceipt.evidence.retainPaths, false);

  const peerAdd = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "5",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Late peer",
    "--paths",
    "src/resumerelease",
  ]);
  assert.notEqual(peerAdd.code, 0);
  assert.match(peerAdd.stderr, /overlap/);

  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("resume fails closed when peer acquired overlapping reserved path and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Card A",
        status: "backlog",
        owner: "",
        paths: ["src/shared"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const { owner } = await reachClaimed(root, "TASK-20260711-001");
  await blockCard(root, "TASK-20260711-001", owner, "3", "release path", false);

  const peerAdd = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Peer card",
    "--paths",
    "src/shared",
  ]);
  assert.equal(peerAdd.code, 0, peerAdd.stderr);
  assert.equal(JSON.parse(peerAdd.stdout).revision, 5);

  await readyCard(root, "TASK-20260711-002", {}, "5");

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "resume",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "6",
    "--owner",
    owner,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /overlap/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("resume rejects owner WIP violation after intervening peer claim and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Card A",
        status: "backlog",
        owner: "",
        paths: ["src/area"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("wip-session");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await blockCard(root, "TASK-20260711-001", owner, "3", "blocked once", false);

  const peerAdd = await run([
    "add",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--id",
    "TASK-20260711-002",
    "--outcome",
    "Peer card",
    "--paths",
    "src/other",
  ]);
  assert.equal(peerAdd.code, 0, peerAdd.stderr);
  await readyCard(root, "TASK-20260711-002", {}, "5");
  await claimCard(root, "TASK-20260711-002", owner, "6");

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "resume",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "7",
    "--owner",
    owner,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /owner already has WIP card/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("resume rejects wrong owner, stale revision, and nonblocked source with bytes and lock preserved", async (context) => {
  const cases = [
    {
      name: "wrong owner",
      args: ["--owner", codexOwner("session-other")],
      expect: /is not owned by/,
    },
    {
      name: "stale revision",
      args: ["--expected-revision", "3"],
      expect: /expected revision 3 but found 4/,
    },
    {
      name: "nonblocked source",
      tamper: async (root, id) => {
        const current = JSON.parse(
          await readFile(join(root, ".workplan", "state.json"), "utf8"),
        );
        current.cards.find((c) => c.id === id).status = "claimed";
        await writeFile(
          join(root, ".workplan", "state.json"),
          `${JSON.stringify(current, null, 2)}\n`,
        );
      },
      expect: /is not in blocked status/,
    },
  ];

  for (const c of cases) {
    const { root, parent } = await repository(
      state([
        card({
          id: "TASK-20260711-001",
          title: "Resume reject",
          status: "backlog",
          owner: "",
          paths: ["src/resumereject"],
        }),
      ]),
    );
    context.after(() => rm(parent, { recursive: true, force: true }));

    const owner = codexOwner("wip-session");
    await readyCard(root, "TASK-20260711-001");
    await claimCard(root, "TASK-20260711-001", owner);
    await blockCard(root, "TASK-20260711-001", owner, "3", "blocked", true);

    if (c.tamper) {
      await c.tamper(root, "TASK-20260711-001");
    }

    const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
    const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

    const baseArgs = [
      "resume",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "4",
      "--owner",
      owner,
    ];
    const extra = c.args || [];
    const result = await run([...baseArgs, ...extra]);

    assert.notEqual(result.code, 0, `case ${c.name} should fail`);
    assert.match(result.stderr, c.expect, `case ${c.name} message mismatch`);

    assert.deepEqual(
      await readFile(join(root, "WORKPLAN.md")),
      workplanBefore,
      `case ${c.name} changed WORKPLAN.md`,
    );
    assert.deepEqual(
      await readFile(join(root, ".workplan", "state.json")),
      stateBytesBefore,
      `case ${c.name} changed state.json`,
    );
    await assert.rejects(stat(join(root, ".workplan", "lock")), {
      code: "ENOENT",
    });
  }
});

test("resume rejects wrong caller worktree from same repository and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Resume wrong tree",
        status: "backlog",
        owner: "",
        paths: ["src/resumewrongtree"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const owner = codexOwner("worker-1");
  await readyCard(root, "TASK-20260711-001");
  await claimCard(root, "TASK-20260711-001", owner);
  await blockCard(root, "TASK-20260711-001", owner, "3", "wrong tree", true);

  const worktreePath = join(parent, "linked");
  await execGit(["-C", root, "worktree", "add", worktreePath]);

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "resume",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    worktreePath,
    "--expected-revision",
    "4",
    "--owner",
    owner,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /worktreePath must match callerWorktree/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("resume rejects tampered blockedFrom, blockReason, and retainPaths and preserves bytes", async (context) => {
  const cases = [
    {
      name: "tampered blockedFrom",
      tamper: { blockedFrom: "garbage" },
      expect: /invalid blockedFrom/,
    },
    {
      name: "tampered blockReason empty",
      tamper: { blockReason: "" },
      expect: /blockReason must be a non-empty string/,
    },
    {
      name: "tampered retainPaths type",
      tamper: { retainPaths: "true" },
      expect: /retainPaths must be a boolean/,
    },
  ];

  for (const c of cases) {
    const { root, parent } = await repository(
      state([
        card({
          id: "TASK-20260711-001",
          title: "Resume tamper field",
          status: "backlog",
          owner: "",
          paths: ["src/resumetamper"],
        }),
      ]),
    );
    context.after(() => rm(parent, { recursive: true, force: true }));

    const owner = codexOwner("worker-1");
    await readyCard(root, "TASK-20260711-001");
    await claimCard(root, "TASK-20260711-001", owner);
    await blockCard(root, "TASK-20260711-001", owner, "3", "good reason", true);

    const current = JSON.parse(
      await readFile(join(root, ".workplan", "state.json"), "utf8"),
    );
    const cardRef = current.cards.find((c2) => c2.id === "TASK-20260711-001");
    for (const [key, value] of Object.entries(c.tamper)) {
      cardRef[key] = value;
    }
    await writeFile(
      join(root, ".workplan", "state.json"),
      `${JSON.stringify(current, null, 2)}\n`,
    );

    const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
    const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

    const result = await run([
      "resume",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "4",
      "--owner",
      owner,
    ]);
    assert.notEqual(result.code, 0, `case ${c.name} should fail`);
    assert.match(result.stderr, c.expect, `case ${c.name} message mismatch`);

    assert.deepEqual(
      await readFile(join(root, "WORKPLAN.md")),
      workplanBefore,
      `case ${c.name} changed WORKPLAN.md`,
    );
    assert.deepEqual(
      await readFile(join(root, ".workplan", "state.json")),
      stateBytesBefore,
      `case ${c.name} changed state.json`,
    );
    await assert.rejects(stat(join(root, ".workplan", "lock")), {
      code: "ENOENT",
    });
  }
});

test("resume rejects tampered latest blocked receipt field, evidence, and revision parity and preserves bytes", async (context) => {
  const cases = [
    {
      name: "extra field on receipt",
      tamper: (r) => {
        r.extra = "x";
      },
      expect: /blocked receipt has incorrect fields/,
    },
    {
      name: "evidence reason mismatch",
      tamper: (r) => {
        r.evidence.reason = "different";
      },
      expect: /blocked receipt evidence reason mismatch/,
    },
    {
      name: "evidence retainPaths mismatch",
      tamper: (r) => {
        r.evidence.retainPaths = false;
      },
      expect: /blocked receipt evidence retainPaths mismatch/,
    },
    {
      name: "revisionTo parity broken",
      tamper: (r) => {
        r.revisionTo = r.revisionFrom + 2;
      },
      expect: /blocked receipt revisionTo must be revisionFrom \+ 1/,
    },
    {
      name: "at non-ISO timestamp",
      tamper: (r) => {
        r.at = "not-iso";
      },
      expect: /blocked receipt at must be a valid ISO timestamp/,
    },
  ];

  for (const c of cases) {
    const { root, parent } = await repository(
      state([
        card({
          id: "TASK-20260711-001",
          title: "Resume tamper receipt",
          status: "backlog",
          owner: "",
          paths: ["src/resumetamperrec"],
        }),
      ]),
    );
    context.after(() => rm(parent, { recursive: true, force: true }));

    const owner = codexOwner("worker-1");
    await readyCard(root, "TASK-20260711-001");
    await claimCard(root, "TASK-20260711-001", owner);
    await blockCard(root, "TASK-20260711-001", owner, "3", "good reason", true);

    const current = JSON.parse(
      await readFile(join(root, ".workplan", "state.json"), "utf8"),
    );
    const cardRef = current.cards.find((c2) => c2.id === "TASK-20260711-001");
    const blockedReceipts = cardRef.receipts.filter((r) => r.to === "blocked");
    c.tamper(blockedReceipts[blockedReceipts.length - 1]);
    await writeFile(
      join(root, ".workplan", "state.json"),
      `${JSON.stringify(current, null, 2)}\n`,
    );

    const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
    const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

    const result = await run([
      "resume",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "4",
      "--owner",
      owner,
    ]);
    assert.notEqual(result.code, 0, `case ${c.name} should fail`);
    assert.match(result.stderr, c.expect, `case ${c.name} message mismatch`);

    assert.deepEqual(
      await readFile(join(root, "WORKPLAN.md")),
      workplanBefore,
      `case ${c.name} changed WORKPLAN.md`,
    );
    assert.deepEqual(
      await readFile(join(root, ".workplan", "state.json")),
      stateBytesBefore,
      `case ${c.name} changed state.json`,
    );
    await assert.rejects(stat(join(root, ".workplan", "lock")), {
      code: "ENOENT",
    });
  }
});

test("block injected fault after projection leaves canonical state and sync repairs projection", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Faulty block",
        status: "backlog",
        owner: "",
        paths: ["src/faultyblock"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const { owner } = await reachClaimed(root, "TASK-20260711-001");
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run(
    [
      "block",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "3",
      "--owner",
      owner,
      "--reason",
      "fault",
      "--retain-paths",
      "true",
    ],
    { WORKPLAN_FAULT_AFTER_PROJECTION: "1" },
  );
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /injected fault after projection/);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );

  const projectionAfterFault = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterFault, /TASK-20260711-001/);
  assert.match(projectionAfterFault, /\| blocked \|/);

  const repair = await run(["sync", "--control-root", root]);
  assert.equal(repair.code, 0, repair.stderr);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  const projectionAfterRepair = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterRepair, /TASK-20260711-001/);
  assert.match(projectionAfterRepair, /claimed/);
  assert.doesNotMatch(projectionAfterRepair, /\| blocked \|/);
});

test("resume injected fault after projection leaves canonical state and sync repairs projection", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Faulty resume",
        status: "backlog",
        owner: "",
        paths: ["src/faultyresume"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  const { owner } = await reachClaimed(root, "TASK-20260711-001");
  await blockCard(root, "TASK-20260711-001", owner, "3", "wait", true);
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run(
    [
      "resume",
      "TASK-20260711-001",
      "--control-root",
      root,
      "--caller-worktree",
      root,
      "--expected-revision",
      "4",
      "--owner",
      owner,
    ],
    { WORKPLAN_FAULT_AFTER_PROJECTION: "1" },
  );
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /injected fault after projection/);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );

  const projectionAfterFault = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterFault, /TASK-20260711-001/);
  assert.match(projectionAfterFault, /\| in_progress \|/);

  const repair = await run(["sync", "--control-root", root]);
  assert.equal(repair.code, 0, repair.stderr);

  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  const projectionAfterRepair = await readFile(join(root, "WORKPLAN.md"), "utf8");
  assert.match(projectionAfterRepair, /TASK-20260711-001/);
  assert.match(projectionAfterRepair, /blocked/);
  assert.doesNotMatch(projectionAfterRepair, /\| in_progress \|/);
});

test("resume rejects extra evidence key in blocked receipt and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Extra evidence",
        status: "backlog",
        owner: "",
        paths: ["src/extraevidence"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { owner } = await reachClaimed(root, "TASK-20260711-001");
  await blockCard(root, "TASK-20260711-001", owner, "3", "wait", true);

  const tamperedState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const tamperedCard = tamperedState.cards.find((c) => c.id === "TASK-20260711-001");
  tamperedCard.receipts[tamperedCard.receipts.length - 1].evidence.extra = true;
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(tamperedState, null, 2)}\n`,
  );

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "resume",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /blocked receipt evidence has incorrect fields/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("resume rejects whitespace-only blockReason with matching receipt and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Whitespace reason",
        status: "backlog",
        owner: "",
        paths: ["src/whitereason"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { owner } = await reachClaimed(root, "TASK-20260711-001");
  await blockCard(root, "TASK-20260711-001", owner, "3", "valid reason", true);

  const tamperedState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const tamperedCard = tamperedState.cards.find((c) => c.id === "TASK-20260711-001");
  tamperedCard.blockReason = "   ";
  tamperedCard.receipts[tamperedCard.receipts.length - 1].evidence.reason = "   ";
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(tamperedState, null, 2)}\n`,
  );

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "resume",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /blockReason must be a non-empty string/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("resume rejects trailing non-block receipt and preserves bytes", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Trailing receipt",
        status: "backlog",
        owner: "",
        paths: ["src/trailing"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const { owner } = await reachClaimed(root, "TASK-20260711-001");
  await blockCard(root, "TASK-20260711-001", owner, "3", "wait", true);

  const tamperedState = JSON.parse(
    await readFile(join(root, ".workplan", "state.json"), "utf8"),
  );
  const tamperedCard = tamperedState.cards.find((c) => c.id === "TASK-20260711-001");
  tamperedCard.receipts.push({
    from: "blocked",
    to: "in_progress",
    actor: owner,
    revisionFrom: 4,
    revisionTo: 5,
    at: new Date().toISOString(),
    evidence: { blockedFrom: "claimed", reason: "wait", retainPaths: true },
  });
  await writeFile(
    join(root, ".workplan", "state.json"),
    `${JSON.stringify(tamperedState, null, 2)}\n`,
  );

  const workplanBefore = await readFile(join(root, "WORKPLAN.md"));
  const stateBytesBefore = await readFile(join(root, ".workplan", "state.json"));

  const result = await run([
    "resume",
    "TASK-20260711-001",
    "--control-root",
    root,
    "--caller-worktree",
    root,
    "--expected-revision",
    "4",
    "--owner",
    owner,
  ]);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /latest receipt is not a blocked receipt/);

  assert.deepEqual(await readFile(join(root, "WORKPLAN.md")), workplanBefore);
  assert.deepEqual(
    await readFile(join(root, ".workplan", "state.json")),
    stateBytesBefore,
  );
  await assert.rejects(stat(join(root, ".workplan", "lock")), {
    code: "ENOENT",
  });
});

test("direct imported block rejects invalid reason and retainPaths", async (context) => {
  const { root, parent } = await repository(
    state([
      card({
        id: "TASK-20260711-001",
        title: "Direct block",
        status: "backlog",
        owner: "",
        paths: ["src/directblock"],
      }),
    ]),
  );
  context.after(() => rm(parent, { recursive: true, force: true }));

  await seedCommit(root);
  await ensureMainBranch(root);

  const owner = codexOwner("worker-1");
  await reachClaimed(root, "TASK-20260711-001", owner);

  await assert.rejects(
    block({
      controlRoot: root,
      callerWorktree: root,
      expectedRevision: 3,
      id: "TASK-20260711-001",
      owner,
      reason: "   ",
      retainPaths: true,
    }),
    /--reason must be a non-empty string/,
  );

  await assert.rejects(
    block({
      controlRoot: root,
      callerWorktree: root,
      expectedRevision: 3,
      id: "TASK-20260711-001",
      owner,
      reason: "valid",
      retainPaths: "yes",
    }),
    /--retain-paths must be true or false/,
  );
});
