/**
 * homeFeed.js — known-good data layer for the "de-sectioned homepage".
 *
 * Copy into src/pages/Home/ alongside MixedRow.jsx. Pairs with HomePage.jsx:
 *
 *   const specs = useMemo(() => buildRowSpecs(), []);
 *   const [rows, setRows] = useState(null);
 *   useEffect(() => {
 *     let cancelled = false;
 *     loadHomeFeed(specs, 20).then(r => { if (!cancelled) setRows(r); });
 *     return () => { cancelled = true; };
 *   }, [specs]);
 *
 * Row order: 1) movie trending global  2) series trending global
 *            3) movie Indonesia        4) series Indonesia
 *            5) anime & kartun         6-10) random genre pairs
 *
 * Dedup is GLOBAL across all rows — a title never appears twice on the page.
 * Rows get first pick in order, so headline rows keep their titles.
 */

const API_KEY = import.meta.env.VITE_TMDB_API;
const BASE_URL = import.meta.env.VITE_BASE_URL;

const get = async (path, params = {}) => {
  try {
    const url = new URL(`${BASE_URL}${path}`);
    url.searchParams.append('api_key', API_KEY);
    url.searchParams.append('language', 'en-US');
    Object.entries(params).forEach(([k, v]) => url.searchParams.append(k, String(v)));
    const res = await fetch(url);
    if (!res.ok) return [];
    const data = await res.json();
    return data.results ?? [];
  } catch {
    return [];
  }
};

/* Over-fetch: after global dedup a single page starves a row. */
const pages = async (path, params = {}, count = 3) => {
  const batches = await Promise.all(
    Array.from({ length: count }, (_, i) => get(path, { ...params, page: i + 1 }))
  );
  return batches.flat();
};

/* /discover does NOT return media_type (unlike /trending) — tag it ourselves. */
const tag = (items, mediaType) =>
  items.filter((i) => i.poster_path).map((i) => ({ ...i, media_type: i.media_type ?? mediaType }));

/* Alternate one-by-one. [...a, ...b] puts all movies first and looks unmixed. */
const interleave = (a, b) => {
  const out = [];
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if (a[i]) out.push(a[i]);
    if (b[i]) out.push(b[i]);
  }
  return out;
};

const shuffle = (arr) => {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
};

/* Movie and TV genre IDs DIFFER on TMDB — pair them explicitly. */
const RANDOM_POOL = [
  { label: 'Action', movie: 28, tv: 10759 },
  { label: 'Comedy', movie: 35, tv: 35 },
  { label: 'Drama', movie: 18, tv: 18 },
  { label: 'Sci-Fi', movie: 878, tv: 10765 },
  { label: 'Horror & Mystery', movie: 27, tv: 9648 },
  { label: 'Thriller & Crime', movie: 53, tv: 80 },
  { label: 'Romance', movie: 10749, tv: 10766 },
  { label: 'Adventure', movie: 12, tv: 10759 },
  { label: 'Family', movie: 10751, tv: 10751 },
  { label: 'Fantasy', movie: 14, tv: 10765 },
  { label: 'Documentary', movie: 99, tv: 99 },
  { label: 'War & History', movie: 10752, tv: 10768 },
];

export const buildRowSpecs = () => {
  const randoms = shuffle(RANDOM_POOL).slice(0, 5);

  return [
    {
      key: 'trending-movie',
      more: { type: 'movie', genreId: null },
      fetch: async () => tag(await pages('/trending/movie/week', {}, 3), 'movie'),
    },
    {
      key: 'trending-tv',
      more: { type: 'tv', genreId: null },
      fetch: async () => tag(await pages('/trending/tv/week', {}, 3), 'tv'),
    },
    {
      // with_origin_country=ID, NOT with_original_language=id — also catches co-productions
      key: 'id-movie',
      more: { type: 'movie', genreId: null },
      fetch: async () =>
        tag(await pages('/discover/movie', {
          sort_by: 'popularity.desc', with_origin_country: 'ID', include_adult: 'false',
        }, 3), 'movie'),
    },
    {
      key: 'id-tv',
      more: { type: 'tv', genreId: null },
      fetch: async () =>
        tag(await pages('/discover/tv', {
          sort_by: 'popularity.desc', with_origin_country: 'ID', include_adult: 'false',
        }, 3), 'tv'),
    },
    {
      key: 'anime-cartoon',
      more: { type: 'tv', genreId: -1 }, // SPECIAL_CATEGORIES "Anime" in tmdb.js
      fetch: async () => {
        const [mv, tv] = await Promise.all([
          pages('/discover/movie', {
            sort_by: 'popularity.desc', with_genres: '16',
            'vote_count.gte': '30', include_adult: 'false',
          }, 2),
          pages('/discover/tv', {
            sort_by: 'popularity.desc', with_genres: '16',
            'vote_count.gte': '15', include_adult: 'false',
          }, 2),
        ]);
        return interleave(tag(mv, 'movie'), tag(tv, 'tv'));
      },
    },
    ...randoms.map((g) => ({
      key: `random-${g.label}`,
      more: { type: 'movie', genreId: g.movie },
      fetch: async () => {
        const [mv, tv] = await Promise.all([
          pages('/discover/movie', {
            sort_by: 'popularity.desc', with_genres: String(g.movie),
            'vote_count.gte': '60', include_adult: 'false',
          }, 2),
          pages('/discover/tv', {
            sort_by: 'popularity.desc', with_genres: String(g.tv),
            'vote_count.gte': '30', include_adult: 'false',
          }, 2),
        ]);
        return interleave(tag(mv, 'movie'), tag(tv, 'tv'));
      },
    })),
  ];
};

/**
 * Run every fetch, then slice `perRow` per row through ONE shared `seen` Set.
 * Row order == priority order: row 1 picks first.
 */
export const loadHomeFeed = async (specs, perRow = 20) => {
  const raw = await Promise.all(specs.map((s) => s.fetch()));
  const seen = new Set();

  return specs.map((spec, i) => {
    const picked = [];
    for (const item of raw[i]) {
      const id = `${item.media_type}-${item.id}`;
      if (seen.has(id)) continue;
      seen.add(id);
      picked.push(item);
      if (picked.length >= perRow) break;
    }
    return { ...spec, items: picked };
  });
};
