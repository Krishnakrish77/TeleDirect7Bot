import { FormEvent, KeyboardEvent, RefObject, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { signInTelegram } from '../api';
import { useSuggestions } from '../hooks/data';
import { localAppHref } from '../navigation';
import { BookmarkIcon, BroadcastIcon, ChartIcon, ChevronDownIcon, ChevronUpIcon, FilmIcon, HeartIcon, HomeIcon, ListIcon, ListPlusIcon, LogOutIcon, MusicIcon, PlayIcon, SearchIcon, ShieldIcon, TvIcon, UserIcon, XIcon } from '../icons';
import type { MeResponse, Suggestion, TelegramAuthUser, User, ViewValue } from '../types';
import { tmdbImageUrl } from '../utils/tmdb';
import { Button } from './ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';
import { Dialog, DialogClose, DialogContent, DialogTitle } from './ui/dialog';

declare global {
  interface Window {
    onTeleDirectTelegramAuth?: (user: TelegramAuthUser) => void;
  }
}

export function PrimaryNav({
  user,
  activeView,
  activeSection,
}: {
  user: User | null;
  activeView: ViewValue | '';
  activeSection: 'home' | 'movies' | 'series' | 'music' | 'live-tv' | 'watchlist' | 'liked-songs' | 'playlists' | 'stats' | '';
}) {
  return (
    <nav className="primary-nav" aria-label="Primary">
      <a className={activeSection === 'home' ? 'active' : ''} href="/">
        <HomeIcon />
        <span>Home</span>
      </a>
      <a className={activeView === 'movies' && activeSection === 'movies' ? 'active' : ''} href="/?view=movies">
        <FilmIcon />
        <span>Movies</span>
      </a>
      <a className={activeView === 'series' && activeSection === 'series' ? 'active' : ''} href="/?view=series">
        <TvIcon />
        <span>Series</span>
      </a>
      <a className={activeView === 'music' && activeSection === 'music' ? 'active' : ''} href="/?view=music">
        <MusicIcon />
        <span>Music</span>
      </a>
      <a className={activeSection === 'live-tv' ? 'active' : ''} href="/live-tv">
        <BroadcastIcon />
        <span>Live TV</span>
      </a>
    </nav>
  );
}

export function Header({
  me,
  user,
  query,
  setQuery,
  searchRef,
  accountOpen,
  setAccountOpen,
  onSearchSubmit,
  onSearchClear,
  onSuggestionNavigate,
  onRequestTitle = () => {},
  onSignIn,
  onSignOut,
}: {
  me: MeResponse | null;
  user: User | null;
  query: string;
  setQuery: (next: string) => void;
  searchRef: RefObject<HTMLInputElement | null>;
  accountOpen: boolean;
  setAccountOpen: Dispatch<SetStateAction<boolean>>;
  onSearchSubmit: () => void;
  onSearchClear: () => void;
  onSuggestionNavigate: (href: string) => void;
  onRequestTitle?: () => void;
  onSignIn: () => void;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [avatarFailed, setAvatarFailed] = useState(false);
  const searchWrapRef = useRef<HTMLFormElement | null>(null);
  const { items: suggestions, loading: suggestionsLoading, searched: suggestionsSearched } = useSuggestions(query.trim());
  const hasSuggestionQuery = query.trim().length >= 2;
  const suggestionsOpen = open && hasSuggestionQuery;
  const activeSuggestionId = suggestionsOpen && activeIndex >= 0 ? `top-search-suggestion-${activeIndex}` : undefined;

  // Telegram Login Widget photo URLs can expire before the session JWT does.
  // Reset the fallback if a different account/photo is received.
  useEffect(() => setAvatarFailed(false), [user?.photo, user?.sub]);

  useEffect(() => {
    setActiveIndex((current) => {
      if (!suggestions.length) return -1;
      return current >= suggestions.length ? suggestions.length - 1 : current;
    });
  }, [suggestions.length]);

  useEffect(() => {
    if (!open) return;
    const closeOnPointer = (event: PointerEvent) => {
      if (!searchWrapRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setActiveIndex(-1);
      }
    };
    document.addEventListener('pointerdown', closeOnPointer);
    return () => document.removeEventListener('pointerdown', closeOnPointer);
  }, [open]);

  const closeSuggestions = () => {
    setOpen(false);
    setActiveIndex(-1);
  };

  const suggestionHref = (item: Suggestion) => localAppHref(item.url) || item.url;

  const pickSuggestion = (item: Suggestion) => {
    closeSuggestions();
    searchRef.current?.blur();
    const href = suggestionHref(item);
    if (href.startsWith('/')) {
      onSuggestionNavigate(href);
    } else {
      window.location.href = href;
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    closeSuggestions();
    onSearchSubmit();
  };

  const handleClear = () => {
    closeSuggestions();
    onSearchClear();
  };

  const handleKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      closeSuggestions();
      return;
    }
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && suggestions.length > 0) {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => {
        if (event.key === 'ArrowDown') return current >= suggestions.length - 1 ? 0 : current + 1;
        return current <= 0 ? suggestions.length - 1 : current - 1;
      });
      return;
    }
    if (event.key === 'Enter' && suggestionsOpen && activeIndex >= 0) {
      event.preventDefault();
      const item = suggestions[activeIndex];
      if (item) pickSuggestion(item);
    }
  };

  return (
    <header className="app-header">
      <a className="brand" href="/" aria-label="TeleDirect">
        <span className="brand-mark">
          <PlayIcon />
        </span>
        <span>TeleDirect</span>
      </a>

      <form className="top-search" role="search" onSubmit={handleSubmit} ref={searchWrapRef}>
        <button type="submit" className="icon-button search-leading search-submit" aria-label="Search">
          <SearchIcon />
        </button>
        <input
          ref={searchRef}
          value={query}
          onChange={(event) => {
            setQuery(event.currentTarget.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKey}
          placeholder="Search library"
          autoComplete="off"
          aria-autocomplete="list"
          aria-expanded={suggestionsOpen}
          aria-controls="top-search-suggestions"
          aria-activedescendant={activeSuggestionId}
        />
        {suggestionsLoading && (
          <span className="search-progress" role="status" aria-label="Searching library" />
        )}
        {query && (
          <button type="button" className="icon-button clear-search" onClick={handleClear} aria-label="Clear search">
            <XIcon />
          </button>
        )}
        {suggestionsOpen && (
          <SearchMenu
            suggestions={suggestions}
            loading={suggestionsLoading}
            searched={suggestionsSearched}
            activeIndex={activeIndex}
            getHref={suggestionHref}
            onActiveIndexChange={setActiveIndex}
            onPick={closeSuggestions}
            onRequestTitle={onRequestTitle}
          />
        )}
      </form>

      <div className="header-actions">
        {user ? (
          <DropdownMenu open={accountOpen} onOpenChange={setAccountOpen}>
            <DropdownMenuTrigger asChild>
              <button
              className="profile-chip"
              type="button"
              >
                <span className="profile-avatar">
                  {user.photo && !avatarFailed ? (
                    <img src={user.photo} alt="" onError={() => setAvatarFailed(true)} />
                  ) : (
                    <span>{(user.name || 'U')[0].toUpperCase()}</span>
                  )}
                </span>
                <strong>{user.name || user.username || 'User'}</strong>
                <ChevronDownIcon className="profile-chevron" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent className="account-menu" align="end">
              <DropdownMenuLabel className="account-menu-identity">
                  <strong>{user.name || user.username || 'Signed in'}</strong>
                  {user.username && <span>@{user.username}</span>}
              </DropdownMenuLabel>
              <DropdownMenuItem asChild>
                <a href="/watchlist">
                  <BookmarkIcon />
                  <span>Watchlist</span>
                </a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/liked-songs">
                  <HeartIcon />
                  <span>Liked songs</span>
                </a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/requests">
                  <ListPlusIcon />
                  <span>My requests</span>
                </a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/playlists">
                  <ListIcon />
                  <span>Playlists</span>
                </a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/stats">
                  <ChartIcon />
                  <span>Stats</span>
                </a>
              </DropdownMenuItem>
              <DropdownMenuSeparator className="account-menu-divider" />
              {user.is_admin && (
                <DropdownMenuItem asChild>
                  <a href="/admin">
                    <ShieldIcon />
                    <span>Admin panel</span>
                  </a>
                </DropdownMenuItem>
              )}
              <DropdownMenuItem asChild>
                <button
                  type="button"
                  onClick={() => {
                    onSignOut();
                  }}
                >
                  <LogOutIcon />
                  <span>Sign out</span>
                </button>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          <button className="signin-button" type="button" onClick={onSignIn} disabled={me === null}>
            <UserIcon />
            <span>Sign in</span>
          </button>
        )}
      </div>
    </header>
  );
}

export function SearchMenu({
  suggestions,
  loading,
  searched,
  activeIndex,
  getHref,
  onActiveIndexChange,
  onPick,
  onRequestTitle,
}: {
  suggestions: Suggestion[];
  loading: boolean;
  searched: boolean;
  activeIndex: number;
  getHref: (item: Suggestion) => string;
  onActiveIndexChange: (index: number) => void;
  onPick: () => void;
  onRequestTitle: () => void;
}) {
  return (
    <div className="search-menu" id="top-search-suggestions" role="listbox">
      {loading && (
        <div className="search-menu-state" role="status">
          <span className="search-menu-spinner" aria-hidden="true" />
          Searching your library…
        </div>
      )}
      {!loading && searched && suggestions.length === 0 && (
        <div className="search-menu-state search-menu-empty" role="status">
          <span>No quick matches. Press Enter to search the full library.</span>
          <button type="button" className="search-menu-request" onClick={() => { onPick(); onRequestTitle(); }}><ListPlusIcon /> Request a title</button>
        </div>
      )}
      {suggestions.map((item, index) => {
        const isAudio = item.media_kind === 'audio' || item.kind === 'audio' || item.kind === 'album';
        const fallbackArt = `/thumb/${item.secure_hash}${item.message_id}.jpg${isAudio ? '?v=audio3' : ''}`;
        return (
          <a
            key={item.url}
            id={`top-search-suggestion-${index}`}
            href={getHref(item)}
            role="option"
            aria-selected={index === activeIndex}
            className={index === activeIndex ? 'suggestion active' : 'suggestion'}
            onMouseEnter={() => onActiveIndexChange(index)}
            onClick={onPick}
          >
            <span className="suggestion-art">
              {item.poster_path ? (
                <img src={tmdbImageUrl(item.poster_path, 'w92')} alt="" loading="lazy" decoding="async" />
              ) : (
                <img src={fallbackArt} alt="" loading="lazy" decoding="async" />
              )}
            </span>
            <span className="suggestion-copy">
              <strong>{item.title}</strong>
              <span>{[item.year, item.kind].filter(Boolean).join(' - ')}</span>
            </span>
          </a>
        );
      })}
    </div>
  );
}


export function ScrollToTop() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > 400);
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);
  if (!visible) return null;
  return (
    <button
      type="button"
      className="scroll-top-btn"
      onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
      aria-label="Scroll to top"
    >
      <ChevronUpIcon />
    </button>
  );
}

export function SignInModal({
  open,
  botUsername,
  onClose,
}: {
  open: boolean;
  botUsername: string;
  onClose: () => void;
}) {
  const [telegramRoot, setTelegramRoot] = useState<HTMLDivElement | null>(null);
  const [widgetState, setWidgetState] = useState<'idle' | 'loading' | 'ready' | 'failed'>('idle');
  const [widgetAttempt, setWidgetAttempt] = useState(0);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open || !botUsername || !telegramRoot) return;
    telegramRoot.replaceChildren();
    setError('');
    setWidgetState('loading');

    window.onTeleDirectTelegramAuth = async (telegramUser: TelegramAuthUser) => {
      try {
        const data = await signInTelegram(telegramUser);
        if (data.token) sessionStorage.setItem('td:auth', data.token);
        window.location.reload();
      } catch (_) {
        setError('Sign in failed');
      }
    };

    const script = document.createElement('script');
    script.async = true;
    script.src = 'https://telegram.org/js/telegram-widget.js?22';
    script.setAttribute('data-telegram-login', botUsername.replace(/^@/, ''));
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-radius', '8');
    script.setAttribute('data-onauth', 'onTeleDirectTelegramAuth(user)');
    script.setAttribute('data-request-access', 'write');
    const observer = new MutationObserver(() => {
      if (telegramRoot.querySelector('iframe')) {
        setWidgetState('ready');
        observer.disconnect();
      }
    });
    observer.observe(telegramRoot, { childList: true, subtree: true });
    script.onerror = () => setWidgetState('failed');
    telegramRoot.appendChild(script);
    const timeout = window.setTimeout(() => {
      if (!telegramRoot.querySelector('iframe')) setWidgetState('failed');
    }, 8000);

    return () => {
      window.clearTimeout(timeout);
      observer.disconnect();
      delete window.onTeleDirectTelegramAuth;
      telegramRoot.replaceChildren();
    };
  }, [botUsername, open, telegramRoot, widgetAttempt]);

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }}>
      <DialogContent className="modal-panel" aria-describedby={undefined}>
        <DialogTitle>Sign in</DialogTitle>
        <DialogClose asChild>
          <Button type="button" variant="ghost" size="icon-sm" className="modal-close" aria-label="Close">
          <XIcon />
          </Button>
        </DialogClose>
        <div className="telegram-slot">
          <div className="telegram-widget-root" ref={setTelegramRoot} />
          {widgetState === 'loading' && <span className="telegram-widget-status" role="status">Loading Telegram sign-in…</span>}
        </div>
        {!botUsername && <p className="form-error">Telegram login unavailable</p>}
        {widgetState === 'failed' && botUsername && (
          <div className="telegram-widget-error">
            <p className="form-error">Telegram sign-in did not load. Check content blockers or your connection.</p>
            <Button type="button" variant="secondary" size="sm" onClick={() => setWidgetAttempt((value) => value + 1)}>
              Retry
            </Button>
          </div>
        )}
        {error && <p className="form-error">{error}</p>}
      </DialogContent>
    </Dialog>
  );
}
