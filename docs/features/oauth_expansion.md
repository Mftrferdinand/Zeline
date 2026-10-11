# OAuth Expansion — Notion, Linear, GitLab, Spotify, X

Selain GitHub, Google, Slack, dan Discord, framework OAuth2 Zeline sekarang
mendukung lima provider baru. Menambah provider hanya berupa entry config di
`zeline/oauth.py` (`OAUTH_PROVIDERS`) — alur PKCE S256, state, dan token
exchange sudah generik.

| Provider | Cara pakai | Scope default | Catatan |
|---|---|---|---|
| notion | `zeline connect notion` | *(kosong — Notion tidak pakai scope)* | Integrasi harus **public**; token endpoint pakai HTTP Basic auth + body JSON |
| linear | `zeline connect linear` | `read` | Token exchange ke `https://api.linear.app/oauth/token` (bukan `linear.app`) |
| gitlab | `zeline connect gitlab` | `read_user read_api` | Alur authorization code + PKCE |
| spotify | `zeline connect spotify` | `user-read-private` | Token endpoint pakai HTTP Basic auth |
| x | `zeline connect x` | `tweet.read users.read` | PKCE S256 **wajib** (selalu dikirim oleh flow) |

## Cara setup

1. Daftarkan OAuth app di provider (Notion: integration **public** di
   notion.so/my-integrations; X: app dengan OAuth 2.0 + PKCE enabled di
   developer portal).
2. Redirect URI yang didaftarkan harus localhost — CLI Zeline memakai
   `http://127.0.0.1:<port acak>/callback`.
3. Jalankan `zeline connect <provider>`. Saat diminta, tempelkan **Client ID**
   dan **Client Secret** — disimpan di `~/.zeline/oauth-apps/<provider>.json`
   dengan mode `0600`. Alternatif: env var
   `ZELINE_OAUTH_<PROVIDER>_CLIENT_ID` / `ZELINE_OAUTH_<PROVIDER>_CLIENT_SECRET`.
4. Buka URL yang dicetak di browser, setujui akses, dan tunggu callback.
   Token tersimpan via `zeline.connectors.store` (mode `0600`).

## Catatan per provider

- **Notion** tidak punya scope OAuth — hak akses diatur di integrasi Notion
  (pilih halaman/database saat authorize) dan param `owner=user` otomatis
  ditambahkan ke URL authorize. Token tidak membawa `expires_in` secara
  publik; refresh tidak didukung framework ini untuk Notion.
- **Linear**: token berlaku 24 jam; `refresh_if_needed()` memakai refresh
  token otomatis bila tersedia. App OAuth bersifat per-workspace.
- **X**: access token kedaluwarsa 2 jam. Scope di atas tidak mencakup
  `offline.access`, jadi tidak ada refresh token — koneksi ulang diperlukan
  setelah token habis. Tambahkan `offline.access` di app X bila ingin refresh.
- **GitLab**: scope bisa diganti saat connect via parameter `scopes`
  (framework), misalnya read-only penuh.
- Client secret **tidak pernah di-log** — pesan error hanya memuat status
  HTTP atau deskripsi error provider, bukan nilai secret.

## Security

- `state` bersifat single-use dan kedaluwarsa 10 menit (CSRF).
- PKCE S256 selalu dipakai; verifier tidak pernah keluar dari proses kecuali
  lewat token exchange TLS.
- `redirect_uri` wajib localhost — flow menolak URL non-localhost.
- Token endpoint selalu diambil dari registry internal, bukan dari input
  user (tidak ada jalur user-controlled URL).
