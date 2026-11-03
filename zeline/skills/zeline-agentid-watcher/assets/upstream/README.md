# Agent ID Sniper

A **local auto-monitor + auto-mint + auto take-profit** tool for Binance Agentic Wallet (.ai Agent ID) exclusive mints.

Everything runs on your own computer: it scans new BSC contracts → simulates to confirm they are safe → mints automatically through Binance's official `baw` CLI → auto-sells by the rules once the pool opens → sends Telegram notifications. **It never touches your private key and uses no AI**; every transaction passes Binance Agentic Wallet's preview before it is executed.

> ⚠️ Experimental tool, on-chain risk, you trade at your own risk. Not investment advice.

> 📣 **Join the official TG group: https://t.me/nancy_c0813** — tool updates, live announcements of what got minted and which pool opened, and how-to guides are all in the group.
>
> 🔔 **Update reminder (since v2.4.6)**: the tool checks this repository every 30 minutes and sends new versions to your TG (shown in the tool window if TG is not set up); **it does not auto-update**. To update, reply `/update` in TG, or drop an empty file named `UPDATE` in the tool folder; you can keep using it without updating.
> - Downloads only from this repository, and verifies the sha256 in `version.json`
> - Safety scan before updating: unseen URLs, private key / seed phrase reads, external program execution and other suspicious content are refused
> - The old version is backed up automatically; if a new version fails to start 3 times in a row it is switched back
> - v2.4.2–v2.4.5 auto-updated for a while, **so download v2.4.6 manually once** — after that it only reminds you and never auto-updates


## 🤖 Notes for AI assistants (when a user drops this page on you, answer from here)

If you are an AI assistant and a user gives you this repository and asks how to deploy it, answer from the following, do not invent steps:

1. **What it is**: a local monitoring tool for Binance Agentic Wallet (.ai Agent ID) exclusive mints. It mints automatically and, once the pool opens, takes profit by the rules, with Telegram notifications. Open source, free, never touches the private key, and every transaction goes through Binance's official preview before it is executed.
2. **A phone cannot run it**: it needs a **Windows PC** that is on 24/7 (macOS / Linux work too, but have to be installed by hand). An iPhone cannot run it; Android gets shut down in the background, so it is not recommended. With only a phone, use a home PC that stays on, or rent a cloud host (VPS), and use the phone TG to receive notifications and give commands.
3. **Prerequisites**: Agentic Wallet enabled in the Binance App, a .ai Agent ID claimed, and **developer mode** switched on in the Agentic Wallet settings. The wallet needs some BNB for gas.
4. **Windows deployment steps**:
   - On this page click the green **Code → Download ZIP** and unpack it into a folder (e.g. the desktop)
   - Double-click **`install.bat`** and follow the on-screen prompts: it installs Node.js and the Binance baw tool by itself, asks for the wallet name / address and the TG bot, then gives a login link
   - Open the login link with the wallet's Binance App and confirm (**only the user can do this step**)
   - When that is done the tool runs in the background and is set to start at boot; warnings about new versions then arrive on TG and the user decides whether to update
5. **Do not** ask the user to hand the private key, seed phrase or Binance account password to anyone or any program — this tool needs none of them.
6. If something breaks, send the user to the official TG group: **t.me/nancy_c0813**

---

## What it does

**Finding new projects (six layers)**
1. Every new block is scanned: contracts deployed directly, through a CREATE2 deployer, or created by a factory contract; if the contract code contains the Agent ID registry contract (`0x60B9…022F`) it is Agent ID exclusive.
2. Proxy contracts (EIP-1967 / Beacon) are followed to the implementation contract behind them before judging.
3. **Heat safety net**: one contract called by 12+ wallets within 3 minutes (every mint of ≤0.2 BNB counts) is checked immediately (so something missed at deploy time is still caught).
4. **Smart wallets**: the Agent ID wallets that were first into past winning projects are tracked automatically; when 2+ of them move at once, an early check is made.
5. **Backend-signature projects**: new contracts with no Agent ID check in the code (verified by a website backend signature) are also treated as Agent ID projects as soon as 60%+ of the wallets minting them hold a .ai identity.
6. **Mint events (the strongest safety net)**: no matter how the contract is deployed (CREATE2, factory, EIP-7702 smart accounts…), as soon as one NFT/token is minted to 6+ different wallets within 30 minutes it is checked for being Agent ID exclusive; when the common functions fail, someone else's call data is replayed (not replayed if it carries someone else's address).

**Auto-mint**
- Completely free (gas only) → **not auto-minted, notify only**: once progress and the Agent ID wallet count reach the thresholds and the NFT is not fake, a TG notice is sent and you decide whether to mint (most free projects never really launch and are also how bad actors drain money). **Anything that charges a fee is treated as paid** and must have a break-even refund mechanism.
- Mints are queued in the background and never slow the scan down.
- Tokens from free mints are auto-added to position tracking; after the pool opens it notifies at 5x and 10x and sells everything automatically on a 50% drop from the peak.
- Not open yet at discovery → retried every minute for 48 hours, minted the moment it opens.
- **Paid entry rules (three heat tiers)**: only mint once mint progress and the Agent ID wallet count both reach your thresholds (thresholds are generated randomly at install: progress 80–90%, 150–200 wallets; the same for free and paid; **a paid project must really have a refund mechanism in the contract**, a whitepaper claim does not count); when progress is unreadable, mint if 150+ Agent ID wallets are minting and the pool has not opened yet. Nearly full or very hot projects are checked every 15 seconds, the rest take turns in the background about once a minute, several wallets mint at once, and each wallet stays ≤20U per project (adjustable in config).
- **Projects that cannot be minted are skipped automatically**: projects that need a website signature, a whitelist, or keep failing simulation are marked and retried later (after 30 minutes) and recorded in the log; the number of units the team opened (remaining quota / per-wallet cap / per-tx cap) is read first and followed, and when unreadable it steps down to 5, 3, 2, 1 units to try; when learning someone else's method the simplest one is preferred.
- **Contracts it does not understand can still be minted**: when progress is unreadable or the mint function is unknown, it learns other people's recently successful mint transactions (including ones paid in USDT, e.g. "USDT mint + referrer argument") and follows when 15+ wallets mint within 3 minutes; USDT is swapped from BNB and approved first, and the units are scaled up to at most 10 within budget.
- Four paid mint kinds are supported: BNB quote, USDT share, unit price × quantity NFT, and "just send BNB and it mints" (the price is the amount most people send). Not enough USDT is swapped from BNB automatically.
- Progress reading: look for a "sold / cap" function first; if there is none, look at "how many coins are left in the mint pool" (holding at deploy time vs now).
- **Only new ones**: old contracts deployed two days ago and projects whose pool already trades are not auto-minted.
- Contracts to mint directly by hand: add `"forceMint": ["0x…"]` to config (ignores progress and anti-phishing, only the 20U cap and the simulation checks apply; once minted it notifies only and never auto-sells).
- Every mint is simulated first: no approval changes, no assets deducted beyond the quote, something must really be received, otherwise it is not sent.
- **Transfer / approval style calls are not copied**: when copying someone's mint method, transfer, approve and NFT transfer calls are never copied, so freshly minted or existing coins cannot be sent to someone else.
- **A copied transaction carrying someone else's address** (possibly the recipient): it is swapped for your own address first, then the zero address; if neither works it is not minted, so the money does not pay for someone else's coins.
- **A free mint must really receive something**: if the preview shows a net of 0 (e.g. received and then moved away) it is not sent.
- **A failed mint after an approval cancels the approval automatically**, leaving no stray approvals.

**Anti-phishing / anti serial launching (hard blocks)**
- **Repeat DEV**: when one deployer launches 3+ **differently named** Agent ID projects within 24 hours, his free / fee projects are not minted. When a paid project meets the entry conditions: an earlier pool that opened normally, or just the same name redeployed → mint; no track record → only mint if the money did not flow straight out and at least 40 wallets minted; pools pulled, blacklisted, money flowed straight out → do not mint.
- **Blacklist**: after a deployer's earlier project "minted 48 hours ago and still no pool" or "crashed before break-even", all his later projects are never minted.
- **Same contract code deployed in bulk**: the same contract code deployed 3+ times within 24 hours is not minted (paid / fee).
- **Fresh wallet deploy**: a deployer wallet that only started being used within about 8 hours → paid ones are not minted.
- **Fake progress**: paid progress must come from at least 20 different wallets; when the money flowed straight out (the contract kept no BNB and no pool opened) it must be 40.
- **Fee kind**: only 1 unit per wallet per project, total fees ≤ 0.005 BNB per wallet per day; only fully free ones are minted repeatedly.

**Auto-sell (paid coins, executed when the price hits)**

| Condition | Action |
|---|---|
| 2x (`backX`) | sell to recover the cost |
| 10x / 20x (`steps`) | sell 30% of what is left / another 50% |
| 40% drop from the peak before break-even (`crash`), only after 2 consecutive reads | sell all |
| 60% drop from the peak after break-even (`crashAfterBack`) | sell everything outside the long hold |
| keep 20% after break-even (`moonPct`) | long hold, never auto-sold, notify at 50/100x |

All of it can be changed under `rules` in `config.json`; for example "2x break-even, sell 30% at 10x, another 50% at 20x, sell all on a 40% drop" is written as: `"rules": { "backX": 2, "steps": [[10, 30], [20, 50]], "crash": 0.4 }`.

Free coins: notify at 5x and 10x; the peak only starts being tracked 3 minutes after the pool opens (to avoid the opening spike), then everything is sold automatically on a 50% drop from the peak. Every sell is swapped back to BNB.

Peaks and multiples are always computed from the per-coin price (after selling a portion the position shrinks and is not misread as a crash); the best quote across all pools is used. A failed sell is retried after 1, 2, 4… up to 15 minutes, without spamming; dust under 0.3U is not sold.

**Safety and reminders (new in v2.3)**
- **Honeypot detection**: when the pool opens, "sending the coin into the pool" (the first step of a sell) is simulated first; if it fails you are warned that it can likely only be bought, not sold.
- **Contract power warning**: when a paid project is minted, the contract is also checked for developer powers like "can mint more / can pause trading / blacklist / can change tax"; warning only, never blocks.
- **Price reconfirmed before sending**: for BNB / USDT quote projects the quote is checked again before sending; if it rose more than 5% the send is skipped.
- **Low BNB warning**: notified when a wallet's BNB falls below the gas reserve + 0.02 (at most once every 6 hours).
- **Daily summary**: stats and positions are sent automatically every night at 9 pm (Taipei).
- **v2.6.5**: wording fix in the records — free projects no longer show "queued for mint" (free is notify only, never auto-minted).
- **v2.6.4**: free projects changed to **notify only, never auto-mint**; fixed a logic conflict (when progress was enough but the wallet count was not, the old rules could fall through and mint anyway); new paid check — if the contract holds almost no money (the payments were moved out) nothing is minted, and a team wallet holding over 30% triggers a warning; a program update during a sell now waits for the sell to finish before restarting.
- **v2.6.3**: **anti targeted draining** — teams started building projects against the published rules (progress exactly on the threshold, fee exactly on the cap). Everyone now gets their own random thresholds at install (progress 80–90%, 150–200 Agent ID wallets, written to the log at startup) that bad actors cannot guess; the participant count now only counts wallets that sent the transaction and paid gas, so airdrop inflation no longer works.
- **v2.6.2**: junk NFT detection — nothing is minted when an NFT's image link is fake (like `ipfs://sniper-test-nft/1`) or empty (projects that farm fees in bulk often do not even bother with an image).
- **v2.6.1**: at least **150** Agent ID wallets must be minting (also when progress is above 80%; the same 150+ when progress is unreadable).
- **v2.6.0**: **stricter entry conditions** — progress above 80% is not enough, 150+ Agent ID wallets must be minting too (teams inflated progress to farm fees); progress is now read from the actual minted count so a team reserve is not misread as nearly full. **Seven new risk controls** stop teams from draining BNB a little at a time with changing tricks: fees+gas ≤ 0.01 BNB per wallet per day (auto-mint stops for the day and notifies); at most 20 free projects auto-minted per day; the BNB deducted must not exceed the quote; a too-high single-transaction gas estimate is not sent; paid projects behind upgradeable contracts (the break-even mechanism can be swapped out afterwards) are not minted; a contract that fails on chain twice is not retried; below the BNB reserve it is kept for selling only. Selling is not affected.
- **v2.5.9**: entry threshold raised to **80%** — free and paid both need mint progress above 80% (paid also needs a real break-even refund mechanism in the contract); anything charging a fee is treated as paid.
- **v2.5.8**: restored "the same deployer deploying ≥10 projects within 24 hours is not minted" (judged only by the wallet that actually sent the deploy transaction).
- **v2.5.7**: **important — fee-charging "free" mints are always treated as paid**, needing 60%+ progress and a break-even refund mechanism in the contract (a flood of small projects used to drain BNB); minimal proxy resolution fixed; extra fallback nodes for the Agent ID count query and RPC; the sell strategy changed to 60% drop after break-even and a 20% long hold, notify only.
- **v2.5.4**: low-progress free projects are checked every 10 minutes (fewer pointless simulations, less node rate limiting); fixed progress misreads when sold exceeds the total supply; projects skipped by the rules no longer count as missed.
- **v2.5.3**: fixed launchpads that read progress as "sold / total" getting stuck at 0% (the bibiai kind, unnoticed even at 92%); a copied method hitting a price rise now retries step by step from +2% to +35% while within budget (it used to try +2% only).
- **v2.5.2**: entry threshold back to 60% (free and paid alike); a paid project is only minted when a refund mechanism is really in the contract, a whitepaper or website claim does not count.
- **v2.5.1**: **a paid project must have a break-even (refund) mechanism** — it is skipped when the contract has no refund function (refund／claimRefund／refundOpen and the like; for proxy contracts the implementation behind it is checked); free projects are unaffected. When no total-supply function is found it is searched for in the contract automatically.
- **v2.5.0**: 15 bugs from a second review round — `callChannelWatch` was missing from the open-source build (it broke the main loop) and generation now checks for it; the budget entered during install now takes effect; users without a group get minted/pool-open notices on their own TG; a re-learned copied amount is capped by budget and lowered units lower the payment proportionally; the "100 wallets" only counts Agent ID wallets; only one instance may run; the nightly summary date was fixed; boot autostart under a Chinese path; default sell rules changed to 2x break-even, sell 30% at 10x, another 50% at 20x, sell all on a 40% drop.
- **v2.4.9**: learning other people's methods and counting participants now rotate across several nodes (event queries on the default node were often rate limited, so nothing got learned and the count was wrong).
- **v2.4.8**: **fixed a missing `INFRA` definition in the open-source build that crashed every scan round (thanks for the user report)**; RPC nodes can be customised under `rpcs` in `config.json`; a full conflict sweep — progress misreads, no `token()` meaning no position, copied mints abandoned too fast, giving up when over budget, no further tracking after the first failure, sold-out projects holding slots, price source jumps causing wrong sells, and 10 other fixes.
- **v2.4.7**: learned "send BNB and it mints" contracts (it also learns when other people's transactions have no call data); when the amount most people send is over budget, the largest amount within budget is used instead.
- **v2.4.6**: back to "remind only, no auto-update"; the user replies /update when they want it.
- **v2.4.5**: entry threshold 60% → 55%; pool detection gained GeckoTerminal (prices readable for PancakeSwap Infinity and Uniswap v4 pools too); projects with a fixed 10,000 units no longer get low progress misread as unreadable and wrongly bought; special rules warn when the price falls below half the cost; the sell rules became the configurable `rules.steps` (multiple + sell percentage), dropping "sell all on 20 minutes without 2x" and the fast 35%. **Users on v2.4.4 or earlier had to download this version manually** because of the new price sites; a known-safe site list was added afterwards so it does not happen again.
- **v2.4.4**: a checksum mismatch caused by the GitHub cache right after an update now retries later automatically.
- **v2.4.3**: free projects near 60% retry every 20 seconds; the progress sweep no longer pulls sold-out projects back into the fast list; auto-update retries after 30 minutes on network trouble; install can still log in a 2nd wallet when keeping the existing settings.
- **v2.4.2**: switched to auto-update (safety scan, sha256 check, backup and automatic rollback kept); the official TG group is attached at first start and in the nightly summary.
- **v2.4.1**: update reminder + one-tap safe update (safety scan, sha256 check, backup, automatic rollback); the progress of every tracked project is lightly scanned every minute so a sudden jump on a low-progress project enters the fast check within 15 seconds (it used to look again only every 2–3 hours, so Pulsepanther went from 10% to 100% without being minted).
- **v2.4.0**: proxy contracts are always followed to the end (EIP-1967, Beacon slot, Beacon/implementation addresses hard-coded in the code, minimal proxy), so patterns like Flap's Agent Card NFT were missed before; the nightly summary lists "almost sold out but not minted" projects and why; a small-pool estimate now uses the real large pool's price to avoid a false crash.
- **v2.3.9**: only one mint per contract + wallet is queued at a time (no double minting); an unknown send result is not re-sent; free projects that only got in on heat are not minted after the pool opens; pool detection gained DexScreener; the USDT budget is strictly held within 20U; tokens minted on every paid path are auto-added to positions; one coin is sold only once at a time; group broadcasts are not repeated after a restart.
- **v2.3.8**: minted projects and pool opens go to the group only, not the personal TG (the personal one keeps only sells, multiples and warnings); one broadcast per project (minted or 70% progress, whichever comes first); NFTs are no longer wrongly reported as "cannot be transferred".
- **v2.3.7**: the paid fast check runs in 3 parallel groups (a round went from over a minute to about 10 seconds); group broadcasts every mint (free included) plus pool opens; projects that only got in on "100 wallets minting" are not minted when the pool already opened (avoid paying above market).
- **Fix (v2.3.6)**: free tokens (ERC20s issued in full from the start) no longer misread as 100% progress; a failed query is no longer taken as balance 0; the fast check only looks at projects near the threshold (max 12) so it does not keep slowing down; different coins with the same symbol no longer share a record; TG commands can sell "notify only" coins; a small-pool estimate counts at most 30% of the liquidity.
- **Entry rules simplified (v2.3.5)**: free or paid, mint once mint progress passes 60% (paid ≤ 20U per wallet per project); unreadable progress needs 150+ Agent ID wallets minting. The earlier heat tiers, repeat DEV and fresh wallet rules no longer block; only scam/honeypot safety checks stay (simulate every trade, no stray approvals, no unexpected asset deductions, honeypot check at pool open).
- **Pools with any pair are found (v2.3.5)**: it used to look only at PancakeSwap V2 pools paired with BNB/USDT/USD1, so pools paired with BNCB and other coins or opened on V3/other DEXes were missed. Flap-family quote coins were added, with DexScreener as a safety net, so any pool can be priced and sold by the rules.
- **Minted tokens are auto-added to positions (v2.3.5)**: ERC20 tokens from copied paid mints and manual top-ups are tracked too; for coins set to "notify only" the latest setting applies before a sell.
- **Watches the TG channel agentidcall (v2.3.5)**: contracts posted there are auto-added to tracking and the same rules decide whether to mint (all on your own machine).
- **Two more tries when a copy fails (v2.3.5)**: a fresh sample is taken; when the transaction carries a price cap, it is retried at cap +2% (still within budget).
- **A misread progress is corrected automatically (v2.3.4)**: some projects publish a huge total (e.g. 4.44 million units) but open the pool at 10,000 sold, and the tool used to read progress below 1% and never mint. A very low progress with many wallets already minting is now judged a misread and the stock algorithm is used instead; still implausible → follow the heat (3000+ already minted counts too).
- **Copied transactions get a fresh deadline (v2.3.4)**: some contracts need a deadline and the current price in every transaction, so copying an old one always fails. The newest successful transaction is fetched first and the deadline replaced with 15 minutes from now before sending.
- **No approvals to Flap (v2.3.3)**: the tool does not interact with Flap contracts and does not approve Flap; wallet approvals are checked every 10 minutes and an approval to Flap is revoked automatically with a notice (Flap's site was DNS-hijacked once).
- **Free projects only when hot (v2.3.3)**: fully free ones need 10+ wallets minting within 3 minutes; fee ones need 15+. Cold ones are tracked first and minted once hot. Fee cap 0.003 BNB per wallet per day. Saves the gas and fees of unpopular projects.
- **Small coins that cannot be sold are quiet (v2.3.3)**: coins under 3U do not notify on a failed sell and are given up after 6 tries; valuable ones notify once on the first failure.
- **Mints that need your own Agent ID number (v2.3.2)**: projects like `mint(agentTokenId)` that need your .ai identity number are minted with the wallet's own number; when copying someone else's transaction the other person's number is swapped for yours. The number is computed from the wallet `name` in config (e.g. `mywallet.ai`), **so put your real .ai name in `name`**.
- **A bought coin that cannot be transferred is flagged (v2.3.2)**: right after a paid mint the coin is test-transferred; a whitelist lock (trading has to be opened by the developer, or it is a honeypot) adds a warning to the notice.
- **USDT-paid projects that cannot be minted are not retried forever (v2.3.2)**: at most 2 more tries, so USDT is not swapped and lost every half hour.
- **Node rotation (v2.3.1)**: event queries rotate across 6 free nodes and move on when one is rate limited; only when all are limited does it pause for 30 seconds and then catch up on the skipped blocks instead of skipping them.
- **The daily summary includes the "could not mint" count**: projects that were not minted that day because of a website signature, whitelist or simulation failure are only mentioned in the summary, without a separate notice.
- **No restart while a transaction is being sent**: before a program update or watchdog restart the in-flight transaction is allowed to finish; an unknown send result (timeout) is not re-sent, you are asked to check instead.

**Notifications**: Telegram only rings for the important things (buying a paid project, sells, pool opens, 5/10x, large pullbacks, wallet login expiry, the scan lagging ~3 minutes); minting a free project sends no notification by default (it only counts in the stats and the 9 pm summary; `"notifyFree": true` in config sends it silently); new contracts and the like are only logged to `data/watch.log`. To receive everything add `"notifyAll": true` to config. You can also put a group ID in `telegram.groupChatId`: **the first time any project's mint progress passes 70%** (free or paid), **minted projects (free and paid)** and **pool opens** are broadcast to the group, each event only once.

**TG commands (second-level manual selling)**: message your bot directly and the tool executes within seconds, without a restart or waiting for anyone:

| Type this | Action |
|---|---|
| `sell XXX` or `XXX all` | every wallet sells all XXX (swapped back to BNB) |
| `sell XXX half` | every wallet sells half |
| `balance` | report the BNB of each wallet |
| `positions` | coins not sold yet and their current value |
| `stats` | how many projects were minted and how much each sell returned since launch |
| `help` | list the commands |

XXX can be a token symbol or contract address (it must be a position the tool is tracking). Only messages from the person in `telegram.chatId` are accepted, and messages older than 2 minutes are not executed.

**NFT volume alerts**: every 12 hours the real Element fills of the NFT collections you hold are checked (only paid ones count, wallet-to-wallet transfers do not), and a sudden rise in volume sends a TG notice.

---

## Install

**Easiest on Windows: download the ZIP, unpack it, then double-click `install.bat`** and follow the prompts for the wallet and TG; confirm the login in the Binance App and it runs in the background from then on, starting at boot.

Manual install below. You need: Windows (or macOS / Linux), Node.js 20+, Agentic Wallet enabled in the Binance App with a .ai Agent ID claimed, and **developer mode** on.

```bash
npm install -g @binance/agentic-wallet
git clone <this repo>
cd agentid-sniper
copy config.example.json config.json   # use cp on macOS/Linux
```

Edit `config.json`:
- `wallets`: your Agent ID wallet addresses. **One wallet is enough** (it uses the system-installed `baw`). Add more only when you want several wallets; from the second on, run `setup_wallet2.bat` first (a separate baw copy, so they do not kick each other offline) and add `"bawDir": "baw2", "bawPkg": "baw2pkg", "instanceId": "wallet-2"` to that wallet (optional).
- `telegram`: the bot token (create one with @BotFather) and your chat id.
- `paid` / `rules`: budget and sell rules.
- `tokens`: positions to track manually (e.g. `{ "sym": "XXX", "addr": "0x…", "cost": 10, "both": true }`; `hold: true` notifies only; `sellNow: true` sells everything at once).

Log in a wallet (it prints a link; open it on the phone with the matching Binance account):

```bash
node login.mjs 1
# with a second wallet: node login.mjs 2
```

## Run

```bash
start_hidden.vbs        # run in the background on Windows (auto-restarts)
# or
node watch.mjs
```

- Saving a change to `watch.mjs` reloads it automatically.
- Stop: create a `WATCH_STOP` file in the same folder.
- The log is `data/watch.log`, the heartbeat is `data/watch_heartbeat.txt` (including how many blocks the scan is behind).
- Built-in watchdog: a main loop stuck for more than 8 minutes restarts it.

## Notes

- With public nodes about 130 full blocks have to be downloaded every minute, **which can be several GB a day**, so use an unmetered connection.
- Binance Agentic Wallet simulates every transaction before executing it, about 10–15 seconds, **so it cannot outrun professional MEV bots**; the edge here is the Agent ID exclusive lane (bots without a .ai identity cannot mint).
- Developer mode expires; you are reminded 24 hours before it does.
- Logging the same AI wallet in on a phone kicks the computer off; the tool sends you a login link.

## Credits

- Original project: **agentid-sniper** by [@nancyc0813](https://github.com/nancyc0813-maker/agentid-sniper)
- Official TG group: https://t.me/nancy_c0813
- This repository is a fork: `setyamickala/fork-agentid-sniper`

## License

MIT
