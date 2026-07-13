import { afterEach, describe, expect, it, vi } from 'vitest';
import { aiSuggestItem, ApiError, fetchAiModels, fetchTmdbPreview, resolveTmdbImdb } from './api';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api helpers', () => {
  it('uses JSON admin API routes for edit modal helpers', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([{ id: 'gemini-2.5-flash-lite', name: 'Gemini Flash Lite' }]))
      .mockResolvedValueOnce(jsonResponse({ title: 'Suggested title' }))
      .mockResolvedValueOnce(jsonResponse({ tmdb_id: 123, kind: 'tv' }))
      .mockResolvedValueOnce(jsonResponse({ tmdb_id: 123, kind: 'tv', imdb_id: 'tt1234567' }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchAiModels();
    await aiSuggestItem(42, 'gemini-2.5-flash-lite', 'title');
    await fetchTmdbPreview(123, 'tv');
    await resolveTmdbImdb('https://www.imdb.com/title/tt1234567/');

    expect(fetchMock.mock.calls[0][0]).toBe('/api/app/admin/ai-models');
    expect(fetchMock.mock.calls[1][0]).toBe('/api/app/admin/item/42/ai-suggest?model=gemini-2.5-flash-lite&fields=title');
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST', credentials: 'same-origin' });
    expect(fetchMock.mock.calls[2][0]).toBe('/api/app/admin/tmdb-preview?id=123&kind=tv');
    expect(fetchMock.mock.calls[3][0]).toBe('/api/app/admin/tmdb-resolve-imdb?imdb_id=https%3A%2F%2Fwww.imdb.com%2Ftitle%2Ftt1234567%2F');
  });

  it('sanitizes HTML error pages instead of exposing page markup', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response('<html><body>Koyeb gateway error</body></html>', {
        status: 502,
        statusText: 'Bad Gateway',
        headers: { 'content-type': 'text/html' },
      }),
    ));

    let caught: unknown;
    try {
      await aiSuggestItem(42, 'gemini-2.5-flash-lite');
    } catch (err) {
      caught = err;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({
      status: 502,
      message: 'Server returned an HTML error page (502). Try again shortly.',
    });
    expect((caught as Error).message).not.toContain('<html');
  });

  it('rejects redirected HTML pages even when the HTTP status is ok', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response('<!doctype html><html><body>Admin sign in</body></html>', {
        status: 200,
        headers: { 'content-type': 'text/html' },
      }),
    ));

    await expect(fetchAiModels()).rejects.toMatchObject({
      status: 200,
      message: 'Server returned an HTML page instead of JSON. Sign in again and retry.',
    });
  });
});
