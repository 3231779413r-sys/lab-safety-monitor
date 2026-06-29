const { mkdirSync, createWriteStream } = require("fs");
const { join } = require("path");
const { spawn } = require("child_process");

function pad(value) {
  return String(value).padStart(2, "0");
}

function resolveLogPath() {
  const now = new Date();
  const root = process.env.FRONTEND_LOG_ROOT || "/logs";
  const dir = join(
    root,
    "frontend",
    String(now.getFullYear()),
    pad(now.getMonth() + 1),
    pad(now.getDate())
  );
  mkdirSync(dir, { recursive: true });
  return join(dir, `${pad(now.getHours())}.log`);
}

let currentHourKey = "";
let currentStream = null;

function ensureStream() {
  const now = new Date();
  const nextHourKey = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}`;
  if (currentStream && currentHourKey === nextHourKey) {
    return currentStream;
  }
  if (currentStream) {
    currentStream.end();
  }
  currentHourKey = nextHourKey;
  currentStream = createWriteStream(resolveLogPath(), { flags: "a", encoding: "utf8" });
  return currentStream;
}

function writeLog(chunk) {
  const text = chunk.toString();
  process.stdout.write(text);
  ensureStream().write(text);
}

const child = spawn("node", ["server.js"], {
  cwd: process.cwd(),
  env: process.env,
  stdio: ["inherit", "pipe", "pipe"],
});

child.stdout.on("data", writeLog);
child.stderr.on("data", writeLog);

child.on("exit", (code, signal) => {
  if (currentStream) {
    currentStream.end();
  }
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
