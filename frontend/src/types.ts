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
  streamHref: string;
  watchKey: string;
  eyebrow: string;
  badge: string;
  aspect: CardAspect;
  variantCount?: number;
  episodeCount?: number;
  seasonCount?: number;
  trackCount?: number;
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
}

export interface WatchResponse {
  mediaKind: string;
  classicHref?: string;
  item: WatchTrack | HubCard;
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

export interface TelegramAuthUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: number;
  hash: string;
}
