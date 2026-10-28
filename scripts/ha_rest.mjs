// Calls the Home Assistant REST API using the hass-mcp credentials.
// Usage: node ha_rest.mjs METHOD /api/path ['<json body>']
import fs from "node:fs";
import os from "node:os";

const cfg = JSON.parse(fs.readFileSync(os.homedir() + "/.claude.json", "utf8"));
let env = null;
for (const p of Object.values(cfg.projects || {})) {
  const s = p?.mcpServers?.homeassistant;
  if (s?.env?.HASS_TOKEN) { env = s.env; break; }
}
if (!env) throw new Error("no HA credentials found");

const [method, path, body] = process.argv.slice(2);
const res = await fetch(env.HASS_URL.replace(/\/$/, "") + path, {
  method,
  headers: { Authorization: `Bearer ${env.HASS_TOKEN}`, "Content-Type": "application/json" },
  body: body || undefined,
});
console.log(res.status);
const text = await res.text();  // /api/template answers with plain text
try { console.log(JSON.stringify(JSON.parse(text), null, 1)); } catch { console.log(text); }
