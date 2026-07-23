#!/usr/bin/env node

import {
  link,
  mkdtemp,
  open,
  readFile,
  realpath,
  rename,
  rm,
  stat,
  unlink,
  writeFile,
} from "node:fs/promises";
import { randomBytes, createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { isAbsolute, join, posix } from "node:path";
import { pathToFileURL } from "node:url";
import { hostname } from "node:os";
import process from "node:process";

/**
 * Additional names this same host is known by.
 *
 * macOS commonly resolves to more than one name depending on network state
 * (e.g. a Bonjour/local hostname and a DHCP-assigned name), and that name can
 * flap between sessions. A card claimed under one name can therefore fail a
 * later submit/integrate/accept whose `--owner` host is checked against the
 * *current* `os.hostname()`. Operators list the host's accepted aliases in the
 * `WORKPLAN_HOST_ALIASES` environment variable (comma-separated) so any of
 * them is treated as "this host" for owner/actor/lock-host validation. The
 * frozen card owner string still pins the namespace:session identity exactly;
 * aliases only relax the host-equality precondition.
 */
function hostAliases() {
  const raw = process.env.WORKPLAN_HOST_ALIASES ?? "";
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function isThisHost(host) {
  return host === hostname() || hostAliases().includes(host);
}

const VALID_STATUSES = [
  "backlog",
  "ready",
  "claimed",
  "in_progress",
  "review",
  "verifying",
  "blocked",
  "done",
  "cancelled",
];
const STATUS_ORDER = Object.fromEntries(
  VALID_STATUSES.map((status, index) => [status, index]),
);

function fail(message) {
  const error = new Error(message);
  error.isExpected = true;
  throw error;
}

function parseArgs(argv) {
  const args = argv.slice(2);
  const command = args[0];
  const flags = Object.create(null);
  const positionals = [];
  for (let index = 1; index < args.length; index += 1) {
    const flag = args[index];
    if (!flag.startsWith("--")) {
      positionals.push(flag);
      continue;
    }
    if (index + 1 >= args.length) {
      fail(`missing value for ${flag}`);
    }
    flags[flag] = args[index + 1];
    index += 1;
  }
  return { command, flags, positionals };
}

function requireFlag(flags, name) {
  if (!(name in flags)) {
    fail(`${name} is required`);
  }
  return flags[name];
}

function parseNonnegativeInteger(value, name) {
  if (!/^\d+$/.test(value)) {
    fail(`${name} must be a nonnegative integer`);
  }
  return Number(value);
}

function parseOptionalCommaSeparated(value) {
  if (value === undefined) return undefined;
  return value.split(",").map((entry) => entry.trim());
}

function parseAddFlags(flags, positionals) {
  if (positionals.length > 0) {
    fail(`unexpected positional argument: ${positionals[0]}`);
  }
  const controlRoot = requireFlag(flags, "--control-root");
  const callerWorktree = requireFlag(flags, "--caller-worktree");
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }
  const expectedRevision = parseNonnegativeInteger(
    requireFlag(flags, "--expected-revision"),
    "--expected-revision",
  );
  const id = requireFlag(flags, "--id");
  if (id.length === 0) {
    fail("--id must be a non-empty string");
  }
  const outcome = requireFlag(flags, "--outcome");
  if (outcome.length === 0) {
    fail("--outcome must be a non-empty string");
  }
  const paths = parseOptionalCommaSeparated(requireFlag(flags, "--paths"));

  const priority = flags["--priority"];
  if (priority !== undefined && !/^\d+$/.test(priority)) {
    fail("--priority must be a nonnegative integer");
  }

  return {
    controlRoot,
    callerWorktree,
    expectedRevision,
    id,
    outcome,
    paths,
    priority: priority === undefined ? undefined : Number(priority),
    trace: flags["--trace"],
    dependencies: parseOptionalCommaSeparated(flags["--dependencies"]),
    evidence: parseOptionalCommaSeparated(flags["--evidence"]),
    next: parseOptionalCommaSeparated(flags["--next"]),
  };
}

function parseReadyFlags(flags, positionals) {
  if (positionals.length !== 1) {
    fail("ready requires exactly one CARD argument");
  }
  const id = positionals[0];
  if (id.length === 0) {
    fail("CARD must be a non-empty string");
  }

  const controlRoot = requireFlag(flags, "--control-root");
  const callerWorktree = requireFlag(flags, "--caller-worktree");
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }
  const expectedRevision = parseNonnegativeInteger(
    requireFlag(flags, "--expected-revision"),
    "--expected-revision",
  );
  const owner = requireFlag(flags, "--owner");
  if (!/^coordinator:[^:]+:[^:]+$/.test(owner)) {
    fail("owner must be coordinator:<host>:<session>");
  }
  const ownerHost = owner.slice("coordinator:".length).split(":")[0];
  if (!isThisHost(ownerHost)) {
    fail("owner host must match this host");
  }
  const definitionJson = requireFlag(flags, "--definition-json");
  if (!isAbsolute(definitionJson)) {
    fail("--definition-json must be an absolute path");
  }

  return {
    id,
    controlRoot,
    callerWorktree,
    expectedRevision,
    owner,
    definitionJson,
  };
}

function parseClaimOwner(owner) {
  const match = owner.match(/^(codex|claude):([^:]+):([^:]+)$/);
  if (!match) {
    fail("owner must be codex:<host>:<session> or claude:<host>:<session>");
  }
  const [, namespace, host, session] = match;
  if (!isThisHost(host)) {
    fail("owner host must match this host");
  }
  if (session.length === 0) {
    fail("owner session must be nonempty");
  }
  return { namespace, host, session };
}

function parseCoordinatorActor(actor) {
  if (!/^coordinator:[^:]+:[^:]+$/.test(actor)) {
    fail("actor must be coordinator:<host>:<session>");
  }
  const actorHost = actor.slice("coordinator:".length).split(":")[0];
  if (!isThisHost(actorHost)) {
    fail("actor host must match this host");
  }
}

function parseClaimFlags(flags, positionals) {
  if (positionals.length !== 1) {
    fail("claim requires exactly one CARD argument");
  }
  const id = positionals[0];
  if (id.length === 0) {
    fail("CARD must be a non-empty string");
  }

  const controlRoot = requireFlag(flags, "--control-root");
  const callerWorktree = requireFlag(flags, "--caller-worktree");
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }
  const expectedRevision = parseNonnegativeInteger(
    requireFlag(flags, "--expected-revision"),
    "--expected-revision",
  );
  const owner = requireFlag(flags, "--owner");
  parseClaimOwner(owner);

  return {
    id,
    controlRoot,
    callerWorktree,
    expectedRevision,
    owner,
  };
}

function parseClaimNextFlags(flags, positionals) {
  if (positionals.length > 0) {
    fail(`unexpected positional argument: ${positionals[0]}`);
  }

  const controlRoot = requireFlag(flags, "--control-root");
  const callerWorktree = requireFlag(flags, "--caller-worktree");
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }
  const expectedRevision = parseNonnegativeInteger(
    requireFlag(flags, "--expected-revision"),
    "--expected-revision",
  );
  const owner = requireFlag(flags, "--owner");
  parseClaimOwner(owner);

  return {
    controlRoot,
    callerWorktree,
    expectedRevision,
    owner,
  };
}

function parseStartFlags(flags, positionals) {
  if (positionals.length !== 1) {
    fail("start requires exactly one CARD argument");
  }
  const id = positionals[0];
  if (id.length === 0) {
    fail("CARD must be a non-empty string");
  }

  const controlRoot = requireFlag(flags, "--control-root");
  const callerWorktree = requireFlag(flags, "--caller-worktree");
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }
  const expectedRevision = parseNonnegativeInteger(
    requireFlag(flags, "--expected-revision"),
    "--expected-revision",
  );
  const owner = requireFlag(flags, "--owner");
  parseClaimOwner(owner);

  return {
    id,
    controlRoot,
    callerWorktree,
    expectedRevision,
    owner,
  };
}

function parseSubmitFlags(flags, positionals) {
  if (positionals.length !== 1) {
    fail("submit requires exactly one CARD argument");
  }
  const id = positionals[0];
  if (id.length === 0) {
    fail("CARD must be a non-empty string");
  }

  const controlRoot = requireFlag(flags, "--control-root");
  const callerWorktree = requireFlag(flags, "--caller-worktree");
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }
  const expectedRevision = parseNonnegativeInteger(
    requireFlag(flags, "--expected-revision"),
    "--expected-revision",
  );
  const owner = requireFlag(flags, "--owner");
  parseClaimOwner(owner);
  const expectedHead = requireFlag(flags, "--expected-head");
  if (!/^[0-9a-f]{40}$|^[0-9a-f]{64}$/.test(expectedHead)) {
    fail("--expected-head must be 40 or 64 lowercase hex characters");
  }

  return {
    id,
    controlRoot,
    callerWorktree,
    expectedRevision,
    owner,
    expectedHead,
  };
}

function parseAcceptFlags(flags, positionals) {
  if (positionals.length !== 1) {
    fail("accept requires exactly one CARD argument");
  }
  const id = positionals[0];
  if (id.length === 0) {
    fail("CARD must be a non-empty string");
  }

  const controlRoot = requireFlag(flags, "--control-root");
  const callerWorktree = requireFlag(flags, "--caller-worktree");
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }
  const expectedRevision = parseNonnegativeInteger(
    requireFlag(flags, "--expected-revision"),
    "--expected-revision",
  );
  const actor = requireFlag(flags, "--actor");
  parseCoordinatorActor(actor);
  const acceptanceJson = requireFlag(flags, "--acceptance-json");
  if (!isAbsolute(acceptanceJson)) {
    fail("--acceptance-json must be an absolute path");
  }

  return {
    id,
    controlRoot,
    callerWorktree,
    expectedRevision,
    actor,
    acceptanceJson,
  };
}

function parseBlockFlags(flags, positionals) {
  if (positionals.length !== 1) {
    fail("block requires exactly one CARD argument");
  }
  const id = positionals[0];
  if (id.length === 0) {
    fail("CARD must be a non-empty string");
  }

  const controlRoot = requireFlag(flags, "--control-root");
  const callerWorktree = requireFlag(flags, "--caller-worktree");
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }
  const expectedRevision = parseNonnegativeInteger(
    requireFlag(flags, "--expected-revision"),
    "--expected-revision",
  );
  const owner = requireFlag(flags, "--owner");
  parseClaimOwner(owner);
  const reason = requireFlag(flags, "--reason");
  if (reason.trim().length === 0) {
    fail("--reason must be a non-empty string");
  }
  const retainPathsFlag = requireFlag(flags, "--retain-paths");
  if (retainPathsFlag !== "true" && retainPathsFlag !== "false") {
    fail("--retain-paths must be true or false");
  }

  return {
    id,
    controlRoot,
    callerWorktree,
    expectedRevision,
    owner,
    reason,
    retainPaths: retainPathsFlag === "true",
  };
}

function parseResumeFlags(flags, positionals) {
  if (positionals.length !== 1) {
    fail("resume requires exactly one CARD argument");
  }
  const id = positionals[0];
  if (id.length === 0) {
    fail("CARD must be a non-empty string");
  }

  const controlRoot = requireFlag(flags, "--control-root");
  const callerWorktree = requireFlag(flags, "--caller-worktree");
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }
  const expectedRevision = parseNonnegativeInteger(
    requireFlag(flags, "--expected-revision"),
    "--expected-revision",
  );
  const owner = requireFlag(flags, "--owner");
  parseClaimOwner(owner);

  return {
    id,
    controlRoot,
    callerWorktree,
    expectedRevision,
    owner,
  };
}

function parseIntegrateFlags(flags, positionals) {
  if (positionals.length !== 1) {
    fail("integrate requires exactly one CARD argument");
  }
  const id = positionals[0];
  if (id.length === 0) {
    fail("CARD must be a non-empty string");
  }

  const controlRoot = requireFlag(flags, "--control-root");
  const callerWorktree = requireFlag(flags, "--caller-worktree");
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }
  const expectedRevision = parseNonnegativeInteger(
    requireFlag(flags, "--expected-revision"),
    "--expected-revision",
  );
  const actor = requireFlag(flags, "--actor");
  parseCoordinatorActor(actor);
  const integrationCommit = requireFlag(flags, "--integration-commit");
  if (!/^[0-9a-f]{40}$|^[0-9a-f]{64}$/.test(integrationCommit)) {
    fail("--integration-commit must be 40 or 64 lowercase hex characters");
  }

  return {
    id,
    controlRoot,
    callerWorktree,
    expectedRevision,
    actor,
    integrationCommit,
  };
}

async function resolveGitCommonDir(dir) {
  return new Promise((resolve, reject) => {
    execFile(
      "git",
      ["-C", dir, "rev-parse", "--path-format=absolute", "--git-common-dir"],
      { timeout: 10000 },
      (error, stdout, stderr) => {
        if (error) {
          reject(
            new Error(
              `git metadata unavailable for ${dir}: ${stderr || error.message}`,
            ),
          );
          return;
        }
        const commonDir = stdout.trim();
        if (commonDir.length === 0) {
          reject(new Error(`git metadata unavailable for ${dir}`));
          return;
        }
        resolve(commonDir);
      },
    );
  });
}

async function assertSameRepository(controlRoot, callerWorktree) {
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }
  const [controlCommon, callerCommon] = await Promise.all([
    resolveGitCommonDir(controlRoot),
    resolveGitCommonDir(callerWorktree),
  ]);
  const [controlReal, callerReal] = await Promise.all([
    realpath(controlCommon),
    realpath(callerCommon),
  ]);
  if (controlReal !== callerReal) {
    fail(
      "caller-worktree is not part of the same repository as control-root",
    );
  }
}

function validateOwnedPath(value) {
  if (typeof value !== "string" || value.length === 0) {
    fail("path must be a non-empty string");
  }
  if (!/[^\s]/.test(value)) {
    fail("path must not be whitespace-only");
  }
  if (value.includes("\0")) {
    fail("path must not contain NUL");
  }
  if (value.startsWith("/")) {
    fail("path must not start with a slash");
  }
  if (value.includes("\\")) {
    fail("path must not contain backslash");
  }
  if (/^[A-Za-z]:/.test(value)) {
    fail("path must not start with a Windows drive letter");
  }
  if (posix.normalize(value) !== value) {
    fail("path must be normalized");
  }
  for (const segment of value.split("/")) {
    if (segment.length === 0) {
      fail("path must not contain empty segments");
    }
    if (segment === "." || segment === "..") {
      fail("path must not contain . or .. segments");
    }
  }
}

const PATH_RESERVING_STATUSES = [
  "ready",
  "claimed",
  "in_progress",
  "review",
  "verifying",
];

function pathsOverlap(a, b) {
  if (a === b) return true;
  if (a.startsWith(`${b}/`)) return true;
  if (b.startsWith(`${a}/`)) return true;
  return false;
}

function isPathReserving(card) {
  if (PATH_RESERVING_STATUSES.includes(card.status)) return true;
  if (card.status === "blocked" && card.retainPaths !== false) return true;
  return false;
}

function validateCardPaths(card) {
  for (let i = 0; i < card.paths.length; i += 1) {
    for (let j = i + 1; j < card.paths.length; j += 1) {
      if (pathsOverlap(card.paths[i], card.paths[j])) {
        fail(
          `card ${card.id} has overlapping paths: ${card.paths[i]} and ${card.paths[j]}`,
        );
      }
    }
  }
}

function validatePathExclusivity(state) {
  const reserving = state.cards.filter(isPathReserving);
  for (let i = 0; i < reserving.length; i += 1) {
    const cardA = reserving[i];
    for (let j = i + 1; j < reserving.length; j += 1) {
      const cardB = reserving[j];
      for (const pathA of cardA.paths) {
        for (const pathB of cardB.paths) {
          if (pathsOverlap(pathA, pathB)) {
            fail(
              `cards ${cardA.id} and ${cardB.id} have overlapping paths: ${pathA} and ${pathB}`,
            );
          }
        }
      }
    }
  }
}

function validateState(state) {
  if (state === null || typeof state !== "object" || Array.isArray(state)) {
    fail("state must be an object");
  }
  if (state.schemaVersion !== 1) {
    fail("state.schemaVersion must be 1");
  }
  if (!Number.isInteger(state.revision) || state.revision < 0) {
    fail("state.revision must be a nonnegative integer");
  }
  if (!Array.isArray(state.cards)) {
    fail("state.cards must be an array");
  }
  const seenIds = new Set();
  for (const card of state.cards) {
    if (card === null || typeof card !== "object" || Array.isArray(card)) {
      fail("each card must be an object");
    }
    if (typeof card.id !== "string" || card.id.length === 0) {
      fail("card.id must be a non-empty string");
    }
    if (typeof card.title !== "string") {
      fail("card.title must be a string");
    }
    if (!VALID_STATUSES.includes(card.status)) {
      fail(`card.status must be one of: ${VALID_STATUSES.join(", ")}`);
    }
    if (typeof card.owner !== "string") {
      fail("card.owner must be a string");
    }
    if (!Array.isArray(card.paths)) {
      fail("card.paths must be an array");
    }
    for (const path of card.paths) {
      if (typeof path !== "string") {
        fail("card.paths entries must be strings");
      }
      validateOwnedPath(path);
    }
    validateCardPaths(card);
    if (seenIds.has(card.id)) {
      fail(`duplicate card id ${card.id}`);
    }
    seenIds.add(card.id);
  }
  validatePathExclusivity(state);
}

function escapeCell(value) {
  return String(value ?? "").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
}

function renderPaths(paths) {
  if (paths.length === 0) return "";
  return paths.map((path) => `\`${escapeCell(path)}\``).join("; ");
}

function renderWorkplan(state) {
  const lines = [
    "<!-- Generated by workplan sync. Do not edit manually. -->",
    "",
  ];
  if (state.bootstrapException) {
    lines.push(`> Bootstrap exception: ${escapeCell(state.bootstrapException)}`);
    lines.push("");
  }
  lines.push(`Revision: ${state.revision}`);
  lines.push("");
  lines.push("| ID | Title | Status | Owner | Paths |");
  lines.push("|---|---|---|---|---|");

  const sorted = [...state.cards].sort((a, b) => {
    const orderDiff = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
    if (orderDiff !== 0) return orderDiff;
    return a.id.localeCompare(b.id);
  });

  for (const card of sorted) {
    lines.push(
      `| ${escapeCell(card.id)} | ${escapeCell(card.title)} | ${escapeCell(
        card.status,
      )} | ${escapeCell(card.owner)} | ${renderPaths(card.paths)} |`,
    );
  }

  return lines.join("\n") + "\n";
}

const ADD_LOCK_OWNER = "workplan:add";
const READY_LOCK_OWNER = "workplan:ready";
const CLAIM_LOCK_OWNER = "workplan:claim";
const TRANSITION_LOCK_OWNER = "workplan:transition";
const KNOWN_LOCK_OWNERS = [
  ADD_LOCK_OWNER,
  READY_LOCK_OWNER,
  CLAIM_LOCK_OWNER,
  TRANSITION_LOCK_OWNER,
];
const LOCK_FILE = "lock";
const RECOVERY_FILE = "lock.recovery";

function makeLockMetadata(owner) {
  return {
    schemaVersion: 1,
    host: hostname(),
    pid: process.pid,
    owner,
  };
}

function makeTempName(targetName) {
  return `.tmp-${targetName}.${process.pid}.${randomBytes(8).toString("hex")}`;
}

async function writeAtomicFile(dir, targetName, metadata) {
  const tempPath = join(dir, makeTempName(targetName));
  const targetPath = join(dir, targetName);
  const handle = await open(tempPath, "w", 0o600);
  await handle.writeFile(JSON.stringify(metadata));
  await handle.sync();
  await handle.close();
  try {
    await link(tempPath, targetPath);
  } finally {
    await unlink(tempPath).catch(() => {});
  }
  return targetPath;
}

async function readLockMetadata(lockPath) {
  let content;
  try {
    content = await readFile(lockPath, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") {
      return { ok: false, reason: "missing" };
    }
    throw error;
  }
  if (content.trim() === "") {
    return { ok: false, reason: "empty" };
  }
  try {
    return { ok: true, metadata: JSON.parse(content) };
  } catch {
    return { ok: false, reason: "malformed" };
  }
}

function isLiveProcess(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if (error.code === "EPERM") return true;
    if (error.code === "ESRCH") return false;
    return true;
  }
}

async function classifyLock(lockPath, allowedOwners) {
  const read = await readLockMetadata(lockPath);
  if (!read.ok) {
    return { stale: false, reason: read.reason };
  }
  const metadata = read.metadata;
  if (
    metadata.schemaVersion !== 1 ||
    typeof metadata.host !== "string" ||
    !isThisHost(metadata.host) ||
    typeof metadata.owner !== "string" ||
    metadata.owner.length === 0 ||
    !allowedOwners.includes(metadata.owner) ||
    typeof metadata.pid !== "number" ||
    !Number.isInteger(metadata.pid)
  ) {
    return { stale: false, reason: "foreign or ownerless" };
  }
  if (isLiveProcess(metadata.pid)) {
    return { stale: false, reason: "live" };
  }
  return { stale: true, metadata };
}

async function acquireRecoveryGuard(workplanDir, metadata) {
  const guardPath = join(workplanDir, RECOVERY_FILE);
  try {
    await stat(guardPath);
    fail("recovery in progress");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  await writeAtomicFile(workplanDir, RECOVERY_FILE, metadata);
  return guardPath;
}

async function recoverLock(workplanDir, lockPath, metadata) {
  const guardPath = join(workplanDir, RECOVERY_FILE);
  let guardHeld = false;
  try {
    await acquireRecoveryGuard(workplanDir, metadata);
    guardHeld = true;

    const classification = await classifyLock(lockPath, KNOWN_LOCK_OWNERS);
    if (!classification.stale) {
      fail("lock exists");
    }

    const quarantineName = `${LOCK_FILE}.quarantine-${randomBytes(16).toString("hex")}`;
    const quarantinePath = join(workplanDir, quarantineName);
    try {
      await rename(lockPath, quarantinePath);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }

    try {
      await writeAtomicFile(workplanDir, LOCK_FILE, metadata);
    } catch (error) {
      if (error.code === "EEXIST") {
        fail("another acquirer won");
      }
      throw error;
    }

    const st = await stat(lockPath);
    return { path: lockPath, dev: st.dev, ino: st.ino, metadata };
  } finally {
    if (guardHeld) {
      await unlink(guardPath).catch(() => {});
    }
  }
}

function lockMetadataMatches(a, b) {
  return (
    a.schemaVersion === b.schemaVersion &&
    a.host === b.host &&
    a.pid === b.pid &&
    a.owner === b.owner
  );
}

async function acquireLock(controlRoot, owner) {
  const workplanDir = join(controlRoot, ".workplan");
  const lockPath = join(workplanDir, LOCK_FILE);
  const guardPath = join(workplanDir, RECOVERY_FILE);
  const metadata = makeLockMetadata(owner);

  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      await stat(guardPath);
      fail("recovery in progress");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }

    try {
      await writeAtomicFile(workplanDir, LOCK_FILE, metadata);
      const st = await stat(lockPath);
      return { path: lockPath, dev: st.dev, ino: st.ino, metadata };
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
    }

    const classification = await classifyLock(lockPath, KNOWN_LOCK_OWNERS);
    if (classification.stale) {
      return recoverLock(workplanDir, lockPath, metadata);
    }
    if (classification.reason === "missing") {
      continue;
    }
    fail("lock exists");
  }

  fail("unable to acquire lock");
}

async function releaseLock(acquired) {
  if (!acquired) return;
  try {
    const st = await stat(acquired.path);
    if (st.dev !== acquired.dev || st.ino !== acquired.ino) return;

    const read = await readLockMetadata(acquired.path);
    if (!read.ok) return;
    if (!lockMetadataMatches(read.metadata, acquired.metadata)) return;

    await unlink(acquired.path);
  } catch (error) {
    if (error.code === "ENOENT") return;
    // Ignore release errors to avoid leaking failures.
  }
}

async function acquireAddLock(controlRoot) {
  return acquireLock(controlRoot, ADD_LOCK_OWNER);
}

async function releaseAddLock(acquired) {
  return releaseLock(acquired);
}

async function acquireReadyLock(controlRoot) {
  return acquireLock(controlRoot, READY_LOCK_OWNER);
}

async function releaseReadyLock(acquired) {
  return releaseLock(acquired);
}

async function acquireClaimLock(controlRoot) {
  return acquireLock(controlRoot, CLAIM_LOCK_OWNER);
}

async function releaseClaimLock(acquired) {
  return releaseLock(acquired);
}

async function acquireTransitionLock(controlRoot) {
  return acquireLock(controlRoot, TRANSITION_LOCK_OWNER);
}

async function releaseTransitionLock(acquired) {
  return releaseLock(acquired);
}

async function sync(controlRoot) {
  if (!isAbsolute(controlRoot)) {
    fail("--control-root must be an absolute path");
  }
  const statePath = join(controlRoot, ".workplan", "state.json");
  const raw = await readFile(statePath, "utf8");
  const state = JSON.parse(raw);
  validateState(state);
  const output = renderWorkplan(state);
  const workplanPath = join(controlRoot, "WORKPLAN.md");
  const tempPath = join(controlRoot, `WORKPLAN.md.tmp-${process.pid}`);
  await writeFile(tempPath, output);
  await rename(tempPath, workplanPath);
  return { revision: state.revision, synchronized: true };
}

async function add(options) {
  const {
    controlRoot,
    callerWorktree,
    expectedRevision,
    id,
    outcome,
    paths,
    priority,
    trace,
    dependencies,
    evidence,
    next,
  } = options;

  if (!isAbsolute(controlRoot)) {
    fail("--control-root must be an absolute path");
  }
  if (callerWorktree === undefined) {
    fail("--caller-worktree is required");
  }
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }

  for (const path of paths) {
    validateOwnedPath(path);
  }

  await assertSameRepository(controlRoot, callerWorktree);

  const acquired = await acquireAddLock(controlRoot);
  try {
    const statePath = join(controlRoot, ".workplan", "state.json");
    const raw = await readFile(statePath, "utf8");
    const state = JSON.parse(raw);
    validateState(state);

    if (state.revision !== expectedRevision) {
      fail(
        `expected revision ${expectedRevision} but found ${state.revision}`,
      );
    }
    if (state.cards.some((card) => card.id === id)) {
      fail(`duplicate id ${id}`);
    }

    const newCard = {
      id,
      title: outcome,
      status: "backlog",
      owner: "workplan:add",
      paths,
    };
    if (priority !== undefined) newCard.priority = priority;
    if (trace !== undefined) newCard.trace = trace;
    if (dependencies !== undefined) newCard.dependencies = dependencies;
    if (evidence !== undefined) newCard.evidence = evidence;
    if (next !== undefined) newCard.next = next;

    validateCardPaths(newCard);
    for (const existing of state.cards) {
      if (!isPathReserving(existing)) continue;
      for (const newPath of newCard.paths) {
        for (const existingPath of existing.paths) {
          if (pathsOverlap(newPath, existingPath)) {
            fail(
              `path ${newPath} overlaps with path ${existingPath} on card ${existing.id}`,
            );
          }
        }
      }
    }

    state.cards.push(newCard);
    state.revision += 1;

    const workplanPath = join(controlRoot, "WORKPLAN.md");
    const tempMd = join(controlRoot, `WORKPLAN.md.tmp-${process.pid}`);
    await writeFile(tempMd, renderWorkplan(state));
    await rename(tempMd, workplanPath);

    if (process.env.WORKPLAN_FAULT_AFTER_PROJECTION === "1") {
      fail("injected fault after projection");
    }

    const tempState = join(
      controlRoot,
      ".workplan",
      `state.json.tmp-${process.pid}`,
    );
    await writeFile(tempState, `${JSON.stringify(state, null, 2)}\n`);
    await rename(tempState, statePath);

    return { revision: state.revision, added: true };
  } finally {
    await releaseAddLock(acquired);
  }
}

const DEFINITION_FIELDS = [
  "sourceRevision",
  "definitionHash",
  "worktreePath",
  "branch",
  "baseCommit",
  "currentHead",
  "plane",
  "frozenAxes",
  "budget",
  "stopCondition",
  "evaluatorAuthority",
  "acceptanceCommands",
  "nextCheckpoint",
  "allowedOwnerNamespaces",
];

const AUTHORITY_GRANT = {
  executionCoordination: true,
  evaluator: false,
  merge: false,
  promotion: false,
  deployment: false,
  credentials: false,
  runtime: false,
};

function deepSortKeys(value) {
  if (Array.isArray(value)) {
    return value.map(deepSortKeys);
  }
  if (value !== null && typeof value === "object") {
    const sorted = Object.create(null);
    for (const key of Object.keys(value).sort()) {
      sorted[key] = deepSortKeys(value[key]);
    }
    return sorted;
  }
  return value;
}

function canonicalizeDefinition(definition) {
  const copy = { ...definition };
  delete copy.definitionHash;
  return JSON.stringify(deepSortKeys(copy));
}

function computeDefinitionHash(definition) {
  const hash = createHash("sha256")
    .update(canonicalizeDefinition(definition))
    .digest("hex");
  return `sha256:${hash}`;
}

function validateDefinition(definition, callerWorktree) {
  if (
    definition === null ||
    typeof definition !== "object" ||
    Array.isArray(definition)
  ) {
    fail("definition must be an object");
  }
  const keys = Object.keys(definition);
  if (
    keys.length !== DEFINITION_FIELDS.length ||
    !keys.every((key) => DEFINITION_FIELDS.includes(key))
  ) {
    fail("definition has incorrect fields");
  }

  if (typeof definition.sourceRevision !== "string") {
    fail("sourceRevision must be a string");
  }
  if (
    typeof definition.definitionHash !== "string" ||
    !/^sha256:[0-9a-f]{64}$/.test(definition.definitionHash)
  ) {
    fail("definitionHash must be sha256:<64hex>");
  }
  if (
    typeof definition.worktreePath !== "string" ||
    definition.worktreePath !== callerWorktree
  ) {
    fail("worktreePath must match callerWorktree");
  }
  if (typeof definition.branch !== "string") {
    fail("branch must be a string");
  }
  if (typeof definition.baseCommit !== "string") {
    fail("baseCommit must be a string");
  }
  if (typeof definition.currentHead !== "string") {
    fail("currentHead must be a string");
  }
  if (definition.plane !== "development") {
    fail("plane must be development");
  }
  if (
    !Array.isArray(definition.frozenAxes) ||
    definition.frozenAxes.length !== 3 ||
    definition.frozenAxes[0] !== "H" ||
    definition.frozenAxes[1] !== "E" ||
    definition.frozenAxes[2] !== "W"
  ) {
    fail('frozenAxes must be exactly ["H","E","W"]');
  }
  if (typeof definition.budget !== "string") {
    fail("budget must be a string");
  }
  if (typeof definition.stopCondition !== "string") {
    fail("stopCondition must be a string");
  }
  if (typeof definition.evaluatorAuthority !== "string") {
    fail("evaluatorAuthority must be a string");
  }
  if (
    !Array.isArray(definition.acceptanceCommands) ||
    definition.acceptanceCommands.length === 0 ||
    !definition.acceptanceCommands.every((cmd) => typeof cmd === "string")
  ) {
    fail("acceptanceCommands must be a nonempty string array");
  }
  if (typeof definition.nextCheckpoint !== "string") {
    fail("nextCheckpoint must be a string");
  }
  if (
    !Array.isArray(definition.allowedOwnerNamespaces) ||
    definition.allowedOwnerNamespaces.length === 0 ||
    !definition.allowedOwnerNamespaces.every(
      (ns) => typeof ns === "string" && ["codex", "claude"].includes(ns),
    ) ||
    new Set(definition.allowedOwnerNamespaces).size !==
      definition.allowedOwnerNamespaces.length
  ) {
    fail(
      'allowedOwnerNamespaces must be a nonempty unique subset of ["codex","claude"]',
    );
  }
}

const WIP_STATUSES = ["claimed", "in_progress", "review", "verifying"];

function validateOwnerNamespace(card, owner) {
  const { namespace } = parseClaimOwner(owner);
  if (!card.definition?.allowedOwnerNamespaces?.includes(namespace)) {
    fail(`owner namespace ${namespace} is not allowed for card ${card.id}`);
  }
}

function validateStoredDefinition(card, callerWorktree) {
  if (!card.definition) {
    fail(`card ${card.id} has no definition`);
  }
  validateDefinition(card.definition, callerWorktree);
  const expectedHash = computeDefinitionHash(card.definition);
  if (expectedHash !== card.definition.definitionHash) {
    fail("definitionHash mismatch");
  }
}

function validateClaimPriority(card) {
  if (card.priority !== undefined) {
    if (!Number.isInteger(card.priority) || card.priority < 0) {
      fail("priority must be a nonnegative integer");
    }
  }
}

function validateDependenciesDone(card, state) {
  if (!card.dependencies) return;
  for (const depId of card.dependencies) {
    const dep = state.cards.find((c) => c.id === depId);
    if (!dep) {
      fail(`dependency ${depId} not found`);
    }
    if (dep.status !== "done") {
      fail(`dependency ${depId} is not done`);
    }
  }
}

function validateClaimPathExclusivity(card, state) {
  for (const other of state.cards) {
    if (other.id === card.id) continue;
    if (!isPathReserving(other)) continue;
    for (const path of card.paths) {
      for (const otherPath of other.paths) {
        if (pathsOverlap(path, otherPath)) {
          fail(
            `path ${path} overlaps with path ${otherPath} on card ${other.id}`,
          );
        }
      }
    }
  }
}

function validateNoOwnerWip(owner, card, state) {
  for (const other of state.cards) {
    if (other.id === card.id) continue;
    if (!WIP_STATUSES.includes(other.status)) continue;
    if (other.owner === owner) {
      fail(`owner already has WIP card ${other.id}`);
    }
  }
}

function validateClaimCandidateBasics(card, callerWorktree) {
  if (card.status !== "ready") {
    fail(`card ${card.id} is not in ready status`);
  }
  validateStoredDefinition(card, callerWorktree);
  validateClaimPriority(card);
}

function validateClaimEligibility(card, owner, state) {
  validateOwnerNamespace(card, owner);
  validateDependenciesDone(card, state);
  validateClaimPathExclusivity(card, state);
  validateNoOwnerWip(owner, card, state);
}

function makeClaimSnapshot(card, owner, revisionFrom, revisionTo) {
  const def = card.definition;
  return {
    owner,
    definitionHash: def.definitionHash,
    sourceRevision: def.sourceRevision,
    worktreePath: def.worktreePath,
    branch: def.branch,
    baseCommit: def.baseCommit,
    currentHead: def.currentHead,
    paths: [...card.paths],
    plane: def.plane,
    frozenAxes: [...def.frozenAxes],
    budget: def.budget,
    acceptanceCommands: [...def.acceptanceCommands],
    nextCheckpoint: def.nextCheckpoint,
    revisionFrom,
    revisionTo,
    at: new Date().toISOString(),
  };
}

function appendReceipt(
  card,
  from,
  to,
  actor,
  revisionFrom,
  revisionTo,
  evidence,
) {
  card.receipts = [...(card.receipts || [])];
  const receipt = {
    from,
    to,
    actor,
    revisionFrom,
    revisionTo,
    at: new Date().toISOString(),
  };
  if (evidence !== undefined) {
    receipt.evidence = evidence;
  }
  card.receipts.push(receipt);
}

async function claim(options) {
  const { controlRoot, callerWorktree, expectedRevision, id, owner } = options;

  if (!isAbsolute(controlRoot)) {
    fail("--control-root must be an absolute path");
  }
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }

  parseClaimOwner(owner);
  await assertSameRepository(controlRoot, callerWorktree);

  const acquired = await acquireClaimLock(controlRoot);
  try {
    const statePath = join(controlRoot, ".workplan", "state.json");
    const raw = await readFile(statePath, "utf8");
    const state = JSON.parse(raw);
    validateState(state);

    if (state.revision !== expectedRevision) {
      fail(
        `expected revision ${expectedRevision} but found ${state.revision}`,
      );
    }

    const card = state.cards.find((c) => c.id === id);
    if (!card) {
      fail(`card ${id} not found`);
    }

    validateClaimCandidateBasics(card, callerWorktree);
    validateClaimEligibility(card, owner, state);

    const snapshot = makeClaimSnapshot(
      card,
      owner,
      expectedRevision,
      expectedRevision + 1,
    );
    card.status = "claimed";
    card.owner = owner;
    card.claimSnapshot = snapshot;
    appendReceipt(
      card,
      "ready",
      "claimed",
      owner,
      expectedRevision,
      expectedRevision + 1,
    );

    state.revision += 1;

    const workplanPath = join(controlRoot, "WORKPLAN.md");
    const tempMd = join(controlRoot, `WORKPLAN.md.tmp-${process.pid}`);
    await writeFile(tempMd, renderWorkplan(state));
    await rename(tempMd, workplanPath);

    if (process.env.WORKPLAN_FAULT_AFTER_PROJECTION === "1") {
      fail("injected fault after projection");
    }

    const tempState = join(
      controlRoot,
      ".workplan",
      `state.json.tmp-${process.pid}`,
    );
    await writeFile(tempState, `${JSON.stringify(state, null, 2)}\n`);
    await rename(tempState, statePath);

    return { revision: state.revision, claimed: true };
  } finally {
    await releaseClaimLock(acquired);
  }
}

function sortClaimNextCandidates(candidates) {
  return candidates.sort((a, b) => {
    const aHas = a.priority !== undefined;
    const bHas = b.priority !== undefined;
    if (aHas && bHas) {
      if (a.priority !== b.priority) return a.priority - b.priority;
      return a.id.localeCompare(b.id);
    }
    if (aHas && !bHas) return -1;
    if (!aHas && bHas) return 1;
    return a.id.localeCompare(b.id);
  });
}

async function claimNext(options) {
  const { controlRoot, callerWorktree, expectedRevision, owner } = options;

  if (!isAbsolute(controlRoot)) {
    fail("--control-root must be an absolute path");
  }
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }

  parseClaimOwner(owner);
  await assertSameRepository(controlRoot, callerWorktree);

  const acquired = await acquireClaimLock(controlRoot);
  try {
    const statePath = join(controlRoot, ".workplan", "state.json");
    const raw = await readFile(statePath, "utf8");
    const state = JSON.parse(raw);
    validateState(state);

    if (state.revision !== expectedRevision) {
      fail(
        `expected revision ${expectedRevision} but found ${state.revision}`,
      );
    }

    const readyCards = state.cards.filter((c) => c.status === "ready");
    for (const candidate of readyCards) {
      validateClaimCandidateBasics(candidate, callerWorktree);
    }
    const candidates = sortClaimNextCandidates(readyCards);

    for (const candidate of candidates) {
      try {
        validateClaimEligibility(candidate, owner, state);
      } catch (error) {
        if (!error.isExpected) throw error;
        continue;
      }

      const snapshot = makeClaimSnapshot(
        candidate,
        owner,
        expectedRevision,
        expectedRevision + 1,
      );
      candidate.status = "claimed";
      candidate.owner = owner;
      candidate.claimSnapshot = snapshot;
      appendReceipt(
        candidate,
        "ready",
        "claimed",
        owner,
        expectedRevision,
        expectedRevision + 1,
      );

      state.revision += 1;

      const workplanPath = join(controlRoot, "WORKPLAN.md");
      const tempMd = join(controlRoot, `WORKPLAN.md.tmp-${process.pid}`);
      await writeFile(tempMd, renderWorkplan(state));
      await rename(tempMd, workplanPath);

      if (process.env.WORKPLAN_FAULT_AFTER_PROJECTION === "1") {
        fail("injected fault after projection");
      }

      const tempState = join(
        controlRoot,
        ".workplan",
        `state.json.tmp-${process.pid}`,
      );
      await writeFile(tempState, `${JSON.stringify(state, null, 2)}\n`);
      await rename(tempState, statePath);

      return { revision: state.revision, claimed: true, id: candidate.id };
    }

    fail("no eligible ready card");
  } finally {
    await releaseClaimLock(acquired);
  }
}

function arraysEqual(a, b) {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

const CLAIM_SNAPSHOT_FIELDS = [
  "owner",
  "definitionHash",
  "sourceRevision",
  "worktreePath",
  "branch",
  "baseCommit",
  "currentHead",
  "paths",
  "plane",
  "frozenAxes",
  "budget",
  "acceptanceCommands",
  "nextCheckpoint",
  "revisionFrom",
  "revisionTo",
  "at",
];

function validateIsoTimestamp(value) {
  if (typeof value !== "string") return false;
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return false;
  return new Date(parsed).toISOString() === value;
}

function validateClaimSnapshot(card) {
  if (
    !card.claimSnapshot ||
    typeof card.claimSnapshot !== "object" ||
    Array.isArray(card.claimSnapshot)
  ) {
    fail(`card ${card.id} has no claim snapshot`);
  }
  const snap = card.claimSnapshot;
  const keys = Object.keys(snap);
  if (
    keys.length !== CLAIM_SNAPSHOT_FIELDS.length ||
    !keys.every((key) => CLAIM_SNAPSHOT_FIELDS.includes(key))
  ) {
    fail("claim snapshot has incorrect fields");
  }

  const def = card.definition;
  if (snap.owner !== card.owner) fail("claim snapshot owner mismatch");
  if (snap.definitionHash !== def.definitionHash) {
    fail("claim snapshot definitionHash mismatch");
  }
  if (snap.sourceRevision !== def.sourceRevision) {
    fail("claim snapshot sourceRevision mismatch");
  }
  if (snap.worktreePath !== def.worktreePath) {
    fail("claim snapshot worktreePath mismatch");
  }
  if (snap.branch !== def.branch) fail("claim snapshot branch mismatch");
  if (snap.baseCommit !== def.baseCommit) {
    fail("claim snapshot baseCommit mismatch");
  }
  if (snap.currentHead !== def.currentHead) {
    fail("claim snapshot currentHead mismatch");
  }
  if (!arraysEqual(snap.paths, card.paths)) {
    fail("claim snapshot paths mismatch");
  }
  if (snap.plane !== def.plane) fail("claim snapshot plane mismatch");
  if (!arraysEqual(snap.frozenAxes, def.frozenAxes)) {
    fail("claim snapshot frozenAxes mismatch");
  }
  if (snap.budget !== def.budget) fail("claim snapshot budget mismatch");
  if (!arraysEqual(snap.acceptanceCommands, def.acceptanceCommands)) {
    fail("claim snapshot acceptanceCommands mismatch");
  }
  if (snap.nextCheckpoint !== def.nextCheckpoint) {
    fail("claim snapshot nextCheckpoint mismatch");
  }
  if (!Number.isInteger(snap.revisionFrom) || snap.revisionFrom < 0) {
    fail("claim snapshot revisionFrom must be a nonnegative integer");
  }
  if (
    !Number.isInteger(snap.revisionTo) ||
    snap.revisionTo !== snap.revisionFrom + 1
  ) {
    fail("claim snapshot revisionTo must be revisionFrom + 1");
  }
  if (!validateIsoTimestamp(snap.at)) {
    fail("claim snapshot at must be a valid ISO timestamp");
  }
}

function validateAuthorityGrant(card) {
  const grant = card.authorityGrant;
  if (!grant || typeof grant !== "object" || Array.isArray(grant)) {
    fail("card has no authority grant");
  }
  const expectedKeys = Object.keys(AUTHORITY_GRANT);
  const actualKeys = Object.keys(grant);
  if (
    actualKeys.length !== expectedKeys.length ||
    !actualKeys.every((key) => expectedKeys.includes(key))
  ) {
    fail("authorityGrant has incorrect fields");
  }
  for (const [key, value] of Object.entries(AUTHORITY_GRANT)) {
    if (grant[key] !== value) {
      fail(`authorityGrant.${key} must be ${value}`);
    }
  }
}

function validateTransitionCardBasics(card, requiredStatus) {
  if (card.status !== requiredStatus) {
    fail(`card ${card.id} is not in ${requiredStatus} status`);
  }
  if (!card.definition) {
    fail(`card ${card.id} has no definition`);
  }
  validateStoredDefinition(card, card.definition.worktreePath);
  validateClaimSnapshot(card);
  validateAuthorityGrant(card);
}

function validateTransitionCard(card, owner, callerWorktree, requiredStatus) {
  validateTransitionCardBasics(card, requiredStatus);
  if (card.owner !== owner) {
    fail(`card ${card.id} is not owned by ${owner}`);
  }
  if (card.definition.worktreePath !== callerWorktree) {
    fail("worktreePath must match callerWorktree");
  }
}

const REVIEW_FREEZE_FIELDS = [
  "branch",
  "head",
  "revisionFrom",
  "revisionTo",
  "at",
];

function validateReviewFreeze(card) {
  if (
    !card.reviewFreeze ||
    typeof card.reviewFreeze !== "object" ||
    Array.isArray(card.reviewFreeze)
  ) {
    fail(`card ${card.id} has no review freeze`);
  }
  const freeze = card.reviewFreeze;
  const keys = Object.keys(freeze);
  if (
    keys.length !== REVIEW_FREEZE_FIELDS.length ||
    !keys.every((key) => REVIEW_FREEZE_FIELDS.includes(key))
  ) {
    fail("reviewFreeze has incorrect fields");
  }
  if (typeof freeze.branch !== "string") {
    fail("reviewFreeze.branch must be a string");
  }
  if (
    typeof freeze.head !== "string" ||
    !/^[0-9a-f]{40}$|^[0-9a-f]{64}$/.test(freeze.head)
  ) {
    fail("reviewFreeze.head must be 40 or 64 lowercase hex characters");
  }
  if (!Number.isInteger(freeze.revisionFrom) || freeze.revisionFrom < 0) {
    fail("reviewFreeze.revisionFrom must be a nonnegative integer");
  }
  if (
    !Number.isInteger(freeze.revisionTo) ||
    freeze.revisionTo !== freeze.revisionFrom + 1
  ) {
    fail("reviewFreeze.revisionTo must be revisionFrom + 1");
  }
  if (!validateIsoTimestamp(freeze.at)) {
    fail("reviewFreeze.at must be a valid ISO timestamp");
  }
}

async function gitCommitExists(dir, commit) {
  return new Promise((resolve) => {
    execFile(
      "git",
      ["-C", dir, "cat-file", "-e", `${commit}^{commit}`],
      { timeout: 10000 },
      (error) => {
        resolve(!error);
      },
    );
  });
}

async function gitIsAncestor(dir, ancestor, descendant) {
  return new Promise((resolve, reject) => {
    execFile(
      "git",
      ["-C", dir, "merge-base", "--is-ancestor", ancestor, descendant],
      { timeout: 10000 },
      (error) => {
        if (error) {
          if (error.code === 1) {
            resolve(false);
          } else {
            reject(
              new Error(
                `git ancestry check failed for ${dir}: ${error.message}`,
              ),
            );
          }
          return;
        }
        resolve(true);
      },
    );
  });
}

async function readGitBranchAndHead(dir) {
  return new Promise((resolve, reject) => {
    execFile(
      "git",
      ["-C", dir, "rev-parse", "--abbrev-ref", "HEAD"],
      { timeout: 10000 },
      (error, stdout, stderr) => {
        if (error) {
          reject(
            new Error(
              `git branch unavailable for ${dir}: ${stderr || error.message}`,
            ),
          );
          return;
        }
        const branch = stdout.trim();
        execFile(
          "git",
          ["-C", dir, "rev-parse", "HEAD"],
          { timeout: 10000 },
          (error2, stdout2, stderr2) => {
            if (error2) {
              reject(
                new Error(
                  `git HEAD unavailable for ${dir}: ${stderr2 || error2.message}`,
                ),
              );
              return;
            }
            resolve({ branch, head: stdout2.trim() });
          },
        );
      },
    );
  });
}

async function start(options) {
  const { controlRoot, callerWorktree, expectedRevision, id, owner } = options;

  if (!isAbsolute(controlRoot)) {
    fail("--control-root must be an absolute path");
  }
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }

  parseClaimOwner(owner);
  await assertSameRepository(controlRoot, callerWorktree);

  const acquired = await acquireTransitionLock(controlRoot);
  try {
    const statePath = join(controlRoot, ".workplan", "state.json");
    const raw = await readFile(statePath, "utf8");
    const state = JSON.parse(raw);
    validateState(state);

    if (state.revision !== expectedRevision) {
      fail(
        `expected revision ${expectedRevision} but found ${state.revision}`,
      );
    }

    const card = state.cards.find((c) => c.id === id);
    if (!card) {
      fail(`card ${id} not found`);
    }

    validateTransitionCard(card, owner, callerWorktree, "claimed");

    appendReceipt(
      card,
      "claimed",
      "in_progress",
      owner,
      expectedRevision,
      expectedRevision + 1,
    );
    card.status = "in_progress";

    state.revision += 1;

    const workplanPath = join(controlRoot, "WORKPLAN.md");
    const tempMd = join(controlRoot, `WORKPLAN.md.tmp-${process.pid}`);
    await writeFile(tempMd, renderWorkplan(state));
    await rename(tempMd, workplanPath);

    if (process.env.WORKPLAN_FAULT_AFTER_PROJECTION === "1") {
      fail("injected fault after projection");
    }

    const tempState = join(
      controlRoot,
      ".workplan",
      `state.json.tmp-${process.pid}`,
    );
    await writeFile(tempState, `${JSON.stringify(state, null, 2)}\n`);
    await rename(tempState, statePath);

    return { revision: state.revision, started: true };
  } finally {
    await releaseTransitionLock(acquired);
  }
}

async function submit(options) {
  const {
    controlRoot,
    callerWorktree,
    expectedRevision,
    id,
    owner,
    expectedHead,
  } = options;

  if (!isAbsolute(controlRoot)) {
    fail("--control-root must be an absolute path");
  }
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }

  parseClaimOwner(owner);
  await assertSameRepository(controlRoot, callerWorktree);

  const acquired = await acquireTransitionLock(controlRoot);
  try {
    const statePath = join(controlRoot, ".workplan", "state.json");
    const raw = await readFile(statePath, "utf8");
    const state = JSON.parse(raw);
    validateState(state);

    if (state.revision !== expectedRevision) {
      fail(
        `expected revision ${expectedRevision} but found ${state.revision}`,
      );
    }

    const card = state.cards.find((c) => c.id === id);
    if (!card) {
      fail(`card ${id} not found`);
    }

    validateTransitionCard(card, owner, callerWorktree, "in_progress");

    const { branch, head } = await readGitBranchAndHead(callerWorktree);
    if (head !== expectedHead) {
      fail(`HEAD ${head} does not match expected ${expectedHead}`);
    }

    card.reviewFreeze = {
      branch,
      head,
      revisionFrom: expectedRevision,
      revisionTo: expectedRevision + 1,
      at: new Date().toISOString(),
    };
    appendReceipt(
      card,
      "in_progress",
      "review",
      owner,
      expectedRevision,
      expectedRevision + 1,
      { branch, head },
    );
    card.status = "review";

    state.revision += 1;

    const workplanPath = join(controlRoot, "WORKPLAN.md");
    const tempMd = join(controlRoot, `WORKPLAN.md.tmp-${process.pid}`);
    await writeFile(tempMd, renderWorkplan(state));
    await rename(tempMd, workplanPath);

    if (process.env.WORKPLAN_FAULT_AFTER_PROJECTION === "1") {
      fail("injected fault after projection");
    }

    const tempState = join(
      controlRoot,
      ".workplan",
      `state.json.tmp-${process.pid}`,
    );
    await writeFile(tempState, `${JSON.stringify(state, null, 2)}\n`);
    await rename(tempState, statePath);

    return { revision: state.revision, submitted: true };
  } finally {
    await releaseTransitionLock(acquired);
  }
}

async function integrate(options) {
  const {
    controlRoot,
    callerWorktree,
    expectedRevision,
    id,
    actor,
    integrationCommit,
  } = options;

  if (!isAbsolute(controlRoot)) {
    fail("--control-root must be an absolute path");
  }
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }

  parseCoordinatorActor(actor);
  await assertSameRepository(controlRoot, callerWorktree);

  const acquired = await acquireTransitionLock(controlRoot);
  try {
    const statePath = join(controlRoot, ".workplan", "state.json");
    const raw = await readFile(statePath, "utf8");
    const state = JSON.parse(raw);
    validateState(state);

    if (state.revision !== expectedRevision) {
      fail(
        `expected revision ${expectedRevision} but found ${state.revision}`,
      );
    }

    const card = state.cards.find((c) => c.id === id);
    if (!card) {
      fail(`card ${id} not found`);
    }
    if (card.status !== "review") {
      fail(`card ${card.id} is not in review status`);
    }
    if (card.owner === actor) {
      fail("actor must not be card owner");
    }

    validateTransitionCardBasics(card, "review");
    validateReviewFreeze(card);

    const freeze = card.reviewFreeze;
    const reviewHeadExists = await gitCommitExists(controlRoot, freeze.head);
    if (!reviewHeadExists) {
      fail("review head commit not found");
    }
    const integrationExists = await gitCommitExists(
      controlRoot,
      integrationCommit,
    );
    if (!integrationExists) {
      fail("integration commit not found");
    }
    const isAncestor = await gitIsAncestor(
      controlRoot,
      freeze.head,
      integrationCommit,
    );
    if (!isAncestor) {
      fail("review head is not an ancestor of integration commit");
    }

    card.integrationReceipt = {
      actor,
      reviewBranch: freeze.branch,
      reviewHead: freeze.head,
      integrationCommit,
      definitionHash: card.definition.definitionHash,
      revisionFrom: expectedRevision,
      revisionTo: expectedRevision + 1,
      at: new Date().toISOString(),
    };
    appendReceipt(
      card,
      "review",
      "verifying",
      actor,
      expectedRevision,
      expectedRevision + 1,
      {
        reviewBranch: freeze.branch,
        reviewHead: freeze.head,
        integrationCommit,
      },
    );
    card.status = "verifying";

    state.revision += 1;

    const workplanPath = join(controlRoot, "WORKPLAN.md");
    const tempMd = join(controlRoot, `WORKPLAN.md.tmp-${process.pid}`);
    await writeFile(tempMd, renderWorkplan(state));
    await rename(tempMd, workplanPath);

    if (process.env.WORKPLAN_FAULT_AFTER_PROJECTION === "1") {
      fail("injected fault after projection");
    }

    const tempState = join(
      controlRoot,
      ".workplan",
      `state.json.tmp-${process.pid}`,
    );
    await writeFile(tempState, `${JSON.stringify(state, null, 2)}\n`);
    await rename(tempState, statePath);

    return { revision: state.revision, integrated: true };
  } finally {
    await releaseTransitionLock(acquired);
  }
}

const ACCEPTANCE_FIELDS = [
  "schemaVersion",
  "verdict",
  "evaluator",
  "definitionHash",
  "integrationCommit",
  "commands",
  "evidenceRefs",
  "receiptHash",
];

function computeAcceptanceReceiptHash(acceptance) {
  const copy = { ...acceptance };
  delete copy.receiptHash;
  return `sha256:${createHash("sha256")
    .update(JSON.stringify(deepSortKeys(copy)))
    .digest("hex")}`;
}

function validateAcceptance(acceptance, card, actor) {
  if (
    acceptance === null ||
    typeof acceptance !== "object" ||
    Array.isArray(acceptance)
  ) {
    fail("acceptance must be an object");
  }
  const keys = Object.keys(acceptance);
  if (
    keys.length !== ACCEPTANCE_FIELDS.length ||
    !keys.every((key) => ACCEPTANCE_FIELDS.includes(key))
  ) {
    fail("acceptance has incorrect fields");
  }

  if (acceptance.schemaVersion !== 1) {
    fail("acceptance.schemaVersion must be 1");
  }
  if (acceptance.verdict !== "pass") {
    fail("acceptance.verdict must be pass");
  }
  if (
    typeof acceptance.evaluator !== "string" ||
    acceptance.evaluator !== actor
  ) {
    fail("acceptance.evaluator must equal actor");
  }
  if (acceptance.evaluator === card.owner) {
    fail("acceptance.evaluator must not equal card owner");
  }
  if (acceptance.definitionHash !== card.definition.definitionHash) {
    fail("acceptance definitionHash mismatch");
  }
  if (
    acceptance.integrationCommit !== card.integrationReceipt.integrationCommit
  ) {
    fail("acceptance integrationCommit mismatch");
  }
  if (
    !Array.isArray(acceptance.commands) ||
    !acceptance.commands.every((cmd) => typeof cmd === "string")
  ) {
    fail("acceptance commands must be a string array");
  }
  if (!arraysEqual(acceptance.commands, card.definition.acceptanceCommands)) {
    fail("acceptance commands mismatch");
  }
  if (
    !Array.isArray(acceptance.evidenceRefs) ||
    acceptance.evidenceRefs.length === 0 ||
    !acceptance.evidenceRefs.every(
      (ref) => typeof ref === "string" && ref.length > 0,
    )
  ) {
    fail(
      "acceptance evidenceRefs must be a nonempty array of nonempty strings",
    );
  }
  const expectedHash = computeAcceptanceReceiptHash(acceptance);
  if (acceptance.receiptHash !== expectedHash) {
    fail("acceptance receiptHash mismatch");
  }
}

const INTEGRATION_RECEIPT_FIELDS = [
  "actor",
  "reviewBranch",
  "reviewHead",
  "integrationCommit",
  "definitionHash",
  "revisionFrom",
  "revisionTo",
  "at",
];

function validateIntegrationReceipt(card, expectedRevision) {
  if (
    !card.integrationReceipt ||
    typeof card.integrationReceipt !== "object" ||
    Array.isArray(card.integrationReceipt)
  ) {
    fail(`card ${card.id} has no integration receipt`);
  }
  const receipt = card.integrationReceipt;
  const keys = Object.keys(receipt);
  if (
    keys.length !== INTEGRATION_RECEIPT_FIELDS.length ||
    !keys.every((key) => INTEGRATION_RECEIPT_FIELDS.includes(key))
  ) {
    fail("integrationReceipt has incorrect fields");
  }

  parseCoordinatorActor(receipt.actor);
  if (receipt.actor === card.owner) {
    fail("integrationReceipt actor must not equal card owner");
  }
  if (
    typeof receipt.integrationCommit !== "string" ||
    !/^[0-9a-f]{40}$|^[0-9a-f]{64}$/.test(receipt.integrationCommit)
  ) {
    fail("integrationReceipt integrationCommit must be 40 or 64 lowercase hex characters");
  }
  if (receipt.reviewBranch !== card.reviewFreeze.branch) {
    fail("integrationReceipt reviewBranch mismatch");
  }
  if (receipt.reviewHead !== card.reviewFreeze.head) {
    fail("integrationReceipt reviewHead mismatch");
  }
  if (receipt.definitionHash !== card.definition.definitionHash) {
    fail("integrationReceipt definitionHash mismatch");
  }
  if (!Number.isInteger(receipt.revisionFrom) || receipt.revisionFrom < 0) {
    fail("integrationReceipt.revisionFrom must be a nonnegative integer");
  }
  if (
    !Number.isInteger(receipt.revisionTo) ||
    receipt.revisionTo !== receipt.revisionFrom + 1
  ) {
    fail("integrationReceipt.revisionTo must be revisionFrom + 1");
  }
  // The board revision is global, so an unrelated card's transition advances it
  // between this card's integrate and its accept. Requiring equality here would
  // strand such a card in `verifying` forever, since revisions are monotonic and
  // `block` is gated on the same check. Card-level staleness needs no guard: the
  // only exits from `verifying` are accept (-> done) and block (-> blocked), both
  // gated on that status, so while the card is verifying its latest receipt is
  // necessarily this integration receipt. What must still fail closed is a receipt
  // claiming a revision the board has not reached, which indicates forgery.
  if (receipt.revisionTo > expectedRevision) {
    fail("integrationReceipt revision is ahead of expectedRevision");
  }
  if (!validateIsoTimestamp(receipt.at)) {
    fail("integrationReceipt.at must be a valid ISO timestamp");
  }
}

async function accept(options) {
  const {
    controlRoot,
    callerWorktree,
    expectedRevision,
    id,
    actor,
    acceptanceJson,
  } = options;

  if (!isAbsolute(controlRoot)) {
    fail("--control-root must be an absolute path");
  }
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }

  parseCoordinatorActor(actor);
  await assertSameRepository(controlRoot, callerWorktree);

  let acceptance;
  try {
    acceptance = JSON.parse(await readFile(acceptanceJson, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") {
      fail("acceptance file not found");
    }
    throw error;
  }

  const acquired = await acquireTransitionLock(controlRoot);
  try {
    const statePath = join(controlRoot, ".workplan", "state.json");
    const raw = await readFile(statePath, "utf8");
    const state = JSON.parse(raw);
    validateState(state);

    if (state.revision !== expectedRevision) {
      fail(
        `expected revision ${expectedRevision} but found ${state.revision}`,
      );
    }

    const card = state.cards.find((c) => c.id === id);
    if (!card) {
      fail(`card ${id} not found`);
    }
    if (card.status !== "verifying") {
      fail(`card ${card.id} is not in verifying status`);
    }

    validateTransitionCardBasics(card, "verifying");
    validateReviewFreeze(card);
    validateIntegrationReceipt(card, expectedRevision);

    const reviewHeadExists = await gitCommitExists(
      controlRoot,
      card.reviewFreeze.head,
    );
    if (!reviewHeadExists) {
      fail("review head commit not found");
    }
    const integrationExists = await gitCommitExists(
      controlRoot,
      card.integrationReceipt.integrationCommit,
    );
    if (!integrationExists) {
      fail("integration commit not found");
    }
    const isAncestor = await gitIsAncestor(
      controlRoot,
      card.reviewFreeze.head,
      card.integrationReceipt.integrationCommit,
    );
    if (!isAncestor) {
      fail("review head is not an ancestor of integration commit");
    }

    validateAcceptance(acceptance, card, actor);

    const originalPaths = [...card.paths];
    card.status = "done";
    card.releasedPaths = originalPaths;
    card.paths = [];
    card.acceptanceReceipt = {
      schemaVersion: acceptance.schemaVersion,
      verdict: acceptance.verdict,
      evaluator: acceptance.evaluator,
      definitionHash: acceptance.definitionHash,
      integrationCommit: acceptance.integrationCommit,
      commands: [...acceptance.commands],
      evidenceRefs: [...acceptance.evidenceRefs],
      receiptHash: acceptance.receiptHash,
      actor,
      releasedPaths: originalPaths,
      revisionFrom: expectedRevision,
      revisionTo: expectedRevision + 1,
      at: new Date().toISOString(),
    };
    appendReceipt(
      card,
      "verifying",
      "done",
      actor,
      expectedRevision,
      expectedRevision + 1,
      {
        schemaVersion: acceptance.schemaVersion,
        verdict: acceptance.verdict,
        evaluator: acceptance.evaluator,
        definitionHash: acceptance.definitionHash,
        integrationCommit: acceptance.integrationCommit,
        commands: acceptance.commands,
        evidenceRefs: acceptance.evidenceRefs,
        receiptHash: acceptance.receiptHash,
      },
    );

    state.revision += 1;

    const workplanPath = join(controlRoot, "WORKPLAN.md");
    const tempMd = join(controlRoot, `WORKPLAN.md.tmp-${process.pid}`);
    await writeFile(tempMd, renderWorkplan(state));
    await rename(tempMd, workplanPath);

    if (process.env.WORKPLAN_FAULT_AFTER_PROJECTION === "1") {
      fail("injected fault after projection");
    }

    const tempState = join(
      controlRoot,
      ".workplan",
      `state.json.tmp-${process.pid}`,
    );
    await writeFile(tempState, `${JSON.stringify(state, null, 2)}\n`);
    await rename(tempState, statePath);

    return { revision: state.revision, accepted: true };
  } finally {
    await releaseTransitionLock(acquired);
  }
}

const BLOCKABLE_STATUSES = ["claimed", "in_progress", "review", "verifying"];

function validateBlockedReceipt(receipt, card, expectedRevision) {
  const expectedFields = [
    "from",
    "to",
    "actor",
    "revisionFrom",
    "revisionTo",
    "at",
    "evidence",
  ];
  const keys = Object.keys(receipt);
  if (
    keys.length !== expectedFields.length ||
    !keys.every((key) => expectedFields.includes(key))
  ) {
    fail("blocked receipt has incorrect fields");
  }
  if (receipt.from !== card.blockedFrom) {
    fail("blocked receipt from mismatch");
  }
  if (receipt.to !== "blocked") {
    fail("blocked receipt to mismatch");
  }
  if (receipt.actor !== card.owner) {
    fail("blocked receipt actor mismatch");
  }
  if (!Number.isInteger(receipt.revisionFrom) || receipt.revisionFrom < 0) {
    fail("blocked receipt revisionFrom must be a nonnegative integer");
  }
  if (
    !Number.isInteger(receipt.revisionTo) ||
    receipt.revisionTo !== receipt.revisionFrom + 1
  ) {
    fail("blocked receipt revisionTo must be revisionFrom + 1");
  }
  if (receipt.revisionTo > expectedRevision) {
    fail("blocked receipt revisionTo cannot exceed expected revision");
  }
  if (!validateIsoTimestamp(receipt.at)) {
    fail("blocked receipt at must be a valid ISO timestamp");
  }
  if (
    !receipt.evidence ||
    typeof receipt.evidence !== "object" ||
    Array.isArray(receipt.evidence)
  ) {
    fail("blocked receipt evidence missing");
  }
  const evidenceKeys = Object.keys(receipt.evidence);
  if (
    evidenceKeys.length !== 2 ||
    !evidenceKeys.every((key) => ["reason", "retainPaths"].includes(key))
  ) {
    fail("blocked receipt evidence has incorrect fields");
  }
  if (
    typeof receipt.evidence.reason !== "string" ||
    receipt.evidence.reason.trim().length === 0
  ) {
    fail("blocked receipt evidence reason must be non-empty");
  }
  if (receipt.evidence.reason !== card.blockReason) {
    fail("blocked receipt evidence reason mismatch");
  }
  if (receipt.evidence.retainPaths !== card.retainPaths) {
    fail("blocked receipt evidence retainPaths mismatch");
  }
}

async function block(options) {
  const {
    controlRoot,
    callerWorktree,
    expectedRevision,
    id,
    owner,
    reason,
    retainPaths,
  } = options;

  if (!isAbsolute(controlRoot)) {
    fail("--control-root must be an absolute path");
  }
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }
  if (typeof reason !== "string" || reason.trim().length === 0) {
    fail("--reason must be a non-empty string");
  }
  if (typeof retainPaths !== "boolean") {
    fail("--retain-paths must be true or false");
  }

  parseClaimOwner(owner);
  await assertSameRepository(controlRoot, callerWorktree);

  const acquired = await acquireTransitionLock(controlRoot);
  try {
    const statePath = join(controlRoot, ".workplan", "state.json");
    const raw = await readFile(statePath, "utf8");
    const state = JSON.parse(raw);
    validateState(state);

    if (state.revision !== expectedRevision) {
      fail(
        `expected revision ${expectedRevision} but found ${state.revision}`,
      );
    }

    const card = state.cards.find((c) => c.id === id);
    if (!card) {
      fail(`card ${id} not found`);
    }
    if (!BLOCKABLE_STATUSES.includes(card.status)) {
      fail(`card ${card.id} cannot be blocked from ${card.status} status`);
    }
    if (card.owner !== owner) {
      fail(`card ${card.id} is not owned by ${owner}`);
    }

    validateTransitionCardBasics(card, card.status);
    if (card.definition.worktreePath !== callerWorktree) {
      fail("worktreePath must match callerWorktree");
    }
    if (["review", "verifying"].includes(card.status)) {
      validateReviewFreeze(card);
    }
    if (card.status === "verifying") {
      validateIntegrationReceipt(card, expectedRevision);
    }

    card.blockedFrom = card.status;
    card.blockReason = reason;
    card.retainPaths = retainPaths;
    card.status = "blocked";

    appendReceipt(
      card,
      card.blockedFrom,
      "blocked",
      owner,
      expectedRevision,
      expectedRevision + 1,
      { reason, retainPaths },
    );

    state.revision += 1;

    const workplanPath = join(controlRoot, "WORKPLAN.md");
    const tempMd = join(controlRoot, `WORKPLAN.md.tmp-${process.pid}`);
    await writeFile(tempMd, renderWorkplan(state));
    await rename(tempMd, workplanPath);

    if (process.env.WORKPLAN_FAULT_AFTER_PROJECTION === "1") {
      fail("injected fault after projection");
    }

    const tempState = join(
      controlRoot,
      ".workplan",
      `state.json.tmp-${process.pid}`,
    );
    await writeFile(tempState, `${JSON.stringify(state, null, 2)}\n`);
    await rename(tempState, statePath);

    return { revision: state.revision, blocked: true };
  } finally {
    await releaseTransitionLock(acquired);
  }
}

async function resume(options) {
  const { controlRoot, callerWorktree, expectedRevision, id, owner } = options;

  if (!isAbsolute(controlRoot)) {
    fail("--control-root must be an absolute path");
  }
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }

  parseClaimOwner(owner);
  await assertSameRepository(controlRoot, callerWorktree);

  const acquired = await acquireTransitionLock(controlRoot);
  try {
    const statePath = join(controlRoot, ".workplan", "state.json");
    const raw = await readFile(statePath, "utf8");
    const state = JSON.parse(raw);
    validateState(state);

    if (state.revision !== expectedRevision) {
      fail(
        `expected revision ${expectedRevision} but found ${state.revision}`,
      );
    }

    const card = state.cards.find((c) => c.id === id);
    if (!card) {
      fail(`card ${id} not found`);
    }
    if (card.status !== "blocked") {
      fail(`card ${card.id} is not in blocked status`);
    }
    if (card.owner !== owner) {
      fail(`card ${card.id} is not owned by ${owner}`);
    }

    if (!card.blockedFrom || !BLOCKABLE_STATUSES.includes(card.blockedFrom)) {
      fail("invalid blockedFrom");
    }
    if (
      typeof card.blockReason !== "string" ||
      card.blockReason.trim().length === 0
    ) {
      fail("blockReason must be a non-empty string");
    }
    if (typeof card.retainPaths !== "boolean") {
      fail("retainPaths must be a boolean");
    }

    const latestReceipt = card.receipts.at(-1);
    if (!latestReceipt || latestReceipt.to !== "blocked") {
      fail("latest receipt is not a blocked receipt");
    }
    validateBlockedReceipt(latestReceipt, card, expectedRevision);

    if (["review", "verifying"].includes(card.blockedFrom)) {
      validateReviewFreeze(card);
    }
    if (card.blockedFrom === "verifying") {
      validateIntegrationReceipt(card, latestReceipt.revisionFrom);
    }

    validateTransitionCardBasics(card, "blocked");
    if (card.definition.worktreePath !== callerWorktree) {
      fail("worktreePath must match callerWorktree");
    }
    validateNoOwnerWip(owner, card, state);
    validateClaimPathExclusivity(card, state);

    const blockedFrom = card.blockedFrom;
    const blockReason = card.blockReason;
    const retainPaths = card.retainPaths;

    appendReceipt(
      card,
      "blocked",
      "in_progress",
      owner,
      expectedRevision,
      expectedRevision + 1,
      { blockedFrom, reason: blockReason, retainPaths },
    );

    card.status = "in_progress";
    delete card.blockedFrom;
    delete card.blockReason;
    delete card.retainPaths;

    state.revision += 1;

    const workplanPath = join(controlRoot, "WORKPLAN.md");
    const tempMd = join(controlRoot, `WORKPLAN.md.tmp-${process.pid}`);
    await writeFile(tempMd, renderWorkplan(state));
    await rename(tempMd, workplanPath);

    if (process.env.WORKPLAN_FAULT_AFTER_PROJECTION === "1") {
      fail("injected fault after projection");
    }

    const tempState = join(
      controlRoot,
      ".workplan",
      `state.json.tmp-${process.pid}`,
    );
    await writeFile(tempState, `${JSON.stringify(state, null, 2)}\n`);
    await rename(tempState, statePath);

    return { revision: state.revision, resumed: true };
  } finally {
    await releaseTransitionLock(acquired);
  }
}

async function ready(options) {
  const {
    controlRoot,
    callerWorktree,
    expectedRevision,
    id,
    owner,
    definitionJson,
  } = options;

  if (!isAbsolute(controlRoot)) {
    fail("--control-root must be an absolute path");
  }
  if (!isAbsolute(callerWorktree)) {
    fail("--caller-worktree must be an absolute path");
  }

  await assertSameRepository(controlRoot, callerWorktree);

  let definition;
  try {
    definition = JSON.parse(await readFile(definitionJson, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") {
      fail("definition file not found");
    }
    throw error;
  }

  const acquired = await acquireReadyLock(controlRoot);
  try {
    const statePath = join(controlRoot, ".workplan", "state.json");
    const raw = await readFile(statePath, "utf8");
    const state = JSON.parse(raw);
    validateState(state);

    if (state.revision !== expectedRevision) {
      fail(
        `expected revision ${expectedRevision} but found ${state.revision}`,
      );
    }

    const card = state.cards.find((c) => c.id === id);
    if (!card) {
      fail(`card ${id} not found`);
    }
    if (card.status !== "backlog") {
      fail(`card ${id} is not in backlog status`);
    }
    if (!Array.isArray(card.paths) || card.paths.length === 0) {
      fail(`card ${id} must have nonempty paths`);
    }
    for (const path of card.paths) {
      validateOwnedPath(path);
    }

    if (card.dependencies) {
      for (const depId of card.dependencies) {
        const dep = state.cards.find((c) => c.id === depId);
        if (!dep) {
          fail(`dependency ${depId} not found`);
        }
        if (dep.status !== "done") {
          fail(`dependency ${depId} is not done`);
        }
      }
    }

    validateDefinition(definition, callerWorktree);
    const expectedHash = computeDefinitionHash(definition);
    if (expectedHash !== definition.definitionHash) {
      fail("definitionHash mismatch");
    }

    card.status = "ready";
    validatePathExclusivity(state);

    card.owner = "";
    card.definition = definition;
    card.authorityGrant = { ...AUTHORITY_GRANT };
    card.receipts = [...(card.receipts || [])];
    card.receipts.push({
      from: "backlog",
      to: "ready",
      actor: owner,
      revisionFrom: expectedRevision,
      revisionTo: expectedRevision + 1,
      at: new Date().toISOString(),
    });

    state.revision += 1;

    const workplanPath = join(controlRoot, "WORKPLAN.md");
    const tempMd = join(controlRoot, `WORKPLAN.md.tmp-${process.pid}`);
    await writeFile(tempMd, renderWorkplan(state));
    await rename(tempMd, workplanPath);

    if (process.env.WORKPLAN_FAULT_AFTER_PROJECTION === "1") {
      fail("injected fault after projection");
    }

    const tempState = join(
      controlRoot,
      ".workplan",
      `state.json.tmp-${process.pid}`,
    );
    await writeFile(tempState, `${JSON.stringify(state, null, 2)}\n`);
    await rename(tempState, statePath);

    return { revision: state.revision, ready: true };
  } finally {
    await releaseReadyLock(acquired);
  }
}

export {
  add,
  acquireAddLock,
  releaseAddLock,
  ready,
  claim,
  claimNext,
  acquireClaimLock,
  releaseClaimLock,
  start,
  submit,
  integrate,
  accept,
  block,
  resume,
  acquireTransitionLock,
  releaseTransitionLock,
  computeDefinitionHash,
  computeAcceptanceReceiptHash,
};

async function main() {
  const { command, flags, positionals } = parseArgs(process.argv);

  if (command === "sync") {
    if (positionals.length > 0) {
      fail(`unexpected positional argument: ${positionals[0]}`);
    }
    const controlRoot = flags["--control-root"];
    if (!controlRoot) {
      fail("--control-root is required");
    }
    const result = await sync(controlRoot);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  if (command === "add") {
    const options = parseAddFlags(flags, positionals);
    const result = await add(options);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  if (command === "ready") {
    const options = parseReadyFlags(flags, positionals);
    const result = await ready(options);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  if (command === "claim") {
    const options = parseClaimFlags(flags, positionals);
    const result = await claim(options);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  if (command === "claim-next") {
    const options = parseClaimNextFlags(flags, positionals);
    const result = await claimNext(options);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  if (command === "start") {
    const options = parseStartFlags(flags, positionals);
    const result = await start(options);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  if (command === "submit") {
    const options = parseSubmitFlags(flags, positionals);
    const result = await submit(options);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  if (command === "integrate") {
    const options = parseIntegrateFlags(flags, positionals);
    const result = await integrate(options);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  if (command === "accept") {
    const options = parseAcceptFlags(flags, positionals);
    const result = await accept(options);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  if (command === "block") {
    const options = parseBlockFlags(flags, positionals);
    const result = await block(options);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  if (command === "resume") {
    const options = parseResumeFlags(flags, positionals);
    const result = await resume(options);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  fail("only the sync, add, ready, claim, claim-next, start, submit, integrate, accept, block, and resume commands are supported");
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    process.stderr.write(`workplan: ${error.message}\n`);
    process.exitCode = 1;
  });
}
