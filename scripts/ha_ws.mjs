// Sends Home Assistant websocket commands using the hass-mcp credentials.
// Usage: node ha_ws.mjs '<json array of command objects without id>'
import fs from "node:fs";
import os from "node:os";

const cfg = JSON.parse(fs.readFileSync(os.homedir() + "/.claude.json", "utf8"));
let env = null;
for (const [k, p] of Object.entries(cfg.projects || {})) {
  const s = p?.mcpServers?.homeassistant;
  if (s?.env?.HASS_TOKEN) { env = s.env; break; }
}
if (!env) throw new Error("no HA credentials found");

const url = env.HASS_URL.replace(/^http/, "ws").replace(/\/$/, "") + "/api/websocket";
const cmds = JSON.parse(process.argv[2]);
const ws = new WebSocket(url);
let id = 0;
const results = [];

ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.type === "auth_required") {
    ws.send(JSON.stringify({ type: "auth", access_token: env.HASS_TOKEN }));
  } else if (msg.type === "auth_ok") {
    next();
  } else if (msg.type === "auth_invalid") {
    console.error("auth failed"); process.exit(1);
  } else if (msg.type === "result") {
    results.push({ success: msg.success, result: msg.result, error: msg.error });
    next();
  }
};

function next() {
  if (id >= cmds.length) {
    console.log(JSON.stringify(results, null, 1));
    ws.close();
    return;
  }
  id += 1;
  ws.send(JSON.stringify({ id, ...cmds[id - 1] }));
}
