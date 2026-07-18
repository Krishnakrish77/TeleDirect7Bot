import { useCallback, useEffect, useRef, useState } from 'react';
import { deleteContinueEntry, recordWatchHistory, saveContinueEntry } from '../api';
import { preloadLyrics } from './lyrics';
import type { WatchTrack } from '../types';

export type RepeatMode = 'off' | 'all' | 'one';
export const RESTORE_AUDIO_MEDIA_SESSION_EVENT = 'td:restore-audio-media-session';

export interface PlayerState {
  track: WatchTrack | null;
  queue: WatchTrack[];
  queueIndex: number;
  playing: boolean;
  currentTime: number;
  duration: number;
  error: string;
  speed: number;
  repeatMode: RepeatMode;
  volume: number;
  muted: boolean;
  nextTrack: WatchTrack | null;
  nextCountdown: number;
  queueToast: string;
}

const SPEED_KEY = 'td:speed';
const REPEAT_KEY = 'td:repeat';
const VOLUME_KEY = 'td:volume';
const MUTED_KEY = 'td:muted';
const PLAYER_KEY = 'td:reactPlayer';
const NOW_PLAYING_KEY = 'td:nowplaying';
const QUEUE_KEY = 'td:queue';
const QUEUE_INDEX_KEY = 'td:queueIndex';
const PRELOAD_AT_SECONDS = 30;
const CROSSFADE_SECONDS = 3;
const NEXT_COUNTDOWN_SECONDS = 5;
const PLAYBACK_START_TIMEOUT_MS = 12000;
const DEFAULT_VOLUME = 1;
const SILENT_VOLUME = 0.001;
const MEDIA_ERR_ABORTED = 1;
const MEDIA_ERR_NETWORK = 2;
const MEDIA_ERR_DECODE = 3;
const MEDIA_ERR_SRC_NOT_SUPPORTED = 4;

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function trackSrc(track: WatchTrack): string {
  return new URL(track.streamHref, window.location.origin).href;
}

function readNumber(key: string, fallback: number): number {
  try {
    const stored = localStorage.getItem(key);
    if (stored === null) return fallback;
    const parsed = Number(stored);
    return Number.isFinite(parsed) ? parsed : fallback;
  } catch (_) {
    return fallback;
  }
}

function persistOutputSettings(volume: number, muted: boolean): void {
  try {
    localStorage.setItem(VOLUME_KEY, String(volume));
    localStorage.setItem(MUTED_KEY, muted ? '1' : '0');
  } catch (_) {
    // Preference only.
  }
}

function readRepeatMode(): RepeatMode {
  try {
    const value = localStorage.getItem(REPEAT_KEY);
    return value === 'all' || value === 'one' ? value : 'off';
  } catch (_) {
    return 'off';
  }
}

function initialPlayerState(): PlayerState {
  // Music should start at normal speed. Remove the legacy persisted setting
  // so a past 0.75x choice cannot unexpectedly affect a later session.
  try {
    localStorage.removeItem(SPEED_KEY);
  } catch (_) {
    // Storage is optional in private or quota-limited browsing modes.
  }
  const base: PlayerState = {
    track: null,
    queue: [],
    queueIndex: -1,
    playing: false,
    currentTime: 0,
    duration: 0,
    error: '',
    speed: 1,
    repeatMode: readRepeatMode(),
    volume: clamp(readNumber(VOLUME_KEY, DEFAULT_VOLUME), 0, 1),
    muted: localStorage.getItem(MUTED_KEY) === '1',
    nextTrack: null,
    nextCountdown: NEXT_COUNTDOWN_SECONDS,
    queueToast: '',
  };

  try {
    const stored = JSON.parse(localStorage.getItem(PLAYER_KEY) || 'null') as Partial<PlayerState> | null;
    if (!stored?.track) return base;
    const queue = Array.isArray(stored.queue) && stored.queue.length ? stored.queue : [stored.track];
    const queueIndex = Math.max(0, queue.findIndex((item) => item.key === stored.track?.key));
    return {
      ...base,
      track: stored.track,
      queue,
      queueIndex,
      currentTime: Number(stored.currentTime) || 0,
      duration: Number(stored.duration) || stored.track.duration || 0,
    };
  } catch (_) {
    return base;
  }
}

export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0:00';
  const whole = Math.floor(seconds);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}

function playbackErrorName(error: unknown): string {
  if (!error || typeof error !== 'object' || !('name' in error)) return '';
  const name = (error as { name?: unknown }).name;
  return typeof name === 'string' ? name : '';
}

function playbackFormatLabel(track: WatchTrack | null): string {
  const label = track?.format || track?.qualityLabel || track?.quality || '';
  return label ? label.toUpperCase() : 'audio';
}

function unsupportedPlaybackMessage(track: WatchTrack | null): string {
  return `This browser could not play the ${playbackFormatLabel(track)} stream. It may be unsupported or returning an error page. Try the classic player or download it.`;
}

export function describeAudioPlaybackFailure(
  error: unknown,
  audio: HTMLAudioElement | null,
  track: WatchTrack | null,
): string {
  const name = playbackErrorName(error);
  if (name === 'NotAllowedError') return 'Tap play to start audio.';
  if (typeof navigator !== 'undefined' && 'onLine' in navigator && !navigator.onLine) {
    return 'You appear to be offline. Reconnect and try playing the track again.';
  }

  const mediaError = audio?.error;
  switch (mediaError?.code) {
    case MEDIA_ERR_ABORTED:
      return 'Playback was interrupted before the audio started. Tap play to try again.';
    case MEDIA_ERR_NETWORK:
      return 'Network issue while loading the audio stream. Try again, open the classic player, or download the track.';
    case MEDIA_ERR_DECODE:
      return 'This track loaded but could not be decoded by the browser. Try the classic player or download it.';
    case MEDIA_ERR_SRC_NOT_SUPPORTED:
      return unsupportedPlaybackMessage(track);
    default:
      break;
  }

  if (name === 'NotSupportedError') {
    return unsupportedPlaybackMessage(track);
  }
  if (name === 'AbortError') {
    return 'Playback was interrupted before the audio started. Tap play to try again.';
  }
  return 'Audio playback failed. Try again, open the classic player, or download the track.';
}

function persistNowPlaying(player: PlayerState): void {
  try {
    if (!player.track) {
      localStorage.removeItem(PLAYER_KEY);
      localStorage.removeItem(NOW_PLAYING_KEY);
      sessionStorage.removeItem(QUEUE_KEY);
      sessionStorage.removeItem(QUEUE_INDEX_KEY);
      return;
    }
    localStorage.setItem(PLAYER_KEY, JSON.stringify({
      track: player.track,
      queue: player.queue,
      queueIndex: player.queueIndex,
      currentTime: Math.floor(player.currentTime),
      duration: Math.floor(player.duration || player.track.duration || 0),
      speed: player.speed,
      repeatMode: player.repeatMode,
      volume: player.volume,
      muted: player.muted,
    }));
    localStorage.setItem(NOW_PLAYING_KEY, JSON.stringify({
      url: player.track.classicHref,
      streamUrl: player.track.streamHref,
      title: player.track.title,
      artist: player.track.artist || player.track.albumTitle || '',
      art: player.track.posterUrl || player.track.thumbUrl || '',
      position: Math.floor(player.currentTime),
      nextUrl: player.queue[player.queueIndex + 1]?.classicHref || null,
      nextStreamUrl: player.queue[player.queueIndex + 1]?.streamHref || null,
      nextTitle: player.queue[player.queueIndex + 1]?.title || null,
      nextArtist: player.queue[player.queueIndex + 1]?.artist || null,
      nextArt: player.queue[player.queueIndex + 1]?.posterUrl || null,
      prevUrl: player.queue[player.queueIndex - 1]?.classicHref || null,
      prevStreamUrl: player.queue[player.queueIndex - 1]?.streamHref || null,
    }));
    sessionStorage.setItem(QUEUE_KEY, JSON.stringify(player.queue.map((track) => ({
      hash: track.key,
      watchUrl: track.classicHref,
      streamUrl: track.streamHref,
      title: track.title,
      artist: track.artist,
      art: track.posterUrl || track.thumbUrl,
    }))));
    sessionStorage.setItem(QUEUE_INDEX_KEY, String(player.queueIndex));
  } catch (_) {
    // Storage is best-effort; playback must continue in private/quota-limited modes.
  }
}

export function useAudioPlayer() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const bufferRef = useRef<HTMLAudioElement | null>(null);
  const activeSlotRef = useRef<'primary' | 'buffer'>('primary');
  const preloadedKeyRef = useRef('');
  const crossfadeRef = useRef(false);
  const pendingNextIndexRef = useRef<number | null>(null);
  const explicitSilentOutputRef = useRef(false);
  const toastTimerRef = useRef<number | null>(null);
  const playbackWatchdogRef = useRef<number | null>(null);
  const playbackAttemptRef = useRef(0);
  const playbackAttemptTrackKeyRef = useRef('');
  const cwLastSyncRef = useRef(0);
  const cwSessionKeyRef = useRef('');
  const cwSessionStartedRef = useRef(0);
  const persistLastRef = useRef(0);
  const [player, setPlayer] = useState<PlayerState>(() => initialPlayerState());
  const playerRef = useRef(player);

  useEffect(() => {
    playerRef.current = player;
  }, [player]);

  const getActiveAudio = useCallback(() => (
    activeSlotRef.current === 'primary' ? audioRef.current : bufferRef.current
  ), []);

  const getInactiveAudio = useCallback(() => (
    activeSlotRef.current === 'primary' ? bufferRef.current : audioRef.current
  ), []);

  const applyOutputSettings = useCallback((audio: HTMLAudioElement | null, fadeVolume?: number) => {
    if (!audio) return;
    const current = playerRef.current;
    audio.playbackRate = current.speed;
    audio.muted = current.muted;
    audio.volume = current.muted ? 0 : clamp(fadeVolume ?? current.volume, 0, 1);
  }, []);

  const clearPlaybackWatchdog = useCallback(() => {
    if (!playbackWatchdogRef.current) return;
    window.clearTimeout(playbackWatchdogRef.current);
    playbackWatchdogRef.current = null;
  }, []);

  const beginPlaybackAttempt = useCallback((track?: WatchTrack | null) => {
    playbackAttemptRef.current += 1;
    playbackAttemptTrackKeyRef.current = track?.key || '';
    return playbackAttemptRef.current;
  }, []);

  const cancelPlaybackAttempt = useCallback(() => {
    playbackAttemptRef.current += 1;
    playbackAttemptTrackKeyRef.current = '';
    clearPlaybackWatchdog();
  }, [clearPlaybackWatchdog]);

  const ensureAudibleOutput = useCallback(() => {
    const current = playerRef.current;
    if (explicitSilentOutputRef.current || (!current.muted && current.volume > SILENT_VOLUME)) return;

    const restoredVolume = current.volume > SILENT_VOLUME ? current.volume : DEFAULT_VOLUME;
    persistOutputSettings(restoredVolume, false);
    playerRef.current = {
      ...current,
      muted: false,
      volume: restoredVolume,
      error: '',
    };
    setPlayer((state) => ({
      ...state,
      muted: false,
      volume: restoredVolume,
      error: '',
    }));
    [audioRef.current, bufferRef.current].forEach((audio) => {
      if (!audio) return;
      audio.muted = false;
      audio.volume = restoredVolume;
    });
  }, []);

  const showQueueToast = useCallback((message: string) => {
    if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    setPlayer((state) => ({ ...state, queueToast: message }));
    toastTimerRef.current = window.setTimeout(() => {
      setPlayer((state) => ({ ...state, queueToast: '' }));
    }, 1400);
  }, []);

  const setMediaSessionPlaybackState = useCallback((playing: boolean, hasTrack = Boolean(playerRef.current.track)) => {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.playbackState = hasTrack ? (playing ? 'playing' : 'paused') : 'none';
    } catch (_) {
      // Media Session support is uneven; ignore per-browser failures.
    }
  }, []);

  const setMediaSessionPosition = useCallback((state: PlayerState) => {
    if (!('mediaSession' in navigator) || !state.track || !navigator.mediaSession.setPositionState) return;
    const duration = state.duration || state.track.duration || 0;
    if (duration <= 0) return;
    try {
      navigator.mediaSession.setPositionState({
        duration,
        playbackRate: state.speed,
        position: clamp(state.currentTime, 0, duration),
      });
    } catch (_) {
      // Media Session support is uneven; ignore per-browser failures.
    }
  }, []);

  const setMediaSessionMetadata = useCallback((state: PlayerState) => {
    if (!('mediaSession' in navigator)) return;
    try {
      if (!state.track) {
        navigator.mediaSession.metadata = null;
        setMediaSessionPlaybackState(false, false);
        return;
      }
      if ('MediaMetadata' in window) {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: state.track.title || '',
          artist: state.track.artist || state.track.albumTitle || '',
          album: state.track.albumTitle || '',
          artwork: state.track.posterUrl ? [{ src: state.track.posterUrl, sizes: '512x512', type: 'image/jpeg' }] : [],
        });
      }
      setMediaSessionPlaybackState(state.playing, true);
      setMediaSessionPosition(state);
    } catch (_) {
      // Media Session support is uneven; ignore per-browser failures.
    }
  }, [setMediaSessionPlaybackState, setMediaSessionPosition]);

  const seekActiveAudioTo = useCallback((seconds: number) => {
    const audio = getActiveAudio();
    const current = playerRef.current;
    if (!audio || !current.track || !Number.isFinite(seconds)) return;
    const duration = audio.duration || current.duration || current.track.duration || 0;
    const nextTime = duration > 0 ? clamp(seconds, 0, duration) : Math.max(0, seconds);
    audio.currentTime = nextTime;
    const nextState = {
      ...current,
      currentTime: nextTime,
      duration,
    };
    playerRef.current = nextState;
    setPlayer((state) => ({
      ...state,
      currentTime: nextTime,
      duration,
    }));
    setMediaSessionPosition(nextState);
  }, [getActiveAudio, setMediaSessionPosition]);

  const seekActiveAudioBy = useCallback((offset: number) => {
    const audio = getActiveAudio();
    const current = playerRef.current;
    const baseTime = audio?.currentTime || current.currentTime || 0;
    seekActiveAudioTo(baseTime + offset);
  }, [getActiveAudio, seekActiveAudioTo]);

  const failPlayback = useCallback((track: WatchTrack | null, audio: HTMLAudioElement | null, attemptId: number, error?: unknown) => {
    if (attemptId !== playbackAttemptRef.current) return;
    if (audio && audio !== getActiveAudio()) return;
    const attemptTrackKey = playbackAttemptTrackKeyRef.current;
    if (track?.key && attemptTrackKey && attemptTrackKey !== track.key) return;
    clearPlaybackWatchdog();
    try {
      if (audio && !audio.paused) audio.pause();
    } catch (_) {
      // Some browsers can throw while an element is changing source.
    }
    setMediaSessionPlaybackState(false, Boolean(playerRef.current.track || track));
    const failedKey = track?.key || '';
    setPlayer((state) => {
      if (failedKey && state.track?.key !== failedKey) return state;
      return {
        ...state,
        playing: false,
        error: describeAudioPlaybackFailure(error, audio, state.track || track),
      };
    });
  }, [clearPlaybackWatchdog, getActiveAudio, setMediaSessionPlaybackState]);

  const armPlaybackWatchdog = useCallback((audio: HTMLAudioElement, track: WatchTrack, attemptId: number) => {
    clearPlaybackWatchdog();
    const trackKey = track.key;
    const timer = window.setTimeout(() => {
      if (playbackWatchdogRef.current === timer) playbackWatchdogRef.current = null;
      if (attemptId !== playbackAttemptRef.current) return;
      if (getActiveAudio() !== audio) return;
      if (audio.error || audio.readyState >= 3 || audio.currentTime > 0) return;
      try {
        if (!audio.paused) audio.pause();
      } catch (_) {
        // Keep state recovery working even if pause is rejected by the browser.
      }
      setMediaSessionPlaybackState(false, true);
      setPlayer((state) => {
        if (state.track?.key !== trackKey || !state.playing) return state;
        return {
          ...state,
          playing: false,
          error: 'Still waiting for audio data. The stream may be slow or blocked; try again, open the classic player, or download the track.',
        };
      });
    }, PLAYBACK_START_TIMEOUT_MS);
    playbackWatchdogRef.current = timer;
  }, [clearPlaybackWatchdog, getActiveAudio, setMediaSessionPlaybackState]);

  const playActiveAudioFromSession = useCallback(() => {
    const audio = getActiveAudio();
    const current = playerRef.current;
    if (!audio || !current.track) return;
    ensureAudibleOutput();
    if (audio.error || !audio.src) {
      const reloadFromError = Boolean(audio.error);
      audio.src = trackSrc(current.track);
      audio.currentTime = reloadFromError ? 0 : current.currentTime || 0;
      preloadedKeyRef.current = '';
      audio.load();
    }
    applyOutputSettings(audio);
    setMediaSessionPlaybackState(true, true);
    setPlayer((state) => ({
      ...state,
      playing: true,
      error: '',
    }));
    const attemptId = beginPlaybackAttempt(current.track);
    const promise = audio.play();
    armPlaybackWatchdog(audio, current.track, attemptId);
    if (promise) {
      promise.catch((error) => failPlayback(current.track, audio, attemptId, error));
    }
  }, [applyOutputSettings, armPlaybackWatchdog, beginPlaybackAttempt, ensureAudibleOutput, failPlayback, getActiveAudio, setMediaSessionPlaybackState]);

  const pauseActiveAudioFromSession = useCallback(() => {
    const audio = getActiveAudio();
    if (!audio) return;
    cancelPlaybackAttempt();
    audio.pause();
    setMediaSessionPlaybackState(false, Boolean(playerRef.current.track));
    setPlayer((state) => ({ ...state, playing: false }));
  }, [cancelPlaybackAttempt, getActiveAudio, setMediaSessionPlaybackState]);

  const setMediaSessionAction = useCallback((action: MediaSessionAction, handler: MediaSessionActionHandler | null) => {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.setActionHandler(action, handler);
    } catch (_) {
      // Browsers may expose only a subset of Media Session actions.
    }
  }, []);

  const startAudio = useCallback((track: WatchTrack, reset = false) => {
    const audio = getActiveAudio();
    if (!audio) return;
    const nextSrc = trackSrc(track);
    const shouldReload = audio.src !== nextSrc || Boolean(audio.error);
    if (shouldReload) {
      audio.src = nextSrc;
      reset = true;
      preloadedKeyRef.current = '';
      audio.load();
    }
    if (reset) audio.currentTime = 0;
    applyOutputSettings(audio);
    const attemptId = beginPlaybackAttempt(track);
    const promise = audio.play();
    armPlaybackWatchdog(audio, track, attemptId);
    if (promise) {
      promise.catch((error) => failPlayback(track, audio, attemptId, error));
    }
  }, [applyOutputSettings, armPlaybackWatchdog, beginPlaybackAttempt, failPlayback, getActiveAudio]);

  const stopInactive = useCallback(() => {
    const inactive = getInactiveAudio();
    if (!inactive) return;
    inactive.pause();
    inactive.removeAttribute('src');
    inactive.load();
  }, [getInactiveAudio]);

  const playTrack = useCallback((track: WatchTrack, queue?: WatchTrack[]) => {
    ensureAudibleOutput();
    const current = playerRef.current;
    const nextQueue = queue?.length ? queue : current.queue.length ? current.queue : [track];
    const found = nextQueue.findIndex((item) => item.key === track.key);
    const queueIndex = found >= 0 ? found : 0;
    const sameTrack = current.track?.key === track.key;
    pendingNextIndexRef.current = null;
    stopInactive();
    setPlayer((state) => ({
      ...state,
      track,
      queue: nextQueue,
      queueIndex,
      playing: true,
      currentTime: sameTrack ? state.currentTime : 0,
      duration: sameTrack ? state.duration : track.duration || 0,
      error: '',
      nextTrack: null,
      nextCountdown: NEXT_COUNTDOWN_SECONDS,
    }));
    startAudio(track, !sameTrack);
  }, [ensureAudibleOutput, startAudio, stopInactive]);

  const resolveNextIndex = useCallback((delta: number) => {
    const current = playerRef.current;
    const raw = current.queueIndex + delta;
    if (raw >= 0 && raw < current.queue.length) return raw;
    if (current.repeatMode === 'all' && current.queue.length > 0) {
      return raw < 0 ? current.queue.length - 1 : 0;
    }
    return -1;
  }, []);

  const playQueueIndex = useCallback((index: number) => {
    ensureAudibleOutput();
    const current = playerRef.current;
    const nextTrack = current.queue[index];
    if (!nextTrack) return;
    pendingNextIndexRef.current = null;
    stopInactive();
    setPlayer((state) => ({
      ...state,
      track: nextTrack,
      queueIndex: index,
      playing: true,
      currentTime: 0,
      duration: nextTrack.duration || 0,
      error: '',
      nextTrack: null,
      nextCountdown: NEXT_COUNTDOWN_SECONDS,
    }));
    startAudio(nextTrack, true);
  }, [ensureAudibleOutput, startAudio, stopInactive]);

  const playRelative = useCallback((delta: number) => {
    const index = resolveNextIndex(delta);
    if (index < 0) return;
    playQueueIndex(index);
  }, [playQueueIndex, resolveNextIndex]);

  const playPreviousFromSession = useCallback(() => {
    const audio = getActiveAudio();
    const current = playerRef.current;
    if ((audio?.currentTime || current.currentTime || 0) > 3) {
      seekActiveAudioTo(0);
      return;
    }
    playRelative(-1);
  }, [getActiveAudio, playRelative, seekActiveAudioTo]);

  const confirmNext = useCallback(() => {
    const index = pendingNextIndexRef.current;
    if (index === null) return;
    pendingNextIndexRef.current = null;
    playQueueIndex(index);
  }, [playQueueIndex]);

  const cancelNext = useCallback(() => {
    pendingNextIndexRef.current = null;
    setPlayer((state) => ({
      ...state,
      nextTrack: null,
      nextCountdown: NEXT_COUNTDOWN_SECONDS,
      playing: false,
    }));
  }, []);

  const dismissPlayer = useCallback(() => {
    pendingNextIndexRef.current = null;
    preloadedKeyRef.current = '';
    crossfadeRef.current = false;
    cancelPlaybackAttempt();
    [audioRef.current, bufferRef.current].forEach((audio) => {
      if (!audio) return;
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    });
    if ('mediaSession' in navigator) {
      try {
        setMediaSessionPlaybackState(false, false);
        navigator.mediaSession.metadata = null;
      } catch (_) {
        // Ignore uneven browser Media Session support.
      }
    }
    setPlayer((state) => ({
      ...state,
      track: null,
      queue: [],
      queueIndex: -1,
      playing: false,
      currentTime: 0,
      duration: 0,
      error: '',
      nextTrack: null,
      nextCountdown: NEXT_COUNTDOWN_SECONDS,
      queueToast: '',
      speed: 1,
    }));
  }, [cancelPlaybackAttempt, setMediaSessionPlaybackState]);

  const clearAudioMediaSessionActions = useCallback(() => {
    setMediaSessionAction('play', null);
    setMediaSessionAction('pause', null);
    setMediaSessionAction('nexttrack', null);
    setMediaSessionAction('previoustrack', null);
    setMediaSessionAction('seekto', null);
    setMediaSessionAction('seekforward', null);
    setMediaSessionAction('seekbackward', null);
    setMediaSessionAction('stop', null);
  }, [setMediaSessionAction]);

  const registerAudioMediaSessionActions = useCallback(() => {
    if (!('mediaSession' in navigator)) return undefined;
    setMediaSessionAction('play', playActiveAudioFromSession);
    setMediaSessionAction('pause', pauseActiveAudioFromSession);
    setMediaSessionAction('nexttrack', () => playRelative(1));
    setMediaSessionAction('previoustrack', playPreviousFromSession);
    setMediaSessionAction('seekto', (details) => {
      if (Number.isFinite(details.seekTime)) seekActiveAudioTo(details.seekTime || 0);
    });
    setMediaSessionAction('seekforward', (details) => seekActiveAudioBy(details.seekOffset || 10));
    setMediaSessionAction('seekbackward', (details) => seekActiveAudioBy(-(details.seekOffset || 10)));
    setMediaSessionAction('stop', dismissPlayer);
    setMediaSessionMetadata(playerRef.current);
  }, [
    dismissPlayer,
    pauseActiveAudioFromSession,
    playPreviousFromSession,
    playActiveAudioFromSession,
    playRelative,
    seekActiveAudioBy,
    seekActiveAudioTo,
    setMediaSessionAction,
    setMediaSessionMetadata,
  ]);

  useEffect(() => {
    registerAudioMediaSessionActions();
    return clearAudioMediaSessionActions;
  }, [clearAudioMediaSessionActions, registerAudioMediaSessionActions]);

  useEffect(() => {
    const onRestore = () => registerAudioMediaSessionActions();
    window.addEventListener(RESTORE_AUDIO_MEDIA_SESSION_EVENT, onRestore);
    return () => window.removeEventListener(RESTORE_AUDIO_MEDIA_SESSION_EVENT, onRestore);
  }, [
    registerAudioMediaSessionActions,
  ]);

  const scheduleNext = useCallback((index: number) => {
    const nextTrack = playerRef.current.queue[index];
    if (!nextTrack) return;
    pendingNextIndexRef.current = index;
    setPlayer((state) => ({
      ...state,
      playing: false,
      nextTrack,
      nextCountdown: NEXT_COUNTDOWN_SECONDS,
    }));
  }, []);

  const addToQueue = useCallback((track: WatchTrack, playNext = false) => {
    const message = playNext ? 'Playing next' : 'Added to queue';
    setPlayer((state) => {
      if (!state.track) {
        window.setTimeout(() => playTrack(track, [track]), 0);
        return state;
      }
      const queue = state.queue.length ? [...state.queue] : [state.track];
      const existing = queue.findIndex((item) => item.key === track.key);
      if (existing >= 0) queue.splice(existing, 1);
      const insertAt = playNext ? Math.max(0, state.queueIndex + 1) : queue.length;
      queue.splice(insertAt, 0, track);
      const queueIndex = queue.findIndex((item) => item.key === state.track?.key);
      return { ...state, queue, queueIndex: queueIndex >= 0 ? queueIndex : state.queueIndex, queueToast: message };
    });
    showQueueToast(message);
  }, [playTrack, showQueueToast]);

  const removeFromQueue = useCallback((index: number) => {
    setPlayer((state) => {
      if (index < 0 || index >= state.queue.length) return state;
      if (index === state.queueIndex) return state;
      const queue = state.queue.filter((_, itemIndex) => itemIndex !== index);
      const queueIndex = index < state.queueIndex ? state.queueIndex - 1 : state.queueIndex;
      return { ...state, queue, queueIndex };
    });
  }, []);

  const clearQueue = useCallback(() => {
    setPlayer((state) => state.track
      ? { ...state, queue: [state.track], queueIndex: 0, nextTrack: null, nextCountdown: NEXT_COUNTDOWN_SECONDS }
      : state);
  }, []);

  const moveQueueItem = useCallback((index: number, direction: -1 | 1) => {
    setPlayer((state) => {
      const nextIndex = index + direction;
      if (index < 0 || nextIndex < 0 || index >= state.queue.length || nextIndex >= state.queue.length) return state;
      const queue = state.queue.slice();
      const [item] = queue.splice(index, 1);
      queue.splice(nextIndex, 0, item);
      const queueIndex = queue.findIndex((track) => track.key === state.track?.key);
      return { ...state, queue, queueIndex };
    });
  }, []);

  const shuffleQueue = useCallback((queue: WatchTrack[]) => {
    if (!queue.length) return;
    const shuffled = queue.slice();
    for (let index = shuffled.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(Math.random() * (index + 1));
      [shuffled[index], shuffled[swap]] = [shuffled[swap], shuffled[index]];
    }
    try {
      sessionStorage.setItem(`td:shuffle:${shuffled[0].albumTitle || 'queue'}`, JSON.stringify(shuffled.map((track) => track.key)));
    } catch (_) {
      // Shuffle persistence is only for same-tab continuity.
    }
    playTrack(shuffled[0], shuffled);
  }, [playTrack]);

  const togglePlayback = useCallback((track?: WatchTrack, queue?: WatchTrack[]) => {
    const current = playerRef.current;
    if (track && current.track?.key !== track.key) {
      playTrack(track, queue);
      return;
    }
    const audio = getActiveAudio();
    if (!audio || !current.track) return;
    if (audio.paused) {
      ensureAudibleOutput();
      setMediaSessionPlaybackState(true, true);
      setPlayer((state) => ({ ...state, playing: true, error: '' }));
      if (current.track && (audio.error || !audio.src)) {
        startAudio(current.track, false);
        return;
      }
      applyOutputSettings(audio);
      const attemptId = beginPlaybackAttempt(current.track);
      const promise = audio.play();
      if (current.track) armPlaybackWatchdog(audio, current.track, attemptId);
      if (promise) {
        promise.catch((error) => failPlayback(current.track, audio, attemptId, error));
      }
    } else {
      cancelPlaybackAttempt();
      audio.pause();
      setMediaSessionPlaybackState(false, true);
    }
  }, [
    applyOutputSettings,
    armPlaybackWatchdog,
    beginPlaybackAttempt,
    cancelPlaybackAttempt,
    ensureAudibleOutput,
    failPlayback,
    getActiveAudio,
    playTrack,
    setMediaSessionPlaybackState,
    startAudio,
  ]);

  const seek = useCallback((seconds: number) => {
    seekActiveAudioTo(seconds);
  }, [seekActiveAudioTo]);

  const setSpeed = useCallback((speed: number) => {
    const next = clamp(speed, 0.5, 3);
    setPlayer((state) => ({ ...state, speed: next }));
    if (audioRef.current) audioRef.current.playbackRate = next;
    if (bufferRef.current) bufferRef.current.playbackRate = next;
  }, []);

  const setRepeatMode = useCallback((mode: RepeatMode) => {
    try {
      localStorage.setItem(REPEAT_KEY, mode);
    } catch (_) {
      // Preference only.
    }
    setPlayer((state) => ({ ...state, repeatMode: mode }));
  }, []);

  const cycleRepeatMode = useCallback(() => {
    const current = playerRef.current.repeatMode;
    const next: RepeatMode = current === 'off' ? 'all' : current === 'all' ? 'one' : 'off';
    setRepeatMode(next);
  }, [setRepeatMode]);

  const setVolume = useCallback((value: number) => {
    const next = clamp(value, 0, 1);
    const silent = next <= SILENT_VOLUME;
    explicitSilentOutputRef.current = silent;
    persistOutputSettings(next, silent);
    playerRef.current = {
      ...playerRef.current,
      volume: next,
      muted: silent,
    };
    setPlayer((state) => ({ ...state, volume: next, muted: silent }));
    [audioRef.current, bufferRef.current].forEach((audio) => {
      if (!audio) return;
      audio.muted = silent;
      audio.volume = silent ? 0 : next;
    });
  }, []);

  const toggleMute = useCallback(() => {
    const current = playerRef.current;
    const nextMuted = !current.muted;
    const nextVolume = !nextMuted && current.volume <= SILENT_VOLUME ? DEFAULT_VOLUME : current.volume;
    explicitSilentOutputRef.current = nextMuted;
    persistOutputSettings(nextVolume, nextMuted);
    playerRef.current = {
      ...current,
      volume: nextVolume,
      muted: nextMuted,
    };
    setPlayer((state) => ({ ...state, volume: nextVolume, muted: nextMuted }));
    [audioRef.current, bufferRef.current].forEach((audio) => {
      if (!audio) return;
      audio.muted = nextMuted;
      audio.volume = nextMuted ? 0 : nextVolume;
    });
  }, []);

  const maybeCrossfade = useCallback((audio: HTMLAudioElement) => {
    const current = playerRef.current;
    const nextIndex = resolveNextIndex(1);
    const next = nextIndex >= 0 ? current.queue[nextIndex] : null;
    const duration = audio.duration || current.duration;
    if (!next || current.repeatMode === 'one' || !Number.isFinite(duration) || duration <= 0) return;
    const remaining = duration - audio.currentTime;
    const inactive = getInactiveAudio();
    if (!inactive) return;
    const nextSrc = trackSrc(next);
    if (remaining <= PRELOAD_AT_SECONDS && preloadedKeyRef.current !== next.key) {
      inactive.src = nextSrc;
      inactive.preload = 'auto';
      inactive.load();
      preloadedKeyRef.current = next.key;
    }
    if (remaining > CROSSFADE_SECONDS || crossfadeRef.current) return;
    crossfadeRef.current = true;
    inactive.src = inactive.src || nextSrc;
    inactive.currentTime = 0;
    applyOutputSettings(inactive, 0);
    const from = audio;
    const to = inactive;
    const targetSlot = activeSlotRef.current === 'primary' ? 'buffer' : 'primary';
    to.play()
      .then(() => {
        // Capture faded-out track BEFORE setPlayer switches to the new one.
        const fadedKey = playerRef.current.track?.key;
        const fadedTitle = playerRef.current.track?.title || '';
        activeSlotRef.current = targetSlot;
        pendingNextIndexRef.current = null;
        setPlayer((state) => ({
          ...state,
          track: next,
          queueIndex: nextIndex,
          playing: true,
          currentTime: 0,
          duration: next.duration || 0,
          nextTrack: null,
          nextCountdown: NEXT_COUNTDOWN_SECONDS,
        }));
        const started = performance.now();
        const tick = () => {
          const progress = clamp((performance.now() - started) / (CROSSFADE_SECONDS * 1000), 0, 1);
          const baseVolume = playerRef.current.muted ? 0 : playerRef.current.volume;
          from.volume = baseVolume * (1 - progress);
          to.volume = baseVolume * progress;
          if (progress < 1) {
            requestAnimationFrame(tick);
            return;
          }
          from.pause();
          from.removeAttribute('src');
          from.load();
          applyOutputSettings(to);
          preloadedKeyRef.current = '';
          crossfadeRef.current = false;
          // onEnded is skipped during crossfade — clean up CW for the faded track here.
          if (fadedKey) {
            void recordWatchHistory(fadedKey, fadedTitle).catch(() => undefined);
            void deleteContinueEntry(fadedKey).catch(() => undefined);
            try {
              const cw = JSON.parse(localStorage.getItem('td:cw') || '{}') || {};
              delete cw[fadedKey];
              localStorage.setItem('td:cw', JSON.stringify(cw));
            } catch { /* ignore */ }
          }
        };
        requestAnimationFrame(tick);
      })
      .catch(() => {
        crossfadeRef.current = false;
      });
  }, [applyOutputSettings, getInactiveAudio, resolveNextIndex]);

  useEffect(() => {
    const audioNodes = [audioRef.current, bufferRef.current].filter(Boolean) as HTMLAudioElement[];
    if (!audioNodes.length) return undefined;

    const onTime = (event: Event) => {
      const audio = event.currentTarget as HTMLAudioElement;
      if (audio !== getActiveAudio()) return;
      const now = Date.now();
      const current = playerRef.current;
      const currentTime = audio.currentTime || 0;
      const duration = audio.duration || current.duration || current.track?.duration || 0;
      setPlayer((state) => ({
        ...state,
        currentTime,
        duration,
      }));
      if (currentTime > 0 || audio.readyState >= 3) clearPlaybackWatchdog();
      if (current.track && now - persistLastRef.current > 2000) {
        persistLastRef.current = now;
        persistNowPlaying({ ...current, currentTime, duration });
      }
      if (current.track && currentTime > 5 && duration > 0 && now - cwLastSyncRef.current > 30000) {
        if (cwSessionKeyRef.current !== current.track.key) {
          cwSessionKeyRef.current = current.track.key;
          cwSessionStartedRef.current = now;
        }
        cwLastSyncRef.current = now;
        void saveContinueEntry(current.track.key, {
          pos: Math.floor(currentTime),
          dur: Math.floor(duration),
          t: now,
          title: current.track.title,
          startedAt: cwSessionStartedRef.current || now,
        }).catch(() => undefined);
      }
      if ('mediaSession' in navigator && current.track && duration > 0) {
        setMediaSessionPosition({ ...current, currentTime, duration });
      }
      maybeCrossfade(audio);
    };

    const onPlay = (event: Event) => {
      const audio = event.currentTarget as HTMLAudioElement;
      if (audio !== getActiveAudio()) return;
      const current = playerRef.current;
      if (current.track) armPlaybackWatchdog(audio, current.track, playbackAttemptRef.current);
      setMediaSessionPlaybackState(true, Boolean(playerRef.current.track));
      setPlayer((state) => ({ ...state, playing: true, error: '' }));
    };
    const onPlaying = (event: Event) => {
      if (event.currentTarget !== getActiveAudio()) return;
      clearPlaybackWatchdog();
      setMediaSessionPlaybackState(true, Boolean(playerRef.current.track));
      setPlayer((state) => ({ ...state, playing: true, error: '' }));
    };
    const onPause = (event: Event) => {
      if (event.currentTarget !== getActiveAudio()) return;
      cancelPlaybackAttempt();
      setMediaSessionPlaybackState(false, Boolean(playerRef.current.track));
      setPlayer((state) => ({ ...state, playing: false }));
    };
    const onEnded = (event: Event) => {
      const audio = event.currentTarget as HTMLAudioElement;
      if (audio !== getActiveAudio() || crossfadeRef.current) return;
      clearPlaybackWatchdog();
      const current = playerRef.current;
      if (current.track) {
        void recordWatchHistory(current.track.key, current.track.title).catch(() => undefined);
        void deleteContinueEntry(current.track.key).catch(() => undefined);
        try {
          const cw = JSON.parse(localStorage.getItem('td:cw') || '{}') || {};
          delete cw[current.track.key];
          localStorage.setItem('td:cw', JSON.stringify(cw));
        } catch { /* ignore quota/private-mode */ }
      }
      if (current.repeatMode === 'one' && current.track) {
        audio.currentTime = 0;
        void audio.play();
        return;
      }
      const nextIndex = resolveNextIndex(1);
      if (nextIndex < 0) {
        setPlayer((state) => ({ ...state, playing: false }));
        return;
      }
      scheduleNext(nextIndex);
    };
    const onError = (event: Event) => {
      const audio = event.currentTarget as HTMLAudioElement;
      if (audio !== getActiveAudio()) return;
      const current = playerRef.current;
      if (!current.playing) return;
      failPlayback(current.track, audio, playbackAttemptRef.current);
    };
    const onWaiting = (event: Event) => {
      const audio = event.currentTarget as HTMLAudioElement;
      const current = playerRef.current;
      if (audio !== getActiveAudio() || !current.track || !current.playing) return;
      armPlaybackWatchdog(audio, current.track, playbackAttemptRef.current);
    };

    audioNodes.forEach((audio) => {
      audio.addEventListener('timeupdate', onTime);
      audio.addEventListener('loadedmetadata', onTime);
      audio.addEventListener('durationchange', onTime);
      audio.addEventListener('play', onPlay);
      audio.addEventListener('playing', onPlaying);
      audio.addEventListener('pause', onPause);
      audio.addEventListener('ended', onEnded);
      audio.addEventListener('error', onError);
      audio.addEventListener('stalled', onWaiting);
      audio.addEventListener('waiting', onWaiting);
      applyOutputSettings(audio);
    });

    return () => {
      audioNodes.forEach((audio) => {
        audio.removeEventListener('timeupdate', onTime);
        audio.removeEventListener('loadedmetadata', onTime);
        audio.removeEventListener('durationchange', onTime);
        audio.removeEventListener('play', onPlay);
        audio.removeEventListener('playing', onPlaying);
        audio.removeEventListener('pause', onPause);
        audio.removeEventListener('ended', onEnded);
        audio.removeEventListener('error', onError);
        audio.removeEventListener('stalled', onWaiting);
        audio.removeEventListener('waiting', onWaiting);
      });
    };
  }, [
    applyOutputSettings,
    armPlaybackWatchdog,
    cancelPlaybackAttempt,
    clearPlaybackWatchdog,
    failPlayback,
    getActiveAudio,
    maybeCrossfade,
    resolveNextIndex,
    scheduleNext,
    setMediaSessionPlaybackState,
    setMediaSessionPosition,
  ]);

  useEffect(() => {
    const audio = getActiveAudio();
    const current = playerRef.current;
    if (!audio || !current.track || audio.src) return;
    audio.src = trackSrc(current.track);
    audio.currentTime = current.currentTime || 0;
    applyOutputSettings(audio);
  }, [applyOutputSettings, getActiveAudio]);

  useEffect(() => {
    persistNowPlaying(player);
    setMediaSessionMetadata(player);
  }, [player.queue, player.queueIndex, player.repeatMode, player.speed, player.track, player.volume, player.muted, setMediaSessionMetadata]);

  useEffect(() => {
    if (!player.track || !player.playing) return undefined;
    const currentTrack = player.track;
    const nextTrack = player.queue[player.queueIndex + 1] || null;
    const timer = window.setTimeout(() => {
      void preloadLyrics(currentTrack);
      if (nextTrack) void preloadLyrics(nextTrack);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [player.playing, player.queue, player.queueIndex, player.track]);

  useEffect(() => {
    setMediaSessionPlaybackState(player.playing, Boolean(player.track));
  }, [player.playing, player.track, setMediaSessionPlaybackState]);

  useEffect(() => {
    setMediaSessionPosition(player);
  }, [player.duration, player.speed, player.track, setMediaSessionPosition]);

  useEffect(() => {
    if (!player.nextTrack) return undefined;
    if (player.nextCountdown <= 0) {
      confirmNext();
      return undefined;
    }
    const timer = window.setTimeout(() => {
      setPlayer((state) => state.nextTrack
        ? { ...state, nextCountdown: state.nextCountdown - 1 }
        : state);
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [confirmNext, player.nextCountdown, player.nextTrack]);

  useEffect(() => () => {
    if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    clearPlaybackWatchdog();
  }, [clearPlaybackWatchdog]);

  return {
    audioRef,
    bufferRef,
    player,
    playTrack,
    playRelative,
    playQueueIndex,
    addToQueue,
    removeFromQueue,
    clearQueue,
    moveQueueItem,
    shuffleQueue,
    togglePlayback,
    seek,
    setSpeed,
    cycleRepeatMode,
    setVolume,
    toggleMute,
    confirmNext,
    cancelNext,
    dismissPlayer,
  };
}

export type AudioPlayerHandle = ReturnType<typeof useAudioPlayer>;
