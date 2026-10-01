/**
 * MixedRow — a label-less horizontal row mixing Movie + Series posters,
 * ending in a "More" tile. Built for the "de-sectioned homepage" shape
 * (see SKILL.md Step 11): no section title, no accent bar, no See All,
 * no rank numbers — just posters.
 *
 * Copy into src/pages/Home/ and adjust ContentCard import path / card width.
 * Requires: VITE_TMDB_API + VITE_BASE_URL, a ContentCard component,
 * and a `.hide-scrollbar` utility in index.css.
 *
 * Usage (HomePage.jsx):
 *   <MixedRow
 *     movieGenre={28} tvGenre={10759}
 *     onMore={() => { window.scrollTo({top:0}); navigate(buildBrowsePath('movie', 28)); }}
 *     onSelect={handleSelect}
 *   />
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import PropTypes from 'prop-types';
import { FiArrowRight } from 'react-icons/fi';
import ContentCard from './ContentCard';

const API_KEY = import.meta.env.VITE_TMDB_API;
const BASE_URL = import.meta.env.VITE_BASE_URL;
const POSTER = 'https://image.tmdb.org/t/p/w500';
const CARD_W = 132; // narrow = more posters per phone screen

/* /discover does NOT return media_type — tag it ourselves. */
const discover = async (type, genreId) => {
  if (!genreId) return [];
  try {
    const url = new URL(`${BASE_URL}/discover/${type}`);
    url.searchParams.append('api_key', API_KEY);
    url.searchParams.append('language', 'en-US');
    url.searchParams.append('sort_by', 'popularity.desc');
    url.searchParams.append('include_adult', 'false');
    url.searchParams.append('vote_count.gte', '80');
    url.searchParams.append('with_genres', String(genreId));
    const res = await fetch(url);
    if (!res.ok) return [];
    const data = await res.json();
    return (data.results ?? [])
      .filter((i) => i.poster_path)
      .map((i) => ({ ...i, media_type: type }));
  } catch {
    return [];
  }
};

/* Alternate one-by-one so the row reads as genuinely mixed.
   [...movies, ...tv] would just front-load all movies. */
const interleave = (a, b) => {
  const out = [];
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if (a[i]) out.push(a[i]);
    if (b[i]) out.push(b[i]);
  }
  return out;
};

export default function MixedRow({ movieGenre, tvGenre, onMore, onSelect }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);

  const rowRef = useRef(null);
  const dragStateRef = useRef({ active: false, startX: 0, startScrollLeft: 0, moved: false });
  const suppressClickRef = useRef(false); // without this, a drag ends in an accidental navigate
  const [isDragging, setIsDragging] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([discover('movie', movieGenre), discover('tv', tvGenre)]).then(([mv, tv]) => {
      if (cancelled) return;
      const seen = new Set();
      const merged = interleave(mv, tv).filter((i) => {
        const key = `${i.media_type}-${i.id}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      setItems(merged.slice(0, 20));
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [movieGenre, tvGenre]);

  const onRowMouseDown = useCallback((e) => {
    if (e.button !== 0) return;
    const el = rowRef.current;
    if (!el) return;
    dragStateRef.current = { active: true, startX: e.pageX, startScrollLeft: el.scrollLeft, moved: false };
    setIsDragging(true);
  }, []);

  const onRowMouseMove = useCallback((e) => {
    const el = rowRef.current;
    const drag = dragStateRef.current;
    if (!el || !drag.active) return;
    const delta = e.pageX - drag.startX;
    if (Math.abs(delta) > 4) drag.moved = true;
    el.scrollLeft = drag.startScrollLeft - delta;
  }, []);

  const endRowDrag = useCallback(() => {
    const drag = dragStateRef.current;
    if (!drag.active) return;
    drag.active = false;
    suppressClickRef.current = drag.moved;
    setIsDragging(false);
    setTimeout(() => { suppressClickRef.current = false; }, 0);
  }, []);

  useEffect(() => {
    window.addEventListener('mouseup', endRowDrag);
    return () => window.removeEventListener('mouseup', endRowDrag);
  }, [endRowDrag]);

  if (loading) {
    return (
      <section className="mb-7">
        <div className="flex gap-3 px-4 sm:px-6">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="shrink-0 w-[132px] md:w-[160px] h-[218px] md:h-[262px] rounded-xl bg-white/[0.05] animate-pulse" />
          ))}
        </div>
      </section>
    );
  }

  if (!items.length) return null;

  return (
    <section className="mb-7" style={{ overflow: 'visible' }}>
      <div
        ref={rowRef}
        onMouseDown={onRowMouseDown}
        onMouseMove={onRowMouseMove}
        onMouseLeave={endRowDrag}
        className={`flex gap-3 overflow-x-auto hide-scrollbar px-4 sm:px-6 select-none ${isDragging ? 'cursor-grabbing' : 'cursor-grab'}`}
        style={{ paddingTop: 20, paddingBottom: 20, marginTop: -14, marginBottom: -14 }}
      >
        {items.map((item) => (
          <div key={`${item.media_type}-${item.id}`} className="shrink-0" style={{ width: CARD_W }}>
            <ContentCard
              title={item.title || item.name}
              poster={item.poster_path ? `${POSTER}${item.poster_path}` : null}
              rating={item.vote_average}
              releaseDate={(item.release_date || item.first_air_date || '').slice(0, 4)}
              onClick={() => {
                if (suppressClickRef.current) return;
                onSelect(item, item.media_type);
              }}
              mediaId={item.id}
              mediaType={item.media_type}
              posterPath={item.poster_path}
              voteAverage={item.vote_average}
            />
          </div>
        ))}

        {/* "More" as the last flex child of the SAME scroll row — poster-shaped */}
        <button
          onClick={() => { if (!suppressClickRef.current) onMore(); }}
          className="shrink-0 flex flex-col items-center justify-center gap-2 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] ring-1 ring-white/5 hover:ring-white/15 text-gray-400 hover:text-white transition-all active:scale-95"
          style={{ width: CARD_W, aspectRatio: '2 / 3' }}
        >
          <span className="w-9 h-9 rounded-full bg-white/[0.08] flex items-center justify-center">
            <FiArrowRight className="text-base" />
          </span>
          <span className="text-[12px] font-semibold">More</span>
        </button>
      </div>
    </section>
  );
}

MixedRow.propTypes = {
  movieGenre: PropTypes.number,
  tvGenre: PropTypes.number,
  onMore: PropTypes.func.isRequired, // function, not a path string — parent owns navigate()
  onSelect: PropTypes.func.isRequired,
};
