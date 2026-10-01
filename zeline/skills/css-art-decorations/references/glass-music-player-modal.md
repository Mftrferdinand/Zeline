# Liquid-glass music player modal (with spinning disc)

Pola modal pemutar lagu untuk kartu dekoratif (dipakai di `~/twenty3ph/`, amplop
ke-3 kartu "For Puji"). Pure HTML/CSS/JS, satu `<audio>` element, tanpa library.

## Anatomi

```html
<div class="music-modal" role="dialog" aria-modal="true" aria-hidden="true">
  <section class="music-player">
    <button class="music-close">×</button>
    <span class="music-kicker">A song for you</span>
    <div class="music-disc" aria-hidden="true"><i></i></div>   <!-- vinyl -->
    <h2>All We Know</h2>
    <span class="music-artist">The Chainsmokers</span>          <!-- judul + artis -->
    <p class="music-lyric">lirik<br>...</p>                     <!-- scrollable -->
    <div class="music-progress-wrap">
      <input class="music-progress" type="range" min="0" max="1000" value="0">
      <div class="music-times"><span class="music-current">0:00</span>
        <span class="music-duration">0:40</span></div>
    </div>
    <button class="music-toggle"><span></span></button>         <!-- play/pause -->
    <audio class="music-audio" preload="metadata" src="assets/song.mp3?v=1"></audio>
  </section>
</div>
```

## Panel liquid glass

- `.music-modal`: `position:fixed;inset:0;display:grid;place-items:center`; backdrop
  di-blur via `backdrop-filter:blur(11px)` yang hanya aktif pada `.is-visible`
  (transisi opacity + visibility delay biar fade halus, bukan pop).
- `.music-player`: `background:linear-gradient(145deg,rgba(255,255,255,.3),rgba(255,255,255,.1))`
  + `backdrop-filter:blur(24px) saturate(155%)` + border `rgba(255,255,255,.36)` +
  `box-shadow: inset 0 1px 0 rgba(255,255,255,.55), 0 30px 70px ...`. Masuk dengan
  `transform:translateY(18px) scale(.96)` → `translateY(0) scale(1)` saat visible.
- Lebar `min(350px,90vw)`, `max-height:min(720px,88svh)`; lirik panjang pakai
  `.music-lyric{overflow-y:auto;max-height:260px}` dengan scrollbar tipis.

## Spinning vinyl disc

Disc monokrom yang berputar HANYA saat lagu main (class `.is-playing` di-toggle JS):

```css
.music-disc{width:118px;height:118px;border-radius:50%;
  background:radial-gradient(circle at 50% 50%,#3a2530 0 17%,#1f131b 17.5% 100%);
  box-shadow:inset 0 2px 7px rgba(255,255,255,.22),inset 0 -6px 14px rgba(0,0,0,.4),0 16px 34px rgba(28,13,20,.5)}
.music-disc::before{inset:6px;background:repeating-radial-gradient(circle,rgba(255,255,255,.045) 0 1.5px,transparent 1.5px 5px)} /* alur */
.music-disc::after{inset:0;background:conic-gradient(from 0deg,transparent 0 40%,rgba(255,255,255,.14) 50%,transparent 60% 100%);opacity:.5} /* kilau */
.music-disc i{width:40px;height:40px;border-radius:50%;background:radial-gradient(circle at 36% 30%,#ffe6f0,#e79cbd 55%,#c76d95)} /* label pink */
.music-disc i::after{width:5px;height:5px;border-radius:50%;background:#1f131b} /* spindle */
.music-player.is-playing .music-disc{animation:discSpin 3.6s linear infinite}
@keyframes discSpin{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){.music-player.is-playing .music-disc{animation:none}}
```

## JS (satu `syncMusic` untuk semua state)

- Bind `['loadedmetadata','timeupdate','play','pause','ended']` semua ke satu
  `syncMusic()` — update progress value, waktu (`formatTime`), toggle `.is-playing`,
  dan aria-label. Satu fungsi = tidak ada state yang out-of-sync.
- Fallback durasi kalau metadata belum ada: `Number.isFinite(musicAudio.duration) ? musicAudio.duration : <detik>`.
- Progress bar `<input type=range>`: `input` handler set `currentTime = value/1000*duration`.
- **Tutup modal harus stop lagu**: `closeMusic()` panggil `musicAudio.pause()` dulu.
- Buka via elemen pemicu (mis. `.flower-letter[data-index="3"]`), tutup via tombol
  ×, klik area luar (`event.target===modal`), dan Escape.

## Multi-song (playlist 2+ lagu)

Pola yang dipakai di twenty3ph setelah iterasi user:

- Data di array `MUSIC_SONGS=[{title,artist,cover,src,fallback,lyric}]`. `applySong(i)`
  set semua field + `musicAudio.src` + `musicAudio.load()` + reset progress ke 0 dan
  `--fill:0%`. `switchSong(i)` pause dulu, `applySong`, lalu resume kalau tadi playing.
- Navigasi: tombol `‹` / `›` (`.music-skip[data-dir="-1|1"]`) wrap modulo panjang array.
- Indikator lagu: **user minta TANPA dot indicator**. Ganti dengan teks `"1 of 2"`
  (`.music-count`) di top-bar sejajar kicker `A song for you`
  (`.music-topbar{display:flex;justify-content:space-between}`), update di `applySong`
  via `` musicCount.textContent=`${i+1} of ${MUSIC_SONGS.length}` ``.

## Ukuran seragam antar lagu (preferensi keras Aes)

- **JANGAN** kasih override per-lagu (`.is-song2 .music-lyric{font-size:...}`) — user
  marah kalau font judul/lirik beda antar lagu. Semua lagu pakai ukuran dasar yang sama.
- Judul lagu wajib **1 baris**: `white-space:nowrap;overflow:hidden;text-overflow:ellipsis`
  + font diperkecil (mis. 17px, bukan 22px) supaya judul panjang ("Just the Way You Are")
  muat 1 baris tanpa wrap. Samakan juga di `@media(max-height:650px)`.
- **Card jangan panjang ke bawah / banyak space kosong**: pakai `height:auto` (fit
  content), bukan `height:min(600px,..)` tetap atau `max-height` gede — tinggi ngikut isi.
  Jangan center lirik vertikal dengan `flex:1;justify-content:center` (bikin space kosong).

## Seek / scrub bar (fix maju-mundur durasi)

Bug umum: user gak bisa geser progress buat maju/mundur. Fix:

```js
const seekTo=()=>{
  const dur=Number.isFinite(musicAudio.duration)&&musicAudio.duration>0?musicAudio.duration:MUSIC_SONGS[musicIndex].fallback;
  const t=musicProgress.value/1000*dur;
  musicProgress.style.setProperty('--fill',(musicProgress.value/10)+'%');
  musicCurrent.textContent=formatTime(t);
  if(Number.isFinite(t))try{musicAudio.currentTime=t}catch(e){}
};
musicProgress.addEventListener('pointerdown',()=>{musicSeeking=true});
musicProgress.addEventListener('input',()=>{musicSeeking=true;seekTo()});
const endSeek=()=>{if(musicSeeking){seekTo();musicSeeking=false}};
['change','pointerup','pointercancel'].forEach(e=>musicProgress.addEventListener(e,endSeek));
```

Kunci: `pointerdown/up` lock `musicSeeking` (biar `tickMusic` gak override value pas
digeser), guard `currentTime` dengan `try/catch`. Kalau MASIH macet: server harus support
HTTP byte-range request untuk mp3 (seek butuh `Accept-Ranges: bytes`) — cek dengan
`curl -I -H "Range: bytes=0-1" http://PORT/assets/song.mp3` harus `206 Partial Content`.

## Pitfalls

- Ganti lagu/lirik: edit judul (`<h2>`), artis (`.music-artist`), isi `.music-lyric`
  (pisah baris pakai `<br>`), dan `src` audio SEKALIGUS — jangan biarkan lirik lama
  nyangkut saat judul sudah ganti.
- Bump `?v=N` di `src` audio kalau file diganti tapi nama sama (hindari cache).
- Verifikasi mp3 benar-benar dilayani: `curl -o /dev/null -w "%{http_code}" http://localhost:PORT/assets/song.mp3` harus 200, cek juga ukuran file wajar.
- Kecilkan disc di layar pendek via `@media(max-height:650px){.music-disc{width:92px;height:92px}}` biar panel tidak overflow.
