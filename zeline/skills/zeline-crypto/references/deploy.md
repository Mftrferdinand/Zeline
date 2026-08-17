# Crypto Developer: Compile / Test / Deploy / Verify (v4.0)

Bikin agent bisa **bangun & deploy kontrak**, bukan cuma manggil yang udah ada. Pipeline penuh: scaffold → compile → test → deploy → verify. Pasangan dari contract reader/writer (v4.0).

Script: `scripts/deploy_engine.py`. Tools: Foundry (`forge`, gratis). Dependency: web3, httpx.

## Pipeline

```
scaffold (Foundry + OpenZeppelin)
   → forge build (compile)
   → forge test (test, lokal, gratis)
   → deploy  ← LEWAT governor (deploy = tx, ngeluarin gas)
   → verify Sourcify (KEYLESS)
```

Compile/test itu lokal & read-only → gak perlu gate. **Deploy ngeluarin dana (gas) → gerbang governor WAJIB**, sama kayak contract_writer.

## Compile & test (lokal, gratis)

```python
from deploy_engine import compile_foundry, run_tests, load_artifact
compile_foundry("./my-project")          # forge build
run_tests("./my-project", match="testMint")
art = load_artifact("./my-project", "MyToken")   # {abi, bytecode, metadata}
```

Install Foundry sekali: `curl -L https://foundry.paradigm.xyz | bash && foundryup`.

## Deploy (gated)

```python
from deploy_engine import deploy
res = await deploy(art["abi"], art["bytecode"], account=acct, chain_id=8453,
                   constructor_args=["MyToken", "MTK", 18],
                   usd_value=gas_est_usd, confirm_cb=confirm, private=True)
print(res.status, res.address)   # "deployed" + alamat kontrak
```

Gerbang: build → estimate gas (gagal = revert constructor, ketahuan di sini) → `governor.authorize` → konfirmasi → sign → send → `governor.record`.

## CREATE2 — address deterministik (sama di semua chain)

```python
from deploy_engine import compute_create2_address, CREATE2_FACTORY
addr = compute_create2_address(salt, init_code)   # prediksi address SEBELUM deploy
```

Pakai deterministic deployment proxy (`0x4e59...4956C`, ada di hampir semua chain EVM) → kontrak lo dapet **address yang sama** di Ethereum, Base, Arbitrum, dst. Bagus buat multi-chain protocol.

## Verify (Sourcify, keyless)

```python
from deploy_engine import verify_sourcify
verify_sourcify(res.address, chain_id=8453, metadata_json=art["metadata"],
                sources={"src/MyToken.sol": solidity_source})
```

Setelah verified, kontrak lo langsung bisa dibaca contract_reader (ABI auto-fetch). Lingkaran penuh.

## Scaffolding (template aman)

Pakai OpenZeppelin buat standar (ERC-20/721/1155, AccessControl, ReentrancyGuard) — jangan reinvent. Struktur Foundry: `src/` `test/` `script/` `foundry.toml`.

## Integrasi

- **Audit dulu sebelum deploy** → sk11 (Solidity red flags: reentrancy, tx.origin, unbounded loop, owner mint/burn tanpa timelock).
- **Setelah deploy** → contract_reader/writer (v4.0) buat interaksi.
- **Mint NFT collection sendiri** → setelah deploy ERC-721, pakai sk13 buat mint.

## Catatan

- `forge` harus keinstall di VPS (gratis). Kalau gak ada → engine kasih instruksi install, gak crash.
- Endpoint Sourcify bisa berubah; verify-at-build.
- CREATE2 factory address gue assume standar Arachnid — cek ke-deploy di chain target dulu buat chain non-mainstream.
- Deploy ke mainnet itu irreversible & makan gas nyata — selalu test di fork/testnet dulu (lihat shadow/dry-run kalau udah dibangun).
