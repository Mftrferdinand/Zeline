# Self-Improvement & Autonomous Fix [Zeline Zenith]

> Self-Improvement & Autonomous Fix — modul Zeline Zenith (sumber: zeline-zenith-z55).

# Load when: self-improvement, self-fix, auto-improve, auto-patch, upgrade diri, perbaiki sendiri
# Category: Meta & Self-Improvement

## What this is

Mode yang bikin agent **makin pinter tiap hari**: belajar dari tiap sesi, mendiagnosa masalah sendiri, **auto-fix hal operasional yang reversible tanpa diminta**, dan ngusulin upgrade buat dirinya — **lewat gerbang yang gak bisa di-bypass**.

This skill is an architecture blueprint. It does not bundle `memory_engine.py`,
`reflection.py`, or an integrity manifest. Implement and test those components
inside the target project before using the examples below.

---

## Kenapa ada gerbang (baca dulu)

Agent yang bebas nulis-ulang dirinya sendiri bisa: drift, ngerusak diri, atau **ngehapus rail-nya sendiri** (governor, integrity check) dan akhirnya nguras dana operator. Self-improvement di sini **bukan** "agent bebas ubah apa aja". Ia:

- **Belajar & nyimpen pengetahuan** — bebas, aman.
- **Auto-fix masalah ops reversible** — sendiri, dari allowlist sempit.
- **Ngusulin sisanya** — proposal buat operator, gak pernah auto-apply.

---

## Loop harian (dipanggil dari HEARTBEAT)

```
1. OBSERVE  → tiap task di-log ke memory (apa diminta, apa works, apa gagal, error+resolusi)
2. REFLECT  → scan memory terbaru: kegagalan berulang? jalur lambat? fix manual yang sama terus?
3. LEARN    → distill jadi 'lesson' di memory_engine → next time situasi sama, lesson kepake
4. DIAGNOSE → pas ada masalah, jalanin pola zeline-zenith-z54 (debug) OTOMATIS tanpa diminta
5. AUTO-FIX → kalau action ∈ SAFE_AUTO_ACTIONS → beresin sendiri
6. PROPOSE  → selain itu → tulis proposal ke antrian, kabarin operator
```

Memory yang kepake & kebukti berguna → di-reinforce (weight naik) → makin sering muncul. Itu mekanisme "makin pinter": yang berguna naik, yang gak relevan decay.

---

## Instinct extraction (v4.1)

"Lesson" mentah numpuk jadi noise. Instinct = lesson yang **berulang & kebukti** dipadatkan jadi aturan refleks yang kepake otomatis sebelum mikir.

```
1. CLUSTER  → kelompokin lesson mirip di memory (mis. 5× "RPC X timeout → fallback Y")
2. PROMOTE  → kalau 1 cluster kebukti ≥N kali & konsisten → angkat jadi 'instinct'
3. APPLY    → instinct dicek di awal task sejenis, tanpa nunggu gagal dulu
4. DECAY    → instinct yang berhenti kebukti (mulai meleset) → turun lagi jadi lesson biasa
```

Instinct yang reversible & operasional (pilih RPC, urutan retry, default param aman) → langsung kepake. Yang nyentuh skill/dana/rail → tetap lewat **proposal** (gak pernah jadi reflex otonom). Instinct disimpan di `memory_engine` dengan weight tinggi; bukan file skill baru — biar gak nabrak frozen-paths & integrity.

> Ini bedanya "agent yang nyatet" vs "agent yang makin tajam": bukan cuma inget, tapi naikin yang berulang jadi default. Dikawal `tools/eval.py` (z56) — instinct yang variance-nya jelek gak boleh jadi reflex.

---

## HARD LIMITS (tidak bisa di-override oleh mode ini)

```
NEVER autonomously:
✗ Sign tx / mindahin dana / approve spending
✗ Ubah spend cap atau matiin governor
✗ Regenerate SKILLS.lock (itu langkah operator — biar integrity tetap bermakna)
✗ Edit file FROZEN (SOUL, AGENTS, governor.*, mev.py, security.md,
  skill_integrity.py, reflection.py, USER.md, z55.md)
✗ Apply proposal-nya sendiri
```

In an implementation, enforce this structurally: `guard_write(path)` should
reject every protected path. A prompt-only rule is insufficient.

### SAFE_AUTO_ACTIONS (satu-satunya yang boleh tanpa konfirmasi)

```
retry_with_fallback_rpc · rotate_rpc · switch_llm_provider ·
restart_crashed_process · clear_cache · requote · backoff_and_retry
```

Semua reversible, gak nyentuh dana, gak ubah skill. Apa pun di luar ini → jadi proposal.

---

## Alur proposal (gimana agent "upgrade" dirinya dengan aman)

```
agent nemu perbaikan → reflection.propose(...) → project-local proposal record
   → operator review → kalau setuju, operator ubah manual
   → operator reviews and records the approved change using the project's actual integrity process
```

Operator yang nutup loop. Ini sengaja: kalau agent bisa apply + re-sign sendiri, integrity check jadi teater. Friksi di sini = fitur.

---

## Wiring ke task biasa

```python
from memory_engine import MemoryEngine
from reflection import daily_cycle, auto_fix, can_autofix, propose, Proposal

mem = MemoryEngine()

# setiap kali sesuatu gagal lalu ketemu solusinya:
mem.remember("Base RPC ankr timeout → llamarpc works", "blocker", tags="rpc,base")

# pas ada error operasional, coba auto-fix dulu:
if can_autofix("retry_with_fallback_rpc"):
    auto_fix("retry_with_fallback_rpc", handlers=my_handlers)

# di akhir sesi (HEARTBEAT):
report = daily_cycle(mem)   # learn + hitung proposal pending
```

Audit semua aksi otonom in a project-local, gitignored audit log.

---

## Output ke operator

Pas mode ini aktif & ada sesuatu, agent lapor ringkas (bukan diam-diam):
> `🔧 auto-fix: rotate RPC Base (ankr timeout). | 📝 1 proposal baru: "fallback RPC permanen" — review di proposal store proyek.`

Hal otonom yang berhasil = lapor setelahnya. Hal yang butuh keputusan = proposal, tunggu operator.
