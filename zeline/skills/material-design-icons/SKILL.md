---
name: material-design-icons
description: Dipakai saat Zeline membuat, mendesain, atau merevisi UI (HTML, React, Vue, Next.js, Telegram Mini App, dsb.) agar menggunakan ikon resmi dari Google Material Design Icons / Material Symbols.
category: frontend
---

# Material Design Icons Standard

> Dipakai saat Zeline membuat, mendesain, atau merevisi UI (HTML, React, Vue, Next.js, Telegram Mini App, dsb.) agar menggunakan ikon resmi dari Google Material Design Icons / Material Symbols.

## Overview
Repo resmi: `google/material-design-icons` (Google Material Symbols & Icons).
Skill ini memastikan konsistensi ikonografi di seluruh artefak UI yang dihasilkan Zeline.

## Standard Implementation Rules

### 1. Web Statis & Prototyping (HTML/CSS Vanilla)
Selalu tambahkan stylesheet Material Symbols ke `<head>`:

```html
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" />
```

Gunakan kelas `material-symbols-outlined` dengan teks nama ikon di dalam tag:

```html
<!-- Contoh penggunaan dasar -->
<span class="material-symbols-outlined">search</span>
<span class="material-symbols-outlined">settings</span>
<span class="material-symbols-outlined">home</span>
<span class="material-symbols-outlined">account_circle</span>
```

### 2. React / Next.js Projects
Gunakan paket `@mui/icons-material` atau SVG React components jika menggunakan NPM:

```bash
npm install @mui/icons-material @mui/material @emotion/react @emotion/styled
```

```jsx
import SearchIcon from '@mui/icons-material/Search';
import SettingsIcon from '@mui/icons-material/Settings';

<SearchIcon />
<SettingsIcon />
```

### 3. Inline SVG (Offline / No External CDN)
Jika project melarang CDN eksternal atau offline, gunakan elemen `<svg>` inline dengan viewBox `0 0 24 24` yang diambil dari SVG resmi repo `google/material-design-icons`.

## Styling & Variant Guide

1. **Size Tuning (Font-size & Opsz)**:
   ```css
   .material-symbols-outlined {
     font-size: 24px;
     vertical-align: middle;
     user-select: none;
   }
   ```
2. **Filled vs Outlined**:
   Untuk ikon aktif/selected, gunakan CSS variation settings:
   ```css
   .material-symbols-outlined.filled {
     font-variation-settings: 'FILL' 1, 'wght' 400, 'GRAD' 0, 'opsz' 24;
   }
   ```
3. **Warna & Accessibility**:
   Ikon mewarisi `color` dari elemen induknya (`currentColor`). Selalu pastikan kontras cukup dan berikan `aria-label` jika ikon berdiri sendiri tanpa teks tombol.

## Common Icon Names Reference
- **Actions**: `search`, `settings`, `delete`, `edit`, `add`, `close`, `check`, `refresh`, `download`, `share`, `more_vert`, `more_horiz`
- **Navigation**: `arrow_back`, `arrow_forward`, `chevron_right`, `expand_more`, `menu`, `home`
- **User & Status**: `account_circle`, `person`, `notifications`, `verified`, `error`, `info`, `warning`, `help`
- **Media & Web**: `image`, `videocam`, `link`, `code`, `content_copy`, `file_download`
