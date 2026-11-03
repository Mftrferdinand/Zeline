// Agent ID Sniper — monitoring + auto-mint + automatic take-profit for Binance Agentic Wallet (.ai Agent ID) exclusive mints.
// Original project (credit kept): https://github.com/nancyc0813-maker/agentid-sniper — official TG group: https://t.me/nancy_c0813
// This fork (English edition): https://github.com/setyamickala/fork-agentid-sniper — all text and messages translated to English.
// Runs entirely locally: scan new BSC contracts → simulate → auto-mint via the baw CLI → auto-sell by rules once the pool opens → Telegram notifications. No AI involved.
// To stop: drop a WATCH_STOP file in this folder. See config.example.json for settings.
import { execFile } from 'node:child_process';
import { appendFileSync, writeFileSync, readFileSync, existsSync, statSync, renameSync, copyFileSync, unlinkSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const DIR = dirname(fileURLToPath(import.meta.url));
const SELF = fileURLToPath(import.meta.url), SELF_MT = statSync(SELF).mtimeMs;
const LOG = join(DIR, 'data', 'watch.log'), STATE = join(DIR, 'data', 'watch_state.json');
{ /* review 11/1: two copies running would mint twice, sell twice and clobber each other's records → only allow one instance */ const lk = join(DIR, 'data', 'watch.lock'); try { const old = existsSync(lk) ? Number(readFileSync(lk, 'utf8')) : 0; if (old && old !== process.pid) { let alive = false; try { process.kill(old, 0); alive = true; } catch {} if (alive) { console.log(`Another instance is already running (PID ${old}), this one will not start`); process.exit(0); } } writeFileSync(lk, String(process.pid)); process.on('exit', () => { try { if (Number(readFileSync(lk, 'utf8')) === process.pid) unlinkSync(lk); } catch {} }); } catch {} }
const ts = () => new Date().toLocaleString('en-US', { hour12: false, timeZone: CONFIG.timeZone || 'Asia/Taipei' });
const log = (s) => { try { appendFileSync(LOG, `[${ts()}] ${s}\n`); } catch {} };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CONFIG = JSON.parse(readFileSync(join(DIR, 'config.json'), 'utf8'));
const USDT = '0x55d398326f99059ff775485246999027b3197955', WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c', USD1 = '0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d';
const NATIVE_BNB = '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE'; // always swap back to BNB on a sell
const ROUTER = '0x10ed43c718714eb63d5aa57b78b54704e256024e', FACTORY = '0xca143ce32fe78f1f7019d7d551a6402fc5350c73';
const REGISTRY = '60b9262456de011f7d15a54cdd8c48baaa8b022f'; // Binance Agent ID registry contract: if it appears in the contract code = Agent ID only
const INFRA = new Set(['0x' + REGISTRY]); // the registry contract itself is not a mint project (user report v2.4.4: the open-source build missed this line, ReferenceError: INFRA is not defined)
const T_TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef';
const T_SWAP = '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822';

// manually tracked positions (config.json tokens). cost = cost per wallet (U); both = every wallet carries this cost; hold = never auto-sell, notify only; sellNow = sell everything right away
const TOKENS = (CONFIG.tokens || []).map((t) => ({ ...t, addr: t.addr.toLowerCase() }));
// sell rules (paid coins execute on price hit; free coins notify only), can be overridden under rules in config.json
const ruleTxt = () => `${RULE.backX}x break-even, ${(RULE.steps || []).map(([x, p]) => `${x}x sell ${p}%`).join(', ')}; before break-even sell all on a ${Math.round(RULE.crash * 100)}% drop from the peak, after break-even only sell on a ${Math.round((RULE.crashAfterBack || RULE.crash) * 100)}% drop${RULE.moonPct ? `, and keep ${RULE.moonPct}% as a long hold, notify only` : ''}`;
const RULE = {
  // user-changed paid-coin rules (execute on price hit): 2x → sell to recover the cost; 10x → sell 30% of what is left; 20x → sell another 50% of what is left; otherwise hold, sell all on a 40% drop from the peak
  backX: 2, steps: [[10, 30], [20, 50]], crash: 0.4,
  crashAfterBack: 0.6, moonPct: 20, moonNotifyX: [50, 100], // user 11/1 13:54: widen the post-break-even drop threshold to 60%; keep 20% for a long hold after break-even, never auto-sell, only notify at 50/100x
  fastMin: 30, fastCrash: 0.4,  // check more often within 30 min of the pool opening (same 40% drop threshold)
  stallSec: 300, drop: 0.5, // free coin: sell all on a 50% drop from the peak (unchanged)
  notifyX: [5, 10, 20],
  ...(CONFIG.rules || {}),
};

let TG = null;
{ const a = CONFIG.telegram?.botToken || process.env.TG_BOT_TOKEN, b = CONFIG.telegram?.chatId || process.env.TG_CHAT_ID; if (a && b) TG = { a, b }; }
// user-defined: only ring for things that involve buying/selling or need the user to decide; everything else (auto-mint, coin received, login success…) is delivered silently
const isFreeMintNote = (t) => /^🆓/u.test(t) || (/^🆕/u.test(t) && t.includes('🤖 auto-minted'));
const important = (t) => !isFreeMintNote(t) && (/^(⏰|⚠️|🔑|💰|🔥|🚀|🦋|🏗|🛒)/u.test(t) || t.includes('not free')); 

const sendable = (t) => (!GROUP_ID && /^(🛒|🚀)/u.test(t)) || /^(💰|🔑|⏰|🦋)/u.test(t) /* user 10/31 05:11: free projects are not sent to the personal TG; 10/31 21:17: a minted project (🛒) and a pool open (🚀) go to the group only, not the personal TG (it kept ringing) */ || (/^🚀/u.test(t) && t.includes('platform coin')) || (/^🔥/u.test(t) && /\d+x/.test(t) && !t.includes('rush')) || (/^⚠️/u.test(t) && /(want to sell|pulled back|dropped|login|developer mode|failed|behind|running low|honeypot|Flap)/.test(t));

const GROUP_ID = CONFIG.telegram?.groupChatId || ''; // optional: also post to the TG group (buys, pool opens, multiples, sells)
const groupSent = new Map();
async function toGroup(text) { 
  if (!TG || !GROUP_ID) return;
  let msg = '', key = '';
  const ca = (text.match(/0x[a-fA-F0-9]{40}/) || [])[0] || '';
  
  if (/^🛒/u.test(text)) { const name = (text.match(/project:\s*([^\n（(|]+)/i) || [])[1]?.trim() || ''; const pct = (text.match(/progress\s*~?(\d+)%/) || [])[1]; msg = `🎯 Minted a paid project: ${name || 'new project'}${pct ? ` (mint progress ~${pct}%)` : ''}\nContract: ${ca}`; key = 'mint:' + ca.toLowerCase(); }
  /* user 10/31 20:48: announce everything that gets minted in the group (free included), and announce pool opens too */
  if (!msg && /^🆓/u.test(text)) { const name = (text.match(/project:\s*([^\n（(]+)/i) || [])[1]?.trim() || ''; msg = `🎁 Minted a free project: ${name || 'new project'}\nContract: ${ca}`; key = 'free:' + ca.toLowerCase(); }
  if (!msg && /^🚀/u.test(text) && text.includes('pool')) { const sym = (text.match(/^🚀\s*(\S+)/u) || [])[1] || ''; const tok = (text.match(/Contract:?\s*(0x[a-fA-F0-9]{40})/) || [])[1] || ca; msg = `🚀 ${sym} pool opened${(text.match(/pool opened \(([^)]+)\)/) || [])[1] ? ' (' + text.match(/pool opened \(([^)]+)\)/)[1] + ')' : ''}\nContract: ${tok}`; key = 'open:' + tok.toLowerCase(); }
  if (!msg || !ca) return;
  { const cl = ca.toLowerCase(); S.grpOnce = S.grpOnce || {}; const gk = key.startsWith('open:') ? key : cl; if (S.grpOnce[gk]) return; S.grpOnce[gk] = 1; } /* announce only once per contract in the group (minted / 70%, whichever comes first; pool opens are announced separately) */
  const now = Date.now(); if (groupSent.has(key) && now - groupSent.get(key) < 6 * 3600e3) return; groupSent.set(key, now);
  try { await fetch(`https://api.telegram.org/bot${TG.a}/sendMessage`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ chat_id: GROUP_ID, text: '📡 Agent ID broadcast\n' + msg, disable_web_page_preview: true }), signal: AbortSignal.timeout(10000) }); } catch {}
}
async function freeProgWatch() { // free projects also get their mint progress watched (minted / total); announce to the group once it passes 70%
  S.freeTrack = S.freeTrack || {};
  for (const [c, F] of Object.entries(S.freeTrack)) {
    if (Date.now() - F.at > 72 * 3600e3 || F.done) { delete S.freeTrack[c]; continue; }
    const tsR = await call(c, '0x18160ddd'); if (!tsR || tsR.length < 66) { F.done = 1; continue; } const sup = Number(BigInt(tsR.slice(0, 66)));
    let max = 0; for (const sel of ['0xd5abeb01', '0x32cb6b0c', '0x355274ea', '0xe985e367', '0xf1aee5a2']) { const r = await call(c, sel); if (r && r.length >= 66) { const x = Number(BigInt(r.slice(0, 66))); if (x > 0 && x >= sup && x < 1e15) { max = x; break; } } }
    if (!max) { F.miss = (F.miss || 0) + 1; if (F.miss >= 3) F.done = 1; continue; } // stop tracking when the total supply cannot be read
    const pct = sup / max, prev = F.p; F.p = pct;
    if (pct >= 0.7 && (prev != null ? prev < 0.7 : Date.now() - F.at < 3600e3)) { const nm = tokStr(await call(c, '0x06fdde03')) || ''; await groupProgress(c, nm, pct); }
    if (pct >= 1) F.done = 1;
    await sleep(100);
  }
}
async function groupProgress(c, name, pct) { // any project (free or paid, minted or not) whose mint progress first passes 70% → announce to the group, once per project
  S.g70 = S.g70 || {}; if (!TG || !GROUP_ID || S.g70[c]) return;
  try { const d = S.deployer?.[c] || (await deployerOf(c)); if (d && (S.black?.[d] || S.paid?.[c]?.drain)) { S.g70[c] = 'skip'; return; } } catch {} /* fix review: do not push blacklisted / fund-drained deployers to the group */
  S.g70[c] = 'wait'; await sleep(90e3); /* user 10/31 21:18: announce once per project in the group — wait 90s first; if we mint it meanwhile only the "minted" message is sent */
  if (S.grpOnce?.[c]) { S.g70[c] = 'merged'; return; }
  S.g70[c] = ts(); S.grpOnce = S.grpOnce || {}; S.grpOnce[c] = 1;
  const text = `📡 Agent ID broadcast\n🔥 ${name || 'new project'} mint progress passed 70% (currently ${Math.round(pct * 100)}%)\nContract: ${c}`;
  log('group broadcast: ' + text.replace(/\n/g, ' | '));
  try { await fetch(`https://api.telegram.org/bot${TG.a}/sendMessage`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ chat_id: GROUP_ID, text, disable_web_page_preview: true }), signal: AbortSignal.timeout(10000) }); } catch {}
}
async function riskNote(c) { /* new feature: check whether the contract keeps developer powers like "can mint more / can pause / blacklist / change tax", notify only, never block */
  try { const code = (await rpc('eth_getCode', [c, 'latest'])) || ''; if (code.length < 10) return '';
    const has = (sels) => sels.some((x) => code.includes('63' + x)); const f = [];
    if (has(['40c10f19'])) f.push('can mint more');
    if (has(['8456cb59', '02329a29'])) f.push('can pause trading');
    if (has(['f9f92be4', '44337ea1', '0ecb93c0', '1a895266'])) f.push('has a blacklist');
    if (has(['8c0b5e22', 'c0246668', '5342acb4', 'aa4b6fca', '061c82d0'])) f.push('can change tax/limits');
    if (!f.length) return ''; const ow = await call(c, '0x8da5cb5b'); if (ow && BigInt(ow.slice(0, 66)) === 0n) return '';
    return '\n⚠️ Contract power warning: ' + f.join(', ') + ' (the developer has not renounced)';
  } catch { return ''; } }
async function honeypotCheck(W, t, st, bal) { /* new feature: when the pool opens, simulate "sending the coin into the pool" (first step of a sell); if that fails it is very likely a honeypot */
  if (st.hpChecked || !st.pair || !/^0x[0-9a-f]{40}$/.test(st.pair) || !(bal > 0n)) return; st.hpChecked = 1;
  try { await rpc('eth_call', [{ from: W.addr, to: t.addr, data: '0xa9059cbb' + wa(st.pair) + w(bal / 100n || 1n) }, 'latest']); }
  catch (e) { await tg(`⚠️ [${W.name}] ${t.sym} pool opened, but the simulated sell failed; it may be a honeypot (buy only, cannot sell) and the auto-sell may fail\n${t.addr}`); }
}
async function flapApprovalWatch() { if (!FLAP_BAN) return; /* check wallet approvals every 10 min: revoke and notify immediately if anything is approved to Flap (swap routing may create approvals by itself) */
  for (const W of WALLETS) { const r = await W.baw(['approvals', 'list', '--limit', '100']); const L = r.j?.data?.list || [];
    for (const x of L) { const sp = String(x.spender || '').toLowerCase(); if (!FLAP_BLOCK.has(sp) && !/flap/i.test(String(x.spenderName || ''))) continue;
      const rv = await W.baw(['approvals', 'revoke', '--binanceChainId', '56', '--tokenContract', x.tokenContract, '--spender', x.spender, '--type', x.type || 'approve']);
      await tg(`⚠️ [${W.name}] found an approval to Flap (${x.tokenSymbol || x.tokenContract}), ${rv.j?.success ? 'auto-revoked' : 'auto-revoke failed, please revoke manually: ' + (rv.j?.error?.message || rv.t).slice(0, 80)}`); } }
}
async function lowBnbWatch() { /* new feature: warn when BNB is running low on gas (at most once per wallet every 6 h) */
  S.lowBnbAt = S.lowBnbAt || {};
  for (const W of WALLETS) { const b = Number(BigInt((await rpc('eth_getBalance', [W.addr, 'latest'])) || '0x0')) / 1e18;
    if (b < PAID.keepBnb + 0.02 && Date.now() - (S.lowBnbAt[W.name] || 0) > 6 * 3600e3) { S.lowBnbAt[W.name] = Date.now(); await tg(`⚠️ [${W.name}] BNB is running low: ${b.toFixed(4)} BNB left, may not cover gas; even free projects will fail, please top up some BNB`); } }
}
function pruneState() { /* fix review: trim the state file periodically so it does not keep growing and slowing down saves */
  const cut = (o, max) => { const k = Object.keys(o || {}); if (k.length > max) for (const x of k.slice(0, k.length - max)) delete o[x]; };
  cut(S.seenMints, 30000); cut(S.g70, 3000); cut(S.grpOnce, 5000); cut(S.missed, 500); cut(S.deployer, 20000);
  if (S.trades?.length > 1000) S.trades = S.trades.slice(-1000);
  for (const st of Object.values(S.tokens || {})) if (st.holders) cut(st.holders, 20000);
}
async function dailyReport() { /* new feature: send stats + positions automatically every night at 9 pm (Taipei) */
  const tp = new Date(Date.now() + 8 * 3600e3); const day = tp.toISOString().slice(0, 10);
  if (tp.getUTCHours() !== 21 || S.dailyAt === day) return; S.dailyAt = day; save();
  let um = 0; try { const today = ts().split(/\s/)[0]; /* review 11/1: log dates look like 11/1/2026 (no zero padding), the old compare never matched */ um = new Set(readFileSync(LOG, 'utf8').split('\n').filter((l) => l.startsWith('[' + today) && l.includes('] mint failed ')).map((l) => (l.match(/0x[a-f0-9]{40}/) || [''])[0])).size; } catch {}
  const today2 = ts().split(/\s/)[0]; const ms = Object.entries(S.missed || {}).filter(([, m]) => String(m.at).startsWith(today2)).slice(-8);
  const missTxt = ms.length ? `\n${ms.length} projects that almost sold out today but were not minted:\n` + ms.map(([c, m]) => `· ${m.name}: ${m.why}`).join('\n') : '\nthere were no missed near-sold-out projects today';
  await tgSay(`🌙 Daily summary (sent automatically at 9 pm)${missTxt}\ntoday ${um} projects could not be minted (website signature, whitelist or simulation failure), recorded in the log only${UPD.on ? `\n\nOfficial group: ${UPD.group}` : ''}`); await cmdStats().catch(() => {}); /* the position summary is sent by holdReport at 1 pm / 1 am instead */
}
async function lockNote(c, wAddr) { /* can the bought coin be transferred right now: simulate a transfer of 1 unit to the dead address, warn if blocked (like the aiagentbsc whitelist lock) (gap the user asked to cover on 10/30 21:44) */
  const toks = [...new Set([c, ...(S.dyn || []).filter((t) => t.only === wAddr).slice(-3).map((t) => t.addr)])];
  for (const t of toks) { const dR = await call(t, '0x313ce567'); if (!(dR && dR.length >= 66)) continue; /* NFTs have no decimals, so a token transfer cannot test them (NFTs used to be falsely reported as non-transferable) */ const b = big(await call(t, '0x70a08231' + wa(wAddr))); if (!(b > 0n)) continue;
    try { await rpc('eth_call', [{ from: wAddr, to: t, data: '0xa9059cbb' + wa('0x000000000000000000000000000000000000dead') + w(1n) }, 'latest']); return ''; }
    catch (e) { return `\n⚠️ This coin cannot be transferred right now (${String(e.message).slice(0, 40)}): trading may have to be opened by the developer first, or it is a honeypot`; } }
  return '';
}
async function tg(text) { if (/^🛒/u.test(text)) { const m = text.match(/\n(0x[a-fA-F0-9]{40})\n/); if (m) { text += await riskNote(m[1].toLowerCase()); const wn = (text.match(/^🛒 \[([^\]]+)\]/u) || [])[1]; const W = WALLETS.find((x) => x.name === wn); if (W) text += await lockNote(m[1].toLowerCase(), W.addr).catch(() => ''); } } log('TG: ' + text.replace(/\n/g, ' | ')); toGroup(text).catch(() => {}); if (!TG || (!CONFIG.notifyAll && !sendable(text))) return; try { await fetch(`https://api.telegram.org/bot${TG.a}/sendMessage`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ chat_id: TG.b, text, disable_web_page_preview: true, disable_notification: !important(text) }), signal: AbortSignal.timeout(10000) }); } catch {} }

const RPCS = Array.isArray(CONFIG.rpcs) && CONFIG.rpcs.length ? CONFIG.rpcs : ['https://bsc-dataseed.bnbchain.org', 'https://bsc-rpc.publicnode.com', 'https://bsc.publicnode.com', 'https://bsc.drpc.org', 'https://1rpc.io/bnb', 'https://binance.llamarpc.com']; // can be replaced with your own nodes under rpcs in config.json (public nodes are often rate limited)
let ri = 0;
async function rpc(method, params) {
  let last; for (let i = 0; i < RPCS.length; i++) { const u = RPCS[(ri + i) % RPCS.length]; try { const r = await (await fetch(u, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }), signal: AbortSignal.timeout(12000) })).json(); if (r.error) throw new Error(r.error.message); ri = (ri + i) % RPCS.length; return r.result; } catch (e) { last = e; } }
  throw last;
}
const w = (n) => BigInt(n).toString(16).padStart(64, '0');
const wa = (a) => a.toLowerCase().replace(/^0x/, '').padStart(64, '0');
const call = (to, data) => rpc('eth_call', [{ to, data }, 'latest']).catch(() => null);
const big = (h) => (h && h !== '0x' ? BigInt(h.slice(0, 66)) : 0n);
const parseOut = (res) => (e, so, se) => { const t = String(so || se || e?.message || ''); let j = null; try { j = JSON.parse(t.slice(t.indexOf('{'))); } catch {} res({ t, j }); };
// one baw per wallet (Binance Agentic Wallet CLI). The 2nd and later wallets use a separate BINANCE_BAW_DIR / BINANCE_INSTANCE_ID (see README)
function makeBaw(wc) {
  if (!wc.bawDir) return (args, timeout = 180000) => new Promise((res) => execFile('baw', [...args, '--json'], { shell: true, windowsHide: true, timeout, maxBuffer: 4 << 20 }, parseOut(res)));
  const env = { ...process.env, BINANCE_BAW_DIR: join(DIR, wc.bawDir), BINANCE_INSTANCE_ID: wc.instanceId || wc.name };
  const bin = join(DIR, wc.bawPkg || 'baw2pkg', 'node_modules', '@binance', 'agentic-wallet', 'dist', 'index.js');
  return (args, timeout = 180000) => new Promise((res) => execFile(process.execPath, [bin, ...args, '--json'], { env, windowsHide: true, timeout, maxBuffer: 4 << 20 }, parseOut(res)));
}
const WALLETS = (CONFIG.wallets || []).map((wc) => ({ name: wc.name, addr: wc.address.toLowerCase(), baw: makeBaw(wc) }));
if (!WALLETS.length) { console.error('no wallets in config.json'); process.exit(1); }
const ME = WALLETS[0].addr;
function keccak256(str) { /* small keccak256 (BigInt version), used to compute the Agent ID number */
  const M = (1n << 64n) - 1n, RC = [], R = [1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14, 27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44], P = [10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4, 15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1];
  let r = 1n; for (let i = 0; i < 24; i++) { let c = 0n; for (let j = 0; j < 7; j++) { r = ((r << 1n) ^ ((r >> 7n) * 0x71n)) & 0xffn; if (r & 2n) c ^= 1n << ((1n << BigInt(j)) - 1n); } RC.push(c); }
  const rot = (x, n) => ((x << BigInt(n)) | (x >> BigInt(64 - n))) & M;
  const b = [...Buffer.from(str, 'utf8')]; b.push(1); while (b.length % 136) b.push(0); b[b.length - 1] |= 0x80;
  const s = Array(25).fill(0n);
  for (let o = 0; o < b.length; o += 136) {
    for (let i = 0; i < 17; i++) { let v = 0n; for (let k = 7; k >= 0; k--) v = (v << 8n) | BigInt(b[o + i * 8 + k]); s[i] ^= v; }
    for (let rd = 0; rd < 24; rd++) {
      const C = [0, 1, 2, 3, 4].map((x) => s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20]);
      for (let x = 0; x < 5; x++) { const d = C[(x + 4) % 5] ^ rot(C[(x + 1) % 5], 1); for (let y = 0; y < 25; y += 5) s[x + y] ^= d; }
      let t = s[1]; for (let i = 0; i < 24; i++) { const j = P[i], u = s[j]; s[j] = rot(t, R[i]); t = u; }
      for (let y = 0; y < 25; y += 5) { const a = s.slice(y, y + 5); for (let x = 0; x < 5; x++) s[y + x] = a[x] ^ (~a[(x + 1) % 5] & M & a[(x + 2) % 5]); }
      s[0] ^= RC[rd];
    }
  }
  let h = ''; for (let i = 0; i < 4; i++) for (let k = 0; k < 8; k++) h += Number((s[i] >> BigInt(8 * k)) & 0xffn).toString(16).padStart(2, '0'); return h;
}

/* each wallet's own Agent ID number = keccak256(name without .ai); some projects need this number when minting (like SYNAPE's mint(agentTokenId)) (gap the user asked to cover on 10/30 21:44) */
for (const W of WALLETS) W.agentId = keccak256(W.name.toLowerCase().replace(/\.ai$/, ''));

let S = { tokens: {}, lastBlock: 0, seenMints: {}, lastLoginAlert: 0 };
try { S = { ...S, ...JSON.parse(readFileSync(STATE, 'utf8')) }; } catch {}
for (const k of Object.keys(S.tokens)) delete S.tokens[k].verified;
const save = () => { try { writeFileSync(STATE + '.tmp', JSON.stringify(S)); renameSync(STATE + '.tmp', STATE); } catch {} }; // fix review: write a temp file and rename it, so a crash cannot corrupt the state file

// ---------- positions ----------
const XQ = ['0x4902c5ebc598265ed2212b559b042de8a5eeec3f', '0x02fca66c1d1afb4e2a7884261eb00f63598a7436', '0x205812cdbed920aff76c6580abd681a46d11efc7', '0xbe9d156892e55e7154bcd3cb0fea677f9d3103e1']; /* user 10/31 19:49: Binance Wuyou's pool is paired with BNCB (a Flap-family quote coin); we used to only look at BNB/USDT/USD1 and missed it */
const dsCache = new Map();
async function dexScreener(addr) { /* user 10/31 19:55: no matter which coin it is paired with or which DEX (V2/V3/other), a pool open must be found — use DexScreener as a safety net (60 s cache) */
  const k = dsCache.get(addr); if (k && Date.now() - k.t < 60e3) return k.v;
  let v = null; try { const j = await (await fetch('https://api.dexscreener.com/latest/dex/tokens/' + addr, { signal: AbortSignal.timeout(10000) })).json(); const ps = (j.pairs || []).filter((p) => p.chainId === 'bsc' && String(p.baseToken?.address || '').toLowerCase() === addr && (p.liquidity?.usd || 0) > 50); if (ps.length) { ps.sort((a, b) => (b.liquidity?.usd || 0) - (a.liquidity?.usd || 0)); const p = ps[0]; v = { pair: String(p.pairAddress).toLowerCase(), dex: p.dexId + (p.labels?.length ? ' ' + p.labels.join('') : ''), quote: p.quoteToken?.symbol || '', px: Number(p.priceUsd) || 0, liq: p.liquidity?.usd || 0 }; } } catch {}
  if (!v) v = await geckoPool(addr); /* debug 11/1: NOV's pool opened on PancakeSwap Infinity and DexScreener never lists it, so the tool assumed there was no pool and could not read the price → fall back to GeckoTerminal (Infinity, Uniswap v4 and V3 are all covered) */
  dsCache.set(addr, { t: Date.now(), v }); return v;
}
async function geckoPool(addr) {
  try {
    const j = await (await fetch(`https://api.geckoterminal.com/api/v2/networks/bsc/tokens/${addr}/pools`, { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(10000) })).json();
    const ps = (j.data || []).map((p) => { const a = p.attributes || {}, base = String(p.relationships?.base_token?.data?.id || '').toLowerCase().endsWith(addr); return { id: String(a.address || '').toLowerCase(), name: a.name || '', dex: String(p.relationships?.dex?.data?.id || 'gecko'), px: Number(base ? a.base_token_price_usd : a.quote_token_price_usd) || 0, liq: Number(a.reserve_in_usd) || 0 }; }).filter((x) => x.px > 0 && x.liq > 50).sort((x, y) => y.liq - x.liq);
    if (!ps.length) return null; const p = ps[0];
    return { pair: /^0x[0-9a-f]{40}$/.test(p.id) ? p.id : 'gt:' + p.id, dex: p.dex, quote: (p.name.split('/')[1] || '').trim().split(' ')[0], px: p.px, liq: p.liq };
  } catch { return null; }
}
async function poolOpen(tk) { /* is there already an open pool (any pair, any DEX): common V2 quote coins + DexScreener (fix review) */
  for (const q of [WBNB, USDT, USD1, ...XQ]) { const pr = await cc(FACTORY, '0xe6a43905' + wa(tk) + wa(q), 60e3); if (pr && BigInt(pr) > 0n) { const rs = await call('0x' + pr.slice(26), '0x0902f1ac'); if (rs && BigInt('0x' + rs.slice(2, 66)) > 0n) return true; } }
  return !!(await dexScreener(tk));
}
async function findPair(t) {
  for (const q of [USDT, WBNB, USD1, ...XQ]) { const r = await call(FACTORY, '0xe6a43905' + wa(t.addr) + wa(q)); const p = r ? '0x' + r.slice(-40) : null; if (!p || /^0x0{40}$/.test(p)) continue; const rs = await call(p, '0x0902f1ac'); if (!rs || BigInt('0x' + rs.slice(2, 66)) === 0n || BigInt('0x' + rs.slice(66, 130)) === 0n) continue; /* a pool that exists but has no liquidity added = not really open yet */ return { pair: p, quote: q }; }
  { const d = await dexScreener(t.addr); if (d) return { pair: d.pair, ds: 1, dex: d.dex, quote: d.quote }; } /* no V2 BNB/USDT/USD1/Flap quote coin → use DexScreener to find any pool */
  return null;
}
async function valueUsd(t, amt) { // how much USDT selling everything would roughly return
  if (amt <= 0n) return 0;
  let best = null; for (const path of [[t.addr, USDT], [t.addr, WBNB, USDT], [t.addr, USD1], ...XQ.flatMap((q) => [[t.addr, q, WBNB, USDT], [t.addr, q, USDT]])]) { const r = await call(ROUTER, '0xd06ca61f' + w(amt) + w(64) + w(path.length) + path.map(wa).join('')); if (r) { const x = Number(BigInt('0x' + r.slice(-64))) / 1e18; if (best == null || x > best) best = x; } } // fix review: take the best price across all pools so a tiny pool cannot mislead us
  if (best != null && best > 0) { const d = await dexScreener(t.addr); if (d?.px && d.liq > 2000) { const dec = await tokDec(t.addr).catch(() => 18); const dv = Math.min((Number(amt) / 10 ** dec) * d.px * 0.97, d.liq * 0.3); if (dv > best * 1.5) best = dv; } } /* fix review: when a tiny V2 pool exists but the real large pool is elsewhere, use the large pool's price so we don't dump on a false crash */
  if (best == null) { const d = await dexScreener(t.addr); if (d?.px) { const dec = await tokDec(t.addr).catch(() => 18); best = Math.min((Number(amt) / 10 ** dec) * d.px * 0.97, d.liq * 0.3); } /* a small pool: estimate at most 30% of the pool liquidity so we don't overestimate */ } /* the router cannot price it (V3 / other DEX / other quote coin) → estimate from the DexScreener price minus 3% as slippage */
  return best;
}
async function flow(t, st, from, to) { // new wallet count, trade count
  try {
    const tr = await rpc('eth_getLogs', [{ address: t.addr, topics: [T_TRANSFER], fromBlock: '0x' + from.toString(16), toBlock: '0x' + to.toString(16) }]);
    st.holders = st.holders || {}; let fresh = 0;
    for (const l of tr) { const toA = '0x' + l.topics[2].slice(26); if (!st.holders[toA]) { st.holders[toA] = 1; fresh++; } }
    let swaps = 0; if (st.pair && /^0x[0-9a-f]{40}$/.test(st.pair)) swaps = (await rpc('eth_getLogs', [{ address: st.pair, topics: [T_SWAP], fromBlock: '0x' + from.toString(16), toBlock: '0x' + to.toString(16) }])).length;
    return { fresh, swaps };
  } catch { return { fresh: 0, swaps: 0 }; }
}
const decCache = new Map();
async function tokDec(a) { if (!decCache.has(a)) { const r = await call(a, '0x313ce567'); const d = r && r.length >= 66 ? Number(BigInt(r.slice(0, 66))) : 18; decCache.set(a, d > 0 && d <= 36 ? d : 18); } return decCache.get(a); }
function trackFromSim(W, sim, sym, costUsd) { /* freshly minted tokens are auto-added to position tracking (copied paid mints and manual top-ups used to be missed, e.g. Binance Wuyou was never tracked) (user 10/31 20:13) */
  try { for (const b of sim?.balanceChanges || []) { if (String(b.owner).toLowerCase() !== W.addr || !(Number(b.change) > 0)) continue; const a = String(b.contractAddress || '').toLowerCase(); if (!/^0x[0-9a-f]{40}$/.test(a) || a.startsWith('0xeeee') || a === USDT || a === WBNB) continue; if (String(b.tokenType || '') !== 'Erc20') continue; /* only track ERC20 tokens, not NFTs */
    S.dyn = S.dyn || []; if (!S.dyn.some((t) => t.addr === a && t.only === W.addr)) { S.dyn.push({ sym: sym || a.slice(0, 8), addr: a, cost: +(costUsd || 0).toFixed(2), only: W.addr, boughtAt: Date.now() }); log(`[${W.name}] ${sym || a} added to position tracking (cost ~${(costUsd || 0).toFixed(2)}U)`); } } } catch {}
}
const SELLING = new Set();
async function sell(t, st, amt, why, W = WALLETS[0]) {
  const sk = W.name + '|' + t.addr; if (SELLING.has(sk)) return false; SELLING.add(sk); INFLIGHT++; try { return await sellInner(t, st, amt, why, W); } finally { INFLIGHT--; SELLING.delete(sk); } /* debug 11/1: at 01:17 the CENSUS sell was sent and the program restarted from an update right then, so the wait-for-fill, 💰 and bookkeeping steps were cut off; a sell also counts as "trade in flight" and a restart has to wait for it to finish */ /* fix review: only one sell per coin at a time (TG commands and auto-sell cannot collide) */
}
async function sellInner(t, st, amt, why, W) {
  { const live = (S.dyn || []).find((x) => x.addr === t.addr && x.only === W.addr); if (live?.hold && !/you asked/.test(why)) { log(`${t.sym} is set to notify-only, not auto-selling (${why})`); return false; } } /* fix review: go by the latest setting so a background sell cannot use a stale list (Binance Wuyou 19:56) */
  const dec = await tokDec(t.addr); const qty = (Math.floor((Number(amt) / 10 ** dec) * 1e4) / 1e4).toFixed(4); // compute the amount from the token decimals (fix review: coins with 6/9 decimals used to round to 0)
  if (!(Number(qty) > 0)) return false;
  const bnb0 = BigInt((await rpc('eth_getBalance', [W.addr, 'latest']).catch(() => '0x0')) || '0x0');
  const r = await W.baw(['market-order', 'swap', '--binanceChainId', '56', '--fromTokenQty', qty, '--fromToken', t.addr, '--toToken', NATIVE_BNB]);
  log(`sell ${t.sym} ${qty}: ${r.t.slice(0, 600)}`);
  if (!r.j?.success) { st.sellFail = (st.sellFail || 0) + 1; st.sellFailAt = Date.now(); const msg = (r.j?.error?.message || r.t).slice(0, 200); log(`[${W.name}] ${t.sym} sell failed, attempt ${st.sellFail}: ${msg.replace(/\n/g, ' ')}`);
    /* user 10/31 00:35: it kept ringing. Coins worth under 3U are not notified at all when they fail to sell and are given up after 6 tries; valuable ones are only notified on the 1st failure */
    const small = (st.curV ?? 99) < 3;
    if (small && st.sellFail >= 6) { st.stage = 9; st.gaveUp = ts(); log(`[${W.name}] ${t.sym} worth only ~${(st.curV || 0).toFixed(2)}U, failed to sell 6 times, giving up`); }
    if (!small && st.sellFail === 1) await tg(`⚠️ [${W.name}] ${t.sym} wanted to sell but failed (${why})\n${msg}\nwill retry automatically without repeating the notification; a 💰 notice is sent when it sells`);
    return false; }
  st.sellFail = 0;
  await sleep(15000);
  const bnb1 = BigInt((await rpc('eth_getBalance', [W.addr, 'latest']).catch(() => '0x0')) || '0x0');
  const got = Number(bnb1 - bnb0) / 1e18, px = await bnbUsd().catch(() => 0), gotU = got * px; // how much actually came back from the sell (the user wants to know how much each trade returns)
  S.trades = S.trades || []; S.trades.push({ sym: t.sym, w: W.name, bnb: +got.toFixed(6), usd: +gotU.toFixed(2), cost: t.cost || 0, at: ts(), why });
  await tg(`💰 [${W.name}] sold ${t.sym} (${why})\nqty ${qty}${got > 0 ? `\nfor ${got.toFixed(5)} BNB (~${gotU.toFixed(2)}U)` : ''}\norder ${r.j.data?.orderId || ''}`);
  return true;
}

async function holdings(head, onlyFast = false) {
  await Promise.all(WALLETS.map((W) => holdW(W, head, onlyFast).catch((e) => log('holdings ' + W.name + ' ' + e.message))));
}
async function holdW(W, head, onlyFast) { // all wallets run in parallel (fast mode: sells are not queued)
  for (const t0 of TOKENS.concat(S.dyn || [])) {
    if (t0.only && t0.only !== W.addr) continue;
    const t = { ...t0, cost: (t0.only || t0.both || W.addr === ME) ? t0.cost : 0 };
    const dupSym = TOKENS.concat(S.dyn || []).filter((x) => (!x.only || x.only === W.addr) && x.sym === t.sym && x.addr !== t.addr).length > 0; /* fix review: different coins with the same symbol (like two 555s, two CIOWs) used to share one record and balances jumped back and forth */
    const key = (W.addr === ME ? t.sym : W.name + ':' + t.sym) + (dupSym ? '#' + t.addr.slice(2, 8) : '');
    const st = (S.tokens[key] = S.tokens[key] || { hist: [], peak: 0, peakAt: 0, sold: 0 });
    if (onlyFast && !(st.openAt && Date.now() - st.openAt < RULE.fastMin * 60e3 && st.stage !== 9 && !st.done)) continue;
    const balR = await call(t.addr, '0x70a08231' + wa(W.addr)); if (balR == null) continue; /* a failed query is not treated as 0 (it used to keep falsely reporting "coin received") */ const bal = big(balR);
    const prevBal = BigInt(st.lastBal || '0'); st.lastBal = bal.toString();
    if (bal > prevBal && st.balInit) { const v0 = await valueUsd(t, bal - prevBal); await tg(`🎁 [${W.name}] received ${t.sym} ${(Number(bal - prevBal) / 1e18).toLocaleString()} coins${v0 != null ? ', worth ~' + v0.toFixed(2) + 'U' : ' (no pool to quote yet)'}\ntracking it, will sell at the peak`); }
    st.balInit = true;
    if (bal === 0n) { st.done = true; continue; }
    if (st.pair && !st.verified && !st.pairDs) { const rs = await call(st.pair, '0x0902f1ac'); if (rs == null) { /* query failed: don't judge yet */ } else if (BigInt('0x' + rs.slice(2, 66)) === 0n) { delete st.pair; st.checked = true; } if (rs != null) st.verified = true; } // clear stale empty pools wrongly judged by older versions
    if (!st.pair) {
      const p = await findPair(t);
      const first = !st.checked; st.checked = true;
      if (p) { st.pair = p.pair; if (p.ds) { st.pairDs = 1; st.pairInfo = `${p.dex}, paired with ${p.quote}`; } st.openAt = st.openAt || Date.now(); st.fromBlock = head - 400; if (!first) await honeypotCheck(W, t, st, bal); if (!first) await tg(`🚀 ${t.sym} pool opened (${st.pairInfo || 'PancakeSwap'})\nContract: ${t.addr}\nPool: ${p.pair}\ntracking the price now, will sell at the peak`); }
      else { if (t0.deployer && t0.boughtAt && Date.now() - t0.boughtAt > 48 * 3600e3) blacklist(t0.deployer, `${t.sym} still no pool 48 hours after minting`); continue; }
    }
    const v = await valueUsd(t, bal); if (v == null) continue; st.curV = v;
    // compute the peak and multiples from the per-coin price (fix review: after selling part the position shrinks and no longer gets misread as a crash)
    const dec = await tokDec(t.addr); const balN = Number(bal) / 10 ** dec || 1e-18; const px = v / balN;
    if (!st.qty0 || balN > st.qty0 * 1.0001) st.qty0 = balN; // original holding amount
    const vFull = px * st.qty0; // what the full original holding would be worth now (used for 3/5/10x)
    const from = Math.max(st.fromBlock || head - 20, head - 2000); const f = await flow(t, st, from, head); st.fromBlock = head + 1;
    const now = Date.now(); st.hist.push({ t: now, v, fresh: f.fresh, swaps: f.swaps }); st.hist = st.hist.filter((h) => now - h.t < 20 * 60e3);
    if (v > st.peak) { st.peak = v; st.peakAt = now; }
    if (px > (st.ppeak || 0)) { st.ppeak = px; st.ppeakAt = now; }
    const sum = (a, b, k) => st.hist.filter((h) => now - h.t >= a * 1000 && now - h.t < b * 1000).reduce((s, h) => s + h[k], 0);
    const fresh5 = sum(0, 300, 'fresh'), swaps5 = sum(0, 300, 'swaps'), swapsPrev5 = sum(300, 600, 'swaps');
    if (st.stage == null) st.stage = st.sold >= 2 ? 9 : st.sold === 1 ? 1 : 0; // convert old state
    if (st.stage >= 9) { st.last = { v, at: ts() }; continue; }
    if (st.sellFail && now - (st.sellFailAt || 0) < Math.min(2, 2 ** (st.sellFail - 1)) * 60e3) { st.last = { v, at: ts() }; continue; } // unsellable: retry after 1, 2, 4… max 15 minutes, don't hammer it
    st.notified = st.notified || [];
    if (t.cost > 0 && !t.hold && !t.custom) for (const x of RULE.notifyX) if (vFull >= t.cost * x && !st.notified.includes(x)) { st.notified.push(x); await tg(`🔥 [${W.name}] ${t.sym} hit ${x}x! (cost ${t.cost}U, position now ~${v.toFixed(2)}U)\nby the rules: ${ruleTxt()}`); }
    if (t.sellNow) { if (!st.sellNowDone) { if (await sell(t, st, bal, 'you asked to sell everything', W)) { st.sellNowDone = true; st.stage = 9; } } continue; }
    if (!(t.cost > 0) || t.hold) { // free, or the user said don't sell yet
      if (!st.basePx) st.basePx = px; // for free coins the first price after the pool opened is the baseline
      for (const x of RULE.notifyX) { const hit = t.hold && t.cost > 0 ? vFull >= t.cost * x : px >= st.basePx * x; /* review 11/1: a hold coin with cost 0 would falsely report 5/10/20x all at once */ if (hit && !st.notified.includes(x)) { st.notified.push(x); await tg(`🔥 [${W.name}] ${t.hold ? '' : 'free coin '}${t.sym} rose to ${x}x ${t.hold ? 'the cost' : 'the pool-open price'}! market value ~${v.toFixed(2)}U\n${t.hold ? 'you said don\'t sell yet, to sell type "sell ' + t.sym + '" in TG' : 'sell all automatically on a 50% drop from the peak; to sell earlier type "sell ' + t.sym + '" in TG'}`); } }
      if (st.openAt && now - st.openAt >= 180e3 && px > (st.fpp || 0)) st.fpp = px; // only start tracking the peak 3 minutes after the pool opens
      const dropNow = !t.hold && st.fpp > 0 && px < st.fpp * (1 - RULE.drop) && v >= 0.3; st.dropHit = dropNow ? (st.dropHit || 0) + 1 : 0; /* review 11/1: require 2 consecutive reads so a jumping price source cannot fake a crash */
      if (dropNow && st.dropHit >= 2) { // free coin drops 50% from the peak → sell all
        if (await sell(t, st, bal, `free coin dropped 50% from the peak, selling all (~${v.toFixed(2)}U)`, W)) { st.stage = 9; continue; }
      }
      if (t.hold && px < st.ppeak * (1 - RULE.drop) && st.dropWarnedPeak !== st.ppeak) { st.dropWarnedPeak = st.ppeak; await tg(`⚠️ [${W.name}] ${t.sym} pulled back 50% from the peak, now ~${v.toFixed(2)}U\nyou said don't sell yet, it will not auto-sell; to sell type "sell ${t.sym}" in TG`); }
      st.last = { v, at: ts() }; continue;
    }
    if (v < 0.3) { st.last = { v, at: ts() }; continue; } // dust under 0.3U is not sold (not worth the fee)
    st.done2 = st.done2 || [];
    if (t.custom === 'fist') { /* user 10/31 20:39: FISTAGENT special rule — sell to recover the cost at 5x, sell 30% at 10x, above 20x only send a TG notice; none of the other auto-sell rules apply */
      const xNow = vFull / t.cost;
      if (xNow < 0.5 && !st.done2.includes('cw')) { st.done2.push('cw'); await tg(`⚠️ [${W.name}] FISTAGENT fell below half the cost (cost ${t.cost}U, now ~${v.toFixed(2)}U)\nyour setup has no "sell on a crash", so it will not auto-sell; to sell type "sell FISTAGENT" in TG`); } /* debug 11/1: special rules with only upside targets had no downside warning at all — NOV dropped 88% without a notice */
      if (xNow >= 5 && !st.done2.includes('f5')) { const part = (bal * BigInt(Math.max(1, Math.min(100, Math.ceil((t.cost / v) * 100))))) / 100n; if (await sell(t, st, part, `FISTAGENT hit 5x, selling to recover the cost ${t.cost}U (position ~${v.toFixed(2)}U)`, W)) st.done2.push('f5'); }
      else if (xNow >= 10 && !st.done2.includes('f10')) { if (await sell(t, st, (bal * 30n) / 100n, `FISTAGENT hit 10x, selling 30% (position ~${v.toFixed(2)}U)`, W)) st.done2.push('f10'); }
      else if (xNow >= 20 && !st.done2.includes('f20')) { st.done2.push('f20'); await tg(`🔥 [${W.name}] FISTAGENT hit ${Math.floor(xNow)}x! (cost ${t.cost}U, position now ~${v.toFixed(2)}U)\nby your setup it does not auto-sell above 20x; to sell type "sell FISTAGENT" or "sell FISTAGENT half" in TG`); }
      st.last = { v, at: ts() }; continue;
    }
    if (t.custom === 'nova') { /* user 10/31 23:18: NOVA sells to recover the cost at 5x, sends a TG notice at 10x, nothing else auto-sells */
      const xNow = vFull / t.cost;
      if (xNow < 0.5 && !st.done2.includes('cw')) { st.done2.push('cw'); await tg(`⚠️ [${W.name}] NOVA fell below half the cost (cost ${t.cost}U, now ~${v.toFixed(2)}U)\nyour setup has no "sell on a crash", so it will not auto-sell; to sell type "sell NOV" in TG`); } /* debug 11/1: special rules with only upside targets had no downside warning at all — NOV dropped 88% without a notice */
      if (xNow >= 5 && !st.done2.includes('n5')) { const part = (bal * BigInt(Math.max(1, Math.min(100, Math.ceil((t.cost / v) * 100))))) / 100n; if (await sell(t, st, part, `NOVA hit 5x, selling to recover the cost ${t.cost}U (position ~${v.toFixed(2)}U)`, W)) st.done2.push('n5'); }
      else if (xNow >= 10 && !st.done2.includes('n10')) { st.done2.push('n10'); await tg(`🔥 [${W.name}] NOVA hit ${Math.floor(xNow)}x! (cost ${t.cost}U, position now ~${v.toFixed(2)}U)\nby your setup it does not auto-sell; to sell type "sell NOV" or "sell NOV half" in TG`); }
      st.last = { v, at: ts() }; continue;
    }
    const backDone = st.done2.includes('back');
    if (backDone && st.moonPend && RULE.moonPct) { st.moon = ((bal * BigInt(RULE.moonPct)) / 100n).toString(); delete st.moonPend; log(`[${W.name}] ${t.sym} keeping ${RULE.moonPct}% as a long hold after break-even (never auto-sell, notify at 50/100x)`); }
    const moon = backDone && st.moon ? BigInt(st.moon) : 0n; const freeAmt = bal > moon ? bal - moon : 0n;
    if (moon > 0n && freeAmt * 50n <= moon) { /* only the long-hold portion is left: never auto-sell, notify at 50/100x */
      for (const x of RULE.moonNotifyX || []) if (vFull >= t.cost * x && !st.done2.includes('m' + x)) { st.done2.push('m' + x); await tg(`🔥 [${W.name}] ${t.sym} long-hold portion hit ${x}x! (cost ${t.cost}U, now ~${v.toFixed(2)}U)\nby the settings it does not auto-sell; to sell type "sell ${t.sym}" or "sell ${t.sym} half" in TG`); }
      st.last = { v, at: ts() }; continue;
    }
    const crash = backDone ? (RULE.crashAfterBack || RULE.crash) : RULE.crash; /* user 11/1 13:54: sell all on a 40% drop before break-even; widened to 60% after break-even */
    const crashNow = st.ppeak > 0 && px < st.ppeak * (1 - crash); st.crashHit = crashNow ? (st.crashHit || 0) + 1 : 0; /* review 11/1: the price source sometimes jumps to a tiny pool and fakes a crash → require 2 consecutive reads below the threshold before selling all */
    if (crashNow && st.crashHit >= 2) {
      if (await sell(t, st, moon > 0n ? freeAmt : bal, `dropped ${Math.round(crash * 100)}% from the peak, ${moon > 0n ? `selling everything outside the long hold (keeping ${RULE.moonPct}%)` : 'selling all'} (~${v.toFixed(2)}U)`, W)) { if (!(moon > 0n)) st.stage = 9; }
    } else if (!backDone && vFull >= t.cost * RULE.backX) {
      const pct = Math.min(100, Math.ceil((t.cost / v) * 100 * 1.03));
      if (await sell(t, st, (bal * BigInt(pct)) / 100n, `hit ${RULE.backX}x, selling ${pct}% to recover the cost ${t.cost}U (position ~${v.toFixed(2)}U)`, W)) { st.done2.push('back'); if (pct < 100) st.moonPend = 1; }
    } else for (const [x, pc] of RULE.steps) {
      if (!st.done2.includes('s' + x) && !(x === 10 && st.done2.includes('h10')) && vFull >= t.cost * x) { if (await sell(t, st, ((moon > 0n ? freeAmt : bal) * BigInt(pc)) / 100n, `hit ${x}x, selling ${pc}%${moon > 0n ? ' (long-hold portion untouched)' : ''} (position ~${v.toFixed(2)}U)`, W)) st.done2.push('s' + x); break; }
    }
    st.last = { v, fresh5, swaps5, swapsPrev5, at: ts() };
  }
}

// ---------- new Agent ID exclusive mints ----------
const CANDS = [['mint()', '0x1249c58b'], ['freeMint()', '0x5b70ea9f'], ['claim()', '0x4e71d92d'], ['mint(1)', '0xa0712d68' + w(1)], ['publicMint()', '0x26092b83'], ['commitFreeMint()', '0x74d04cfc'], ['registerForRewards()', '0x9f92d0dd'], ['freeMint(1)', '0x7c928fe9' + w(1)], ['claim(1)', '0x379607f5' + w(1)], ['mintFree()', '0x8ab53447'], ['claimFree()', '0xf366afc9'], ['agentMint()', '0x1be3587a']];
// ---- keccak256 (used to compute CREATE2 addresses) ----
const KRC = [1n,0x8082n,0x800000000000808an,0x8000000080008000n,0x808bn,0x80000001n,0x8000000080008081n,0x8000000000008009n,0x8an,0x88n,0x80008009n,0x8000000an,0x8000808bn,0x800000000000008bn,0x8000000000008089n,0x8000000000008003n,0x8000000000008002n,0x8000000000000080n,0x800an,0x800000008000000an,0x8000000080008081n,0x8000000000008080n,0x80000001n,0x8000000080008008n];
const KROT = [0,1,62,28,27,36,44,6,55,20,3,10,43,25,39,41,45,15,21,8,18,2,61,56,14];
const M64 = (1n << 64n) - 1n; const rotl = (x, n) => (n === 0 ? x : ((x << BigInt(n)) | (x >> BigInt(64 - n))) & M64);
function keccakHex(hex) {
  const msg = Buffer.from(hex, 'hex'); const rate = 136; const padLen = rate - (msg.length % rate); const p = Buffer.concat([msg, Buffer.alloc(padLen)]); p[msg.length] ^= 0x01; p[p.length - 1] ^= 0x80;
  const A = new Array(25).fill(0n);
  for (let off = 0; off < p.length; off += rate) {
    for (let i = 0; i < rate / 8; i++) A[i] ^= p.readBigUInt64LE(off + i * 8);
    for (let r = 0; r < 24; r++) {
      const C = [0, 1, 2, 3, 4].map((x) => A[x] ^ A[x + 5] ^ A[x + 10] ^ A[x + 15] ^ A[x + 20]);
      for (let x = 0; x < 5; x++) { const D = C[(x + 4) % 5] ^ rotl(C[(x + 1) % 5], 1); for (let y = 0; y < 25; y += 5) A[y + x] ^= D; }
      const B = new Array(25);
      for (let x = 0; x < 5; x++) for (let y = 0; y < 5; y++) B[y + 5 * ((2 * x + 3 * y) % 5)] = rotl(A[x + 5 * y], KROT[x + 5 * y]);
      for (let x = 0; x < 5; x++) for (let y = 0; y < 5; y++) A[x + 5 * y] = B[x + 5 * y] ^ (~B[((x + 1) % 5) + 5 * y] & M64 & B[((x + 2) % 5) + 5 * y]);
      A[0] ^= KRC[r];
    }
  }
  const out = Buffer.alloc(32); for (let i = 0; i < 4; i++) out.writeBigUInt64LE(A[i], i * 8); return out.toString('hex');
}
const DEPLOYERS = ['0x4e59b44847b379578588920ca78fbf26c0b4956c', '0xba5ed099633d3b313e4d5f7bdc1305d3c28ba5ed', '0x0000000000ffe8b47b3e2130213b802212439497', '0x914d7fec6aac8cd542e72bca78b30650d45643d7'];

// ---------- smart wallets: Agent ID wallets that were first into previous winning projects (JAGENT/SWARM/BIBIAI/BNBS…); as soon as they act, check early ----------
const WINNERS = ['0x8e6442663a4f8c50b6132c68d42a30e7dfe4ae15', '0x04fd77c2f97d044b3c719282fd0a0568f4254afa', '0x415aac3e46264c046d103c994a1da71f05c547f9', '0x171f264f938d55d968afc5d6b133c4157a61f05f'];
let SMART = new Set(S.smart || []);
async function buildSmart() {
  const head = parseInt(await rpc('eth_blockNumber', []), 16); const cnt = {};
  for (const a of WINNERS) {
    try {
      const has = async (b) => (((await rpc('eth_getCode', [a, '0x' + b.toString(16)]).catch(() => '0x')) || '0x').length > 2);
      let lo = head - 400000, hi = head; if (await has(lo)) continue;
      while (hi - lo > 1) { const m = (lo + hi) >> 1; if (await has(m)) hi = m; else lo = m; }
      const tos = [];
      for (let f = hi; f < Math.min(head, hi + 60000) && tos.length < 300; f += 3000) {
        const logs = await rpc('eth_getLogs', [{ address: a, fromBlock: '0x' + f.toString(16), toBlock: '0x' + (f + 2999).toString(16), topics: [T_TRANSFER, '0x' + '0'.repeat(64)] }]).catch(() => []);
        for (const l of logs || []) { const t = '0x' + l.topics[2].slice(26); if (!tos.includes(t)) tos.push(t); if (tos.length >= 300) break; }
      }
      for (const t of tos) cnt[t] = (cnt[t] || 0) + 1;
    } catch (e) { log('smart ' + a + ' ' + e.message); }
  }
  for (const W of WALLETS) delete cnt[W.addr];
  const list = Object.entries(cnt).filter(([, c]) => c >= 2).map(([t]) => t);
  if (list.length) { SMART = new Set(list); S.smart = list; S.smartAt = Date.now(); log(`smart wallet list updated: ${list.length}`); }
}

// ---------- safety net: one contract called by many wallets within a short time (like Nai Bao Long kicking off) → immediately check whether it is Agent ID exclusive ----------
const HOT = new Map(), hotChecked = new Map();
function countHot(txs) {
  const now = Date.now(); if (!SMART.size && S.smart?.length) SMART = new Set(S.smart);
  for (const t of txs) {
    const recv = (t.input || '0x') === '0x' && BigInt(t.value || '0x0') > 0n; // sending BNB directly mints (like BNBS)
    if (!t.to || (t.input || '').length > 2 + 8 + 64 * 4 || BigInt(t.value || '0x0') > 2n * 10n ** 17n) continue; // mints often cost BNB (QUOGENT 0.0005, Denglongzhen 0.0125–0.1), every call paying ≤0.2 BNB counts
    if (/^0x(a9059cbb|095ea7b3|23b872dd|d0e30db0|2e1a7d4d|39509351|a457c2d7)/.test(t.input || '')) continue; // transfers, approvals and deposits are not mints // only look at short calls paying ≤0.001 BNB (what a mint looks like)
    const k = t.to.toLowerCase(); let h = HOT.get(k); if (!h) { h = { from: new Set(), at: now, sel: new Map() }; HOT.set(k, h); }
    if (now - h.at > 180e3) { h.from.clear(); h.at = now; h.sel.clear(); h.smart = new Set(); h.vals = new Map(); }
    if (SMART.has(t.from.toLowerCase())) (h.smart = h.smart || new Set()).add(t.from.toLowerCase());
    h.from.add(t.from.toLowerCase()); const sl = (t.input || '').slice(0, 10); h.sel.set(sl, (h.sel.get(sl) || 0) + 1); if (recv) { h.vals = h.vals || new Map(); const vv = BigInt(t.value).toString(); h.vals.set(vv, (h.vals.get(vv) || 0) + 1); }
  }
  if (HOT.size > 20000) for (const [k, h] of HOT) if (now - h.at > 180e3) HOT.delete(k);
}
// ---------- third safety net: watch "mint events" directly (no matter how the contract is deployed or how much it pays) ----------
// every Transfer(from=0x0) in every block: the same token/NFT minted to 15+ different wallets within 3 minutes → find the contract everyone calls → decide whether it is Agent ID exclusive → mint by the rules
const MINTS = new Map(), mintChecked = new Map();
const ZERO32 = '0x' + '0'.repeat(64);
async function mintLogs(from, to) {
  // 1) drpc supports getLogs without an address (cheapest on traffic)
  try { const r = await (await fetch('https://bsc.drpc.org', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_getLogs', params: [{ fromBlock: '0x' + from.toString(16), toBlock: '0x' + to.toString(16), topics: [T_TRANSFER, ZERO32] }] }), signal: AbortSignal.timeout(10000) })).json(); if (Array.isArray(r.result)) return r.result; } catch {}
  // 2) otherwise: fetch whole block receipts and filter them ourselves
  const out = [], nodes = ['https://bsc-dataseed.bnbchain.org', 'https://bsc-dataseed1.defibit.io', 'https://bsc-dataseed1.ninicoin.io', 'https://bsc-rpc.publicnode.com'];
  await Promise.all(Array.from({ length: to - from + 1 }, (_, i) => from + i).map(async (b, i) => {
    for (let k = 0; k < nodes.length; k++) { try { const r = await (await fetch(nodes[(i + k) % nodes.length], { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_getBlockReceipts', params: ['0x' + b.toString(16)] }), signal: AbortSignal.timeout(12000) })).json(); if (Array.isArray(r.result)) { for (const rc of r.result) for (const l of rc.logs || []) if (l.topics?.[0] === T_TRANSFER && l.topics[1] === ZERO32 && l.topics[2]) out.push(l); return; } } catch {} }
  }));
  return out;
}
async function fullCode(c, code) { /* fix review (why FAGENT was missed): always follow a proxy contract to the real code behind it — EIP-1967, Beacon slot, Beacon/implementation addresses hard-coded in the code, minimal proxy (EIP-1167) */
  code = String(code || '').toLowerCase(); if (code.length < 4 || code.length >= 4000 || code.includes(REGISTRY)) return code;
  const add = async (a) => { const x = ((await rpc('eth_getCode', [a, 'latest']).catch(() => '')) || '').toLowerCase(); code += x; return x; };
  try { const impl = await rpc('eth_getStorageAt', [c, '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc', 'latest']); if (impl && BigInt(impl) !== 0n) await add('0x' + impl.slice(-40)); } catch {}
  try { const bc = await rpc('eth_getStorageAt', [c, '0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50', 'latest']); if (bc && BigInt(bc) !== 0n) { const r = await call('0x' + bc.slice(-40), '0x5c60da1b'); if (r && BigInt(r) !== 0n) await add('0x' + r.slice(-40)); } } catch {}
  const emb0 = []; { const h = code.replace(/^0x/, '').slice(0, 1200); const m1167 = h.match(/^363d3d373d3d3d363d73([0-9a-f]{40})5af4/); if (m1167) emb0.push('0x' + m1167[1]); for (let i = 0; i + 42 <= h.length; i += 2) { const op = h.slice(i, i + 2); if (op === '73') { emb0.push('0x' + h.slice(i + 2, i + 42)); i += 40; } else if (op === '7f') { const wv = h.slice(i + 2, i + 66); if (/^0{24}/.test(wv)) emb0.push('0x' + wv.slice(24)); i += 64; } else { const n = parseInt(op, 16); if (n >= 0x60 && n <= 0x7f) i += (n - 0x5f) * 2; } } } /* debug 11/1: string matching used to slice at the wrong offsets (in the minimal proxy 363d3d37… the "37 3d" was read as opcode 73), so bibiai's implementation was never found → falsely judged as having no refund mechanism; now it is parsed byte by byte */
  const emb = [...new Set(emb0)].filter((a) => !/^0x0{40}$/.test(a) && a.length === 42).slice(0, 3);
  for (const a of emb) { if (code.includes(REGISTRY)) break; const r = await call(a, '0x5c60da1b'); if (r && r.length >= 66 && BigInt(r.slice(0, 66)) !== 0n) await add('0x' + r.slice(-40)); else await add(a); }
  return code;
}
async function mintScan(head) {
  if (!S.mintBlock || head - S.mintBlock > 300) S.mintBlock = head - 5;
  const to = Math.min(head, S.mintBlock + 60); if (to <= S.mintBlock) return;
  const logs = await mintLogs(S.mintBlock + 1, to); S.mintBlock = to;
  const now = Date.now();
  for (const l of logs) {
    const a = l.address.toLowerCase(); let m = MINTS.get(a); if (!m) { m = { at: now, to: new Set(), txs: [] }; MINTS.set(a, m); }
    if (now - m.at > 1800e3) { m.at = now; m.to.clear(); m.txs = []; } // accumulate over 30 minutes (slow-burn projects get caught too)
    m.to.add('0x' + l.topics[2].slice(26)); if (m.txs.length < 8) m.txs.push(l.transactionHash);
  }
  if (MINTS.size > 30000) for (const [k, m] of MINTS) if (now - m.at > 1800e3) MINTS.delete(k);
  for (const [a, m] of MINTS) {
    if (m.to.size < 6 || (mintChecked.has(a) && m.to.size < mintChecked.get(a) * 2) || S.seenMints[a]) continue; // seen: check again only once the count has doubled // check once 6 different wallets have minted (QUOGENT only had a dozen wallets in its first 15 minutes)
    mintChecked.set(a, m.to.size); if (mintChecked.size > 20000) mintChecked.delete(mintChecked.keys().next().value);
    await checkMinted(a, m).catch((e) => log('mintScan ' + a + ' ' + e.message));
  }
}
const CLAIM = new Set();
async function checkMinted(tok, m) {
  // find the contract people actually call (the mint contract may differ from the token contract)
  const txs = (await Promise.all(m.txs.slice(0, 6).map((h) => rpc('eth_getTransactionByHash', [h]).catch(() => null)))).filter(Boolean);
  const cnt = {}; for (const t of txs) if (t.to) cnt[t.to.toLowerCase()] = (cnt[t.to.toLowerCase()] || 0) + 1;
  const target = Object.entries(cnt).sort((x, y) => y[1] - x[1])[0]?.[0]; if (!target) return;
  if (S.seenMints[target] || S.seenMints[tok] || CLAIM.has(target)) return; CLAIM.add(target); // fix review: only one flow handles a contract so it cannot be minted twice
  // is it Agent ID exclusive: the code contains the registry contract, or 60%+ of the minting wallets hold a .ai identity
  let code = ((await rpc('eth_getCode', [target, 'latest']).catch(() => '')) || '').toLowerCase();
  code = await fullCode(target, code);
  const tcode = tok === target ? code : ((await rpc('eth_getCode', [tok, 'latest']).catch(() => '')) || '').toLowerCase();
  let gated = code.includes(REGISTRY) || tcode.includes(REGISTRY), share = 0;
  if (!gated) {
    // ones without an Agent ID check: only look at contracts deployed within the last two days (excluding LPs and old tokens that mint every day)
    const head = parseInt(await rpc('eth_blockNumber', []), 16);
    const old = (await rpc('eth_getCode', [tok, '0x' + (head - 400000).toString(16)]).catch(() => '0x')) || '0x';
    if (old.length > 2) return;
    const sample = [...m.to].slice(0, 12); let n = 0;
    for (const x of sample) { const r = await call('0x' + REGISTRY, '0x70a08231' + wa(x)); if (r && BigInt(r.slice(0, 66)) > 0n) n++; }
    share = n / sample.length; if (n < 4 || share < 0.6) return;
    gated = true;
  }
  if (INFRA.has(target) || INFRA.has(tok)) return; // the registry contract itself is not a project
  S.seenMints[target] = ts(); S.seenMints[tok] = ts();
  const name = tokStr(await call(tok, '0x06fdde03')) || tokStr(await call(target, '0x06fdde03'));
  // try the common free functions first; otherwise "replay someone else's call data" (only if it carries no foreign address)
  let minted = '';
  const sampleTx = txs.find((t) => t.to && t.to.toLowerCase() === target);
  for (const W of WALLETS) {
    let ff = await findFree(W.addr, target, code);
    if (!ff && sampleTx && sampleTx.input.length >= 10 && !badSel(sampleTx.input) && !sampleTx.input.toLowerCase().includes(sampleTx.from.slice(2).toLowerCase())) {
      for (const v of NEAR_FREE) { try { await rpc('eth_call', [{ from: W.addr, to: target, data: sampleTx.input, value: '0x' + v.toString(16) }, 'latest']); const nm = 'replay' + sampleTx.input.slice(0, 10) + '@' + target.slice(2, 10); if (!CANDS.some((c) => c[0] === nm)) CANDS.push([nm, sampleTx.input]); ff = { n: nm, v }; break; } catch {} }
    }
    if (ff) minted += `[${W.name}] ` + queueMint(target, ff.n, W, ff.v) + '\n';
  }
  if (!minted) {
    S.paid = S.paid || {};
    const val = sampleTx ? BigInt(sampleTx.value || '0x0') : 0n;
    const noAddr = sampleTx && !sampleTx.input.toLowerCase().includes(sampleTx.from.slice(2).toLowerCase());
    S.paid[target] = { at: Date.now(), name, done: {}, wallets: m.to.size, ...((sampleTx?.input || '0x') === '0x' && val > 0n ? { recvValue: val.toString(), recvSeen: m.to.size } : {}), ...(noAddr && sampleTx.input !== '0x' && !badSel(sampleTx.input) ? { replay: { data: sampleTx.input, value: val.toString() } } : {}) };
  }
  await tg(`🔥 Found an Agent ID project being minted${name ? ': ' + name : ''} (minted to ${m.to.size} wallets within 3 minutes${share ? `, ${Math.round(share * 100)}% hold a .ai identity` : ''})\n${target}\n${minted || '⚪ not free → added to tracking: auto-mint once progress and the Agent ID wallet count both pass the threshold and every wallet stays ≤20U'}`);
}

async function hotScan() {
  for (const [c, h] of HOT) {
    if (INFRA.has(c) || CLAIM.has(c)) continue;
    if ((h.from.size < 12 && (h.smart?.size || 0) < 2) || S.seenMints[c] || hotChecked.has(c)) continue;
    CLAIM.add(c); hotChecked.set(c, Date.now()); if (hotChecked.size > 5000) hotChecked.delete(hotChecked.keys().next().value);
    let code = ((await rpc('eth_getCode', [c, 'latest']).catch(() => '')) || '').toLowerCase();
    code = await fullCode(c, code);
    let viaAgents = 0;
    if (!code.includes(REGISTRY)) {
      // the code has no Agent ID registry contract (it may verify with a backend signature, like Denglongzhen, Silicon Dreamers) → instead check whether most wallets hitting it hold an Agent ID
      if (code.length < 4) continue;
      const head = parseInt(await rpc('eth_blockNumber', []), 16);
      const old = (await rpc('eth_getCode', [c, '0x' + (head - 400000).toString(16)]).catch(() => '0x')) || '0x';
      if (old.length > 2) continue; // a contract that already existed two days ago is not a new project
      const sample = [...h.from].slice(0, 16); let n = 0;
      for (const a of sample) { const r = await call('0x' + REGISTRY, '0x70a08231' + wa(a)); if (r && BigInt(r.slice(0, 66)) > 0n) n++; }
      if (n < 8 || n / sample.length < 0.6) continue;
      viaAgents = n / sample.length;
    }
    S.seenMints[c] = ts();
    const name = tokStr(await call(c, '0x06fdde03'));
    // also add the hot-called function to the candidates (a mint function name that is not in CANDS)
    const topSel = [...h.sel.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
    let minted = '';
    for (const W of WALLETS) {
      let ff = await findFree(W.addr, c, code);
      if (!ff && topSel && topSel.length === 10 && !badSel(topSel)) for (const v of NEAR_FREE) { try { await rpc('eth_call', [{ from: W.addr, to: c, data: topSel, value: '0x' + v.toString(16) }, 'latest']); { const nm = 'hotfn' + topSel + '@' + c.slice(2, 10); if (!CANDS.some((x) => x[0] === nm)) CANDS.push([nm, topSel]); ff = { n: nm, v }; } break; } catch {} }
      if (ff) minted += `[${W.name}] ` + queueMint(c, ff.n, W, ff.v) + '\n';
    }
    if (!minted) {
      S.paid = S.paid || {};
      // mints on a plain BNB transfer: use the amount most people send as the "price per unit" (at least 10 identical sends to count)
      const top = [...(h.vals || new Map()).entries()].sort((a, b) => b[1] - a[1])[0];
      S.paid[c] = { at: Date.now(), name, done: {}, wallets: h.from.size, ...(top && top[1] >= 2 ? { recvValue: top[0], recvSeen: top[1] } : {}) };
    }
    await tg(`🔥 Found an Agent ID project being rush-minted${name ? ': ' + name : ''}${viaAgents ? ` (the code has no Agent ID check, but ${Math.round(viaAgents * 100)}% of the wallets hitting it hold a .ai identity, so it may be backend signature verification)` : ''} (${h.from.size} wallets within 3 minutes${h.smart?.size ? ', ' + h.smart.size + ' of them smart wallets' : ''})\n${c}\n${minted || '⚪ not free → added to tracking: auto-mint once progress and the Agent ID wallet count both pass the threshold and every wallet stays ≤20U'}\nhttps://bscscan.com/address/${c}`);
  }
}

const BG = []; let bgRun = 0; // background job queue (max 3 at a time)
function bgJob(fn) { BG.push(fn); pumpBG(); }
function pumpBG() { while (bgRun < 3 && BG.length) { const f = BG.shift(); bgRun++; f().catch((e) => log('bg ' + e.message)).finally(() => { bgRun--; pumpBG(); }); } }
async function scanNew(head) {
  if (!S.lastBlock) S.lastBlock = head - 5;
  if (head - S.lastBlock > 1500) { log(`scan lagging too far behind (${head - S.lastBlock} blocks), jumping to the latest`); S.lastBlock = head - 200; }
  // BSC now has ~0.45 s blocks (~130 per minute); scanning only 40 blocks at a time fell further and further behind (once 2.5 hours) → fetch in parallel across several nodes
  const to = Math.min(head, S.lastBlock + 400);
  const nums = []; for (let b = S.lastBlock + 1; b <= to; b++) nums.push(b);
  const blocks = new Map(); let idx = 0;
  const fetchBlk = async (b) => { for (let k = 0; k < RPCS.length; k++) { const u = RPCS[(b + k) % RPCS.length]; try { const r = await (await fetch(u, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_getBlockByNumber', params: ['0x' + b.toString(16), true] }), signal: AbortSignal.timeout(15000) })).json(); if (r.result) return r.result; } catch {} } return null; };
  await Promise.all(Array.from({ length: 24 }, async () => { while (idx < nums.length) { const b = nums[idx++]; const blk = await fetchBlk(b); if (blk) { countHot(blk.transactions); } if (blk) blocks.set(b, blk.transactions.filter((t) => !t.to || ((t.input || '').length > 2000 && ((t.input || '').toLowerCase().includes(REGISTRY) || DEPLOYERS.includes(t.to.toLowerCase()))))); else blocks.set(b, null); } }));
  let done = S.lastBlock;
  for (const b of nums) { if (blocks.get(b) == null) break; done = b; }
  if (done === S.lastBlock && nums.length) { log('scan failed to fetch blocks ' + nums[0]); return; }
  S.scanLag = head - done;
  for (let b = S.lastBlock + 1; b <= done; b++) {
    for (const tx of blocks.get(b) || []) {
      // new contracts are born three ways: ① a direct deploy ② through a CREATE2 deployer (Denglongzhen does this) ③ created by another factory contract
      const inp = (tx.input || '').toLowerCase();
      if (tx.to && !(inp.length > 2000 && (inp.includes(REGISTRY) || DEPLOYERS.includes(tx.to.toLowerCase())))) continue;
      let cs = [];
      try {
        if (!tx.to) { const rc = await rpc('eth_getTransactionReceipt', [tx.hash]); if (rc?.contractAddress) cs = [rc.contractAddress.toLowerCase()]; }
        else if (tx.to.toLowerCase() === '0x4e59b44847b379578588920ca78fbf26c0b4956c') { const salt = inp.slice(2, 66), init = inp.slice(66); cs = ['0x' + keccakHex('ff' + tx.to.slice(2).toLowerCase() + salt + keccakHex(init)).slice(24)]; }
        else { const rc = await rpc('eth_getTransactionReceipt', [tx.hash]); if (rc?.status !== '0x1') continue; const as = [...new Set((rc.logs || []).map((l) => l.address.toLowerCase()))]; for (const a of as) { const before = await rpc('eth_getCode', [a, '0x' + (b - 1).toString(16)]).catch(() => '0x'); if (!before || before === '0x') cs.push(a); } }
      } catch { continue; }
      for (const c of cs) try {
        if (S.seenMints[c]) continue;
        let code = (await rpc('eth_getCode', [c, 'latest'])) || '';
        code = await fullCode(c, code); /* follow proxy contracts (however written) to the real code behind them */
        if (!code.toLowerCase().includes(REGISTRY) && !code.includes('9f92d0dd')) continue;
        S.seenMints[c] = ts(); noteDeploy(code); S.deployer = S.deployer || {}; S.deployer[c] = (tx.from || '').toLowerCase(); S.depFrom = S.depFrom || {}; S.depFrom[c] = S.deployer[c]; noteDev(S.deployer[c], c);
        bgJob(async () => {
        const ok = [];
        { const ff = await findFree(ME, c, code); if (ff) ok.push(ff.n + (ff.v > 0n ? ` (pays ${Number(ff.v) / 1e18} BNB fee)` : '')); }
        const str = (r) => { try { const h = r.slice(2); const len = Number(BigInt('0x' + h.slice(64, 128))); return Buffer.from(h.slice(128, 128 + len * 2), 'hex').toString('utf8'); } catch { return ''; } };
        let name = str(await call(c, '0x06fdde03'));
        if (!name) for (const sel of ['0xfc0c546a']) { const r = await call(c, sel); if (r && r.length >= 66 && BigInt(r.slice(0, 66)) > 0n) { const ta = '0x' + r.slice(26, 66); const n2 = str(await call(ta, '0x06fdde03')), s2 = str(await call(ta, '0x95d89b41')); if (n2) { name = `${n2}${s2 ? ' ($' + s2 + ')' : ''}`; break; } } }
        const lc = code.toLowerCase();
        const pay = lc.includes('55d398326f99059ff775485246999027b3197955') ? 'requires USDT' : lc.includes('8ac76a51cc950d9822d68b83fe1ad97b32cd580d') ? 'requires USDC' : 'probably requires BNB';
        const factoryLike = ['27cca59f', '7d38bb0d', '1737a4bd'].some((x) => code.includes(x)) || /(6c61756e6368|666163746f7279)/.test(code.toLowerCase());
        if (factoryLike) await tg(`🏗 Found a suspected new Agent ID launchpad / factory contract${name ? ': ' + name : ''}\n${c}\nIt may be a platform like ToshX or Flap Agent, check it on BscScan yourself`);
        let minted = '';
        for (const W of WALLETS) { const ff = await findFree(W.addr, c, code); const f = ff?.n; if (f) minted += `[${W.name}] ` + queueMint(c, f, W, ff.v) + '\n'; }
        if (!ok.length) { S.paid = S.paid || {}; S.paid[c] = { at: Date.now(), name, done: {} }; }
        await tg(`🆕 Found a new Agent ID exclusive contract${name ? ': ' + name : ''}\n${c}\n${ok.length ? '✅ simulation says it mints for free: ' + ok.join(', ') + '\n' + minted : `⚪ not free right now (${pay}, or not open yet) → added to tracking:\n· free → auto-mint once progress and the Agent ID wallet count both pass the threshold\n· any fee → treated as paid: the contract must also have a refund mechanism (or the progress is unreadable while many wallets mint) and every wallet stays ≤20U → auto-mint`}\nhttps://bscscan.com/address/${c}`);
        }); // handle the new contract in the background (simulate, mint, notify) so the scan does not wait
      } catch {}
    }
  }
  
  if (!globalThis._hsBusy) { globalThis._hsBusy = true; hotScan().catch((e) => log('hotScan ' + e.message)).finally(() => { globalThis._hsBusy = false; }); }
  if (!globalThis._msBusy) { globalThis._msBusy = true; mintScan(done).catch((e) => log('mintScan ' + e.message)).finally(() => { globalThis._msBusy = false; }); }
  { /* proxy contract scan: switch nodes when rate limited; if all are limited pause 30 s, then catch up on the unscanned blocks without skipping */
    S.proxyFrom = S.proxyFrom || S.lastBlock + 1; if (done - S.proxyFrom > 3000) S.proxyFrom = done - 3000;
    if (Date.now() > (globalThis._proxyPause || 0)) { const to = Math.min(done, S.proxyFrom + 500); try { await proxyScan(S.proxyFrom, to); S.proxyFrom = to + 1; globalThis._proxyFails = 0; } catch (e) { globalThis._proxyFails = (globalThis._proxyFails || 0) + 1; globalThis._proxyPause = Date.now() + 30e3; if (globalThis._proxyFails === 1 || globalThis._proxyFails % 20 === 0) log(`proxyScan all nodes rate limited (#${globalThis._proxyFails}), pausing 30 s then catching up: ${e.message.slice(0, 80)}`); } }
  }
  S.lastBlock = done;
}

// ---------- proxy contracts created by factory contracts (like AIDROP vaults): caught via "proxy upgrade" events ----------
const T_BEACON_UP = '0x1cf3b03a6cf19fa2baba4df148e9dcabedea7f8a5c07840e207e5c089be95d3e'; // BeaconUpgraded(address)
const T_UPGRADED = '0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b';  // Upgraded(address)
const implCache = new Map();
const VAULT_FACTORIES = ['0x90497450f2a706f1951b5bdda52b4e5d16f34c06']; // Flap vault factory (it created AIDROP)
const LOG_NODES = ['https://bsc-rpc.publicnode.com', 'https://bsc.blockpi.network/v1/rpc/public', 'https://bsc.drpc.org', 'https://1rpc.io/bnb', 'https://binance.llamarpc.com', 'https://bsc.publicnode.com'];
let logNi = 0; const logBad = new Map();
async function logsRpc(params) { /* multi-node rotation for log queries: cool a node for 2 minutes when it is rate limited and move to the next */
  let last; for (let i = 0; i < LOG_NODES.length; i++) { const u = LOG_NODES[(logNi + i) % LOG_NODES.length]; if ((logBad.get(u) || 0) > Date.now()) continue;
    try { const r = await (await fetch(u, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_getLogs', params }), signal: AbortSignal.timeout(12000) })).json(); if (r.error) throw new Error(r.error.message); if (!Array.isArray(r.result)) throw new Error('bad result'); logNi = (logNi + i) % LOG_NODES.length; return r.result; }
    catch (e) { last = e; logBad.set(u, Date.now() + 120e3); } }
  throw last || new Error('all nodes cooling down');
}
async function proxyScan(from, to) {
  if (to < from) return;
  // public nodes do not allow getLogs without an address, so watch the Flap vault factory: every new vault fires an event, then find the new proxy contract in that transaction's receipt
  const fl = await logsRpc([{ address: VAULT_FACTORIES, fromBlock: '0x' + from.toString(16), toBlock: '0x' + to.toString(16) }]);
  const txs = [...new Set((fl || []).map((l) => l.transactionHash))];
  const logs = [];
  for (const h of txs) { const rc = await rpc('eth_getTransactionReceipt', [h]).catch(() => null); for (const l of rc?.logs || []) if (l.topics[0] === T_BEACON_UP || l.topics[0] === T_UPGRADED) logs.push(l); }
  for (const l of logs) {
    const c = l.address.toLowerCase(); if (S.seenMints[c]) continue;
    let impl = '0x' + l.topics[1].slice(26);
    if (l.topics[0] === T_BEACON_UP) { const r = await call(impl, '0x5c60da1b'); if (!r) continue; impl = '0x' + r.slice(-40); }
    if (!implCache.has(impl)) { const code = (await rpc('eth_getCode', [impl, 'latest']).catch(() => '')) || ''; implCache.set(impl, code); if (implCache.size > 500) implCache.delete(implCache.keys().next().value); }
    const code = implCache.get(impl);
    if (!code.toLowerCase().includes(REGISTRY) && !code.includes('9f92d0dd')) continue;
    S.seenMints[c] = ts();
    let minted = '';
    for (const W of WALLETS) { const ff = await findFree(W.addr, c, code); const f = ff?.n; if (f) minted += `[${W.name}] ` + queueMint(c, f, W, ff.v) + '\n'; }
    await tg(`🧩 Found a factory-created Agent ID contract (a proxy contract, like the AIDROP kind)\n${c}\n${minted || '⚪ simulation cannot join for free right now (it may be paid or not open yet)'}`);
  }
}

// ---------- auto-mint (enabled at the user's request): only mints that cost 0, need no approvals and deduct none of the user's assets ----------
// "practically free": some projects charge a small anti-sybil fee (like Nai Bao Long's 0.00022 BNB), accept at most 0.001 BNB
const NEAR_FREE = [0n, 10n ** 14n, 22n * 10n ** 13n, 3n * 10n ** 14n, 5n * 10n ** 14n, 10n ** 15n];
async function findFree(from, c, code) {
  for (const [n, d] of CANDS) { if (n.includes('@') && !n.endsWith('@' + c.slice(2, 10))) continue; if (n.includes('#') && !n.includes('#' + (WALLETS.find((x) => x.addr === from)?.name || '?') + '@')) continue; if (d.length < 10 || badSel(d) || !code.includes(d.slice(2, 10))) continue; /* an empty call (a plain 0 BNB transfer) is not a mint */ const dd = /replay|hotfn/.test(n) ? freshDeadline(d) : d; for (const v of NEAR_FREE) { try { await rpc('eth_call', [{ from, to: c, data: dd, value: '0x' + v.toString(16) }, 'latest']); return { n, v }; } catch {} } }
  const W0 = WALLETS.find((x) => x.addr === from); /* mints that need our own Agent ID number: mint(number), claim(number) */
  if (W0?.agentId) for (const sel of ['a0712d68', '379607f5', '6a627842']) { if (!code.includes(sel)) continue; const d = '0x' + sel + W0.agentId, n = `agentId${sel}#${W0.name}@${c.slice(2, 10)}`; for (const v of NEAR_FREE) { try { await rpc('eth_call', [{ from, to: c, data: d, value: '0x' + v.toString(16) }, 'latest']); if (!CANDS.some((x) => x[0] === n)) CANDS.push([n, d]); return { n, v }; } catch {} } }
  return null;
}
// mint queue: one background queue per wallet (transactions must be sent one at a time), so the scan is never blocked
const MQ = new Map();

const BAD_SEL = new Set(['0xa9059cbb', '0x095ea7b3', '0x23b872dd', '0x42842e0e', '0xb88d4fde', '0xa22cb465', '0x39509351', '0xd505accf', '0xf242432a', '0x2eb2c2d6']);
const badSel = (d) => BAD_SEL.has(String(d || '').slice(0, 10).toLowerCase());
const freeNoted = new Map(); // free mint notice: for the same project wait until both wallets are done, then merge into one message
function noteFree(c, W, name = '') { const first = !freeNoted.has(c); if (first) freeNoted.set(c, new Set()); freeNoted.get(c).add(W.name); if (first) setTimeout(async () => { const ok = freeNoted.get(c) || new Set(); freeNoted.delete(c); const nm = name || tokStr(await call(c, '0x06fdde03')) || c.slice(0, 10); await tg(`🆓 Minted a free project: ${nm} (${WALLETS.map((x) => x.name + (ok.has(x.name) ? ' ✅' : ' ❌')).join(', ')})\n${c}`).catch(() => {}); }, 150e3); }
const MQK = new Set(); /* fix review: only one mint job queued per contract + wallet so it cannot be minted twice */
function queueMint(c, fname, W, v) {
  const qk = c + '|' + W.name; if (MQK.has(qk)) return '⏳ already queued'; if (S.paid?.[c]?.done?.[W.name] && S.paid[c].done[W.name] !== 'pool-open') return '✅ this wallet minted already';
  MQK.add(qk);
  const prev = MQ.get(W.name) || Promise.resolve();
  const job = prev.then(() => autoMintMax(c, fname, W, v)).finally(() => MQK.delete(qk)).then(async (m) => { if (m.startsWith('🤖') || m.includes('result unknown')) { S.paid = S.paid || {}; if (!S.paid[c]) S.paid[c] = { at: Date.now(), name: '', done: {} }; S.paid[c].done[W.name] = m.startsWith('🤖') ? 'free' : 'unknown'; } else if (!m.startsWith('⏸') && !m.startsWith('✅')) { S.paid = S.paid || {}; if (!S.paid[c]) S.paid[c] = { at: Date.now(), name: '', done: {}, waitHot: 1 }; } /* review 11/1: something that failed the first time (simulation failure, not enough BNB…) used to never be tracked again */ log(`[${W.name}] ${c} ${m.replace(/\n/g, ' | ')}`); if (m.startsWith('🤖')) { S.freeTrack = S.freeTrack || {}; S.freeTrack[c] = S.freeTrack[c] || { at: Date.now() }; } if (m.startsWith('🤖')) noteFree(c, W); }).catch((e) => log(`[${W.name}] mint failed ${c} ${e.message}`));
  MQ.set(W.name, job);
  return '⏳ queued for a check (the rules decide whether to mint, the result is reported later)';
}

// per wallet, several units can be minted (per-wallet cap >1): keep minting while the simulation still passes, max MAX_REPEAT times
const MAX_REPEAT = 10;
async function autoMintMax(c, fname, W, val = 0n) {
  const d = Object.fromEntries(CANDS)[fname]; let first = await autoMint(c, fname, W, val), n = first.startsWith('🤖') ? 1 : 0;
  while (n > 0 && n < MAX_REPEAT) {
    await sleep(6000);
    // the first unit free, later ones cost a little (like QUOGENT): find a passing amount each time (≤0.001 BNB)
    // anti-phishing: only fully free projects are minted repeatedly; anything with a fee gets 1 unit per wallet per project (so a project cannot farm fees)
    let v2 = null; try { await rpc('eth_call', [{ from: W.addr, to: c, data: d, value: '0x0' }, 'latest']); v2 = 0n; } catch {}
    if (v2 == null) break;
    const m = await autoMint(c, fname, W, v2); if (!m.startsWith('🤖')) break; n++;
  }
  return n > 1 ? first + `\n(${n} units minted in total from this wallet)` : first;
}
const fpCache = new Map();
async function freeProgress(c) { /* progress of free projects: minted / total; returns null when unreadable (30 s cache) */
  const k = fpCache.get(c); if (k && Date.now() - k.t < 30e3) return k.p;
  let p = null; const decR = await call(c, '0x313ce567'); const isErc20 = !!(decR && decR.length >= 66 && BigInt(decR.slice(0, 66)) > 0n && BigInt(decR.slice(0, 66)) <= 36n); /* ERC20s are often fully issued from the start, so "issued ÷ total" would be misread as 100% */
  const tmR = isErc20 ? null : await call(c, '0xa2309ff8'); /* debug 11/1: read the actual minted count totalMinted first; totalSupply includes the team's reserve, so SmokeeAgent with only 72 minted read as 100% */
  const tsR = isErc20 ? null : (tmR && tmR.length >= 66 ? tmR : await call(c, '0x18160ddd'));
  if (tsR && tsR.length >= 66) { const sup = Number(BigInt(tsR.slice(0, 66))); for (const sel of ['0xd5abeb01', '0x32cb6b0c', '0x355274ea', '0xe985e367', '0xf1aee5a2', '0x0f2cdd6c', '0xa0e23ebd']) { const r = await call(c, sel); if (r && r.length >= 66) { const x = Number(BigInt(r.slice(0, 66))); if (x > 0 && x >= sup && x < 1e15) { p = sup / x; break; } } } }
  if (p == null) { const code = (await rpc('eth_getCode', [c, 'latest']).catch(() => '')) || ''; const pr = await progressOf(c, code.toLowerCase(), '').catch(() => ({})); if (pr.progress > 0) p = pr.progress; }
  fpCache.set(c, { t: Date.now(), p }); return p;
}
async function progressSweep() { /* user 10/31 23:21 checked why Pulsepanther was not minted: the tracking list has 500+ entries, a normal check only looks at 16 per round and each takes 10–30 s, so low-progress projects were revisited only every 2–3 hours and one going from 10% straight to 100% was never noticed. Now read progress only, cheaply, every minute (max 80, 6 in parallel); anything above 45% enters the fast list and is checked every 15 s */
  const now = Date.now(); const list = Object.entries(S.paid || {}).filter(([c, P]) => !P.unmintable && (P.progress || 0) < 0.999 && now - (P.at || 0) < 3 * 86400e3 && !WALLETS.every((W) => P.done?.[W.name])).sort((x, y) => (x[1].sweepAt || 0) - (y[1].sweepAt || 0)).slice(0, 80);
  let i = 0; await Promise.all(Array.from({ length: 6 }, async () => { while (i < list.length) { const [c, P] = list[i++]; try { if (P.progAt && Date.now() - P.progAt < 180e3) { P.sweepAt = Date.now(); continue; } /* debug: do not overwrite what the formal check read within 3 minutes (a different reading method could pull sold-out projects back into the fast list and take a slot) */ let fp = null; if (P.soldSel) { /* debug 11/1: bibiai AGENT ID can only be read as "sold / total" (sold a2309ff8, total a0e23ebd), the normal free reading fails → stuck at 0% for two hours and rising to 92% went unnoticed */ const sr = await call(c, P.soldSel); const sold = sr && sr.length >= 66 ? Number(BigInt(sr.slice(0, 66))) : null; if (sold > 0) { if (P.capSel === undefined) P.capSel = (await findCapSel(c, await fullCode(c, await codeOf(c)).catch(() => ''), sold).catch(() => '')) || ''; if (P.capSel) { const cr = await call(c, '0x' + P.capSel); const cap = cr && cr.length >= 66 ? Number(BigInt(cr.slice(0, 66))) : 0; if (cap >= sold && cap > 0) fp = sold / cap; } } }
      if (fp == null) fp = await freeProgress(c); P.sweepAt = Date.now(); if (fp != null && fp > 0) { if (fp >= 0.45 && (P.progress || 0) < 0.45 && !P.swLog) { P.swLog = 1; log(`progress sweep: ${P.name || c} at ${Math.round(fp * 100)}%, moving to fast checks`); } P.progress = fp >= 0.999 && !P.progAt ? 1 : Math.min(fp, 0.99); /* review 11/1: the formal check of a free contract does not read progress, so sold-out ones would stick at 99% and hold a fast-list slot → record 100% directly */ /* 100% is left to the formal fast check to confirm, so a misread does not park it forever */ } } catch {} } }));
}
async function autoMint(c, fname, W = WALLETS[0], val = 0n) {
  /* user 10/31 19:21: new rule — free or paid, mint once progress passes 50%, none of the earlier rules (repeat DEV, blacklist, heat, fee cap…) apply; only honeypot-type safety checks stay (simulate every trade, no approvals, no unexpected asset deduction, Flap banned). Only use heat to decide when progress is unreadable */
  { const sd = await serialDev(c); if (sd) return `⛔ ${sd}, not minting by the rules`; }
  if (val > 0n) { /* user 11/1 14:16: anything that "charges a fee" counts as paid — small projects like that drained about 0.195 BNB today; if money is involved the contract must have a break-even (refund) mechanism */
    S.feeRefund = S.feeRefund || {}; if (S.feeRefund[c] == null) { const fc = await fullCode(c, await codeOf(c)).catch(() => ''); S.feeRefund[c] = REFUND_SELS.some((x) => fc.includes(x)) ? 1 : 0; }
    if (!S.feeRefund[c]) return `⏸ charges a ${Number(val) / 1e18} BNB fee (not really free), the contract has no break-even mechanism, not minting by the rules`;
    if (await upgradeable(c).catch(() => false)) return '⏸ charges a fee and is an upgradeable contract (the break-even mechanism could be swapped out), not minting per risk control';
  }
  const fp = await freeProgress(c);
  const agentsN = S.paid?.[c]?.agents || 0; /* review 11/1: count Agent ID wallets only */ /* user 10/31 19:45: same for free ones — only mint once progress passes 60%; when progress is unreadable require 100+ wallets minting */
  if (fp != null && fp < GO_PCT) { /* user 11/1 02:37: threshold 60% */ S.paid = S.paid || {}; if (!S.paid[c]) S.paid[c] = { at: Date.now(), name: '', done: {}, waitHot: 1 }; return `⏸ progress ${Math.floor(fp * 100)}%, not at ${Math.round(GO_PCT * 100)}% yet, tracking first and will mint once it gets there`; }
  if (fp != null && agentsN < AG_MIN) { S.paid = S.paid || {}; if (!S.paid[c]) S.paid[c] = { at: Date.now(), name: '', done: {}, waitHot: 1 }; return `⏸ progress ${Math.floor(fp * 100)}% is enough, but only ${agentsN} Agent ID wallets are minting (needs ${AG_MIN}+, so the team cannot inflate progress), tracking first`; }
  if (fp != null && !(S.xHits || {})[c]) globalThis.xCheck?.(c, S.paid?.[c]?.name || '').catch(() => {}); /* a free project that passed the on-chain conditions: look it up on X in the background (record/warn only, never blocks) */
  if (fp == null && agentsN >= AG_MIN) { let tk = c; const tr0 = await call(c, '0xfc0c546a'); if (tr0 && tr0.length >= 66 && BigInt(tr0.slice(0, 66)) > 0n) tk = '0x' + tr0.slice(26, 66); if (await poolOpen(tk)) return '⏸ progress unreadable and the pool already opened, not minting (minting usually costs more than the market price)'; }
  if (fp == null && agentsN < AG_MIN) { S.paid = S.paid || {}; if (!S.paid[c]) S.paid[c] = { at: Date.now(), name: '', done: {}, waitHot: 1 }; return `⏸ progress unreadable, ${agentsN} wallets minting right now, needs ${AG_MIN}+ to mint, tracking first`; }
  if (val === 0n) { await freeNotify(c, S.paid?.[c]?.name || '', fp, agentsN); return '🔔 free project meets the conditions, notified (by the rules it is not auto-minted)'; }
  if (val > 0n && (Number(val) / 1e18) * ((await bnbUsd().catch(() => 0)) || 700) > PAID.maxUsd) return `⏸ the fee exceeds the ${PAID.maxUsd}U cap, not minted`;
  let d = Object.fromEntries(CANDS)[fname]; if (d && /replay|hotfn/.test(fname)) d = freshDeadline(d); /* also refresh the deadline on copied free mints */
  const today = new Date().toDateString(); if (S.mintDay !== today) { S.mintDay = today; S.mintCount = 0; }
  S.mintCnt = S.mintCnt && S.mintCnt.d === today ? S.mintCnt : { d: today }; if ((S.mintCnt[W.name] || 0) >= 1000) return '⏸ this wallet hit the 1000 auto-mints per day cap, not minted';
  const bnb = BigInt((await rpc('eth_getBalance', [W.addr, 'latest']).catch(() => '0x0')) || '0x0');
  if (bnb < 5n * 10n ** 14n) return '⚠️ not enough BNB for gas (< 0.0005), not minted';
  { const rb = await riskBlock(W, c, val, d, val === 0n); if (rb) return '⏸ risk control: ' + rb + ', not minted'; }
  const p = await W.baw(['contract-call', 'preview', '--binanceChainId', '56', '--from', W.addr, '--to', c, '--value', val.toString(), '--inputData', d]);
  const sim = p.j?.data?.simulationResult, rid = p.j?.data?.requestId;
  if (!p.j?.success || !rid) return '❌ simulation failed, not minted: ' + (p.j?.error?.message || '').slice(0, 120);
  { const fg = flapGuard(c, d, sim); if (fg) return '⛔ ' + fg + ', not minted'; }
  const losing = (sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr && Number(b.change) < 0 && !(String(b.contractAddress).toLowerCase().startsWith('0xeeee') && -Number(b.change) <= Number(val) && val > 0n)); /* risk control: the BNB deducted must not exceed what was agreed */
  if (sim?.allowanceChanges?.length || sim?.authorityChanges?.length || losing.length) return '⚠️ simulation shows an approval or a deduction of your assets, not minted to be safe';
  { const dr = await drainCheck(c, val); if (dr) return '⛔ ' + dr + ', not minted'; }
  if (!(sim?.balanceChanges || []).some((b) => String(b.owner).toLowerCase() === W.addr && (Number(b.change) > 0 || (/Erc721|Erc1155/i.test(b.tokenType || '') && !(Number(b.change) < 0))))) return '⚪ simulation shows nothing received (it may mint to someone else or net out to 0), not minted'; 
  INFLIGHT++; let x; try { x = await W.baw(['contract-call', 'execute', '--requestId', rid]); } finally { INFLIGHT--; }
  if (!x.j && /timed? ?out|ETIMEDOUT|killed/i.test(x.t || '')) return '⚠️ send result unknown (timeout), not re-minting for now';
  if (!x.j?.success) return '❌ send failed: ' + (x.j?.error?.message || x.t).slice(0, 120);
  S.mintCount++; S.mintCnt[W.name] = (S.mintCnt[W.name] || 0) + 1; riskAfter(W, c, x.j.data?.txHash || '', val, val === 0n);
  // tokens from a free mint also go into position tracking (cost 0 → only 5/10x and pullback notices, never auto-sell)
  for (const b of sim?.balanceChanges || []) { if (String(b.owner).toLowerCase() !== W.addr || b.tokenType !== 'Erc20' || !(Number(b.change) > 0)) continue; const ta = String(b.contractAddress).toLowerCase(); S.dyn = S.dyn || []; if (!S.dyn.some((t) => t.addr === ta && t.only === W.addr)) { const sy = tokStr(await call(ta, '0x95d89b41')) || ta.slice(0, 8); S.dyn.push({ sym: sy, addr: ta, cost: 0, only: W.addr }); } }
  for (const b of sim?.balanceChanges || []) if (String(b.owner).toLowerCase() === W.addr && /Erc721|Erc1155/i.test(b.tokenType || '')) { S.nftCols = S.nftCols || {}; S.nftCols[String(b.contractAddress).toLowerCase()] = 1; } // record the NFT collections held (volume checked every 12 hours)
  const got = (sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr).map((b) => (b.tokenType === 'Erc721' ? 'NFT ×1' : b.tokenType + ' +' + b.change)).join(', ');
  return `🤖 auto-minted ${fname}${got ? ' (' + got + ')' : ''}${fname.startsWith('commit') ? '\n(this one is two-step, it needs a reveal before you get it, handle it manually)' : ''}\ntx ${x.j.data?.txHash || x.j.data?.orderId || ''}`;
}

// ---------- AIDROP vault: notify on new event types (e.g. claims opening / distribution) ----------
const AIDROP_VAULT = (CONFIG.watchVault || '').toLowerCase(); // optional: a Flap vault to watch (e.g. AIDROP)
async function aidropWatch(head) {
  if (!AIDROP_VAULT) return;
  S.aidrop = S.aidrop || { topics: {}, from: head - 2000 };
  try {
    const logs = await rpc('eth_getLogs', [{ address: AIDROP_VAULT, fromBlock: '0x' + Math.max(S.aidrop.from, head - 4000).toString(16), toBlock: '0x' + head.toString(16) }]);
    const first = Object.keys(S.aidrop.topics).length === 0;
    let alerted = false;
    for (const l of logs) { const t0 = l.topics[0]; if (['0x8a587a0e', '0xe988651c'].includes(t0.slice(0, 10))) { S.aidrop.topics[t0] = 1; continue; } if (!S.aidrop.topics[t0]) { S.aidrop.topics[t0] = 1; if (!first && !alerted && (alerted = true)) await tg(`📣 The AIDROP vault shows new on-chain activity (a new event type)\nclaims may be opening or a distribution starting, check it yourself\ntx ${l.transactionHash}`); } }
    S.aidrop.from = head + 1;
  } catch {}
}

// ---------- ToshX: Agent ID exclusive small IGOs (paid, not auto-bought). Analyze new projects and notify via TG ----------
const TOSH_FACTORIES = [['BNB', '0x091d0cbd1e3b7d8b2a6fd8e0795b784eddffb590'], ['USDT', '0x8296fa2d430dcf25233a39cc3306ee503967a6bb']];
async function toshWatch() {
  S.tosh = S.tosh || { counts: {} };
  for (const [quote, f] of TOSH_FACTORIES) {
    const r = await call(f, '0x27cca59f'); if (!r) continue; const n = Number(BigInt(r.slice(0, 66)));
    const prev = S.tosh.counts[f];
    if (prev === undefined) { S.tosh.counts[f] = n; continue; } // the first time only record the count and skip old projects
    for (let i = prev; i < n; i++) {
      try {
        const L = await call(f, '0x7b443a76' + w(i)); const W4 = L.slice(2).match(/.{64}/g);
        const token = '0x' + W4[0].slice(24), hook = '0x' + W4[1].slice(24), creator = '0x' + W4[2].slice(24), createdAt = Number(BigInt('0x' + W4[3]));
        const str = async (sel) => { const h = await call(token, sel); try { const x = h.slice(2); const len = Number(BigInt('0x' + x.slice(64, 128))); return Buffer.from(x.slice(128, 128 + len * 2), 'hex').toString('utf8'); } catch { return '?'; } };
        const name = await str('0x06fdde03'), sym = await str('0x95d89b41');
        // how many the creator launched before (both factories count)
        let byCreator = 0; for (const [, f2] of TOSH_FACTORIES) { const c2 = await call(f2, '0x27cca59f'); const n2 = c2 ? Number(BigInt(c2.slice(0, 66))) : 0; for (let k = Math.max(0, n2 - 60); k < n2; k++) { const L2 = await call(f2, '0x7b443a76' + w(k)); if (L2 && L2.slice(2).match(/.{64}/g)[2].slice(24) === creator.slice(2)) byCreator++; } }
        // website metadata (description, twitter, TG)
        let meta = null; try { const j = await (await fetch('https://toshx.xyz/api/projects', { signal: AbortSignal.timeout(10000) })).json(); meta = (j.data || []).find((p) => String(p.token_address).toLowerCase() === token); } catch {}
        const raisedRaw = quote === 'BNB' ? await rpc('eth_getBalance', [hook, 'latest']).catch(() => '0x0') : await call(USDT, '0x70a08231' + wa(hook));
        const raised = Number(BigInt(raisedRaw || '0x0')) / 1e18;
        const flags = [];
        if (!meta) flags.push('no website data yet (may be a test launch)');
        if (meta && !meta.twitter) flags.push('no twitter');
        if (meta && !meta.website) flags.push('no website');
        if (byCreator > 1) flags.push(`the same creator has launched ${byCreator} already`);
        if (/test/i.test(name + sym)) flags.push('the name looks like a test');
        const desc = (meta?.description || '').replace(/\s+/g, ' ').slice(0, 220);
        const verdict = flags.length >= 2 ? '🔴 high risk, better to wait' : flags.length === 1 ? '🟡 some doubts, look closer' : '🟢 data complete, worth considering';
        // user's rule: generic launchpad tokens are not minted, only platform coins
        const PLATFORM_DEPLOYER = '0x790091e8965ecc1adfa40aa9b95515c6289dce9d'; // creator of the TOSH platform token
        const isPlatform = creator.toLowerCase() === PLATFORM_DEPLOYER || /platform token|platform coin|platform/i.test((meta?.description || '') + name);
        if (!isPlatform) { log(`ToshX new project ${name}(${sym}) is not a platform coin, not notified by the rules`); continue; }
        await tg(`🚀 ToshX new project related to the platform coin: ${name} ($${sym})\nQuote coin: ${quote} · raised ~${raised.toFixed(3)} ${quote}\nCreated: ${new Date(createdAt * 1000).toLocaleString('en-US', { hour12: false, timeZone: CONFIG.timeZone || 'Asia/Taipei' })}\n${desc ? 'Description: ' + desc + '\n' : ''}${meta?.twitter ? 'X: ' + meta.twitter + '\n' : ''}${meta?.telegram ? 'TG: ' + meta.telegram + '\n' : ''}Token ${token}\nVerdict: ${verdict}${flags.length ? ' (' + flags.join(', ') + ')' : ''}\nRule: one quota per Agent ID and it usually fills minutes after opening. To mint, handle it manually — "mint ${sym}" and how much per wallet\nhttps://toshx.xyz/launchpad`);
      } catch (e) { log('tosh err ' + e.message); }
    }
    S.tosh.counts[f] = n;
  }
}

// ---------- Flap "ID AGENT CURVE": an Agent ID only curve launchpad, analyze new launches and send TG ----------
const FLAP_READER = '0x18aa6c93f1887a243fdc348c9a77f5136555174a';
const FLAP_QUOTES = { '0x4902c5ebc598265ed2212b559b042de8a5eeec3f': 'BNCB', '0x02fca66c1d1afb4e2a7884261eb00f63598a7436': 'NVDAB', '0x205812cdbed920aff76c6580abd681a46d11efc7': 'QQQB', '0xbe9d156892e55e7154bcd3cb0fea677f9d3103e1': 'SPCXB' };
function decStrAt(hex, byteOff) { const h = hex.replace(/^0x/, ''); const len = Number(BigInt('0x' + h.slice(byteOff * 2, byteOff * 2 + 64))); return Buffer.from(h.slice(byteOff * 2 + 64, byteOff * 2 + 64 + len * 2), 'hex').toString('utf8'); }
async function flapAgentWatch() {
  S.flapAgent = S.flapAgent || { total: null, names: {} };
  const p0 = await call(FLAP_READER, '0x8f82e165' + w(0) + w(1)); if (!p0) return;
  const total = Number(BigInt('0x' + p0.slice(2 + 128, 2 + 192)));
  if (S.flapAgent.total === null) { S.flapAgent.total = total; return; }
  for (let i = S.flapAgent.total; i < total; i++) {
    try {
      const pg = await call(FLAP_READER, '0x8f82e165' + w(i) + w(1)); const W5 = pg.slice(2).match(/.{64}/g); const off = Number(BigInt('0x' + W5[0])) / 32; const m = '0x' + W5[off + 1].slice(24);
      const st = await call(FLAP_READER, '0x51c023e2' + wa(m)); const SW = st.slice(2).match(/.{64}/g);
      const t0 = Number(BigInt('0x' + SW[0])) / 32, inner = t0 + Number(BigInt('0x' + SW[t0])) / 32;
      const name = decStrAt(st, (inner + Number(BigInt('0x' + SW[inner])) / 32) * 32), sym = decStrAt(st, (inner + Number(BigInt('0x' + SW[inner + 1])) / 32) * 32);
      const buyTax = Number(BigInt('0x' + SW[inner + 5])) / 100, sellTax = Number(BigInt('0x' + SW[inner + 6])) / 100, maxW = Number(BigInt('0x' + SW[inner + 11])) / 100;
      const quote = '0x' + SW[t0 + 1].slice(24); const qn = /^0x0{40}$/.test(quote) ? 'BNB' : FLAP_QUOTES[quote] || quote.slice(0, 10);
      const key = (sym || name).toLowerCase(); const dup = S.flapAgent.names[key]; S.flapAgent.names[key] = (dup || 0) + 1;
      const flags = []; if (buyTax + sellTax > 8) flags.push(`tax is high ${buyTax}%/${sellTax}%`); if (dup) flags.push('copycat name (a same-name launch already exists)'); if (/<|script|onerror|http/i.test(name + sym)) flags.push('⚠️ the name contains suspicious code, do not click it on the web page');
      const verdict = flags.length >= 2 ? '🔴 better to skip' : flags.length === 1 ? '🟡 some doubts' : '🟢 first launch, reasonable tax';
      if (!/^(flap|flapx)$/i.test(sym.trim())) { log(`Flap Agent new launch ${name}(${sym}) is a launchpad token, not notified by the rules`); continue; }
      await tg(`🦋 Flap ID AGENT CURVE new launch: ${name.slice(0, 40)} (${sym.slice(0, 20)})\nBase pool: ${qn} · tax ${buyTax}%/${sellTax}% · per-wallet cap ${maxW}%\nVerdict: ${verdict}${flags.length ? ' (' + flags.join(', ') + ')' : ''}\nMarket ${m}\nTo mint, handle it manually — "mint ${sym.slice(0, 20)}" and the amount`);
    } catch (e) { log('flapAgent err ' + e.message); }
  }
  S.flapAgent.total = total;
}

// ---------- AI wallet login state ----------
// ---------- confirmed by the user: Agent ID exclusive "paid" mints are auto-minted too (with a budget cap + filters) ----------
// projects the user marked "mint directly" (ignores the 80% and the anti-phishing rules, only the 20U cap and the simulation safety checks apply)
const FORCE = new Set((CONFIG.forceMint || []).map((a) => a.toLowerCase())); // forceMint in config.json: contracts to mint directly
const PAID = {
  
  goPct: 0.8, earlyPct: 0.8, riseDelta: 0.02, maxUsd: Math.min(Number(CONFIG.paid?.maxUsd) || 20, 20), 
  maxBnbPerDay: CONFIG.paid?.maxBnbPerDay ?? 1e9,        // the user said mint no matter what → no daily cap (only limited by the wallet balance)
  minSold: 30,              // at least 30 units must have been minted by others before following (avoid ignored/trap contracts)
  maxAgeH: 168,              // give up when no pool opened within 48 hours of discovery
  keepBnb: CONFIG.paid?.keepBnb ?? 0.006,           // always keep this much BNB in the wallet for gas
};
const codeCache = new Map();
async function codeOf(a) { if (!codeCache.has(a)) codeCache.set(a, ((await rpc('eth_getCode', [a, 'latest']).catch(() => '')) || '').toLowerCase()); return codeCache.get(a); }
async function bnbUsd() {
  const p = await call(FACTORY, '0xe6a43905' + wa(WBNB) + wa(USDT)); const pair = '0x' + (p || '').slice(26); const r = await call(pair, '0x0902f1ac'); const t0 = await call(pair, '0x0dfe1681');
  if (!r || !t0) return 0; const a = BigInt(r.slice(0, 66)), b = BigInt('0x' + r.slice(66, 130)); return t0.slice(26) === WBNB.slice(2) ? Number(b) / Number(a) : Number(a) / Number(b);
}
const tokStr = (r) => { try { const h = r.slice(2); const len = Number(BigInt('0x' + h.slice(64, 128))); return Buffer.from(h.slice(128, 128 + len * 2), 'hex').toString('utf8'); } catch { return ''; } };
async function readLimit(W, c) { 
  const code = ((await codeOf(c)) || '').toLowerCase(); const has = (s) => code.includes(s); const num = (r) => (r && r.length >= 66 ? BigInt(r.slice(0, 66)) : null); const me = wa(W.addr); const lim = [];
  for (const s of ['d872e413', 'b399b0bc', 'b0b5230f', '7aa9a9d8']) if (has(s)) { const x = num(await call(c, '0x' + s + me)); if (x != null && x < 1000000n) { lim.push(x); break; } }
  let per = null; for (const s of ['453c2310', 'b228d925', '0f2cdd6c', 'f516a2e6', '3c8463a1', 'a4183dbe', '6ac8c1b7']) if (has(s)) { const x = num(await call(c, '0x' + s)); if (x != null && x > 0n && x < 1000000n) { per = x; break; } }
  if (per == null) for (const s of ['1cee24f5', 'ef23b3a3']) if (has(s)) { const x = num(await call(c, '0x' + s + me)); if (x != null && x > 0n && x < 1000000n) { per = x; break; } }
  if (per != null) { let mine = 0n; for (const s of ['1e7269c5', '3cef28d2', 'dc33e681', 'fddcb5ea', 'f5eb42dc', '7b622c72']) if (has(s)) { const x = num(await call(c, '0x' + s + me)); if (x != null && x < 1000000n) { mine = x; break; } } lim.push(per > mine ? per - mine : 0n); }
  for (const s of ['f968adbe', 'f43a22dc', 'de7fcb1d']) if (has(s)) { const x = num(await call(c, '0x' + s)); if (x != null && x > 0n && x < 1000000n) { lim.push(x); break; } }
  return lim.length ? lim.reduce((a, b) => (b < a ? b : a)) : null;
}
async function fitShares(W, c, data, v) { // share count: read how many units the team opened; if unreadable, step down to 5, 3, 2, 1 in turn
  const ws = data.slice(10).match(/.{64}/g) || []; if (!ws.length) return data;
  let n0 = BigInt('0x' + ws[0]); if (n0 < 1n || n0 > 100n) return data;
  const lim = await readLimit(W, c).catch(() => null);
  if (lim != null) { if (lim === 0n) throw new Error('this wallet has used up its quota'); if (lim < n0) { log(`read that the team opened ${lim} units, minting that many: ${c}`); n0 = lim; } }
  const nOrig = BigInt('0x' + ws[0]);
  for (const n of [n0, ...[5n, 3n, 2n, 1n].filter((x) => x < n0)]) {
    const d2 = data.slice(0, 10) + w(n) + ws.slice(1).join(''); const vv = (BigInt(v || 0) * n) / nOrig; /* review 11/1: when the share count drops the payment must drop proportionally too (it used to pay the original amount) */
    try { await rpc('eth_call', [{ from: W.addr, to: c, data: d2, value: '0x' + vv.toString(16) }, 'latest']); if (n !== n0) log(`only mintable after lowering to ${n} units: ${c}`); return d2; } catch {}
  }
  return data;
}
const REFUND_SELS = ['590e1ae3', 'b5545a3c', '278ecde1', '5b7baf64', '110f8874', '16bfe25c', '38e771ab', 'b2d5ae44', 'fa89401a', 'bffa55d5', '68ec0d99', '74c1b1c9', 'b30196e5', 'c033a490', '4c4a386f', 'd233e4eb']; // refund-related functions: refund()、claimRefund()、refundOpen()、refundable(address)…
const scaleV = (d0, d1, v) => { /* the share count in d0's first argument becomes d1's, the amount is adjusted proportionally */ try { const a = BigInt('0x' + d0.slice(10, 74)), b = BigInt('0x' + d1.slice(10, 74)); if (a >= 1n && a <= 100n && b >= 1n && b < a) return (BigInt(v) * b) / a; } catch {} return BigInt(v); };
async function safeAddrs(W, c, data, v) { 
  let ws = data.slice(10).match(/.{64}/g) || []; const me = W.addr.slice(2).toLowerCase();
  if (W.agentId) { let sw = 0; for (let i = 0; i < ws.length; i++) { const x = ws[i].toLowerCase(); if (/^0{24}/.test(x) || BigInt('0x' + x) < 2n ** 128n || x === W.agentId) continue; const o = await call('0x' + REGISTRY, '0x6352211e' + x); if (o && BigInt(o.slice(0, 66)) > 0n && o.slice(26, 66).toLowerCase() !== me) { ws[i] = W.agentId; sw = 1; } } if (sw) { data = data.slice(0, 10) + ws.join(''); log(`replaced someone else's Agent ID number with our own before copying: ${c}`); } } /* a copied transaction carries someone else's Agent ID number → swap in our own */
  const skip = new Set([me, c.slice(2).toLowerCase(), USDT.slice(2).toLowerCase(), WBNB.slice(2).toLowerCase()]);
  const idx = ws.map((x, i) => [x.toLowerCase(), i]).filter(([x]) => /^0{24}[0-9a-f]{40}$/.test(x) && x.slice(24, 32) !== '00000000' && !skip.has(x.slice(24))).map(([, i]) => i);
  if (!idx.length) return data;
  for (const r of [me.padStart(64, '0'), '0'.repeat(64)]) {
    const d2 = data.slice(0, 10) + ws.map((x, i) => (idx.includes(i) ? r : x)).join('');
    try { await rpc('eth_call', [{ from: W.addr, to: c, data: d2, value: '0x' + BigInt(v || 0).toString(16) }, 'latest']); log(`replaced someone else's address with ${r === '0'.repeat(64) ? 'the zero address' : 'our own address'} before copying: ${c}`); return d2; } catch {}
  }
  throw new Error('the transaction carries someone else\'s address and neither our own nor the zero address works, not minting to be safe');
}
async function revokeOnFail(W, token, spender, fn) { 
  try { return await fn(); } catch (e) { try { await previewExec(W, token, '0x095ea7b3' + wa(spender) + w(0), 0n, () => ''); log(`[${W.name}] mint failed, approval to ${spender} revoked`); } catch (e2) { log(`[${W.name}] revoke failed ${e2.message}`); } throw e; }
}
/* user 10/31 10:49: ban all approvals to Flap. Never interact with, and never approve, Flap contracts */
const FLAP_BLOCK = new Set(['0xe2ce6ab80874fa9fa2aae65d277dd6b8e65c9de0', '0x90497450f2a706f1951b5bdda52b4e5d16f34c06', '0x18aa6c93f1887a243fdc348c9a77f5136555174a', '0x8b4329947e34b6d56d71a3385cac122bade7d78d', '0x5dd913731c12ad8df3e574859fde45412bf4aad9']);
const FLAP_BAN = false; /* user 10/31 19:26: Flap interaction is allowed again now (switch to true to ban it again) */
function flapGuard(to, data, sim) {
  if (!FLAP_BAN) return '';
  if (FLAP_BLOCK.has(String(to).toLowerCase())) return 'banned: do not interact with Flap contracts';
  const d = String(data || '').toLowerCase(); if (/^0x(095ea7b3|39509351)/.test(d) && FLAP_BLOCK.has('0x' + d.slice(34, 74))) return 'banned: no approvals to Flap';
  if ((sim?.allowanceChanges || []).some((a) => FLAP_BLOCK.has(String(a.spender).toLowerCase()))) return 'would approve to Flap, banned';
  return '';
}
let INFLIGHT = 0; // number of transactions being sent (a restart must wait for it to reach zero so nothing is re-sent)
async function previewExec(W, to, data, valueWei, check, recheck = null, opts = {}) {
  const p = await W.baw(['contract-call', 'preview', '--binanceChainId', '56', '--from', W.addr, '--to', to, '--value', valueWei.toString(), '--inputData', data]);
  const sim = p.j?.data?.simulationResult, rid = p.j?.data?.requestId;
  if (!p.j?.success || !rid) throw new Error('simulation failed ' + (p.j?.error?.message || p.t).slice(0, 120));
  const fg = flapGuard(to, data, sim); if (fg) throw new Error('unsafe simulation: ' + fg);
  const why = check(sim); if (why) throw new Error('unsafe simulation: ' + why);
  if (!opts.manual && !opts.noDrain) { const dr = await drainCheck(to, valueWei); if (dr) throw new Error('unsafe simulation: ' + dr); }
  { const outBnb = (sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr && String(b.contractAddress || '').toLowerCase().startsWith('0xeeee') && Number(b.change) < 0).reduce((a, b) => a - Number(b.change), 0); if (outBnb > Number(valueWei) * 1.0000001 + 1) throw new Error('unsafe simulation: more BNB would be deducted than agreed (risk control)'); }
  if (recheck) { const r2 = await recheck(); if (r2) throw new Error('recheck before sending failed: ' + r2); } // recheck the price before sending (review suggestion 5)
  INFLIGHT++; let x; try { x = await W.baw(['contract-call', 'execute', '--requestId', rid]); } finally { INFLIGHT--; }
  if (!x.j && /timed? ?out|ETIMEDOUT|killed/i.test(x.t || '')) throw new Error('result unknown: send timed out, it may already be on chain, not re-sending for now');
  if (!x.j?.success) throw new Error('send failed ' + (x.j?.error?.message || x.t).slice(0, 120));
  riskAfter(W, to, x.j.data?.txHash || '', 0n, false, false);
  await sleep(8000); return { tx: x.j.data?.txHash || '', sim };
}
async function poolOut(amtIn, from, to) { // how much the pool can buy right now (0 when there is no pool)
  const r = await call(ROUTER, '0xd06ca61f' + w(amtIn) + w(64) + w(2) + wa(from) + wa(to)); if (!r || r.length < 2 + 64 * 4) return 0n; return BigInt('0x' + r.slice(2 + 64 * 3, 2 + 64 * 4));
}
const gotTok = (sim, W) => (sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr && Number(b.change) > 0).reduce((a, b) => a + Number(b.change), 0);
// mint progress: look for "sold / cap" style functions first; when there are none, look at "how many coins are left in the mint pool" (what the contract held right after deployment vs now)
const SOLD_SELS = ['a2309ff8', 'f4514d2e', '505e05fd', 'cc84efea', '192fc516', '9106d7ba', '67153ab4', 'f3f8a4fd', '06ed0717', '9659867e', '4f02c420', '1f21bfbf'];
const CAP_SELS = ['d8f93498', 'c65b61ca', '3a98ef39', 'b6b6f0c3', 'cce132d1', 'd5abeb01', '32cb6b0c', '06517a29', '2163837f', 'aa2d9a02', '76c71ca1', '98f1312e', '355274ea', '85e3f997'];
async function progressOf(c, code, tokAddr) {
  let sold = null, cap = null;
  for (const s of SOLD_SELS) { if (!code.includes(s)) continue; const r = await call(c, '0x' + s); if (r && r.length >= 66) { sold = Number(BigInt(r.slice(0, 66))); break; } }
  if (sold != null) for (const s of CAP_SELS) { if (!code.includes(s)) continue; const r = await call(c, '0x' + s); if (r && r.length >= 66) { const x = Number(BigInt(r.slice(0, 66))); if (x > 10 && x >= sold && x < 1e12) { cap = x; break; } } }
  if (sold != null && cap) return { progress: sold / cap, sold, cap, how: 'sold/cap' };
  S.prog = S.prog || {}; let pg = S.prog[c];
  const tok = tokAddr || c;
  if (!pg) {
    const head = parseInt(await rpc('eth_blockNumber', []), 16);
    const has = async (b) => (((await rpc('eth_getCode', [c, '0x' + b.toString(16)]).catch(() => '0x')) || '0x').length > 2);
    let lo = head - 400000, hi = head; if (await has(lo)) { S.prog[c] = { none: 1 }; return { progress: 0, sold, cap }; }
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (await has(m)) hi = m; else lo = m; }
    let init = 0n; for (const b of [hi, hi + 1, hi + 5, hi + 20]) { const r = await rpc('eth_call', [{ to: tok, data: '0x70a08231' + wa(c) }, '0x' + b.toString(16)]).catch(() => null); if (r && r.length >= 66 && BigInt(r.slice(0, 66)) > 0n) { init = BigInt(r.slice(0, 66)); break; } }
    pg = S.prog[c] = init > 0n ? { init: init.toString(), tok } : { none: 1 };
  }
  if (pg.none) return { progress: 0, sold, cap };
  const now = big(await call(pg.tok, '0x70a08231' + wa(c)));
  const init = BigInt(pg.init); return { progress: Number(init - now) / Number(init), sold, cap, how: 'mint-pool remainder' };
}

// ---------- anti "serial launch phishing": the same contract code (redeployed under another name) showing up 3+ times within 24 hours → treated as a batch launch, paid/fee mints are never minted ----------
function codePrint(code) { const c = (code || '').toLowerCase().replace(/0x/, ''); return c.length + ':' + c.slice(0, 120) + c.slice(-120); }
function noteDeploy(code) { const fp = codePrint(code); S.fps = S.fps || {}; const now = Date.now(); const arr = (S.fps[fp] || []).filter((t) => now - t < 86400e3); arr.push(now); S.fps[fp] = arr.slice(-20); if (Object.keys(S.fps).length > 3000) S.fps = {}; return arr.length; }
function isSerial(code) { const arr = (S.fps || {})[codePrint(code)] || []; return arr.filter((t) => Date.now() - t < 86400e3).length >= 3; }

// ---------- third layer: check the team's background ----------
async function deployerOf(c) {
  S.deployer = S.deployer || {}; if (S.deployer[c]) return S.deployer[c];
  const ow = await call(c, '0x8da5cb5b'); if (ow && ow.length >= 66 && BigInt(ow.slice(0, 66)) > 0n) return (S.deployer[c] = '0x' + ow.slice(26, 66));
  return '';
}
async function freshWallet(a) { // is the deployer a fresh wallet only used within about the last 8 hours (assume not when unknown)
  try { const head = parseInt(await rpc('eth_blockNumber', []), 16); const old = await rpc('eth_getTransactionCount', [a, '0x' + (head - 60000).toString(16)]); const now = await rpc('eth_getTransactionCount', [a, 'latest']); return parseInt(old, 16) === 0 && parseInt(now, 16) < 50; } catch { return false; }
}
// repeat DEV: the same deployer launching 3+ Agent ID projects within 24 hours → hard block (free, fee and paid alike)
function noteDev(dep, c) { if (!dep) return; S.devs = S.devs || {}; const now = Date.now(); const arr = (S.devs[dep] || []).filter((x) => now - x.t < 86400e3 && x.c !== c); arr.push({ t: now, c }); S.devs[dep] = arr.slice(-30); if (Object.keys(S.devs).length > 5000) S.devs = {};
  (async () => { let n = tokStr(await call(c, '0x95d89b41')) || tokStr(await call(c, '0x06fdde03')); if (!n) { const tr = await call(c, '0xfc0c546a'); if (tr && tr.length >= 66 && BigInt(tr.slice(0, 66)) > 0n) n = tokStr(await call('0x' + tr.slice(26, 66), '0x95d89b41')); } const e = (S.devs[dep] || []).find((x) => x.c === c); if (e && n) e.n = n; })().catch(() => {}); }

const devKey = (x) => String(x.n || S.paid?.[x.c]?.name || x.c).toLowerCase().replace(/\s*\(.*?\)\s*/g, '').replace(/^\$/, '').trim();
const DEV_MAX = 10;
const FIXED_THR = existsSync(join(DIR, 'data', 'fixed_thr.txt')); /* put this file on your own machine: fix it at 80% / 150 */
if (!FIXED_THR && !(S.thr && S.thr.go >= 0.8 && S.thr.ag >= 150)) { S.thr = { go: +(0.8 + Math.random() * 0.1).toFixed(3), ag: 150 + Math.floor(Math.random() * 51) }; save(); } /* anti-reverse-engineering 11/1: after open-sourcing, bad actors build projects to fit the thresholds (the noon batch landed exactly on 60% and 0.001 BNB); everyone gets random thresholds at install time (progress 80–90%, wallets 150–200) that they cannot guess */
const AG_MIN = FIXED_THR ? 150 : S.thr.ag; /* user 11/1 14:59: changed to 150 */ /* user 11/1 14:48: enough progress is not enough, many Agent ID wallets must be minting too (so the team cannot inflate progress itself) */
const GO_PCT = FIXED_THR ? 0.8 : S.thr.go;
setTimeout(() => log(`entry thresholds: progress ${Math.round(GO_PCT * 1000) / 10}%, ${AG_MIN}+ Agent ID wallets minting${FIXED_THR ? ' (fixed)' : ' (random per install, bad actors cannot guess)'}`), 3000); /* user 11/1 14:39: free and paid both need progress above 80% (paid also needs break-even) */ // user 11/1 14:21: the same deployer deploying ≥10 projects within 24 hours is not minted (the mass-deploy fee farming kind)
// ---------- risk control (user 11/1 14:46: stop teams from draining BNB a little at a time with changing tricks) ----------
const RISK = { dayBnb: 0.01, freePerDay: 20, gasMax: 0.0003, gasMaxPaid: 0.001, revMax: 2 }; // per-wallet daily fee+gas cap, max free mints per day, per-tx gas cap, on-chain failures per contract before giving up
function riskDay() { const d = new Date().toDateString(); if (!S.risk || S.risk.d !== d) S.risk = { d, spend: {}, free: {}, warned: {}, rev: (S.risk && S.risk.rev) || {} }; return S.risk; }
const UPG_SLOTS = ['0x360894a13ba1a3210667c828492db98dca3e2076cc3735a8ce03b6d4d6b6bbc', '0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50', '0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036a5a723fd8ee048ed3f8c3'];
async function upgradeable(c) { /* upgradeable proxy contract: the team can swap the whole code (break-even mechanism included) afterwards */ S.upg = S.upg || {}; if (S.upg[c] != null) return !!S.upg[c]; let u = 0; for (const sl of UPG_SLOTS) { const v = await rpc('eth_getStorageAt', [c, sl, 'latest']).catch(() => null); if (v && /[1-9a-f]/i.test(v.slice(2))) { u = 1; break; } } S.upg[c] = u; return !!u; }
async function drainCheck(c, valueWei) { /* user 11/1 15:59: the paid money must stay in the contract (as pool liquidity / refundable if it does not sell out). The simulation only shows our own wallet, so look at how much BNB the contract actually holds: progress at 80% while the contract is nearly empty = the money was moved out and refunds are fake, don't mint */
  if (!(BigInt(valueWei || 0) > 0n)) return '';
  S.drainC = S.drainC || {}; const k = S.drainC[c]; if (k && Date.now() - k.t < 600e3) return k.r;
  let tok = ''; const tr = await call(c, '0xfc0c546a'); if (tr && tr.length >= 66 && BigInt(tr.slice(0, 66)) > 0n) tok = '0x' + tr.slice(26, 66);
  let bal = 0; for (const a of [c, tok].filter(Boolean)) bal += Number(BigInt((await rpc('eth_getBalance', [a, 'latest']).catch(() => '0x0')) || '0x0')) / 1e18;
  const pair = await call(FACTORY, '0xe6a43905' + wa(tok || c) + wa(WBNB)).catch(() => null); const hasPool = !!(pair && pair.length >= 66 && BigInt(pair.slice(0, 66)) > 0n);
  const r = bal < 0.01 && !hasPool ? `only ${bal.toFixed(4)} BNB left in the contract, the paid money was moved out (refund / pool may be fake)` : '';
  S.drainC[c] = { t: Date.now(), r }; return r;
}
async function ownerHeavy(c, name) { /* user 11/1 15:59: the team keeping too many coins (>30%) can dump after the pool opens → warn only, no block, to check whether it is accurate */
  S.ownW = S.ownW || {}; if (S.ownW[c]) return; S.ownW[c] = 1;
  let tok = c; const tr = await call(c, '0xfc0c546a'); if (tr && tr.length >= 66 && BigInt(tr.slice(0, 66)) > 0n) tok = '0x' + tr.slice(26, 66);
  const ts0 = big(await call(tok, '0x18160ddd')); if (!(ts0 > 0n)) return;
  const who = new Set(); const ow = await call(c, '0x8da5cb5b'); if (ow && ow.length >= 66 && BigInt(ow.slice(0, 66)) > 0n) who.add('0x' + ow.slice(26, 66).toLowerCase()); const dep = (S.depFrom || {})[c]; if (dep) who.add(dep.toLowerCase());
  for (const a of who) { const code = ((await rpc('eth_getCode', [a, 'latest']).catch(() => '0x')) || '0x'); if (code.length > 2) continue; const b = big(await call(tok, '0x70a08231' + wa(a))); const pct = Number((b * 10000n) / ts0) / 100; if (pct > 30) { log(`⚠️ ${name || c}: team wallet ${a.slice(0, 10)}… holds ${pct}% of the supply`); await tg(`⚠️ ${name || c} meets the entry conditions, but the team wallet itself holds ${pct}% of the supply and may dump after the pool opens (warning only, it is still minted by the rules)\n${c}`); return; } }
}
async function freeNotify(c, name, prog, agents) { /* user 11/1 15:56: free projects mostly never really launch and are also how bad actors drain money → do not auto-mint, only notify when the conditions are met, the user mints manually if they want */
  S.freeNote = S.freeNote || {}; if (S.freeNote[c]) return; S.freeNote[c] = ts(); const ks = Object.keys(S.freeNote); if (ks.length > 3000) delete S.freeNote[ks[0]];
  let nm = name; if (!nm || /channel project/.test(nm)) nm = tokStr(await call(c, '0x95d89b41')) || tokStr(await call(c, '0x06fdde03')) || c.slice(0, 10);
  const nj = await nftJunk(c).catch(() => ''); if (nj) { log(`free project ${nm} meets the conditions but ${nj}, not notified`); return; }
  log(`🔔 free project meets the conditions (notify only, no auto-mint): ${nm} ${c}`);
  await tg(`🔔 Free project meets the conditions (by the rules it is not auto-minted): ${nm}\nProgress ${prog != null ? Math.floor(prog * 100) + '%' : 'unreadable'} · ${agents || 0} Agent ID wallets minting\n${c}\n${UPD.on ? 'Mint it yourself by hand if you want it' : 'To mint, handle it manually — "mint ' + nm + '"'}`);
}
async function nftJunk(c) { /* user 11/1 15:03: if the NFT image link is fake or empty (like ipfs://sniper-test-nft/1) don't mint — the fee-farming junk projects do not even bother with an image */
  S.nftJunk = S.nftJunk || {}; if (S.nftJunk[c] != null) return S.nftJunk[c];
  const code = ((await rpc('eth_getCode', [c, 'latest']).catch(() => '')) || '').toLowerCase(); let fc = code; try { fc = await fullCode(c, code); } catch {}
  if (!fc.includes('c87b56dd')) return (S.nftJunk[c] = ''); /* not an NFT (no tokenURI) */
  const str = (r) => { try { const b = Buffer.from(r.slice(2), 'hex'); const n = Number(BigInt('0x' + b.slice(32, 64).toString('hex'))); return b.slice(64, 64 + n).toString('utf8').trim(); } catch { return ''; } };
  let uri = null; for (const id of [1n, 0n]) { const r = await call(c, '0xc87b56dd' + w(id)); if (r && r.length > 130) { uri = str(r); break; } if (r) uri = uri ?? ''; }
  if (uri == null) return ''; /* unreadable (maybe nothing minted yet), don't judge */
  const ok = /^data:/i.test(uri) || /^https?:\/\/[a-z0-9-]+(\.[a-z0-9-]+)+\//i.test(uri) || /^ar:\/\/[A-Za-z0-9_-]{30,}/.test(uri) || /^ipfs:\/\/(ipfs\/)?(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{50,})/.test(uri);
  return (S.nftJunk[c] = ok ? '' : `NFT image link is ${uri ? 'fake (' + uri.slice(0, 40) + ')' : 'empty'}`);
}
async function riskBlock(W, c, valWei, data, isFree, paid = false) { /* returns the reason not to mint; an empty string = can mint */
  const R = riskDay();
  { const nj = await nftJunk(c).catch(() => ''); if (nj) return nj + ', likely a junk project'; }
  if ((R.rev[c] || 0) >= RISK.revMax) return `this contract failed on chain ${R.rev[c]} times, not trying again (so it stops burning gas)`;
  if (!paid && (R.spend[W.name] || 0) >= RISK.dayBnb) { if (!R.warned[W.name]) { R.warned[W.name] = 1; tg(`⚠️ [${W.name}] fees+gas already spent ${(R.spend[W.name]).toFixed(4)} BNB today, hitting the daily cap of ${RISK.dayBnb} BNB, auto-mint stopped for today (selling is unaffected)`).catch(() => {}); } return `fees+gas already hit the ${RISK.dayBnb} BNB cap today`; }
  if (isFree && !(R.free[W.name] || []).includes(c) && (R.free[W.name] || []).length >= RISK.freePerDay) return `already auto-minted ${RISK.freePerDay} free projects today, cap reached`;
  const bal = Number(BigInt((await rpc('eth_getBalance', [W.addr, 'latest']).catch(() => '0x0')) || '0x0')) / 1e18;
  if (bal - Number(valWei) / 1e18 < PAID.keepBnb) return `BNB below the ${PAID.keepBnb} reserve, kept for selling only`;
  try { const g = await rpc('eth_estimateGas', [{ from: W.addr, to: c, data, value: '0x' + BigInt(valWei).toString(16) }]); const gp = await rpc('eth_gasPrice', []); const fee = (Number(BigInt(g)) * Number(BigInt(gp))) / 1e18; if (fee > (paid ? RISK.gasMaxPaid : RISK.gasMax)) return `estimated gas ${fee.toFixed(5)} BNB is too high`; } catch {}
  return '';
}
function riskAfter(W, c, tx, valWei, isFree, countFee = true) { /* bookkeeping after sending: fee + actual gas; on-chain failure count */
  const R = riskDay(); if (countFee) R.spend[W.name] = (R.spend[W.name] || 0) + Number(valWei) / 1e18; if (isFree) { const f = (R.free[W.name] = Array.isArray(R.free[W.name]) ? R.free[W.name] : []); if (!f.includes(c)) f.push(c); }
  if (!tx || !/^0x[0-9a-f]{64}$/i.test(tx)) return;
  (async () => { for (let i = 0; i < 6; i++) { await sleep(5000); const r = await rpc('eth_getTransactionReceipt', [tx]).catch(() => null); if (!r) continue; const gas = (Number(BigInt(r.gasUsed || '0x0')) * Number(BigInt(r.effectiveGasPrice || '0x0'))) / 1e18; const R2 = riskDay(); R2.spend[W.name] = (R2.spend[W.name] || 0) + gas; if (r.status === '0x0') { R2.rev[c] = (R2.rev[c] || 0) + 1; log(`[${W.name}] on-chain failure (burned ${gas.toFixed(5)} BNB gas): ${c}, #${R2.rev[c]} today`); } save(); return; } })().catch(() => {});
}
async function serialDev(c) { const dep = (S.depFrom || {})[c]; if (!dep) return ''; /* judge only by the wallet that actually sent the deploy transaction, not owner() (launchpad projects often share the platform as owner and would be falsely killed) */ const n = new Set(((S.devs || {})[dep] || []).filter((x) => Date.now() - x.t < 86400e3).map((x) => x.c)).size; return n >= DEV_MAX ? `the same deployer ${dep.slice(0, 10)}… deployed ${n} projects within 24 hours` : ''; }
function isRepeatDev(dep) { return !!dep && new Set(((S.devs || {})[dep] || []).filter((x) => Date.now() - x.t < 86400e3).map(devKey)).size >= 3; }
function blacklist(dep, why) { if (!dep) return; S.black = S.black || {}; if (!S.black[dep]) { S.black[dep] = { at: ts(), why }; log(`blacklist: ${dep} (${why})`); } }

const heatTier = (P) => ((P.agents || 0) >= 200 || (P.burst || 0) >= 30 ? 2 : (P.agents || 0) >= 100 || (P.burst || 0) >= 15 ? 1 : 0);
function markUnmintable(c, P, sym, progress, why = 'needs a website signature or whitelist') { P.lastReason = why; // met the entry conditions but we cannot mint: mark it and stop retrying, notify the user once
  if (P.unmintable) return; P.unmintable = ts(); P.unmintableAt = Date.now();
  log(`mint failed ${c} (${sym}): ${why}`);
  if (false && (heatTier(P) >= 1 || ((progress || 0) >= 0.4 && (progress || 0) < 0.95))) tg(`⚠️ ${sym || P.name || 'new project'} met the entry conditions (${P.agents || 0} agents so far${progress > 0 ? ', progress ~' + Math.round(progress * 100) + '%' : ''}), but the tool cannot mint it: ${why}. Mint it by hand if you want\n${c}`).catch(() => {});
}
function noteFail(c, P, sym, progress) { P.cf = (P.cf || 0) + 1; if (P.cf >= 8) markUnmintable(c, P, sym, progress, 'simulation keeps failing, it may be sold out, need a website signature or whitelist, or already closed'); } // no retry after 4 rounds (two wallets) fail
const agentCache = new Map(); // address → has a .ai Agent ID
async function rpcBatch(reqs) { const out = []; for (let i = 0; i < reqs.length; i += 100) { for (const u of ['https://bsc-rpc.publicnode.com', 'https://bsc-dataseed.bnbchain.org', 'https://bsc.publicnode.com']) { let got = 0; try { const j = await (await fetch(u, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(reqs.slice(i, i + 100).map((r, k) => ({ jsonrpc: '2.0', id: i + k, ...r }))), signal: AbortSignal.timeout(20000) })).json(); for (const r of (Array.isArray(j) ? j : [])) if (r.result !== undefined) { out[r.id] = r.result; got++; } } catch {} if (got) break; } } return out; } /* debug 11/1: with only one node a rate limit lost the whole batch → the Agent ID count read 0 (GOOSE had 534 minting but showed 0) */
async function heatOf(c, P) { // compute heat: how many distinct Agent ID wallets minted in about the last 2.5 hours, and how many within the last 3 minutes
  const head = parseInt(await rpc('eth_blockNumber', []), 16); let tok = ''; const tr = await call(c, '0xfc0c546a'); if (tr && tr.length >= 66 && BigInt(tr.slice(0, 66)) > 0n) tok = '0x' + tr.slice(26, 66).toLowerCase();
  const last = new Map(), txb = new Map(); const skip = new Set([c, tok, '0x0000000000000000000000000000000000000000', '0x000000000000000000000000000000000000dead']);
  const span = P.heatAt ? 6000 : 20000; // about 2.5 hours the first time, afterwards only the last ~45 minutes (the cumulative count keeps the max)
  for (let b = head - span; b <= head; b += 10000) {
    const q = [{ address: tok ? [c, tok] : c, fromBlock: '0x' + b.toString(16), toBlock: '0x' + Math.min(b + 9999, head).toString(16) }]; const L = await rpc('eth_getLogs', q).catch(() => logsRpc(q).catch(() => [])); /* debug 11/1: fall back to several nodes when the default one is rate limited, so heat (participant count) is accurate */
    for (const l of L || []) { if (!l.transactionHash) continue; const bn = parseInt(l.blockNumber, 16); if (!txb.has(l.transactionHash) || txb.get(l.transactionHash) < bn) txb.set(l.transactionHash, bn); }
  }
  /* anti-reverse-engineering 11/1: every address seen in the on-chain records (recipients of airdrops included) used to count as a participant, so a team could inflate heat by airdropping to 150 Agent ID wallets; now only wallets that sent the transaction and paid gas count */
  const hashes = [...txb.entries()].sort((x, y) => y[1] - x[1]).slice(0, 800);
  if (hashes.length) { const txs = await rpcBatch(hashes.map(([h]) => ({ method: 'eth_getTransactionByHash', params: [h] }))); hashes.forEach(([h, bn], i) => { const a = String(txs[i]?.from || '').toLowerCase(); if (!/^0x[0-9a-f]{40}$/.test(a) || skip.has(a)) return; if (!last.has(a) || last.get(a) < bn) last.set(a, bn); }); }
  const unknown = [...last.keys()].filter((a) => !agentCache.has(a)).slice(0, 400);
  if (unknown.length) { const res = await rpcBatch(unknown.map((a) => ({ method: 'eth_call', params: [{ to: '0x' + REGISTRY, data: '0x70a08231' + wa(a) }, 'latest'] }))); unknown.forEach((a, i) => { if (res[i] !== undefined) agentCache.set(a, !!res[i] && res[i].length >= 66 && BigInt(res[i].slice(0, 66)) > 0n); }); }
  let agents = 0, burst = 0; for (const [a, bn] of last) if (agentCache.get(a)) { agents++; if (bn >= head - 400) burst++; }
  P.agents = Math.max(P.agents || 0, agents); P.burst = burst; P.heatAt = Date.now();
}
const hk = (P) => (P.heatAt || 0) + (heatTier(P) >= 1 || (P.progress || 0) >= 0.2 ? 0 : 540e3);
async function heatWatch() { // background: compute heat for tracked paid projects and, for contracts we cannot read, learn other people's mint method on the way (does not slow the 15 s fast check down)
  const ents = Object.entries(S.paid || {}).filter(([c, P]) => !INFRA.has(c) && !P.unmintable && (P.progress || 0) < 0.999 && !WALLETS.every((W) => P.done?.[W.name]))
    .sort((a, b) => ((b[1].needLearn && (b[1].progress || 0) >= 0.45) - (a[1].needLearn && (a[1].progress || 0) >= 0.45)) || hk(a[1]) - hk(b[1])).slice(0, 8); // nearly full or very hot ones refresh about every minute, the rest about every 10; ones near the threshold that we cannot mint yet go first (debug 11/1: BNBCAT at 57% never got scheduled)
  for (const [c, P] of ents) {
    try {
      await heatOf(c, P).catch((e) => log('heat ' + c + ' ' + e.message)); /* debug 11/1: when computing heat hit a rate limit the learning was skipped along with it → handled separately */
      if (P.needLearn && (!P.replay || Date.now() - (P.replay.learnedAt || 0) > 120e3) && (heatTier(P) >= 1 || (P.progress || 0) >= 0.2 || (P.agents || 0) >= 20)) { const lr = await learnReplay(c).catch(() => null); if (lr?.recv) { P.recvValue = lr.recv; P.recvSeen = lr.recvSeen; P.recvVals = lr.recvVals; P.replay = { hot: lr.hot, learnedAt: lr.learnedAt }; delete P.needLearn; log(`learned the mint method for ${P.name || c}: mint by simply sending ${Number(BigInt(lr.recv)) / 1e18} BNB (${lr.recvSeen} transactions)`); } else if (lr) P.replay = lr; }
    } catch (e) { log('heat ' + c + ' ' + e.message); }
  }
}
function freshDeadline(data) { /* if a copied transaction carries a "deadline" (a number that looks like a timestamp), replace it with 15 minutes from now, otherwise the old transaction always expires (Binance Wuyou 10/31) */
  const now = Math.floor(Date.now() / 1000); const ws = data.slice(10).match(/.{64}/g) || []; let ch = 0;
  const out = ws.map((x) => { const v = BigInt('0x' + x); if (v > BigInt(now - 30 * 86400) && v < BigInt(now + 30 * 86400)) { ch = 1; return (BigInt(now + 900)).toString(16).padStart(64, '0'); } return x; });
  return ch ? data.slice(0, 10) + out.join('') : data;
}
async function findCapSel(c, code, sold) { /* find a parameterless function in the contract code that looks like a total supply (greater than sold, round hundreds, ≤10 million), prefer 10000 */
  const hex = String(code || '').toLowerCase().replace(/^0x/, ''); const sels = new Set(); for (let i = 0; i < hex.length - 10; i += 2) if (hex.slice(i, i + 2) === '63') sels.add(hex.slice(i + 2, i + 10));
  const cand = []; for (const sel of [...sels].slice(0, 160)) { const r = await call(c, '0x' + sel); if (!r || r.length !== 66) continue; const v = Number(BigInt(r)); if (v > sold && v <= 1e7 && v % 100 === 0) cand.push([sel, v]); }
  if (!cand.length) return ''; cand.sort((x, y) => (y[1] === 10000) - (x[1] === 10000) || x[1] - y[1]); log(`found the total supply function 0x${cand[0][0]} = ${cand[0][1]} for ${c}`); return cand[0][0];
}
async function learnReplay(c) { // learn someone else's recent successful mint: the most common function, how much BNB/USDT to pay, how many wallets minted recently
  const head = parseInt(await rpc('eth_blockNumber', []), 16);
  const L = await logsRpc([{ address: c, fromBlock: '0x' + (head - 1500).toString(16), toBlock: '0x' + head.toString(16) }]).catch(() => rpc('eth_getLogs', [{ address: c, fromBlock: '0x' + (head - 1500).toString(16), toBlock: 'latest' }]).catch(() => [])); /* debug 11/1: event queries on the default node are often rate limited (limit exceeded) → nothing gets learned; switched to multi-node rotation */
  const hs = [...new Set((L || []).map((l) => l.transactionHash))].slice(-40);
  const all = (await Promise.all(hs.map((h) => rpc('eth_getTransactionByHash', [h]).catch(() => null)))).filter((t) => t && t.to && t.to.toLowerCase() === c);
  const txs = all.filter((t) => t.input && t.input.length >= 10 && !badSel(t.input) && !t.input.toLowerCase().includes(t.from.slice(2).toLowerCase()));
  { /* debug 11/1: for contracts like BNBCAT that mint on a plain BNB transfer other people's transactions have no call data, so nothing was learned → not minted even past 55% progress. Now learn "the amount most people send" */
    const plain = all.filter((t) => (t.input || '0x') === '0x' && BigInt(t.value || '0x0') > 0n);
    if (plain.length >= 3 && plain.length >= txs.length) { const vc = {}; for (const t of plain) { const v = BigInt(t.value).toString(); vc[v] = (vc[v] || 0) + 1; } const top = Object.entries(vc).sort((x, y) => y[1] - x[1])[0]; const hot = new Set(plain.filter((t) => parseInt(t.blockNumber, 16) >= head - 400).map((t) => t.from.toLowerCase())).size; return { recv: top[0], recvSeen: top[1], recvVals: Object.keys(vc), hot, learned: 1, learnedAt: Date.now() }; }
  }
  if (!txs.length) return null;
  const cnt = {}; for (const t of txs) cnt[t.input.slice(0, 10)] = (cnt[t.input.slice(0, 10)] || 0) + 1;
  const sel = Object.entries(cnt).sort((a, b) => b[1] - a[1])[0][0];
  const cands = txs.filter((t) => t.input.startsWith(sel)).reverse().sort((a, b) => a.input.length - b.input.length); // prefer learning the simplest one (copying one where someone passed their own NFT id or similar extra args fails)
  for (const pick of cands.slice(0, 3)) {
    const rc = await rpc('eth_getTransactionReceipt', [pick.hash]).catch(() => null); if (!rc || rc.status !== '0x1') continue;
    let payTok = '', payAmt = '0';
    for (const l of rc.logs || []) if (l.topics?.[0] === T_TRANSFER && l.topics.length === 3 && ('0x' + l.topics[1].slice(26)).toLowerCase() === pick.from.toLowerCase()) { payTok = l.address.toLowerCase(); payAmt = BigInt(l.data).toString(); break; }
    const hot = new Set(txs.filter((t) => parseInt(t.blockNumber, 16) >= head - 400).map((t) => t.from.toLowerCase())).size;
    return { data: pick.input, value: BigInt(pick.value).toString(), payTok, payAmt, hot, learned: 1, learnedAt: Date.now() };
  }
  return null;
}
async function repeatDevOk(dep, c, P) {
  const mine = devKey({ c, n: P.name });
  const others = ((S.devs || {})[dep] || []).filter((x) => x.c !== c);
  if (others.some((x) => devKey(x) === mine)) return 'same name redeployed';
  for (const x of others) { // did his earlier projects open a normal pool (pool still funded, nothing drained)
    let tk = x.c; const tr = await call(x.c, '0xfc0c546a'); if (tr && tr.length >= 66 && BigInt(tr.slice(0, 66)) > 0n) tk = '0x' + tr.slice(26, 66);
    for (const q of [WBNB, USDT]) { const pr = await call(FACTORY, '0xe6a43905' + wa(tk) + wa(q)); if (!pr || !(BigInt(pr) > 0n)) continue; const rs = await call('0x' + pr.slice(26), '0x0902f1ac'); if (rs && BigInt('0x' + rs.slice(2, 66)) > 0n && BigInt('0x' + rs.slice(66, 130)) > 0n) return 'an earlier project of his opened a normal pool'; }
  }
  return '';
}
const memoC = new Map(); const cc = async (to, data, ttl = 600e3) => { const k = to + data, m = memoC.get(k); if (m && Date.now() - m.t < ttl) return m.v; const v = await call(to, data); memoC.set(k, { v, t: Date.now() }); if (memoC.size > 20000) memoC.clear(); return v; }; // cache of contract data that rarely changes, to speed the check up
const busyP = new Set(); // handle a project only once at a time (the fast check and the normal check run separately so nothing is minted twice)
async function paidLoop(mode = 'all', shard = null) {
  if (S.umReset !== 3) { S.umReset = 3; for (const P of Object.values(S.paid || {})) { if (P.unmintable) { delete P.unmintable; P.fail = 0; P.cf = 0; if (P.replay?.learned) delete P.replay; } } } // one-off for this version: retry everything marked unmintable before
  S.paid = S.paid || {}; S.dyn = S.dyn || []; S.paidSpend = S.paidSpend || {};
  if (S.missed && !S.missedClean1) { S.missedClean1 = 1; for (const [k, m] of Object.entries(S.missed)) if (String(m.why || '').includes('break-even')) delete S.missed[k]; } /* ones skipped by the rules do not count as missed */
  if (S.agentsV !== 2) { S.agentsV = 2; for (const P of Object.values(S.paid || {})) { delete P.agents; delete P.heatAt; } } /* anti-reverse-engineering 11/1: the participant count now only counts wallets that sent transactions, clear the old (possibly airdrop-inflated) numbers and recount */
  if (S.refundReset !== 1) { S.refundReset = 1; for (const P of Object.values(S.paid)) { delete P.refundOk; delete P.noRefundLog; } } /* debug 11/1: after fixing proxy resolution, decide once more whether a refund mechanism exists */
  const today = new Date().toDateString(); if (S.paidSpend.day !== today) S.paidSpend = { day: today };
  let price = 0;
  if (!S.forceDone) S.forceDone = { '0xc7d563a7bd5af3de83567a67058b3eb9f285149e': 1 }; // VARC was already minted in the morning
  for (const c of FORCE) if (S.forceDone[c] && S.paid[c]) delete S.paid[c];
  for (const c of FORCE) if (!S.paid[c] && !S.forceDone[c]) S.paid[c] = { at: Date.now(), name: '', done: {} }; // fix review: a manually specified project is not re-added for another mint once done
  if (!S.migrated2) { S.migrated2 = 1; for (const c of Object.keys(S.seenMints || {})) { if (S.paid[c]) continue; const cd = await codeOf(c); if ((cd.includes('e38fd349') && cd.includes('68bce8cd')) || (cd.includes('725c4058') && cd.includes('94bf804d'))) { S.paid[c] = { at: Date.now(), name: '', done: {} }; log('back-filled paid tracking ' + c); } } }
  // anything at ≥40% progress (nearly full) is checked every round instead of queueing with the other hundred-plus; the rest take turns, 16 per round
  const all = Object.entries(S.paid);
  
  const hotP = all.filter(([c, P]) => FORCE.has(c) || (((P.progress || 0) >= 0.45 || (fpCache.get(c)?.p || 0) >= 0.45 || (!(P.progress > 0) && Math.max(P.agents || 0, HOT.get(c)?.from.size || 0) >= 70)) && (P.progress || 0) < 0.999 && !P.unmintable)).filter(([c, P]) => !WALLETS.every((W) => P.done[W.name])).sort((a, b) => (b[1].progress || 0) - (a[1].progress || 0) || (b[1].agents || 0) - (a[1].agents || 0)).slice(0, 12); /* user 10/31 20:29: the fast check only looks at ones near the threshold (progress ≥45%, or no readable progress but 70+ wallets already), max 12, so it does not keep slowing down */
  // the fast check (mode='hot') only looks at nearly full / very hot ones every 15 s; the normal check (mode='rest') cycles the rest in the background about once a minute, they do not slow each other down
  const rest = mode === 'hot' ? [] : all.filter((e) => !hotP.includes(e)).sort((a, b) => (a[1].lastTry || 0) - (b[1].lastTry || 0)).slice(0, 16);
  const ents = mode === 'rest' ? rest : mode === 'hot' ? (shard == null ? hotP : hotP.filter((e, i) => i % 3 === shard)) : hotP.concat(rest); /* run the fast check in 3 parallel shards (fix review: one at a time takes over a minute) */
  for (const [c, P] of ents) {
    if (INFRA.has(c)) { delete S.paid[c]; continue; } // the registry contract itself is not a project (it registers .ai identities), not minted
    if (busyP.has(c)) continue; busyP.add(c); const tE = Date.now();
    try {
    P.lastTry = Date.now(); if (WALLETS.every((W) => P.done[W.name])) { if (FORCE.has(c)) { S.forceDone = S.forceDone || {}; S.forceDone[c] = 1; } delete S.paid[c]; continue; }
    if (Date.now() - P.at > PAID.maxAgeH * 3600e3) { delete S.paid[c]; continue; }
    const code = await codeOf(c);
    // "free" mints that were not open at discovery: retry every minute and mint as soon as it opens
    const fcd = (P.progress || 0) >= GO_PCT - 0.02 || Math.max(P.agents || 0, HOT.get(c)?.from.size || 0) >= 70 ? 20e3 : 120e3; /* debug 11/1: free projects near 60% retry every 20 s (it used to be a fixed 2 minutes and 0xa69adf sold out from 63% within those 2 minutes) */
    if (Date.now() - (P.freeTryAt || 0) > fcd && !((P.progress || 0) > 0 && P.progress < GO_PCT - 0.02 && Date.now() - (P.freeTryAt || 0) < 600e3)) { /* optimisation 11/1: a free project with readable progress below 58% does not need simulating every 2 minutes (850 wasted runs a night, plus node rate limits); check every 10 minutes and only retry densely near the threshold */ P.freeTryAt = Date.now(); for (const W of WALLETS) { if (P.done[W.name]) continue; { const ff = await findFree(W.addr, c, code); if (!ff || P.qAt?.[W.name] > Date.now() - (fcd - 5e3)) continue; P.qAt = P.qAt || {}; P.qAt[W.name] = Date.now(); queueMint(c, ff.n, W, ff.v); log(`[${W.name}] project ${P.name || c} is open now (free), checked by the rules (free ones only notify, no auto-mint)`); } } }
    let kind = code.includes('e38fd349') && code.includes('68bce8cd') ? 'bnb' : code.includes('725c4058') && code.includes('94bf804d') ? 'usdt' : null;
    let unit = 0n; // generic NFT: mint(quantity) pays "unit price × quantity" BNB (like Sentient Syndicate's PRICE)
    if (!kind && code.includes('a0712d68')) for (const sel of ['8d859f3e', 'a035b1fe', '6817c76c', '13faede6', '817b1cd2']) { if (!code.includes(sel)) continue; const r = await cc(c, '0x' + sel); if (r && r.length >= 66 && BigInt(r.slice(0, 66)) > 0n) { unit = BigInt(r.slice(0, 66)); kind = 'gen'; break; } }
    if (!kind && P.recvValue) kind = 'recv';
    if (P.replay && badSel(P.replay.data)) delete P.replay;
    if (!kind && P.replay?.data) kind = 'replay'; // a rare mint function: copy someone else's call data and value
    
    if (!kind || kind === 'replay') P.needLearn = 1; // a contract we cannot read: leave it to the background heat monitor to learn other people's method, do not wait here
    if (!kind) continue;
    if (P.unmintable && P.unmintableAt && Date.now() - P.unmintableAt > 30 * 60e3 && (P.umN || 0) < 2) { P.umN = (P.umN || 0) + 1; delete P.unmintable; P.fail = 0; P.cf = 0; } // retry after 30 minutes, at most twice more (so a USDT project does not swap USDT and fail every half hour)
    if (P.unmintable && !FORCE.has(c)) continue;
    let soldR = await call(c, kind === 'bnb' ? '0xa2309ff8' : kind === 'gen' ? '0x18160ddd' : '0x192fc516');
    if (!(soldR && soldR.length >= 66) && (kind === 'replay' || kind === 'recv')) { if (P.soldSel) soldR = await call(c, P.soldSel); else for (const sel of ['0x02c7e7af', '0xa2309ff8', '0x9106d7ba', '0xf4514d2e', '0x505e05fd', '0x3a98ef39']) { const r = await cc(c, sel, 60e3); if (r && r.length >= 66) { soldR = r; P.soldSel = sel; break; } } }
    const sold = soldR && soldR.length >= 66 ? Number(BigInt(soldR.slice(0, 66))) : null;
    let cap = null; { const r = await cc(c, '0xa0e23ebd'); if (r && r.length >= 66 && sold != null) { const x = Number(BigInt(r.slice(0, 66))); if (x >= 1000 && x <= 1e7 && x % 100 === 0) cap = Math.max(x, sold); } } if (!cap) for (const sel of ['0xd8f93498', '0xcef6feda', '0x29b80536', '0xd5abeb01', '0x32cb6b0c', '0x355274ea', '0x3a1d8d3a', '0xf1aee5a2', '0x85e3f997']) { const r = await cc(c, sel); if (r && r.length >= 66) { const x = Number(BigInt(r.slice(0, 66))); if (x > 0 && sold != null && x >= sold && x < 1e12) { cap = x; break; } } }
    if (!cap && sold != null && sold > 0) { /* review 11/1: the bibiai launchpad's cap function is not in the common list (a0e23ebd=10000) → progress unreadable → it would buy at 4% as "unreadable + many minting"; now find the cap function in the contract automatically and remember it */
      if (P.capSel === undefined) P.capSel = await findCapSel(c, code, sold).catch(() => '') || '';
      if (P.capSel) { const r = await cc(c, '0x' + P.capSel, 600e3); if (r && r.length >= 66) { const x = Number(BigInt(r.slice(0, 66))); if (x > 0 && x < 1e9) cap = Math.max(x, sold); } } /* sold above the cap = sold out (AID read as 0.23% when the sales reached 10246/10000) */
    }
    let tokAddr0 = ''; { const tr0 = await cc(c, '0xfc0c546a', 3600e3); if (tr0 && tr0.length >= 66 && BigInt(tr0.slice(0, 66)) > 0n) tokAddr0 = '0x' + tr0.slice(26, 66); }
    let progress = cap && sold != null ? sold / cap : 0, how = 'sold/cap';
    if (!(progress > 0)) { const pr = await progressOf(c, code, tokAddr0); progress = pr.progress || 0; how = pr.how || ''; }
    /* user 10/31 19:02: sanity check on progress (Binance Ren read the cap 100× too large, so 32% progress read as 0.3% and we missed what others minted): a very low progress with many minters → judged a misread, switch to the stock algorithm; still implausible → treat as unreadable and leave it to the heat rule */
    if (progress > 0 && progress < 0.03 && !(how === 'sold/cap' && cap && (kind === 'bnb' && cap === 10000 || ((P.agents || 0) >= 20 && cap / Math.max(P.agents || 1, 1) <= 1000))) /* review 11/1: when the cap is plausible relative to the participant count (≤1000 units per person on average) trust "sold/cap", only a cap too large to be plausible (like Binance Ren reading 100× too big) is judged a misread */ /* debug 11/1: BNB-priced launches open their pool at a fixed 10,000 units and "sold/10,000" is accurate; BNLAI at 201 sold = 2% was misjudged as a misread and buying it as "unreadable + many minting" cost 14U */ && ((P.agents || 0) >= 50 || (sold || 0) >= 1000 || (HOT.get(c)?.from.size || 0) >= 15)) {
      const pr = await progressOf(c, code, tokAddr0).catch(() => ({}));
      if (pr.progress >= 0.03) { progress = pr.progress; how = pr.how || 'stock'; }
      else { if (!P.progBad) { P.progBad = 1; log(`progress looks misread: ${c} read ${(progress * 100).toFixed(2)}% but ${P.agents || 0} agents are already minting, judging by the heat rule instead`); } progress = 0; how = 'unreadable (implausible number)'; }
    }
    const prevProg = P.progress; P.progress = progress; P.progAt = Date.now();
    if (progress >= 0.95 && !P.noRefundLog && !P.serialLog && !String(P.lastReason || '').includes('break-even') && !WALLETS.some((W) => P.done[W.name] && !['pool-open', 'fail', 'unknown'].includes(P.done[W.name]))) { /* ones skipped by the rules (no break-even) do not count as missed */ S.missed = S.missed || {}; if (!S.missed[c]) { S.missed[c] = { name: P.name || c.slice(0, 10), at: ts(), why: P.lastReason || (P.unmintable ? 'cannot mint' : 'no way to mint found yet') }; log(`missed ${c} (${S.missed[c].name}): progress already ${Math.round(progress * 100)}% and we did not mint it, reason: ${S.missed[c].why}`); } } /* make ourselves smarter: record the projects that almost sold out but were not minted and why, listed in the nightly summary so the gaps are easier to find */
    if (progress >= 0.7 && progress <= 1 && (prevProg != null ? prevProg < 0.7 : Date.now() - P.at < 6 * 3600e3)) groupProgress(c, P.name, progress).catch(() => {});
    { const now = Date.now(); P.ph = (P.ph || []).filter((x) => now - x.t < 15 * 60e3); if (!P.ph.length || now - P.ph[P.ph.length - 1].t > 10e3) P.ph.push({ t: now, p: progress }); }
    price = price || (await bnbUsd());
    const forced = FORCE.has(c);
    
    const tier = heatTier(P), needPct = GO_PCT; /* user 11/1 02:37: everything back to minting only above 60% progress (paid also needs a break-even mechanism) */
    const crowdGo = !(progress > 0) && (P.agents || 0) >= AG_MIN; /* review 11/1: count only wallets holding an Agent ID (the heat list includes plain addresses that bots may inflate) */ /* user 10/31 19:45: unreadable progress → mint when 100+ wallets are minting */
    if (!forced && !((progress >= needPct && (P.agents || 0) >= AG_MIN) || crowdGo)) continue; /* reverse check 11/1: this used to look at progress only; when progress was enough but the wallet count was below 150 it fell through to the old rules below (mint on 20 wallets), bypassing the 150 rule */
    if (!forced) { const sd = await serialDev(c); if (sd) { if (!P.serialLog) { P.serialLog = 1; P.lastReason = sd + ', not minted by the rules'; if (S.missed?.[c]) delete S.missed[c]; log(`skipping ${P.name || c}: ${sd}, not minted by the rules`); } continue; } }
    { /* user 11/1 01:56: the risk grew — a paid project must have a break-even mechanism (refund if it does not sell out), otherwise skip. Check whether the contract (including the implementation behind a proxy) has a refund function */
      const paying = !(kind === 'replay' && !(BigInt(P.replay?.value || '0') > 0n) && !P.replay?.payTok);
      if (!forced && !paying) { await freeNotify(c, P.name, progress > 0 ? progress : null, P.agents).catch(() => {}); for (const W of WALLETS) P.done[W.name] = P.done[W.name] || 'free-notify'; continue; } /* user 11/1 15:56: free ones only notify, never auto-mint */
      if (!forced && paying && P.refundOk == null) { const fc = await fullCode(c, code).catch(() => code); P.refundOk = REFUND_SELS.some((x) => fc.includes(x)) ? 1 : 0; } /* a proxy contract has to be judged by the implementation behind it */
      if (!forced && paying && !P.refundOk) { if (!P.noRefundLog) { P.noRefundLog = 1; P.lastReason = 'no break-even (refund) mechanism, not minted by the rules'; if (S.missed?.[c]) delete S.missed[c]; log(`skipping ${P.name || c}: the paid project has no break-even (refund) mechanism, not minted by the rules`); } continue; }
      if (!forced && paying && await upgradeable(c).catch(() => false)) { if (!P.upgLog) { P.upgLog = 1; P.lastReason = 'upgradeable contract, the break-even mechanism could be swapped out, not minted per risk control'; log(`skipping ${P.name || c}: upgradeable proxy contract (the team can swap the break-even mechanism afterwards), not minted per risk control`); } continue; }
    }
    const gate = forced || (progress >= GO_PCT && (P.agents || 0) >= AG_MIN) || crowdGo; /* user 11/1 14:48: enough progress is not enough, many Agent ID wallets must be minting too */ /* above 50% progress: none of the earlier anti-phishing rules apply (only honeypot-type safety checks stay: simulation, approval, assets, Flap) */
    if (!forced && !P.goLogged) { P.goLogged = 1; globalThis.xCheck?.(c, P.name).catch(() => {}); ownerHeavy(c, P.name).catch(() => {}); log(`entry ${c}: ${progress >= GO_PCT ? 'progress above ' + Math.round(GO_PCT * 100) + '%' : 'unreadable progress, ' + AG_MIN + '+ wallets minting'} (${P.agents || 0} agents cumulative, ${P.burst || 0} within 3 minutes), progress ${progress > 0 ? Math.round(progress * 100) + '%' : 'unreadable'}`); }
    // anti-phishing: ① the same code deployed 3+ times within 24 hours (batch launch) is not minted ② progress must be produced by many wallets (at least 20 different ones), not inflated by the team
    if (!gate) { const dep = P.deployer !== undefined ? P.deployer : await deployerOf(c); P.deployer = dep;
      if (dep && !(S.devs?.[dep] || []).some((x) => x.c === c)) noteDev(dep, c);
      if (dep && S.black?.[dep]) { if (!P.blackLogged) { P.blackLogged = 1; log(`skipping ${c}: deployer ${dep} is blacklisted (${S.black[dep].why})`); } continue; }
      if (dep && P.fresh == null) P.fresh = await freshWallet(dep);
      if (P.fresh) { if (!P.freshLogged) { P.freshLogged = 1; log(`skipping ${c}: deployer ${dep} is a brand-new wallet`); } continue; }
      // money flow: almost no BNB left in the contract and no pool yet → the money most likely went straight to the team wallet, so require more minters (40) before trusting it
      if (P.drain == null) { const cb = Number(BigInt((await rpc('eth_getBalance', [c, 'latest']).catch(() => '0x0')) || '0x0')) / 1e18; const pr = tokAddr0 || c; const pair = await call(FACTORY, '0xe6a43905' + wa(pr) + wa(WBNB)); P.drain = cb < 0.01 && !(pair && BigInt(pair) > 0n); }
      
      P.needW = 0;
      if (isRepeatDev(dep)) {
        if (P.repeatOk == null || Date.now() - (P.repeatAt || 0) > 600e3) { P.repeatOk = await repeatDevOk(dep, c, P); P.repeatAt = Date.now(); }
        if (!P.repeatOk) { if (P.drain) { if (!P.devLogged) { P.devLogged = 1; log(`skipping ${c}: repeat DEV ${dep}, no pool history and the money went straight out`); } continue; } P.needW = 40; }
        else if (!P.devLogged) { P.devLogged = 1; log(`repeat DEV ${dep} but minting anyway ${c} (${P.repeatOk})`); }
      }
    }
    // user-defined: only new ones → a contract deployed two days ago is not minted
    if (!gate) { if (P.old == null) { const head = parseInt(await rpc('eth_blockNumber', []), 16); const o = await rpc('eth_getCode', [c, '0x' + (head - 400000).toString(16)]).catch(() => '0x'); P.old = !!o && o.length > 2; } if (P.old) { for (const W of WALLETS) P.done[W.name] = P.done[W.name] || 'old'; continue; } }
    // projects whose pool already trades are no longer auto-minted (minting ≈ buying at market price, not worth it)
    if (!forced && !(progress >= GO_PCT)) { const tk = tokAddr0 || c; if (await poolOpen(tk)) { if (!P.poolSkipLog) { P.poolSkipLog = 1; log(`skipping ${c}: the pool already trades, without readable progress nothing is minted after the pool opens (it still mints once progress reads ≥80%)`); } continue; } } /* review 11/1: it used to mark done and delete, so a single misread meant never minting again */
    if (!gate && isSerial(code)) { if (!P.serialLogged) { P.serialLogged = 1; log(`skipping ${c}: the same code deployed 3+ times within 24 hours, likely a batch launch`); } continue; }
    if (!gate) { const need = Math.max(P.drain ? 40 : 20, P.needW || 0); const seen = Math.max(P.wallets || 0, HOT.get(c)?.from.size || 0, P.agents || 0); /* the Agent ID wallet count from the heat calculation counts too (USDT mints and projects that lock the coins first have no transfer records to count) */ if (seen < need) { if (Date.now() - (P.wAt || 0) < 60e3) continue; P.wAt = Date.now(); const r = await rpc('eth_getLogs', [{ address: tokAddr0 || c, fromBlock: '0x' + (parseInt(await rpc('eth_blockNumber', []), 16) - 3000).toString(16), toBlock: 'latest', topics: [T_TRANSFER] }]).catch(() => []); const uniq = new Set((r || []).map((l) => l.topics[2])).size; P.wallets = Math.max(P.wallets || 0, uniq); if (uniq < need) continue; } } // not at 80% yet (or progress unreadable) → don't mint yet, look again every minute
    const limitUsd = Infinity;
    let tokAddr = ''; const tr = await cc(c, '0xfc0c546a', 3600e3); if (tr && tr.length >= 66) tokAddr = '0x' + tr.slice(26, 66);
    const sym = tokAddr ? tokStr(await cc(tokAddr, '0x95d89b41', 3600e3)) || P.name || c.slice(0, 8) : P.name || c.slice(0, 8);
    await Promise.all(WALLETS.map(async (W) => { 
      if (P.done[W.name]) return;
      if (!forced) { const rb = await riskBlock(W, c, 0n, '0x', false, true).catch(() => ''); if (rb) { if (P.riskLog !== rb) { P.riskLog = rb; log(`[${W.name}] risk control paused ${P.name || c}: ${rb}`); } return; } }
      try {
        price = price || (await bnbUsd());
        const spent = S.paidSpend[W.name] || 0;
        const bal = Number(BigInt((await rpc('eth_getBalance', [W.addr, 'latest'])) || '0x0')) / 1e18;
        let n = 10, costWei = 0n;
        if (kind === 'replay' && P.replay.payTok) { // a mint paid in USDT (like BITO): swap USDT → approve → copy the other person's call, scaling units up to at most 10 within budget
          if (P.replay.payTok !== USDT) { if (!P.payTokLogged) { P.payTokLogged = 1; log(`skipping ${c}: the payment token is not USDT (${P.replay.payTok})`); } return; }
          if (Date.now() - (P.replay.learnedAt || 0) > 90e3) { const lr = await learnReplay(c).catch(() => null); if (lr) P.replay = { ...P.replay, ...lr }; }
          let data = freshDeadline(P.replay.data), need = BigInt(P.replay.payAmt || '0'); const ws = data.slice(10).match(/.{64}/g) || [];
          if (ws.length === 3 && need > 0n) { const n0 = BigInt('0x' + ws[0]), mx = BigInt('0x' + ws[2]); if (n0 > 0n && n0 <= 100n && mx >= need && mx <= (need * 12n) / 10n) { const unit = need / n0; let nn = 10n; while (nn > 1n && (Number(unit * nn) / 1e18) * 1.05 > PAID.maxUsd) nn--; const mp = (unit * nn * 105n) / 100n; data = data.slice(0, 10) + w(nn) + ws[1] + w(mp); need = mp; } }
          if (!(need > 0n) || Number(need) / 1e18 > PAID.maxUsd) return;
          let ub = big(await call(USDT, '0x70a08231' + wa(W.addr)));
          if (ub < need) { const nb = ((Number(need - ub) / 1e18) * 1.04) / price; if (nb > bal - PAID.keepBnb) return; const sw = await W.baw(['market-order', 'swap', '--binanceChainId', '56', '--fromTokenQty', nb.toFixed(6), '--fromToken', NATIVE_BNB, '--toToken', USDT]); if (!sw.j?.success) throw new Error('BNB → USDT swap failed ' + (sw.j?.error?.message || sw.t).slice(0, 100)); for (let i = 0; i < 30 && ub < need; i++) { await sleep(3000); ub = big(await call(USDT, '0x70a08231' + wa(W.addr))); } if (ub < need) throw new Error('USDT did not arrive'); }
          await previewExec(W, USDT, '0x095ea7b3' + wa(c) + w(need), 0n, (sim) => ((sim?.allowanceChanges || []).some((a) => String(a.spender).toLowerCase() !== c) ? 'wrong approval target' : ''));
          const v = BigInt(P.replay.value || '0'); if (v > 0n && ((Number(v) / 1e18) * 1.005 > bal - PAID.keepBnb || Number(need) / 1e18 + (Number(v) / 1e18) * price > PAID.maxUsd)) return; /* fix review: when BNB must be attached alongside the USDT, that counts against the budget too */
          const r = await revokeOnFail(W, USDT, c, async () => { try { data = await safeAddrs(W, c, data, v); data = await fitShares(W, c, data, v); } catch (e) { if (/quota|used up|sold out/.test(e.message)) P.done[W.name] = 'full'; else noteFail(c, P, sym, progress); throw e; } return previewExec(W, c, data, v, (sim) => {
            const mine = (sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr);
            if (sim?.authorityChanges?.length) return 'permission change';
            const out = mine.filter((b) => Number(b.change) < 0); if (out.some((b) => { const a = String(b.contractAddress).toLowerCase(); return a !== USDT && !a.startsWith('0xeeee'); })) return 'would deduct assets other than USDT/BNB';
            if (out.filter((b) => String(b.contractAddress).toLowerCase() === USDT).reduce((a, b) => a - Number(b.change), 0) > Number(need) * 1.01) return 'USDT deducted exceeds the budget'; return '';
          }); });
          const paidU = (r.sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr && String(b.contractAddress).toLowerCase() === USDT && Number(b.change) < 0).reduce((a, b) => a - Number(b.change), 0) / 1e18; const usd = paidU > 0 ? paidU : Number(need) / 1e18; S.paidSpend[W.name] = spent + usd / price; P.done[W.name] = 'minted'; /* record the USDT actually deducted */
          if (paidU > 0 && paidU < (Number(need) / 1e18) * 0.99) previewExec(W, USDT, '0x095ea7b3' + wa(c) + w(0n), 0n, () => '').catch(() => {}); /* revoke the unused part of the approval */
          if (tokAddr && !S.dyn.some((t) => t.addr === tokAddr.toLowerCase() && t.only === W.addr)) S.dyn.push({ sym, addr: tokAddr.toLowerCase(), cost: +usd.toFixed(2), only: W.addr, deployer: P.deployer, boughtAt: Date.now() });
          await tg(`🛒 [${W.name}] auto-minted a paid Agent ID project: ${sym} (copying someone else's method, paid in USDT)\nspent ~${usd.toFixed(2)} USDT · ${progress > 0 ? 'progress ~' + Math.round(progress * 100) + '%' : 'last ' + (P.replay.hot || 0) + ' wallets minting'}\n${c}\ntx ${r.tx}`);
          return;
        }
        if (kind === 'replay') {
          if (Date.now() - (P.replay.learnedAt || 0) > 90e3) { const lr = await learnReplay(c).catch(() => null); if (lr) P.replay = { ...P.replay, ...lr }; } /* use the newest transaction from someone else (the price arguments must be fresh) */
          let v = BigInt(P.replay.value || '0'); let vb = Number(v) / 1e18;
          if (price && vb * price > PAID.maxUsd && P.replay.data) { /* review 11/1: when the learned amount exceeds 20U, if the first argument is a unit count scale the count and the amount down proportionally instead of giving up right away */
            const w0 = P.replay.data.slice(10, 74); const n0 = w0.length === 64 ? Number(BigInt('0x' + w0)) : 0;
            if (n0 >= 2 && n0 <= 100) { const n1 = Math.floor((PAID.maxUsd / (vb * price)) * n0); if (n1 >= 1) { v = (v * BigInt(n1)) / BigInt(n0); vb = Number(v) / 1e18; P.replay = { ...P.replay, data: P.replay.data.slice(0, 10) + n1.toString(16).padStart(64, '0') + P.replay.data.slice(74), value: v.toString() }; log(`copied amount over budget, units ${n0}→${n1}: ${c}`); } }
          }
          if (!price || vb * price > PAID.maxUsd || vb * 1.005 > bal - PAID.keepBnb) return;
          let rdata = freshDeadline(P.replay.data); try { rdata = await safeAddrs(W, c, rdata, v); const rd0 = rdata; rdata = await fitShares(W, c, rdata, v); v = scaleV(rd0, rdata, v); vb = Number(v) / 1e18; } catch (e) { if (/quota|used up|sold out/.test(e?.message || '')) P.done[W.name] = 'full'; else noteFail(c, P, sym, progress); return; } /* fix review: a copied BNB payment must also swap the other person's address and adjust the unit count automatically */
          { /* user 10/31 19:16 "optimise with this logic": do not give up instantly when a copy fails — take the newest sample first, then retry with "price + 2%" (only when the transaction content carries a limit equal to the payment amount) */
            const ok = async (d, val) => { try { await rpc('eth_call', [{ from: W.addr, to: c, data: d, value: '0x' + val.toString(16) }, 'latest']); return true; } catch { return false; } };
            let good = await ok(rdata, v);
            if (!good) { const lr = await learnReplay(c).catch(() => null); if (lr?.data) { P.replay = { ...P.replay, ...lr }; const v2 = BigInt(lr.value || '0'); let d2 = freshDeadline(lr.data); let v2s = v2; try { d2 = await safeAddrs(W, c, d2, v2); const dd = d2; d2 = await fitShares(W, c, d2, v2); v2s = scaleV(dd, d2, v2); } catch {} if ((Number(v2s) / 1e18) * price <= PAID.maxUsd && (Number(v2s) / 1e18) * 1.005 <= bal - PAID.keepBnb && await ok(d2, v2s)) /* review 11/1: a re-learned amount must also fit the 20U budget and the balance */ { rdata = d2; v = v2s; vb = Number(v) / 1e18; good = true; } } }
            if (!good && v > 0n) for (const pct of [102n, 105n, 110n, 120n, 135n]) { /* debug 11/1: AID's price went from 0.0204 to 0.022 (+8%) and only +2% was tried → the contract reverted with MAX_COST; now try +2% to +35% while the amount stays within the 20U budget and the balance */
              const vU = (v * pct) / 100n; if ((Number(vU) / 1e18) * price > PAID.maxUsd || (Number(vU) / 1e18) * 1.005 > bal - PAID.keepBnb) break;
              const ws = rdata.slice(10).match(/.{64}/g) || []; let hit = 0; const d3 = rdata.slice(0, 10) + ws.map((x) => { const y = BigInt('0x' + x); if (y > 0n && y * 1000n >= v * 995n && y * 1000n <= v * 1005n) { hit = 1; return vU.toString(16).padStart(64, '0'); } return x; }).join('');
              if (await ok(hit ? d3 : rdata, vU)) { rdata = hit ? d3 : rdata; v = vU; vb = Number(v) / 1e18; good = true; log(`copy only passed at amount +${pct - 100n}%: ${c}`); P.replay = { ...P.replay, data: rdata, value: v.toString() }; break; }
            }
            if (!good) { noteFail(c, P, sym, progress); return; } /* review 11/1: one failure used to mark it unmintable → now accumulate 8 */
          }
          const r = await previewExec(W, c, rdata, v, (sim) => {
            const mine = (sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr);
            if (sim?.allowanceChanges?.length || sim?.authorityChanges?.length) return 'approval change';
            const out = mine.filter((b) => Number(b.change) < 0); if (out.some((b) => !String(b.contractAddress).toLowerCase().startsWith('0xeeee')) || out.reduce((a, b) => a - Number(b.change), 0) > Number(v) * 1.01) return 'would deduct something other than BNB or more than the amount';
            if (!mine.some((b) => Number(b.change) > 0)) return 'simulation received nothing'; return '';
          });
          S.paidSpend[W.name] = spent + vb; P.done[W.name] = 'minted';
          if (!(v > 0n)) { trackFromSim(W, r.sim, sym, 0); noteFree(c, W, sym); return; }
          trackFromSim(W, r.sim, sym, vb * price); 
          await tg(`🛒 [${W.name}] auto-minted a paid Agent ID project: ${sym} (copying someone else's mint)\nspent ${vb.toFixed(4)} BNB (~${(vb * price).toFixed(1)}U) · progress ~${Math.round(progress * 100)}%\n${c}\ntx ${r.tx}`);
          return;
        }
        if (kind === 'recv') {
          let v = BigInt(P.recvValue);
          if (price && (Number(v) / 1e18) * price > PAID.maxUsd) { /* debug 11/1: when the amount most people send exceeds the 20U budget (BNBCAT 10 units = 0.03 BNB ≈ 21U), use the largest amount others sent that fits the budget instead of giving up */
            const ok = (P.recvVals || []).map((x) => BigInt(x)).filter((x) => (Number(x) / 1e18) * price <= PAID.maxUsd).sort((x, y) => (x > y ? -1 : 1)); if (ok.length) v = ok[0]; }
          const vb = Number(v) / 1e18;
          if (!price || vb * price > PAID.maxUsd || vb * 1.005 > bal - PAID.keepBnb) return;
          try { await rpc('eth_call', [{ from: W.addr, to: c, value: '0x' + v.toString(16) }, 'latest']); } catch { noteFail(c, P, sym, progress); if ((P.cf || 0) >= 3) { delete P.recvValue; delete P.replay; P.needLearn = 1; } return; } /* review 11/1: the BNB amount may have been learned wrong, re-learn after a few failures */ // cannot mint (not eligible, already minted, quota full)
          const r = await previewExec(W, c, '0x', v, (sim) => {
            const mine = (sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr);
            if (sim?.allowanceChanges?.length || sim?.authorityChanges?.length) return 'approval change';
            const out = mine.filter((b) => Number(b.change) < 0); if (out.some((b) => !String(b.contractAddress).toLowerCase().startsWith('0xeeee')) || out.reduce((a, b) => a - Number(b.change), 0) > Number(v) * 1.01) return 'would deduct something other than BNB or more than the amount';
            if (!mine.some((b) => Number(b.change) > 0)) return 'simulation received no tokens'; return '';
          });
          S.paidSpend[W.name] = spent + vb; P.done[W.name] = 'minted';
          const ta = tokAddr0 || c; if (!S.dyn.some((t) => t.addr === ta && t.only === W.addr)) S.dyn.push({ sym, addr: ta, cost: +(vb * price).toFixed(2), only: W.addr, deployer: P.deployer, boughtAt: Date.now() });
          await tg(`🛒 [${W.name}] auto-minted a "send BNB to mint" project: ${sym}\nsent ${vb} BNB (~${(vb * price).toFixed(1)}U, ${P.recvSeen} wallets sent this amount) · progress ~${Math.round(progress * 100)}% (${how})\n${c}\ntx ${r.tx}`);
          return;
        }
        if (kind === 'gen') {
          let data = '';
          for (; n >= 1; n--) { costWei = unit * BigInt(n); if ((Number(costWei) / 1e18) * 1.005 > bal - PAID.keepBnb || !price || (Number(costWei) / 1e18) * price > PAID.maxUsd) continue; data = '0xa0712d68' + w(n); try { await rpc('eth_call', [{ from: W.addr, to: c, data, value: '0x' + costWei.toString(16) }, 'latest']); break; } catch {} }
          if (n < 1) { noteFail(c, P, sym, progress); return; }
          const r = await previewExec(W, c, data, costWei, (sim) => {
            const mine = (sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr);
            if (sim?.allowanceChanges?.length || sim?.authorityChanges?.length) return 'approval change';
            const out = mine.filter((b) => Number(b.change) < 0); if (out.some((b) => !String(b.contractAddress).toLowerCase().startsWith('0xeeee')) || out.reduce((a, b) => a - Number(b.change), 0) > Number(costWei) * 1.01) return 'would deduct something other than BNB or more than the price';
            if (!mine.some((b) => Number(b.change) > 0)) return 'simulation received nothing'; return '';
          });
          const vb = Number(costWei) / 1e18; S.paidSpend[W.name] = spent + vb; P.done[W.name] = 'minted';
          trackFromSim(W, r.sim, sym, vb * price);
          await tg(`🛒 [${W.name}] auto-minted a paid Agent ID project: ${sym}\n${n} units, spent ${vb.toFixed(4)} BNB (~${(vb * price).toFixed(1)}U) · progress ~${Math.round(progress * 100)}% (${sold ?? '?'}/${cap ?? '?'})\n${c}\ntx ${r.tx}`);
          return;
        }
        if (kind === 'bnb') {
          for (; n >= 1; n--) { const q = await call(c, '0x68bce8cd' + w(n)); costWei = q ? BigInt(q.slice(0, 66)) : 0n; if (costWei > 0n && (Number(costWei) / 1e18) * 1.005 <= bal - PAID.keepBnb && price && (Number(costWei) / 1e18) * 1.005 * price <= PAID.maxUsd) break; } // the most units affordable (keeping gas)
          if (n < 1) { P.lastReason = 'each unit is too expensive, over the 20U budget'; return; } // too expensive: don't mint yet (above 85% progress the cap widens to 25U and it is checked again)
          const v = (costWei * 1005n) / 1000n, vb = Number(v) / 1e18;
          if (spent + vb > PAID.maxBnbPerDay || bal - vb < PAID.keepBnb) return;
          const data = '0xe38fd349' + w(n) + wa('0x0000000000000000000000000000000000000000') + w(v);
          try { await rpc('eth_call', [{ from: W.addr, to: c, data, value: '0x' + v.toString(16) }, 'latest']); } catch { noteFail(c, P, sym, progress); return; } // not open yet or not eligible
          const pool = tokAddr ? Number(await poolOut(v, WBNB, tokAddr)) : 0;
          const r = await previewExec(W, c, data, v, (sim) => {
            if (pool > 0 && gotTok(sim, W) < pool * 1.1) log(`note: ${c} pool already opened, minting is not cheaper than the pool (new rule: mint anyway at ≥80% progress)`);
            const mine = (sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr);
            if (sim?.allowanceChanges?.length || sim?.authorityChanges?.length) return 'approval change';
            const out = mine.filter((b) => Number(b.change) < 0); if (out.some((b) => !String(b.contractAddress).toLowerCase().startsWith('0xeeee')) || out.reduce((a, b) => a - Number(b.change), 0) > Number(v) * 1.01) return 'would deduct assets other than BNB or more than the quote';
            if (!mine.some((b) => Number(b.change) > 0)) return 'simulation received no tokens'; return '';
          }, async () => { const q2 = await call(c, '0x68bce8cd' + w(n)); const c2 = q2 ? BigInt(q2.slice(0, 66)) : 0n; return c2 > (costWei * 105n) / 100n ? 'price jumped more than 5% just now' : ''; }); /* new feature: check the price once more before sending */
          S.paidSpend[W.name] = spent + vb; P.done[W.name] = 'minted';
          const usd = vb * price; const tka = tokAddr && !/^0x0{40}$/i.test(tokAddr) ? tokAddr : tokAddr0; if (!tka) trackFromSim(W, r.sim, sym, usd); /* review 11/1: without a token() on the contract the position used to never be added and no sell rule ever ran */ if (tka && !S.dyn.some((x) => x.addr === tka.toLowerCase() && x.only === W.addr)) S.dyn.push({ sym, addr: tka.toLowerCase(), cost: +usd.toFixed(2), only: W.addr, deployer: P.deployer, boughtAt: Date.now(), ...(FORCE.has(c) ? { hold: true } : {}) }); // a user-specified project is handled like a free coin: notify only, never auto-sell
          await tg(`🛒 [${W.name}] auto-minted a paid Agent ID project: ${sym}\n${n} units, spent ${vb.toFixed(4)} BNB (~${usd.toFixed(1)}U) · ${sold ?? '?'} sold · progress ~${Math.round(progress * 100)}%\n${c}\ntx ${r.tx}\nnow tracked: ${ruleTxt()}`);
        } else {
          for (; n >= 1; n--) { const q = await call(c, '0x725c4058' + w(n)); costWei = q ? BigInt(q.slice(0, 66)) : 0n; if (costWei > 0n && price && ((Number(costWei) / 1e18) * 1.03) / price <= bal - PAID.keepBnb && Number(costWei) / 1e18 <= PAID.maxUsd) break; }
          if (n < 1) { P.lastReason = 'each unit is too expensive, over the 20U budget'; return; } // too expensive: don't mint yet (above 85% progress the cap widens to 25U and it is checked again)
          const cu = Number(costWei) / 1e18, needBnb = (cu * 1.03) / price;
          if (!price || spent + needBnb > PAID.maxBnbPerDay || bal - needBnb < PAID.keepBnb) return;
          const data = '0x94bf804d' + w(n) + wa('0x0000000000000000000000000000000000000000');
          let ub = big(await call(USDT, '0x70a08231' + wa(W.addr)));
          if (ub < costWei) {
            const sw = await W.baw(['market-order', 'swap', '--binanceChainId', '56', '--fromTokenQty', needBnb.toFixed(6), '--fromToken', NATIVE_BNB, '--toToken', USDT]);
            if (!sw.j?.success) throw new Error('BNB → USDT swap failed ' + (sw.j?.error?.message || sw.t).slice(0, 100));
            for (let i = 0; i < 30 && ub < costWei; i++) { await sleep(3000); ub = big(await call(USDT, '0x70a08231' + wa(W.addr))); }
            if (ub < costWei) throw new Error('USDT did not arrive');
          }
          await previewExec(W, USDT, '0x095ea7b3' + wa(c) + w(costWei), 0n, (sim) => ((sim?.allowanceChanges || []).some((a) => String(a.spender).toLowerCase() !== c) ? 'wrong approval target' : ''));
          const pool = tokAddr ? Number(await poolOut(costWei, USDT, tokAddr)) : 0;
          const r = await revokeOnFail(W, USDT, c, () => previewExec(W, c, data, 0n, (sim) => {
            if (pool > 0 && gotTok(sim, W) < pool * 1.1) log(`note: ${c} pool already opened, minting is not cheaper than the pool (new rule: mint anyway at ≥80% progress)`);
            const mine = (sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr);
            const out = mine.filter((b) => Number(b.change) < 0); if (out.some((b) => String(b.contractAddress).toLowerCase() !== USDT) || out.reduce((a, b) => a - Number(b.change), 0) > Number(costWei) * 1.01) return 'would deduct something other than USDT or more than the quote';
            if (!mine.some((b) => Number(b.change) > 0)) return 'simulation received no tokens'; return '';
          }, async () => { const q2 = await call(c, '0x725c4058' + w(n)); const c2 = q2 ? BigInt(q2.slice(0, 66)) : 0n; return c2 > (costWei * 105n) / 100n ? 'price jumped more than 5% just now' : ''; })); /* new feature: check the price once more before sending */
          S.paidSpend[W.name] = spent + needBnb; P.done[W.name] = 'minted';
          const tku = tokAddr && !/^0x0{40}$/i.test(tokAddr) ? tokAddr : tokAddr0; if (!tku) trackFromSim(W, r.sim, sym, cu); if (tku && !S.dyn.some((x) => x.addr === tku.toLowerCase() && x.only === W.addr)) S.dyn.push({ sym, addr: tku.toLowerCase(), cost: +cu.toFixed(2), only: W.addr, deployer: P.deployer, boughtAt: Date.now() });
          await tg(`🛒 [${W.name}] auto-minted a paid Agent ID project: ${sym}\n${n} units, spent ${cu.toFixed(2)} USDT (swapped from BNB) · ${sold ?? '?'} sold · progress ~${Math.round(progress * 100)}%\n${c}\ntx ${r.tx}\nnow tracked: ${ruleTxt()}`);
        }
      } catch (e) { if (e.message.includes('result unknown')) { P.done[W.name] = 'unknown'; log(`paid ${c} ${W.name} ${e.message}`); await tg(`⚠️ [${W.name}] ${sym} send result unknown (timeout), stopped to avoid double minting, check your wallet for whether it went through\n${c}`); return; } if (e.message.includes('pool already opened')) { P.done[W.name] = 'pool-cheaper'; log(`paid ${c} ${W.name} ${e.message}`); return; } if (/price jumped/.test(e.message)) { log(`paid ${c} ${W.name} ${e.message} (minting again next round)`); return; } P.failW = P.failW || {}; P.failW[W.name] = (P.failW[W.name] || 0) + 1; P.fail = (P.fail || 0) + 1; log(`paid ${c} ${W.name} ${e.message}`); if (kind === 'replay' && P.fail >= 6) { markUnmintable(c, P, sym, progress, 'simulating the copied method keeps failing'); return; } /* review 11/1: giving up after 2 failures was too fast for copied mints, a price move is not a failure, and failures are counted per wallet */ if (P.failW[W.name] >= 6) { P.done[W.name] = 'fail'; await tg(`⚠️ [${W.name}] paid project ${sym} auto-mint failed, giving up: ${e.message.slice(0, 150)}\n${c}`); } }
    }));
    } finally { busyP.delete(c); const dt = Date.now() - tE; if (dt > 10e3) log(`checking ${c} took ${Math.round(dt / 1000)} s (${mode})`); }
  }
}

async function loginCheck() {
  for (const W of WALLETS) {
    const r = await W.baw(['wallet', 'status'], 60000);
    if (r.j?.data?.status === 'CONNECTED') {
      // developer mode expiry warning (once, within 24 hours before it expires)
      const st = await W.baw(['wallet', 'settings'], 60000); const exp = Date.parse(st.j?.data?.devMode?.expiresAt || '');
      S.devAlert = S.devAlert || {};
      if (exp && exp - Date.now() < 24 * 3600e3 && S.devAlert[W.name] !== exp) { S.devAlert[W.name] = exp; await tg(`⏰ Developer mode for ${W.name} expires at ${new Date(exp).toLocaleString('en-US', { hour12: false, timeZone: CONFIG.timeZone || 'Asia/Taipei' })}\nAuto-mint/auto-sell stop after that; re-enable developer mode in that account's Binance App → Agentic Wallet → Settings`); }
      if (st.j?.data?.devMode && st.j.data.devMode.enabled === false && S.devAlert[W.name + ':off'] !== 1) { S.devAlert[W.name + ':off'] = 1; await tg(`⚠️ Developer mode for ${W.name} is off, automated actions will fail; turn it back on in the App`); }
      if (st.j?.data?.devMode?.enabled) S.devAlert[W.name + ':off'] = 0;
      continue;
    }
    S.lastLoginAlert = S.lastLoginAlert && typeof S.lastLoginAlert === 'object' ? S.lastLoginAlert : {};
    if (Date.now() - (S.lastLoginAlert[W.name] || 0) < 6 * 3600e3) continue;
    S.lastLoginAlert[W.name] = Date.now();
    const si = await W.baw(['auth', 'signin'], 60000); const url = si.j?.data?.urlForWeb, code = si.j?.data?.pairingCode, id = si.j?.data?.qrCodeId;
    await tg(`🔑 The AI wallet login for ${W.name} expired; auto-mint/auto-sell for this wallet are paused.\n${url ? 'Open this link on the phone with the Binance account for "' + W.name + '" to confirm (valid ~5 minutes):\n' + url + '\npairing code ' + code : 'please run the login script to sign in again'}`);
    if (id) { const v = await W.baw(['auth', 'verify', '--qrCodeId', id], 330000); if (v.j?.data?.status === 'SUCCESS') await tg(`✅ ${W.name} signed in again`); }
  }
}


const ELEMENT_EX = '0xb3e3dfcb2d9f3dde16d78b9e6eb3538eb32b5ae1';
const T_721_FILLED = '0x9c248aa1a265aa616f707b979d57f4529bb63a4fc34dc7fc61fdddc18410f74e'; // Element ERC721SellOrderFilled
async function nftVolWatch() {
  S.nftCols = S.nftCols || {}; S.nftVol = S.nftVol || {};
  if (!S.nftColsBackfilled) { // the first time: back-fill the NFT collections minted before from the log
    try { for (const l of readFileSync(LOG, 'utf8').split('\n')) { if (!l.includes('auto-minted') || !l.includes('NFT')) continue; const m = l.match(/\] (0x[a-fA-F0-9]{40}) /); if (m) S.nftCols[m[1].toLowerCase()] = 1; } } catch {}
    S.nftColsBackfilled = true;
  }
  const head = parseInt(await rpc('eth_blockNumber', []), 16), from = head - 96000; // about 12 hours
  // 1) Element fills (fetch them all first, then split by collection)
  const fills = {};
  for (let b = from; b <= head; b += 10000) {
    const L = await rpc('eth_getLogs', [{ address: ELEMENT_EX, topics: [T_721_FILLED], fromBlock: '0x' + b.toString(16), toBlock: '0x' + Math.min(b + 9999, head).toString(16) }]).catch(() => []);
    for (const l of L || []) { const w = l.data.slice(2).match(/.{64}/g) || []; const col = '0x' + (w[7] || '').slice(24); if (!S.nftCols[col]) continue; const isBnb = (w[4] || '').endsWith('eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'); const px = Number(BigInt('0x' + (w[5] || '0'))) / 1e18; (fills[col] = fills[col] || []).push(isBnb ? px : null); }
    await sleep(200);
  }
  // 2) transfers per collection (new mints excluded) + how many we hold
  for (const col of Object.keys(S.nftCols)) {
    let moves = 0;
    for (let b = from; b <= head; b += 50000) { const L = await rpc('eth_getLogs', [{ address: col, topics: [T_TRANSFER], fromBlock: '0x' + b.toString(16), toBlock: '0x' + Math.min(b + 49999, head).toString(16) }]).catch(() => []); for (const l of L || []) if (l.topics.length === 4 && !/^0x0+$/.test(l.topics[1])) moves++; }
    let held = 0; for (const W of WALLETS) { const r = await call(col, '0x70a08231' + wa(W.addr)); if (r && r.length >= 66) held += Number(BigInt(r.slice(0, 66))); }
    const f = fills[col] || [], prev = S.nftVol[col];
    const px = f.filter((x) => x != null), vol = px.reduce((a, b) => a + b, 0);
    const hot = held > 0 && px.length >= 3 && vol > 0 && (prev ? px.length >= 2 * (prev.f || 0) : px.length >= 5); // only count fills that actually paid (user 15:22: transfers back and forth do not count)
    if (hot) {
      const name = tokStr(await call(col, '0x06fdde03')) || col.slice(0, 10);
      await tg(`🦋 NFT volume picking up: ${name}\nthe wallets hold ${held} in total\n${f.length} Element fills in the past 12 hours${prev ? ' (last time ' + (prev.f || 0) + ')' : ''}${px.length ? `, prices ${Math.min(...px).toFixed(4)}～${Math.max(...px).toFixed(4)} BNB, ${vol.toFixed(4)} BNB in total` : ''}\ntransfers ${moves}${prev ? ' (last time ' + (prev.t || 0) + ')' : ''}\n${col}\nhttps://element.market/assets/bsc/${col}/1\nlist or sell it manually`);
    }
    S.nftVol[col] = { f: f.length, t: moves, held, at: ts() };
    await sleep(150);
  }
  log(`NFT volume check done: ${Object.keys(S.nftCols).length} collections`);
}


async function manualMints() {
  const f = join(DIR, 'data', 'manual_mint.json'); if (!existsSync(f)) return;
  let jobs = []; try { jobs = JSON.parse(readFileSync(f, 'utf8')); } catch { return; } if (!jobs.length) return;
  writeFileSync(f, '[]');
  for (const J of jobs) {
    const res = await Promise.all(WALLETS.map(async (W) => {
      let ok = 0, err = '';
      if (J.approvals) { /* list every wallet approval and save it to data/approvals_<wallet>.json (user 10/31 10:34: Flap had an incident, approvals must be revoked) */
        const r = await W.baw(['approvals', 'list', '--limit', '100']); writeFileSync(join(DIR, 'data', `approvals_${W.name}.json`), r.t || ''); return `[${W.name}] approvals listed`;
      }
      if (J.revokeAll) { /* revoke the given approvals: [{token, spender, type}] */
        const out = []; for (const x of J.revokeAll) { const r = await W.baw(['approvals', 'revoke', '--binanceChainId', '56', '--tokenContract', x.token, '--spender', x.spender, '--type', x.type || 'approve']); out.push(`${x.token.slice(0, 8)}→${x.spender.slice(0, 8)} ${r.j?.success ? '✅' : '❌ ' + (r.j?.error?.message || r.t).slice(0, 80)}`); }
        writeFileSync(join(DIR, 'data', `revoke_${W.name}.txt`), out.join('\n')); return `[${W.name}] revoke approvals: ${out.join('; ')}`;
      }
      if (J.swapBack) { // swap the tokens back to BNB and (optionally) revoke the approval
        try {
          const out = [];
          if (J.revoke) { await previewExec(W, J.swapBack, '0x095ea7b3' + wa(J.revoke) + w(0), 0n, () => ''); out.push('approval revoked'); }
          const bal = big(await call(J.swapBack, '0x70a08231' + wa(W.addr)));
          const amt = J.pct ? (bal * BigInt(J.pct)) / 100n : bal; if (amt > 10n ** 16n) { const qty = (Number(amt / 10n ** 14n) / 1e4).toFixed(4); const sw = await W.baw(['market-order', 'swap', '--binanceChainId', '56', '--fromTokenQty', qty, '--fromToken', J.swapBack, '--toToken', NATIVE_BNB]); out.push(sw.j?.success ? `swapped ${qty} back to BNB` : 'swap to BNB failed ' + (sw.j?.error?.message || sw.t).slice(0, 80)); }
          return `[${W.name}] ${out.join(', ') || 'nothing to do'}`;
        } catch (e) { return `[${W.name}] ❌ ${e.message.slice(0, 100)}`; }
      }
      if (J.usdt) { // USDT kind: swap BNB → USDT → approve → mint (J.data is fixed)
        try {
          const need = BigInt(Math.round(J.usdt * 1e6)) * 10n ** 12n; let ub = big(await call(USDT, '0x70a08231' + wa(W.addr)));
          if (ub < need) { const px = await bnbUsd(); const nb = ((Number(need - ub) / 1e18) * 1.04) / px; const sw = await W.baw(['market-order', 'swap', '--binanceChainId', '56', '--fromTokenQty', nb.toFixed(6), '--fromToken', NATIVE_BNB, '--toToken', USDT]); if (!sw.j?.success) throw new Error('BNB → USDT swap failed ' + (sw.j?.error?.message || sw.t).slice(0, 80)); for (let k = 0; k < 30 && ub < need; k++) { await sleep(3000); ub = big(await call(USDT, '0x70a08231' + wa(W.addr))); } if (ub < need) throw new Error('USDT did not arrive'); }
          await previewExec(W, USDT, '0x095ea7b3' + wa(J.c) + w(need), 0n, (sim) => ((sim?.allowanceChanges || []).some((x) => String(x.spender).toLowerCase() !== J.c) ? 'wrong approval target' : ''));
          const r = await revokeOnFail(W, USDT, J.c, () => previewExec(W, J.c, J.data, 0n, (sim) => { const mine = (sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr); if (mine.some((b) => Number(b.change) < 0 && String(b.contractAddress).toLowerCase() !== USDT)) return 'would deduct assets other than USDT'; if (-mine.filter((b) => Number(b.change) < 0).reduce((x, b) => x + Number(b.change), 0) > Number(need) * 1.01) return 'deduction above the quote'; return ''; }));
          ok = 1; S.paidSpend = S.paidSpend || {}; if (J.tok) { S.dyn = S.dyn || []; if (!S.dyn.some((t) => t.addr === J.tok && t.only === W.addr)) S.dyn.push({ sym: J.name || 'TOKEN', addr: J.tok, cost: J.usdt, only: W.addr, boughtAt: Date.now() }); }
          return `[${W.name}] ✅ minted (spent ~${J.usdt}U) tx ${r.tx}`;
        } catch (e) { return `[${W.name}] ❌ not minted (${e.message.slice(0, 100)})`; }
      }
      for (let i = 0; i < (J.n || 1); i++) {
        const data = J.randArg ? J.sel + w(BigInt(Math.floor(Math.random() * 65536))) : J.data;
        try {
          const mr = await previewExec(W, J.c, data, BigInt(J.value || 0), (sim) => {
            const mine = (sim?.balanceChanges || []).filter((b) => String(b.owner).toLowerCase() === W.addr);
            if (sim?.allowanceChanges?.length || sim?.authorityChanges?.length) return 'approval change';
            if (mine.some((b) => Number(b.change) < 0 && !String(b.contractAddress).toLowerCase().startsWith('0xeeee'))) return 'would deduct other assets';
            if (!mine.some((b) => Number(b.change) > 0)) return 'simulation received nothing'; return '';
          }, null, { manual: true });
          ok++; if (BigInt(J.value || 0) > 0n) trackFromSim(W, mr.sim, J.name, (Number(J.value) / 1e18) * ((await bnbUsd().catch(() => 0)) || 0));
        } catch (e) { err = e.message.slice(0, 80); break; }
      }
      if (ok) { S.nftCols = S.nftCols || {}; S.nftCols[J.c] = 1; S.freeTrack = S.freeTrack || {}; S.freeTrack[J.c] = S.freeTrack[J.c] || { at: Date.now() }; }
      return ok ? `[${W.name}] ✅ minted ${ok} units${err ? ' (hit the per-wallet cap for this project)' : ''}` : `[${W.name}] ❌ not minted (${/reverted/.test(err) ? 'the contract rejects this wallet, it may have minted already, be full, or require eligibility' : err})`;
    }));
    log(`manual top-up mint ${J.name || J.c}: ${res.join('; ')}`);
    if (J.swapBack || J.approvals || J.revokeAll) await tgSay(`🛠 ${J.swapBack ? 'swap back to BNB' : 'approval handling'} ${J.name || J.c || ''}\n${res.join('\n')}`);
    else { /* user 11/1 03:42: manual top-up mints are not sent to the personal TG but to the group (only what was minted, no amounts or wallets) */
      const okAny = res.some((x) => x.includes('✅')); const c0 = String(J.c || '').toLowerCase(); S.grpOnce = S.grpOnce || {};
      if (okAny && TG && GROUP_ID && c0 && !S.grpOnce[c0]) { S.grpOnce[c0] = Date.now(); save(); try { await fetch(`https://api.telegram.org/bot${TG.a}/sendMessage`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ chat_id: GROUP_ID, text: `📡 Agent ID broadcast\n🛒 Minted: ${J.name || c0.slice(0, 10)}\nContract: ${c0}`, disable_web_page_preview: true }), signal: AbortSignal.timeout(10000) }); log(`group broadcast: manual top-up mint ${J.name || c0}`); } catch {} }
    }
  }
}
// ── TG commands: the user types "sell XXX", "sell XXX half" or "balance" straight to the bot, executed within seconds without going through Claude ──
// ── update reminder (open-source builds only; user 11/1 00:47: no auto-update, only a reminder; the user replies /update to update, with a safety scan + sha256 + backup beforehand and an automatic rollback if the new version won't start) ──
const UPD = { on: true, ver: '2.6.6', repo: 'setyamickala/fork-agentid-sniper', group: 't.me/nancy_c0813' }; /* user 11/1 00:47: reminder only, no auto-update; point users to the user's TG group */
const verGt = (a, b) => { const x = String(a).split('.').map(Number), y = String(b).split('.').map(Number); for (let i = 0; i < 3; i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0); return false; };
async function updCheck(quiet = false) {
  if (!UPD.on) return null;
  try {
    const r = await fetch(`https://raw.githubusercontent.com/${UPD.repo}/main/version.json?t=${Date.now()}`, { signal: AbortSignal.timeout(15000) }); if (!r.ok) return null;
    const v = await r.json(); if (!v.version || !verGt(v.version, UPD.ver)) return null; UPD.latest = v;
    if (!quiet && S.updNotified !== v.version) { /* user 11/1 00:47: do not auto-update (afraid of complaints) — remind only, the user replies /update when they want it */
      S.updNotified = v.version; save();
      const msg = `🔔 Agent ID Sniper has a new version v${v.version} (you are on v${UPD.ver})\n${(v.notes || []).map((x) => '• ' + x).join('\n')}\n\nTo update: reply "/update" in TG, or drop an empty file named UPDATE in the tool folder (a safety scan and an old-version backup run first).\nYou can also keep using it without updating.\nOfficial group: ${UPD.group}`;
      log(msg.replace(/\n/g, ' | ')); console.log('\n' + '='.repeat(48) + '\n' + msg + '\n' + '='.repeat(48) + '\n'); await tgSay(msg);
    }
    return v;
  } catch { return null; }
}
async function doUpdate() {
  if (!UPD.on) return 'this version does not auto-update';
  const v = UPD.latest && verGt(UPD.latest.version, UPD.ver) ? UPD.latest : await updCheck(true); if (!v) return `already on the latest version v${UPD.ver}`;
  const r = await fetch(`https://raw.githubusercontent.com/${UPD.repo}/main/watch.mjs?t=${Date.now()}`, { signal: AbortSignal.timeout(60000) }).catch(() => null);
  if (!r || !r.ok) return '⚠️ downloading the new version failed, try again later';
  const buf = Buffer.from(await r.arrayBuffer()), code = buf.toString('utf8');
  if (!v.sha256 || createHash('sha256').update(buf).digest('hex') !== v.sha256) return '⚠️ the new file failed the checksum (the GitHub cache may not have synced right after an update, or the download was incomplete), try again later';
  const old = readFileSync(SELF, 'utf8');
  const hosts = (t) => new Set([...t.matchAll(/https?:\/\/([a-z0-9.-]+)/gi)].map((m) => m[1].toLowerCase()));
  const TRUST = new Set(['github.com', 'api.geckoterminal.com', 'api.dexscreener.com', 'raw.githubusercontent.com', 'api.telegram.org', 'bsc-dataseed.bnbchain.org', 'bsc-rpc.publicnode.com']); /* known-safe price/node sites, not blocked when a later version uses them */
  const ho = hosts(old), nh = [...hosts(code)].filter((h) => !ho.has(h) && !TRUST.has(h));
  if (nh.length) return `⛔ the new version connects to sites not seen before (${nh.slice(0, 3).join(', ')}), refusing the update for safety. Review it on GitHub and download it manually.`;
  const risky = [/private[_\s-]?key/gi, /mnemonic/gi, /seed\s?phrase/gi, /助記詞|助记词|私鑰|私钥/g, /\beval\s*\(/g, /new\s+Function\s*\(/g, /child_process/g, /powershell|Invoke-WebRequest|curl\s|wget\s|certutil|bitsadmin/gi, /execSync|spawnSync|\bspawn\s*\(/g, /\.(exe|dll|ps1|bat|cmd|vbs)['"`]/gi, /keystore/gi];
  for (const re of risky) { const n = (t) => (t.match(re) || []).length; if (n(code) > n(old)) return `⛔ the new version has suspicious content (${re.source.slice(0, 40)}), refusing the update for safety. Review it on GitHub and download it manually.`; }
  const tmp = join(DIR, 'data', 'update_new.mjs'); writeFileSync(tmp, code);
  const bad = await new Promise((res) => execFile(process.execPath, ['--check', tmp], { windowsHide: true, timeout: 60000 }, (e, o, er) => res(e ? String(er || e.message) : '')));
  if (bad) { try { unlinkSync(tmp); } catch {} return '⛔ the new version failed the syntax check, update cancelled: ' + bad.slice(0, 120); }
  copyFileSync(SELF, join(DIR, 'data', 'update_backup.mjs')); writeFileSync(SELF, code); try { unlinkSync(tmp); } catch {}
  log(`updated to v${v.version} (old version backed up to data/update_backup.mjs)`);
  return `✅ updated to v${v.version}, the old version is backed up and the tool restarts itself to load it. If the new version fails to start 3 times in a row it switches back automatically.`;
}
async function groupInvite() { if (!UPD.on || S.grpInvited === 1) return; S.grpInvited = 1; save(); const msg = `👋 Welcome to Agent ID Sniper v${UPD.ver}\nJoin the official TG group to get, first thing:\n• tool update notices\n• live announcements of what got minted and which pool opened\n• how-to guides and answers\n👉 ${UPD.group}`; log(msg.replace(/\n/g, ' | ')); console.log('\n' + '='.repeat(48) + '\n' + msg + '\n' + '='.repeat(48) + '\n'); await tgSay(msg); }
if (UPD.on) setTimeout(() => { const b = join(DIR, 'data', 'update_backup.mjs'); if (existsSync(b)) { try { renameSync(b, join(DIR, 'data', 'backup_prev.mjs')); log('the new version has run stably for 10 minutes, keeping the old backup as data/backup_prev.mjs'); } catch {} } }, 10 * 60e3).unref();
async function tgSay(text) { if (!TG) return; log('TG reply: ' + text.replace(/\n/g, ' | ')); try { await fetch(`https://api.telegram.org/bot${TG.a}/sendMessage`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ chat_id: TG.b, text, disable_web_page_preview: true }), signal: AbortSignal.timeout(10000) }); } catch {} }
function findTok(q, W) {
  q = q.toLowerCase();
  return TOKENS.concat(S.dyn || []).find((t) => (!t.only || t.only === W.addr) && (t.sym.toLowerCase() === q || t.addr === q));
}
async function cmdSell(q, half) {
  const head = parseInt(await rpc('eth_blockNumber', []), 16);
  const jobs = WALLETS.map(async (W) => {
    const t0 = findTok(q, W); if (!t0) return `[${W.name}] ${q} not found`;
    const bal = big(await call(t0.addr, '0x70a08231' + wa(W.addr))); if (bal === 0n) return `[${W.name}] no ${t0.sym}`;
    const dupSym = TOKENS.concat(S.dyn || []).filter((x) => (!x.only || x.only === W.addr) && x.sym === t0.sym && x.addr !== t0.addr).length > 0;
    const key = (W.addr === ME ? t0.sym : W.name + ':' + t0.sym) + (dupSym ? '#' + t0.addr.slice(2, 8) : '');
    const st = (S.tokens[key] = S.tokens[key] || { hist: [], peak: 0, peakAt: 0, sold: 0 });
    const ok = await sell(t0, st, half ? bal / 2n : bal, half ? 'you asked in TG to sell half' : 'you asked in TG to sell everything', W);
    if (ok && !half) { st.stage = 9; st.done = true; }
    return ok ? null : `[${W.name}] ${t0.sym} sell failed`;
  });
  const errs = (await Promise.all(jobs)).filter(Boolean); save();
  if (errs.length) await tgSay('⚠️ ' + errs.join('\n'));
  void head;
}
async function cmdBalance() {
  const lines = await Promise.all(WALLETS.map(async (W) => { const b = Number(BigInt(await rpc('eth_getBalance', [W.addr, 'latest']))) / 1e18; return `${W.name}: ${b.toFixed(5)} BNB`; }));
  await tgSay('💼 Wallet balances\n' + lines.join('\n'));
}
async function cmdHold() {
  const out = [];
  for (const W of WALLETS) for (const t0 of TOKENS.concat(S.dyn || [])) {
    if (t0.only && t0.only !== W.addr) continue;
    const bal = big(await call(t0.addr, '0x70a08231' + wa(W.addr))); if (bal === 0n) continue;
    const v = await valueUsd(t0, bal).catch(() => null); const line = `[${W.name}] ${t0.sym}: ${v == null ? 'no pool yet' : '~' + v.toFixed(2) + 'U'}`;
    if (!out.includes(line)) out.push(line);
  }
  await tgSay('📦 Coins not sold yet\n' + (out.length ? out.join('\n') : 'none') + '\n(NFTs are not listed here, a jump in NFT volume is notified separately)');
}
async function holdReport(title = '📦 Position summary') { /* user 11/1 03:19: send a position summary every 12 hours (1 am and 1 pm; changed by the user 11/1 03:36): cost, current value, multiple, status */
  const rows = [], noPool = [], dust = []; let tc = 0, tv = 0;
  for (const W of WALLETS) for (const t0 of TOKENS.concat(S.dyn || [])) {
    if (t0.only && t0.only !== W.addr) continue;
    const bal = big(await call(t0.addr, '0x70a08231' + wa(W.addr)).catch(() => null)); if (!(bal > 0n)) continue;
    const v = await valueUsd(t0, bal).catch(() => null); const wn = W === WALLETS[0] ? 'A' : 'B';
    if (v == null) { if (!noPool.includes(t0.sym)) noPool.push(t0.sym); continue; }
    if (v < 0.3) { if (!dust.includes(t0.sym)) dust.push(t0.sym); continue; }
    const st = (S.tokens || {})[t0.sym] || (S.tokens || {})[W.name + ':' + t0.sym] || {}; const cost = Number(t0.cost) || 0; const x = cost > 0 ? v / cost : 0;
    const status = t0.hold ? 'notify only' : t0.custom ? 'special rule' : (st.done2 || []).includes('back') ? (st.moon ? `break-even done, includes a ${RULE.moonPct}% long hold` : 'break-even done, holding by strategy') : cost > 0 ? 'holding by strategy' : 'free coin';
    tc += cost; tv += v; rows.push({ v, line: `${t0.sym} (wallet ${wn}) ${cost > 0 ? `cost ${cost.toFixed(1)}U → ` : ''}~${v.toFixed(2)}U${x ? ` (${x.toFixed(2)}x)` : ''} · ${status}` });
  }
  rows.sort((a, b) => b.v - a.v);
  const msg = `${title} (wallet A = ${WALLETS[0]?.name || ''}${WALLETS[1] ? ', wallet B = ' + WALLETS[1].name : ''})\n${rows.length ? rows.map((r) => '· ' + r.line).join('\n') : 'no priced positions right now'}\n\npriced total: ~${tv.toFixed(2)}U${tc ? ` (paid cost total ${tc.toFixed(1)}U)` : ''}${noPool.length ? `\nno pool yet (${noPool.length}): ${noPool.slice(0, 20).join(', ')}${noPool.length > 20 ? '…' : ''}` : ''}${dust.length ? `\ndust under 0.3U: ${dust.join(', ')}` : ''}`;
  await tgSay(msg);
}
setInterval(() => { const tp = new Date(Date.now() + 8 * 3600e3), h = tp.getUTCHours(), k = tp.toISOString().slice(0, 10) + '-' + h; if ((h === 1 || h === 13) && S.holdRepAt !== k) { S.holdRepAt = k; save(); holdReport().catch((e) => log('holdReport ' + e.message)); } }, 60e3).unref();
async function cmdStats() {
  // free projects: every "auto-minted" record counts (including ones minted the moment they were found), de-duplicated by contract address; the "is open now" kind is matched back to the contract by name; the fake mints from the morning 0 BNB bug are subtracted
  const free = new Set(), bog = new Set(), paidC = new Set(); let paid = 0; const n2a = {};
  try {
    const lines = readFileSync(LOG, 'utf8').split('\n');
    for (const l of lines) { const m = l.match(/(?:exclusive contract|project):\s*([^|(\n]+?)\s*(?:\([^|]*\))?\s*\|\s*(0x[a-fA-F0-9]{40})/); if (m && !n2a[m[1].trim().toLowerCase()]) n2a[m[1].trim().toLowerCase()] = m[2].toLowerCase(); }
    for (const l of lines) {
      if (l.includes('🛒')) { paid++; const c = l.match(/(?<![0-9a-fA-Fx])(0x[a-fA-F0-9]{40})(?![0-9a-fA-F])/); if (c) paidC.add(c[1].toLowerCase()); }
      if (!l.includes('auto-minted')) continue;
      const nm = l.match(/project\s+([^\n(|]+?)\s+is open now/); let key = '';
      if (nm) key = n2a[nm[1].trim().toLowerCase()] || 'N:' + nm[1].trim().toLowerCase();
      else { const c = l.match(/(?<![0-9a-fA-Fx])(0x[a-fA-F0-9]{40})(?![0-9a-fA-F])/); if (c) key = c[1].toLowerCase(); }
      if (!key) continue;
      (/replay0x(\(| \||$)/.test(l) ? bog : free).add(key);
    }
    for (const k of bog) free.delete(k);
  } catch {}
  const tr = S.trades || [], by = {};
  for (const x of tr) { const k = x.sym; by[k] = by[k] || { usd: 0, n: 0 }; by[k].usd += x.usd; by[k].n++; }
  const px = await bnbUsd().catch(() => 0); let tot = 0; for (const W of WALLETS) tot += Number(BigInt(await rpc('eth_getBalance', [W.addr, 'latest']))) / 1e18;
  const lines = Object.entries(by).map(([k, v]) => `${k}: ${v.n} sells, got back ~${v.usd.toFixed(2)}U`);
  await tgSay(`📊 Stats\nfree projects auto-minted: ${free.size}\npaid mints: ${paidC.size} projects (${paid} transactions)\n${lines.length ? 'recorded sells:\n' + lines.join('\n') + '\n' : ''}the wallets hold ${tot.toFixed(4)} BNB in total (~${(tot * px).toFixed(2)}U)`);
}
async function tgCmdLoop() {
  if (!TG) return;
  if (S.tgOff == null) { try { const r = await (await fetch(`https://api.telegram.org/bot${TG.a}/getUpdates?timeout=0`)).json(); const u = r.result || []; S.tgOff = u.length ? u[u.length - 1].update_id + 1 : 0; save(); } catch { S.tgOff = 0; } }
  for (;;) {
    try {
      const r = await (await fetch(`https://api.telegram.org/bot${TG.a}/getUpdates?timeout=25&offset=${S.tgOff}`, { signal: AbortSignal.timeout(35000) })).json();
      for (const u of r.result || []) {
        S.tgOff = u.update_id + 1; save();
        const m = u.message; if (!m || String(m.chat?.id) !== String(TG.b) || !m.text) continue; // only listen to the user himself
        if (Date.now() / 1000 - m.date > 120) continue; // don't run commands older than 2 minutes
        const txt = m.text.trim().replace(/^\//, '');
        let mm;
        if ((mm = txt.match(/^sell\s+(\S+?)\s*(half)?$/i)) || (mm = txt.match(/^(\S+?)\s+all$/i))) {
          const half = !!mm[2]; await tgSay(`⚡ Got it, selling ${mm[1]} from all wallets ${half ? 'half each' : 'completely'}…`);
          cmdSell(mm[1], half).catch((e) => tgSay('⚠️ sell error: ' + e.message));
        } else if (/^(balance|balances)$/i.test(txt)) { cmdBalance().catch(() => {}); }
        else if (/^(position|positions|holdings?)$/i.test(txt)) { holdReport('📦 Positions').catch((e) => tgSay('⚠️ ' + e.message)); }
        else if (/^(stats?|statistics)$/i.test(txt)) { cmdStats().catch((e) => tgSay('⚠️ ' + e.message)); }
        else if (/^update$/i.test(txt)) { if (!UPD.on) await tgSay('this version does not auto-update'); else { await tgSay('⚡ Got it, downloading the new version and running the safety scan…'); await tgSay(await doUpdate().catch((e) => '⚠️ update error: ' + e.message)); } }
        else if (/^(help|commands?|\?)$/i.test(txt)) await tgSay('Commands:\nsell XXX → all wallets sell everything\nsell XXX half → half each\n(you can also type "XXX all")\nbalance → BNB per wallet\npositions → coins not sold yet and their value\nstats → how much was minted and sold since launch');
      }
    } catch { await sleep(3000); }
  }
}

let lastLoopAt = Date.now();
setInterval(() => { if (Date.now() - lastLoopAt > (INFLIGHT > 0 ? 11 : 8) * 60e3) { log('watchdog: main loop stuck for more than 8 minutes, restarting'); try { save(); } catch {} process.exit(3); } }, 30e3).unref();
async function main() {
  log('monitoring started'); setTimeout(() => groupInvite().catch(() => {}), 20e3);
  tgCmdLoop().catch((e) => log('tgCmd ' + e.message));
  let lastHold = 0, lastLogin = 0;
  for (;;) {
    try { if (statSync(SELF).mtimeMs !== SELF_MT) { if ((INFLIGHT > 0 || globalThis._xBusy) && !globalThis._waitSince) globalThis._waitSince = Date.now(); if ((INFLIGHT === 0 && !globalThis._xBusy) || Date.now() - (globalThis._waitSince || 0) > 300e3) { log('program updated, restarting'); save(); process.exit(0); } } } catch {} // if a trade is being sent, wait for it to finish (max 4 minutes)
    if (UPD.on && existsSync(join(DIR, 'UPDATE'))) { try { unlinkSync(join(DIR, 'UPDATE')); } catch {} doUpdate().then((m) => { log(m); console.log('\n' + m + '\n'); tgSay(m); }).catch((e) => log('update ' + e.message)); }
    if (UPD.on && (!globalThis._updAt || Date.now() - globalThis._updAt > 30 * 60e3)) { globalThis._updAt = Date.now(); updCheck().catch(() => {}); } // check GitHub for a new version every 30 minutes
    if (existsSync(join(DIR, 'WATCH_STOP'))) { log('WATCH_STOP, exiting'); save(); process.exit(0); }
    try {
      const head = parseInt(await rpc('eth_blockNumber', []), 16);
      const tS = Date.now(); await scanNew(head); if (Date.now() - tS > 20e3) log(`a scan round took ${Math.round((Date.now() - tS) / 1000)} s`);
      { const fast = Object.values(S.tokens).some((st) => st.openAt && Date.now() - st.openAt < RULE.fastMin * 60e3 && st.stage !== 9 && !st.done); const full = Date.now() - lastHold > 5000; if (!globalThis._holdBusy && (fast || full)) { if (full) lastHold = Date.now(); globalThis._holdBusy = true; const tH = Date.now(); holdings(head, !full).catch((e) => log('holdings ' + e.message)).finally(() => { globalThis._holdBusy = false; if (Date.now() - tH > 20e3) log(`position check took ${Math.round((Date.now() - tH) / 1000)} s`); }); } } /* position checks run in the background, one at a time, so nothing gets sold twice */
      if (!globalThis._flapAt || Date.now() - globalThis._flapAt > 30e3) { globalThis._flapAt = Date.now(); if (!globalThis._flapBusy) { globalThis._flapBusy = true; flapAgentWatch().catch((e) => log('flap ' + e.message)).finally(() => { globalThis._flapBusy = false; }); } }
      if (!globalThis._toshAt || Date.now() - globalThis._toshAt > 60e3) { globalThis._toshAt = Date.now(); if (!globalThis._toshBusy) { globalThis._toshBusy = true; toshWatch().catch((e) => log('tosh ' + e.message)).finally(() => { globalThis._toshBusy = false; }); } }
      if (!globalThis._miscAt || Date.now() - globalThis._miscAt > 600e3) { globalThis._miscAt = Date.now(); lowBnbWatch().catch(() => {}); flapApprovalWatch().catch((e) => log('flapApproval ' + e.message)); } /* check whether BNB is enough every 10 minutes */
      dailyReport().catch((e) => log('daily ' + e.message));
      if (!globalThis._pruneAt || Date.now() - globalThis._pruneAt > 3600e3) { globalThis._pruneAt = Date.now(); pruneState(); }
      if (!globalThis._aidropAt || Date.now() - globalThis._aidropAt > 120e3) { globalThis._aidropAt = Date.now(); const tA = Date.now(); await aidropWatch(head); if (Date.now() - tA > 20e3) log(`aidrop check took ${Math.round((Date.now() - tA) / 1000)} s`); }
      for (let k = 0; k < 3; k++) { const bk = '_paidBusy' + k, ak = '_paidAt' + k; if (!globalThis[bk] && (!globalThis[ak] || Date.now() - globalThis[ak] > 15e3)) { globalThis[ak] = Date.now(); globalThis[bk] = true; paidLoop('hot', k).catch((e) => log('paidLoop ' + e.message)).finally(() => { globalThis[bk] = false; globalThis._paidDur = Math.round((Date.now() - globalThis[ak]) / 1000); }); } } /* fast check: every 15 s, in 3 groups running at the same time */
      if (!globalThis._swBusy && (!globalThis._swAt || Date.now() - globalThis._swAt > 60e3)) { globalThis._swAt = Date.now(); globalThis._swBusy = true; progressSweep().catch((e) => log('sweep ' + e.message)).finally(() => { globalThis._swBusy = false; }); } // cheap progress read every minute
      if (!globalThis._restBusy && (!globalThis._restAt || Date.now() - globalThis._restAt > 60e3)) { globalThis._restAt = Date.now(); globalThis._restBusy = true; paidLoop('rest').catch((e) => log('paidRest ' + e.message)).finally(() => { globalThis._restBusy = false; }); } // normal check: about once a minute // runs in the background and never blocks the scan
      if (!globalThis._smartBusy && Date.now() - (S.smartAt || 0) > 6 * 3600e3) { S.smartAt = Date.now(); globalThis._smartBusy = true; buildSmart().catch((e) => log('smart ' + e.message)).finally(() => { globalThis._smartBusy = false; }); }
      if (!globalThis._mmBusy && existsSync(join(DIR, 'data', 'manual_mint.json'))) { globalThis._mmBusy = true; manualMints().catch((e) => log('manual ' + e.message)).finally(() => { globalThis._mmBusy = false; }); }
      if (!globalThis._heatBusy && Date.now() - (globalThis._heatAt || 0) > 20e3) { globalThis._heatAt = Date.now(); globalThis._heatBusy = true; heatWatch().catch((e) => log('heatWatch ' + e.message)).finally(() => { globalThis._heatBusy = false; }); } // heat monitoring (background)
      if (!globalThis._fpBusy && Date.now() - (globalThis._fpAt || 0) > 300e3) { globalThis._fpAt = Date.now(); globalThis._fpBusy = true; freeProgWatch().catch((e) => log('freeProg ' + e.message)).finally(() => { globalThis._fpBusy = false; }); } // free project progress, every 5 minutes
      if (!globalThis._nftBusy && Date.now() - (S.nftVolAt || 0) > 12 * 3600e3) { S.nftVolAt = Date.now(); globalThis._nftBusy = true; nftVolWatch().catch((e) => log('nftVol ' + e.message)).finally(() => { globalThis._nftBusy = false; }); } // check NFT volume once every 12 hours (background)
      if (Date.now() - lastLogin > 600e3) { lastLogin = Date.now(); loginCheck().catch(() => {}); }
      lastLoopAt = Date.now(); save(); writeFileSync(join(DIR, 'data', 'watch_heartbeat.txt'), ts() + ' scan behind ' + (S.scanLag ?? '?') + ' blocks · paid check round ' + (globalThis._paidDur ?? '?') + ' s');
      if ((S.scanLag || 0) > 400 && Date.now() - (S.lagAlert || 0) > 3600e3) { S.lagAlert = Date.now(); await tg(`⚠️ New contract scan is ${S.scanLag} blocks behind (~${Math.round(S.scanLag * 0.45 / 60)} minutes), public nodes may have slowed down; handle it manually`); }
    } catch (e) { log('error ' + e.message); }
    await sleep(1000);
  }
}
process.on('uncaughtException', (e) => log('uncaught ' + e.message));
process.on('unhandledRejection', (e) => log('unhandled ' + (e?.message || e)));
main();
