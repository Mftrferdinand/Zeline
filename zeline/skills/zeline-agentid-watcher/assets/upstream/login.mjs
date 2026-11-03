// Log in the Nth wallet from config.json (default: the 1st): node login.mjs 2
// Prints a login link; open it on the phone with that wallet's Binance App and confirm (valid for about 5 minutes)
import { execFile } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const DIR = dirname(fileURLToPath(import.meta.url));
const CONFIG = JSON.parse(readFileSync(join(DIR, 'config.json'), 'utf8'));
const wc = CONFIG.wallets[(Number(process.argv[2]) || 1) - 1];
if (!wc) { console.error('wallet not found'); process.exit(1); }
const run = (args, timeout = 60000) => new Promise((res) => {
  const cb = (e, so, se) => { const t = String(so || se || e?.message || ''); let j = null; try { j = JSON.parse(t.slice(t.indexOf('{'))); } catch {} res({ t, j }); };
  if (!wc.bawDir) return execFile('baw', [...args, '--json'], { shell: true, timeout }, cb);
  const env = { ...process.env, BINANCE_BAW_DIR: join(DIR, wc.bawDir), BINANCE_INSTANCE_ID: wc.instanceId || wc.name };
  execFile(process.execPath, [join(DIR, wc.bawPkg || 'baw2pkg', 'node_modules', '@binance', 'agentic-wallet', 'dist', 'index.js'), ...args, '--json'], { env, timeout }, cb);
});
const si = await run(['auth', 'signin']);
const d = si.j?.data; if (!d?.qrCodeId) { console.log(si.t); process.exit(1); }
console.log(`\n[${wc.name}] Open this link on your phone to confirm the login:\n${d.urlForWeb}\npairing code: ${d.pairingCode}\nwaiting for confirmation…`);
const v = await run(['auth', 'verify', '--qrCodeId', d.qrCodeId], 330000);
console.log(v.j?.data?.status === 'SUCCESS' ? '✅ signed in' : '❌ ' + v.t.slice(0, 300));
