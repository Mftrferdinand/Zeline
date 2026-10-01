# Drag-to-open notification-shade nav

Concept Aes wanted: a top bar (centered wordmark + a bare chevron "V" below it, no bubble) that you pull DOWN like an Android notification shade. The menu panel is hidden ABOVE the viewport; dragging the bar down brings the panel into view and the bar (wordmark + V) gets dragged down with it. Bar + panel move as one unit.

## Structure

```html
<header class="site-header">
  <div class="shade" id="shade">
    <nav class="shade-menu wrap" id="shadeMenu">…links…</nav>   <!-- ABOVE the bar -->
    <div class="shade-bar" id="shadeBar">                       <!-- the handle -->
      <span class="brand"><img src="wordmark-ink.png" …></span>
      <span class="pull-v"><svg viewBox="0 0 18 8"><polyline points="2,2 9,6 16,2"/></svg></span>
    </div>
  </div>
</header>
<div class="header-spacer"></div>   <!-- reserve space; header is position:fixed -->
```

## CSS

```css
.site-header{position:fixed;top:0;left:0;right:0;z-index:50}
.shade{position:relative;transform:translateY(calc(-1 * var(--menuH,260px)));
  transition:transform .44s cubic-bezier(.22,1,.36,1)}
.shade.dragging{transition:none}          /* follow finger 1:1 while dragging */
.shade.open{transform:translateY(0)}
.shade-menu{background:#fff;border-bottom:1px solid var(--line);padding:18px 0 14px}
.shade-menu a{display:block;text-align:center;padding:13px 0;border-bottom:1px solid var(--line)}
.shade-bar{position:relative;background:rgba(244,241,234,.96);backdrop-filter:blur(12px);
  border-bottom:1px solid var(--line);
  display:flex;flex-direction:column;align-items:center;gap:6px;padding:11px 0 6px;
  cursor:grab;touch-action:none}
.brand img{height:42px;pointer-events:none;-webkit-user-drag:none;-webkit-touch-callout:none}
/* bare V, no bubble; gentle nudge to invite the pull */
.pull-v{display:flex;align-items:center;justify-content:center;animation:chevNudge 2.4s ease-in-out infinite}
.pull-v svg{width:22px;height:10px;stroke:rgba(23,22,26,.5);stroke-width:2.4;fill:none;
  transition:transform .34s cubic-bezier(.22,1,.36,1)}
.shade.open .pull-v svg{stroke:var(--accent);transform:rotate(180deg)}
.shade.dragging .pull-v,.shade.open .pull-v{animation:none}
@keyframes chevNudge{0%,86%,100%{transform:translateY(0)}90%{transform:translateY(3px)}94%{transform:translateY(0)}}
.header-spacer{height:66px}   /* ≈ bar height so content isn't hidden */
```

## JS (drag with snap)

```js
(function(){
  const shade=document.getElementById('shade'),
        bar=document.getElementById('shadeBar'),
        menu=document.getElementById('shadeMenu');
  let H=0, open=false, dragging=false, startY=0, startOff=0, moved=false;
  function measure(){ H=menu.offsetHeight; shade.style.setProperty('--menuH',H+'px'); }
  function applyDrag(off){ shade.style.transform='translateY('+(-(H-off))+'px)'; } // off 0..H
  function setOpen(v){ open=v; shade.classList.remove('dragging'); shade.style.transform=''; shade.classList.toggle('open',v); }
  measure(); addEventListener('resize',measure);
  function start(y){dragging=true;moved=false;shade.classList.add('dragging');startY=y;startOff=open?H:0;}
  function move(y){ if(!dragging)return; if(Math.abs(y-startY)>4)moved=true;
    applyDrag(Math.max(0,Math.min(H,startOff+(y-startY)))); }
  function end(){ if(!dragging)return; dragging=false; shade.classList.remove('dragging');
    const m=shade.style.transform.match(/-?\d+\.?\d*/); const cur=m?parseFloat(m[0]):(open?0:-H);
    setOpen((H+cur) > H*0.4); }        // snap: open if pulled past 40%
  bar.addEventListener('touchstart',e=>start(e.touches[0].clientY),{passive:true});
  bar.addEventListener('touchmove',e=>move(e.touches[0].clientY),{passive:true});
  bar.addEventListener('touchend',end);
  bar.addEventListener('mousedown',e=>{start(e.clientY);e.preventDefault();});
  addEventListener('mousemove',e=>{if(dragging)move(e.clientY);});
  addEventListener('mouseup',end);
  bar.addEventListener('click',()=>{ if(!moved) setOpen(!open); });   // tap toggles only if not dragged
  menu.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>setOpen(false)));
})();
```

## Header pushed up by the layer-2 (blue) canvas — USE THE PUSH MODEL, NOT hide/show

Aes's request: as the second background layer (`.canvas2`, starts at About) scrolls up into the fixed top bar, the header should get **shoved up by exactly the overlap** — like the blue canvas is physically pushing it out — then slide back in when you scroll back. He explicitly does NOT want a "disappear/reappear" toggle; he wants "kedorong ke atas ke dorong canvas biru", a continuous push tied to scroll position.

**CRITICAL — do not compute collision from the header's own `getBoundingClientRect()`.** Two earlier direction-aware `classList.add/remove('hidden')` versions both flickered "random banget" on Android. Root cause: the header is the element being transformed, so reading *its* rect while it's mid-transform feeds its own movement back into the collision test → the condition oscillates true/false → the class flip-flops. Direction thresholds and accumulators only masked it; they did not fix it.

**The fix that worked: a single deterministic formula driven only by the canvas position.** No class toggle, no direction tracking, no `lastY`. Every scroll frame sets the transform directly:

```css
.site-header{position:fixed;top:0;left:0;right:0;z-index:50;will-change:transform}
/* no CSS transition on the transform — it's driven per-frame by scroll, a transition just adds lag/jitter */
```

```js
const header=document.querySelector('.site-header');
const canvas=document.querySelector('.canvas2');   // the layer-2 element
function onScroll(){
  if(open){ header.style.transform='translateY(0)'; return; }   // pinned while menu open
  if(!canvas){ header.style.transform='translateY(0)'; return; }
  const hH=header.offsetHeight||40;                 // offsetHeight IGNORES transform → stable
  const canvasTopVp=canvas.getBoundingClientRect().top;   // canvas rect is safe (it's not transformed)
  const push=Math.min(0, canvasTopVp - hH);         // 0 while gap>0; then -overlap, clamped at -hH
  header.style.transform='translateY('+push+'px)';
}
addEventListener('scroll',onScroll,{passive:true});
addEventListener('resize',onScroll);
onScroll();
```

Why it's stable: `push` is a pure function of the canvas's viewport position, and `offsetHeight` never changes when the header is transformed — so there's no feedback loop and the result is identical on every read at a given scroll offset. `Math.min(0, …)` keeps the header put on the white hero and pushes it up smoothly as the blue rises.

If instead you truly want full hide/show (rare — Aes wanted the push), still compute collision from the canvas's ABSOLUTE top (`rect.top + scrollY`) vs `scrollY + header.offsetHeight`, never from the header's live rect.

## Pitfalls learned

- `.dragging` MUST kill the CSS transition, else the drag lags/stutters. Throttle `move` via requestAnimationFrame if touchmove fires faster than paint.
- Track `moved` so a tap doesn't fire after a drag (or vice-versa).
- Wordmark must be non-interactive: `<span>` (not `<a>`) + `pointer-events:none` + `user-select:none` so it can't be clicked/selected/dragged as an image.
- Add `*{-webkit-tap-highlight-color:transparent}` and `user-select:none` on links to kill the blue tap/selection flash on Android.
- Simpler earlier variant (translateY drawer that just drops from `top:100%`, toggled by a small chevron) is fine when the user doesn't want the full drag physics — but he specifically asked for the "bar + wordmark drag down together" shade feel here.
- **V placement is fussy — he flip-flops.** He asked for the chevron variously: below the wordmark inside the bar, then "di luar banner" (outside, hanging below the bar edge via `position:absolute;bottom:-Npx`), then white-colored (`stroke:#fff` + `drop-shadow`), then "no bubble" (kill any pill/border/background — bare `<svg>` only). Keep the V inside `.shade` regardless of visual placement so it still drags down with the bar. When he says "no bubble," remove `background`/`border`/`border-radius` entirely. He has also asked to move the V to the **far-right corner of the bar** (`position:absolute;right:16px;bottom:-Npx`, drop `left:50%`). **When you re-anchor it from center to corner, also strip `translateX(-50%)` from EVERY `chevBreathe`/`chevNudge` keyframe** — the centered version bakes `transform:translateX(-50%) translateY(...)` into each keyframe, so leaving it while moving to `right:16px` makes the chevron jump left and the breathe motion break. Corner-anchored keyframes use `transform:translateY(...)` only.\n- **V final form he liked: FILLED chevron tapered to sharp tips + a \"breathe\" animation.** He asked to make the V \"jangan kaku\" (not stiff): tips lancip at both ends, gerak ke bawah muncul + naik redup. Two moves that satisfied him: (1) replace the stroked `polyline` with a filled `<path>` arrow (thick at the elbow, tapering to points), `fill:#fff;stroke:none` — e.g. `viewBox=\"0 0 26 12\"` `d=\"M2,3.4 L13,9.4 L24,3.4 L13,6.6 Z\"`; (2) swap the sharp `chevNudge` for a smooth `chevBreathe` loop that eases DOWN + brightens (opacity→1, invite) then drifts UP + dims (opacity→.4, rest), `cubic-bezier(.4,0,.2,1)` ~2.8s. Still rotate 180° on `.shade.open`.
- Keep the banner wordmark SMALL (he repeatedly pushed 46→42→28→20px). Match `.shade-bar` and `.shade-menu` bg to the SAME `var(--paper)` main-bg token — he objected when the bar used a translucent/blur variant that read as a different white.
