# Scroll-Snap Vertical Swipe + Inline Music Player

When the user says "rombak page-nya jadi swipe ke bawah kaya [site]" and you have the **local source code** of the reference site (not a live URL to scrape), replicate the scroll-snap mechanic directly from the local HTML.

## Replicating scroll-snap from local source

Unlike `clone-site-aesthetic`'s standard approach (scraping compiled CSS/JS bundles from a live URL), when the reference is a **local project** on the same device:

1. **Read the local HTML** — `read_file ~/reference-site/index.html` to get the full source, including inline `<style>` and `<script>` blocks.
2. **Extract the scroll-snap pattern:**
   - `scroll-snap-type: y mandatory` on `<html>` or container
   - Each section: `height: 100svh`, `scroll-snap-align: start`, `scroll-snap-stop: always`
   - JS `scroll` listener with `requestAnimationFrame` throttling that updates opacity, transform, and blur on each `.chapter-content` based on active page
   - Page indicator dots + top bar with page count
3. **Adapt the blur/opacity transition** — the reference uses `opacity: 0→1`, `transform: translateY(14px)→translateY(0)`, `filter: blur(2px)→blur(0)` on the active page. Non-active pages get the blurred state.
4. **Keep the user's original content** — replace the reference's section text with the user's existing messages/cards. Don't copy the reference's identity.

## Building a Spotify-like inline music player

When the user asks for "3 lagu dengan tampilan kaya Spotify" inside a scroll-snap site:

### HTML structure
```html
<div class="player-card">
  <div class="album-art">
    <img id="albumCover" src="assets/cover.png" alt="">
    <div class="playing-ring"></div>
  </div>
  
  <div class="now-playing">Now Playing</div>
  <div class="song-title">Song Name</div>
  <div class="song-artist">Artist Name</div>
  
  <div class="progress-wrap">
    <span class="progress-time" id="currentTime">0:00</span>
    <div class="progress-track">
      <div class="progress-fill" id="progressFill"></div>
    </div>
    <span class="progress-time" id="totalTime">0:00</span>
  </div>
  
  <div class="controls">
    <button id="prevBtn">⏮</button>
    <button class="play-btn" id="playBtn">▶</button>
    <button id="nextBtn">⏭</button>
  </div>
  
  <div class="playlist" id="playlist"></div>
</div>
```

### Song library (JS data structure)
```javascript
const songs = [
  { file: 'assets/song1.mp3', title: 'Shape of My Heart', artist: 'Sting', cover: 'assets/cover1.png', duration: '3:48' },
  { file: 'assets/song2.mp3', title: 'Song 2', artist: 'Unknown', cover: 'assets/cover2.png', duration: '1:01' },
  { file: 'assets/song3.mp3', title: 'Song 3', artist: 'Unknown', cover: 'assets/cover3.png', duration: '3:00' }
];
```

### Critical JS patterns
- **Play/Pause toggle**: check `audio.src` first, set it if empty, then `audio.play()` / `audio.pause()`
- **Next/Prev**: `selectSong((currentSong + 1) % songs.length)` — wrap around with modulo
- **Progress bar**: `audio.timeupdate` listener sets `progressFill.style.width = (currentTime/duration * 100) + '%'`
- **Seek**: `progressTrack click` → compute `(clientX - rect.left) / rect.width * audio.duration`
- **Format time**: `m + ':' + String(sec).padStart(2, '0')`
- **Auto-next on end**: `audio.addEventListener('ended', () => nextSong())`
- **Playlist highlighting**: `playlist-item.active` for current song, `playlist-item.playing` when actively playing

### CSS notes
- Album art: `width: min(280px, 70vw); height: min(280px, 70vw); border-radius: 20px; box-shadow` with a pulsing ring on play
- Play button larger than nav buttons: `width: 60px; height: 60px; background: linear-gradient(...)`
- Progress fill: `linear-gradient(90deg, accent, accent2)` with a 10px white dot handle on hover
- Playlist items: glass background, border, active state with accent border

### Audio metadata pitfall
MP3 files on Android often have **empty metadata tags** (no title/artist). You cannot identify a song from the audio alone. Either:
- Ask the user for the title and artist
- Check tags with `ffprobe -v quiet -print_format json -show_format file.mp3` and parse the `tags` key
- Use placeholder names and let the user correct them

## Process management: killing orphaned HTTP servers

When a Python HTTP server started with `&` in a shell command is killed via `process(action='kill')`, only the **bash wrapper** dies — the actual Python process may survive. Always verify:
```bash
# Check if process is still alive
curl -s -o /dev/null -w "%{http_code}" http://localhost:PORT

# Force kill by PID
ps aux | grep 'python.*http.server.*PORT' | grep -v grep
kill <PID>
```
Or use `pkill -f "python3 -m http.server PORT"` for a clean kill.