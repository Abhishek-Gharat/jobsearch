/*
 * Fake kilo CLI for automated tests.
 *
 * Driven by env vars:
 *   FAKE_KILO_SCENARIO - success | error | retry | hang | nostop
 *   FAKE_KILO_STATE    - path to a counter file (retry scenario uses it)
 *
 * Mirrors the real kilo run --auto <prompt> --format json --title <t>
 * invocation and emits the same NDJSON event shapes on stdout.
 */
const fs = require("fs");

const args = process.argv.slice(2);
const sessionID = "ses_fake000000000000000000000000";

if (args[0] === "--version") {
  console.log("7.4.22");
  process.exit(0);
}

const scenario = process.env.FAKE_KILO_SCENARIO || "success";
const stateFile = process.env.FAKE_KILO_STATE || "";
const autoIdx = args.indexOf("--auto");
const prompt = autoIdx >= 0 ? args[autoIdx + 1] : "";

const emit = (o) => console.log(JSON.stringify(o));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  if (scenario === "hang") {
    emit({ type: "step_start", timestamp: Date.now(), sessionID, part: { type: "step-start" } });
    setInterval(() => {}, 1000); // keep event loop alive until killed
    return;
  }

  let invocation = 0;
  if (stateFile) {
    try {
      invocation = Number(fs.readFileSync(stateFile, "utf8")) || 0;
    } catch {}
    fs.writeFileSync(stateFile, String(invocation + 1));
  }

  if (scenario === "retry" && invocation === 0) {
    emit({
      type: "error",
      timestamp: Date.now(),
      sessionID,
      error: { name: "UnknownError", data: { message: "Internal server error" } },
    });
    console.error("Error: Internal server error");
    process.exit(1);
  }

  if (scenario === "error") {
    emit({ type: "step_start", timestamp: Date.now(), sessionID, part: { type: "step-start" } });
    await sleep(100);
    emit({
      type: "error",
      timestamp: Date.now(),
      sessionID,
      error: { name: "UnknownError", data: { message: "Model provider exploded" } },
    });
    process.exit(1);
  }

  if (scenario === "nostop") {
    emit({ type: "step_start", timestamp: Date.now(), sessionID, part: { type: "step-start" } });
    await sleep(100);
    emit({
      type: "text",
      timestamp: Date.now(),
      sessionID,
      part: { type: "text", text: "partial answer, no clean stop" },
    });
    process.exit(0); // exits 0 WITHOUT step_finish reason=stop -> must NOT count as success
  }

  emit({ type: "step_start", timestamp: Date.now(), sessionID, part: { type: "step-start" } });
  await sleep(100);
  emit({
    type: "text",
    timestamp: Date.now(),
    sessionID,
    part: { type: "text", text: `Found: Senior React Developer at Pune Corp (prompt: ${prompt})` },
  });
  await sleep(100);
  emit({
    type: "step_finish",
    timestamp: Date.now(),
    sessionID,
    part: { type: "step-finish", reason: "stop" },
  });
  process.exit(0);
})();