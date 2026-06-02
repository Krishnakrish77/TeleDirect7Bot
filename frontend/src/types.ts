export type HubMode = 'shelves' | 'grid';
export type ViewValue = '' | 'list' | 'movies' | 'series' | 'music';

export interface HubParams {
  q: string;
  tag: string;
  quality: string;
  genre: string;
  year: number | null;
  sort: string;
  view: ViewValue;
  offset: number;
  limit: number;
}

export interface FilterOption {
  value: string;
  label: string;
}

export interface TagOption {
  name: string;
  count: number;
}

export interface HubFilters {
  years: number[];
  qualities: string[];
  genres: string[];
  tags: TagOption[];
  sortOptions: FilterOption[];
  views: FilterOption[];
}

export type CardType = 'item' | 'track' | 'series' | 'movie' | 'album' | 'hero';
export type CardAspect = 'poster' | 'square';

export interface HubCard {
  type: CardType;
  itemId: string;
  messageId?: number;
  secureHash?: string;
  title: string;
  subtitle: string;
  year: number | null;
  mediaKind: string;
  posterUrl: string;
  thumbUrl: string;
  backdropUrl: string;
  duration: number;
  durationLabel: string;
  fileSize: number;
  fileSizeLabel: string;
  quality: string;
  genres: string[];
  tags: string[];
  overview: string;
  artist: string;
  albumTitle: string;
  href: string;
  playHref?: string;
  detailsHref?: string;
  streamHref: string;
  watchKey: string;
  eyebrow: string;
  badge: string;
  aspect: CardAspect;
  variantCount?: number;
  episodeCount?: number;
  seasonCount?: number;
  trackCount?: number;
  recMeta?: RecommendationMeta | null;
}

export interface RecommendationMeta {
  tmdbId: number;
  kind: 'movie' | 'tv';
}

export interface HeroItem extends HubCard {
  detailsHref: string;
  playHref: string;
  meta: string[];
}

export interface Shelf {
  name: string;
  href: string | null;
  total: number;
  items: HubCard[];
  dismissable?: boolean;
  recMeta?: Array<RecommendationMeta | null>;
}

export interface HubResponse {
  mode: HubMode;
  params: HubParams;
  filters: HubFilters;
  catalogueSize: number;
  heroes: HeroItem[];
  shelves: Shelf[];
  items: HubCard[];
  total: number;
  nextOffset: number | null;
  nextHref: string | null;
  emptyText: string;
}

export interface WatchTrack {
  key: string;
  itemId: string;
  type: 'track';
  messageId: number;
  secureHash: string;
  title: string;
  year: number | null;
  mediaKind: string;
  posterUrl: string;
  thumbUrl: string;
  backdropUrl: string;
  duration: number;
  durationLabel: string;
  fileSize: number;
  fileSizeLabel: string;
  quality: string;
  genres: string[];
  tags: string[];
  overview: string;
  artist: string;
  albumTitle: string;
  href: string;
  streamHref: string;
  watchKey: string;
  trackNumber: number | null;
  format: string;
  qualityLabel: string;
  appHref: string;
  classicHref: string;
  albumHref: string;
}

export interface PersonLink {
  name: string;
  href: string;
}

export interface VideoChoice extends HubCard {
  key: string;
  label: string;
  playHref: string;
  appHref: string;
  classicHref: string;
  episodeLabel?: string;
  episodeOverview?: string;
  episodeStillUrl?: string;
  firstAired?: string;
}

export interface SubtitleTrack {
  id: string;
  url: string;
  language: string;
  label: string;
  codec: string;
  kind: string;
}

export interface AudioTrackOption {
  index: number;
  language: string;
  label: string;
  codec: string;
}

export interface WatchVideo {
  key: string;
  itemId: string;
  messageId?: number;
  secureHash?: string;
  type: 'video';
  title: string;
  subtitle: string;
  year: number | null;
  mediaKind: string;
  posterUrl: string;
  thumbUrl: string;
  backdropUrl: string;
  duration: number;
  durationLabel: string;
  fileSize: number;
  fileSizeLabel: string;
  quality: string;
  genres: string[];
  tags: string[];
  overview: string;
  artist: string;
  albumTitle: string;
  href: string;
  streamHref: string;
  watchKey: string;
  episodeLabel: string;
  classicHref: string;
  appHref: string;
  directSrc: string;
  hlsSrc: string;
  subtitleBase: string;
  audioTrackBase: string;
  absoluteStreamHref: string;
  downloadHref: string;
  vlcHref: string;
  vlcTrackingToken: string;
  knownUnplayable: boolean;
  videoCodec: string;
  pixFmt: string;
  qualityVariants: VideoChoice[];
  nextEpisode?: {
    key: string;
    url: string;
    title: string;
    season: number | null;
    episode: number | null;
    playHref: string;
    classicHref: string;
    posterUrl: string;
  } | null;
  introStart: number;
  introEnd: number;
  resumeKey: string;
  metadata: {
    title: string;
    year: number | null;
    overview: string;
    posterUrl: string;
    thumbUrl: string;
    backdropUrl: string;
    genres: string[];
    director: string;
    directors: PersonLink[];
    cast: PersonLink[];
    imdbId: string;
    imdbHref: string;
    trailerKey: string;
  };
}

export interface WatchResponse {
  mediaKind: string;
  classicHref?: string;
  item: WatchTrack | HubCard | WatchVideo;
  prev?: WatchTrack | null;
  next?: WatchTrack | null;
  albumTracks?: WatchTrack[];
}

export interface User {
  sub: number;
  name: string;
  username: string;
  photo: string;
  is_admin: boolean;
  exp: number;
}

export interface MeResponse {
  user: User | null;
  botUsername: string;
  app: {
    name: string;
    spaPath: string;
  };
}

export interface Suggestion {
  title: string;
  year: number | null;
  kind: string;
  url: string;
  poster_path: string;
  secure_hash: string;
  message_id: number;
}

export interface ContinueEntry {
  key: string;
  pos: number;
  dur: number;
  t: number;
  title: string;
}

export type ContinueMap = Record<string, Omit<ContinueEntry, 'key'>>;

export interface RatingCounts {
  up: number;
  down: number;
}

export interface RatingResponse {
  rating: 'up' | 'down' | null;
  counts: RatingCounts;
}

export interface ContinueItem {
  key: string;
  title: string;
  series_title: string;
  episode_label: string;
  year: number | null;
  poster_path: string;
  thumb_url: string;
  watch_url: string;
}

export interface WatchlistItem {
  item_id: string;
  url: string;
  title: string;
  year: number | null;
  poster: string;
  kind: string;
  subtitle: string;
  cw_pct?: number | null;
}

export interface WatchlistPageResponse {
  items: WatchlistItem[];
  mongoAvailable: boolean;
}

export interface StatsTitle {
  title: string;
  poster: string;
  url: string;
  media_kind: string;
  year: number | string | null;
  is_series: boolean;
  count?: number;
}

export interface StatsDowBar {
  label: string;
  count: number;
  pct: number;
}

export interface StatsHeatmapCell {
  date: string;
  count: number;
  dow: number;
}

export interface StatsResponse {
  total_seconds: number;
  video_seconds: number;
  audio_seconds: number;
  total_hours: number;
  total_mins: number;
  video_hours: number;
  video_mins: number;
  audio_hours: number;
  audio_mins: number;
  total_plays: number;
  total_titles: number;
  active_days: number;
  equiv_movies: number;
  equiv_flights: number;
  top_title: StatsTitle | null;
  most_replayed: StatsTitle[];
  top_genres: Array<[string, number]>;
  top_genre: string;
  top_director: [string, number] | null;
  top_artists: Array<[string, number]>;
  best_month: [string, number] | null;
  finished: number;
  started: number;
  n_video: number;
  n_audio: number;
  dow_bars: StatsDowBar[];
  best_day: string;
  tod_label: string;
  tod_emoji: string;
  timed_plays: number;
  completion: number;
  personality: string;
  heatmap: StatsHeatmapCell[];
  current_streak: number;
  longest_streak: number;
}

export interface AdminFilterOption {
  value: string;
  label: string;
}

export interface AdminItem {
  messageId: number;
  secureHash: string;
  watchKey: string;
  title: string;
  year: number | null;
  quality: string;
  tags: string[];
  fileName: string;
  fileSize: number;
  fileSizeLabel: string;
  duration: number;
  description: string;
  hidden: boolean;
  duplicate: boolean;
  hasThumb: boolean;
  missingThumb: boolean;
  missingPoster: boolean;
  mediaKind: string;
  seriesTitle: string;
  seriesKey: string;
  season: number | null;
  episode: number | null;
  episodeEnd: number | null;
  tmdbId: number | null;
  tmdbKind: 'movie' | 'tv';
  imdbId: string;
  artist: string;
  albumTitle: string;
  trackNumber: number | null;
  adminLocked: string[];
  posterUrl: string;
  watchHref: string;
  classicHref: string;
}

export interface AdminProgressState {
  running?: boolean;
  done?: number;
  scanned?: number;
  total?: number;
  indexed?: number;
  enriched?: number;
  failed?: number;
  found_incompatible?: number;
  filled?: number;
  phase?: string;
  error?: string;
  last_title?: string;
}

export interface AdminStatusResponse {
  seed: AdminProgressState;
  enrich: AdminProgressState;
  reindex: AdminProgressState;
  probe: AdminProgressState;
  episode_fill: AdminProgressState;
  migrate: AdminProgressState;
  catalogue_size: number;
}

export interface AdminStats {
  total: number;
  total_size_bytes: number;
  kinds: {
    series_episodes: number;
    movies: number;
    movie_variant_groups: number;
    movie_variant_extras: number;
    standalone: number;
  };
  quality_buckets: Array<[string, number]>;
  enrichment: {
    enriched: number;
    attempted_no_match: number;
    never_attempted: number;
  };
  codec_health: {
    probed_playable: number;
    probed_unplayable: number;
    never_probed: number;
  };
  top_genres: Array<[string, number]>;
  missing_poster: number;
  missing_thumb: number;
  duplicate_groups: number;
  duplicate_extras: number;
  audio_count: number;
  album_count: number;
}

export interface AdminResponse {
  items: AdminItem[];
  catalogueSize: number;
  filteredCount: number;
  page: number;
  totalPages: number;
  pageSize: number;
  filterName: string;
  searchQ: string;
  sortCol: string;
  sortDir: 'asc' | 'desc';
  stats: AdminStats;
  knownSeries: string[];
  filters: AdminFilterOption[];
  sortOptions: AdminFilterOption[];
  capabilities: {
    gemini: boolean;
  };
  status: AdminStatusResponse;
}

export interface AdminActionResponse {
  ok: boolean;
  message: string;
  status?: AdminStatusResponse;
}

export interface TelegramAuthUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: number;
  hash: string;
}

export interface RelatedRow {
  name: string;
  items: HubCard[];
}

export interface MovieDetailResponse {
  kind: 'movie';
  key: string;
  savedId: string;
  title: string;
  year: number | null;
  overview: string;
  posterUrl: string;
  backdropUrl: string;
  genres: string[];
  director: string;
  directors: PersonLink[];
  cast: PersonLink[];
  imdbHref: string;
  trailerKey: string;
  playHref: string;
  classicHref: string;
  variants: VideoChoice[];
  related: RelatedRow[];
}

export interface SeriesEntry {
  rep: VideoChoice;
  variants: VideoChoice[];
  duplicateCount: number;
  progressPct: number;
  watched: boolean;
}

export interface SeriesSeasonBlock {
  season: number | null;
  entries: SeriesEntry[];
}

export interface SeriesDetailResponse {
  kind: 'series';
  key: string;
  savedId: string;
  title: string;
  year: number | null;
  overview: string;
  posterUrl: string;
  backdropUrl: string;
  genres: string[];
  director: string;
  directors: PersonLink[];
  cast: PersonLink[];
  imdbHref: string;
  trailerKey: string;
  playHref: string;
  classicHref: string;
  seasonOptions: FilterOption[];
  showSelector: boolean;
  selectedSeason: string;
  episodeCount: number;
  totalEpisodeCount: number;
  seasonCount: number;
  seasonBlocks: SeriesSeasonBlock[];
  related: RelatedRow[];
}

export interface AlbumDetailResponse {
  kind: 'album';
  key: string;
  savedId: string;
  title: string;
  artist: string;
  artistHref: string;
  year: number | null;
  overview: string;
  posterUrl: string;
  backdropUrl: string;
  trackCount: number;
  playHref: string;
  tracks: WatchTrack[];
  related: RelatedRow[];
}

export interface ArtistDetailResponse {
  kind: 'artist';
  key: string;
  title: string;
  subtitle: string;
  artist: string;
  posterUrl: string;
  backdropUrl: string;
  tracks: WatchTrack[];
  albums: HubCard[];
  singles: WatchTrack[];
}

export interface PersonDetailResponse {
  kind: 'person';
  key: string;
  title: string;
  subtitle: string;
  roleLabel: string;
  totalUnique: number;
  posterUrl: string;
  backdropUrl: string;
  castItems: HubCard[];
  directedItems: HubCard[];
}

export type DetailResponse =
  | MovieDetailResponse
  | SeriesDetailResponse
  | AlbumDetailResponse
  | ArtistDetailResponse
  | PersonDetailResponse;

export interface AdminDashboardResponse {
  total: number;
  total_size_bytes: number;
  total_size_label: string;
  kinds: AdminStats['kinds'];
  audio_count: number;
  album_count: number;
  enrichment: AdminStats['enrichment'];
  codec_health: AdminStats['codec_health'];
  top_genres: Array<[string, number]>;
  missing_poster: number;
  missing_thumb: number;
  duplicate_groups: number;
  duplicate_extras: number;
  storage_by_quality: Array<{ quality: string; bytes: number; label: string }>;
  storage_by_codec: Array<{ codec: string; bytes: number; label: string }>;
  year_distribution: Array<{ decade: number; count: number }>;
  year_distribution_max: number;
  quality_counts: Record<string, number>;
  top_series: Array<{ key: string; title: string; count: number }>;
  recent_additions: Array<{
    message_id: number; secure_hash: string; title: string; year: number | null;
    file_size: number; fileSizeLabel: string; series_title: string; season: number | null;
    episode: number | null; quality: string; watchHref: string;
  }>;
  largest_items: Array<{
    message_id: number; secure_hash: string; title: string; year: number | null;
    file_size: number; quality: string; watchHref: string; fileSizeLabel: string;
  }>;
}

export interface AdminTrendingGap {
  title: string;
  year: string;
  kind: 'movie' | 'tv';
  poster: string;
  vote: string;
  tmdb_url: string;
}

export interface AdminItemEditPayload {
  title: string;
  year: number | null;
  tags: string;
  description: string;
  fileName: string;
  seriesTitle: string;
  season: number | null;
  episode: number | null;
  episodeEnd: number | null;
  introStart: number | null;
  introEnd: number | null;
  artist: string;
  albumTitle: string;
  trackNumber: number | null;
  thumbUrl: string;
  tmdbId: number | null;
  tmdbKind: 'movie' | 'tv';
  adminLocked: string[];
}

export interface AiSuggestResponse {
  title?: string;
  year?: number;
  file_name?: string;
  series_title?: string;
  season?: number;
  episode?: number;
  tags?: string;
  description?: string;
  artist?: string;
  album_title?: string;
  track_number?: number;
  reasoning?: string;
  error?: string;
}

export interface TmdbPreviewResult {
  tmdb_id: number;
  kind: string;
  title: string;
  year: number | null;
  overview: string;
  poster_path: string;
  genres: string[];
  imdb_id: string;
  error?: string;
}
