from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DOWNLOAD_QUERY_PARAM = "download"
DOWNLOAD_QUERY_VALUE = "1"


def as_download_url(url: str) -> str:
    """Return a URL that asks the stream route for attachment semantics."""
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != DOWNLOAD_QUERY_PARAM
    ]
    query.append((DOWNLOAD_QUERY_PARAM, DOWNLOAD_QUERY_VALUE))
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(query),
        parts.fragment,
    ))


def is_download_query(query) -> bool:
    return str(query.get(DOWNLOAD_QUERY_PARAM, "")).lower() in {"1", "true", "yes"}
