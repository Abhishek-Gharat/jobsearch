const { spawn, execSync, exec } = require("child_process");
const path = require("path");
const fs = require("fs");

process.env.DATA_DIR = path.join(__dirname, "..", "data");
process.env.LOG_DIR = path.join(__dirname, "..", "logs");
process.env.KILO_BIN = path.join(__dirname, "fake-kilo.cmd");
process.env.FAKE_KILO_SCENARIO = "hang";
process.env.FORCE_COLOR = "0";
process.env.NO_COLOR = "1";

const worker = require("../worker");

function psList() {
  try {
    const out = execSync('powershell -NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process -Filter \\"name=\'node.exe\'\\" | Select-Object ProcessId,@{N=\'CL\';E={$_.CommandLine}} | Format-List"', {
      encoding: "utf8",
      windowsHide: true,
      timeout: 10000,
    });
    return out;
  } catch (e) {
    return "ERR: " + e.message;
  }
}

function findHangPid() {
  const out = execSync('powershell -NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process -Filter \\"name=\'node.exe\'\\" | Where-Object { $_.CommandLine -like \\"*fake-kilo.cmd*\\" } | Select-Object -ExpandProperty ProcessId"', {
    encoding: "utf8",
    windowsHide: true,
    timeout: 10000,
  });
  const ids = out.split(/[\r\n]+/).map((s) => s.trim()).filter((s) => /^\d+$/.test(s)).map(Number);
  return ids;
}

function pidAlive(pid) {
  try {
    execSync(`powershell -NoProfile -NonInteractive -Command "Get-Process -Id ${pid} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"`, {
      encoding: "utf8",
      windowsHide: true,
      timeout: 5000,
    });
    return true;
  } catch {
    return false;
  }
}

(async () => {
  console.log("=== launch hang task ===");
  const r = worker.launchForTask({ id: 1, prompt: "repro-hang" });
  console.log("launch result:", JSON.stringify(r));

  await new Promise((res) => setTimeout(res, 300));

  const pids = findHangPid();
  console.log("hang pids found:", pids);
  if (!pids.length) {
    console.log("FAIL: no hang child found after launch");
    process.exit(1);
  }
  const targetPid = pids[0];
  console.log("target pid:", targetPid);
  console.log("alive before kill:", pidAlive(targetPid));

  console.log("\n=== killRunning ===");
  const killResult = worker.killRunning();
  console.log("kill result:", JSON.stringify(killResult));

  const start = Date.now();
  let alive = true;
  while (alive && Date.now() - start < 5000) {
    alive = pidAlive(targetPid);
    if (alive) await new Promise((res) => setTimeout(res, 100));
  }
  const elapsed = Date.now() - start;
  console.log(`\npid ${targetPid} alive after kill: ${alive}`);
  console.log(`time until dead: ${elapsed}ms`);

  const finalState = worker.getState();
  console.log("\nfinal worker state:", JSON.stringify(finalState, null, 2));
  console.log("\nlaunching:", finalState.launching);
  console.log("taskState:", finalState.taskState);
  console.log("lastSuccess:", finalState.lastSuccess);

  process.exit(alive ? 1 : 0);
})();
