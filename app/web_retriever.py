"""
AMEJA lightweight live-web retriever.

No paid API and no API key are required.

Flow:
    query -> DuckDuckGo HTML result page -> top result pages
          -> robots.txt check -> HTML extraction -> compact snippet

This is intentionally conservative: fetch only a small number of result
pages and return source URLs. It is a fallback, not the primary index.
"""

import re
from urllib.parse import urljoin, urlparse, unquote
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup


SEARCH_URL = "https://html.duckduckgo.com/html/"
TIMEOUT = 7
MAX_RESULTS = 4
MAX_FETCH_PAGES = 2
USER_AGENT = "AmejaBot/1.0"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml",
}


def _clean(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or "")
    ).strip()


def _normalize_url(url):
    if not url:
        return None

    parsed = urlparse(
        url
    )

    if parsed.scheme not in {
        "http",
        "https"
    }:
        return None

    if not parsed.netloc:
        return None

    return url.split(
        "#",
        1
    )[0]


def _robots_allowed(url):
    parsed = urlparse(
        url
    )

    if not parsed.netloc:
        return False

    robots_url = (
        f"{parsed.scheme}://"
        f"{parsed.netloc}/robots.txt"
    )

    parser = RobotFileParser()
    parser.set_url(
        robots_url
    )

    try:

        parser.read()

        return parser.can_fetch(
            USER_AGENT,
            url
        )

    except Exception:
        # Fail closed for on-demand external pages.
        return False


def _search_web(
    query,
    session
):
    response = session.get(
        SEARCH_URL,
        params={
            "q": query
        },
        headers=HEADERS,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    results = []

    for result in soup.select(
        ".result"
    )[:MAX_RESULTS]:

        link = result.select_one(
            "a.result__a"
        )

        if link is None:
            continue

        href = link.get(
            "href"
        )

        title = _clean(
            link.get_text(
                " ",
                strip=True
            )
        )

        if not href or not title:
            continue

        results.append({
            "title": title,
            "url": _normalize_url(
                href
            ),
            "snippet": _clean(
                (
                    result.select_one(
                        ".result__snippet"
                    )
                    or result
                ).get_text(
                    " ",
                    strip=True
                )
            )
        })

    return [
        item
        for item in results
        if item.get("url")
    ]


def _extract_page_text(
    url,
    session
):
    if not _robots_allowed(
        url
    ):
        return None

    response = session.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
        allow_redirects=True
    )

    response.raise_for_status()

    content_type = (
        response.headers
        .get(
            "Content-Type",
            ""
        )
        .lower()
    )

    if "text/html" not in content_type:
        return None

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    title = ""

    if (
        soup.title
        and soup.title.string
    ):
        title = _clean(
            soup.title.string
        )

    for tag in soup.find_all(
        [
            "script",
            "style",
            "noscript",
            "iframe",
            "nav",
            "footer",
            "header",
            "aside",
            "form",
        ]
    ):
        tag.decompose()

    main = (
        soup.find("main")
        or soup.find("article")
        or soup.body
        or soup
    )

    text = _clean(
        main.get_text(
            " ",
            strip=True
        )
    )

    if len(text) < 50:
        return None

    return {
        "title": title,
        "url": _normalize_url(
            response.url
        ) or url,
        "content": text
    }


def _pick_answer_sentence(
    query,
    content
):
    words = {
        word
        for word in re.findall(
            r"\b[a-z0-9]+\b",
            str(query).lower()
        )
        if len(word) >= 3
    }

    sentences = re.split(
        r"(?<=[.!?])\s+",
        _clean(content)
    )

    ranked = []

    for index, sentence in enumerate(
        sentences
    ):

        sentence = _clean(
            sentence
        )

        if len(sentence) < 35:
            continue

        sentence_words = set(
            re.findall(
                r"\b[a-z0-9]+\b",
                sentence.lower()
            )
        )

        overlap = len(
            words & sentence_words
        )

        if overlap <= 0:
            continue

        score = (
            overlap * 30
            + max(
                0,
                10 - index
            )
        )

        ranked.append(
            (
                score,
                sentence
            )
        )

    ranked.sort(
        reverse=True
    )

    return (
        ranked[0][1]
        if ranked
        else None
    )


def web_fallback(query):
    """
    Lightweight web fallback.

    Benefit/advantage queries are sent with intent-preserving search
    terms so result snippets/pages are more likely to answer the actual
    request instead of returning only a definition.
    """

    query = _clean(
        query
    )

    if not query:
        return None

    search_query = query

    # When router passes the stripped topic (e.g. "cloud computing"),
    # reconstruct a useful benefit-oriented retrieval query.
    lowered = query.lower()

    if (
        "benefit" not in lowered
        and "advantage" not in lowered
    ):
        # For the current lightweight router, the caller passes only the
        # topic. Try a second search focused on benefits as a fallback.
        benefit_query = (
            query
            + " benefits advantages"
        )
    else:
        benefit_query = query

    session = requests.Session()
    session.headers.update(
        HEADERS
    )

    queries = [
        benefit_query,
        search_query
    ]

    for current_query in queries:

        try:
            search_results = _search_web(
                current_query,
                session
            )
        except Exception:
            continue

        for result in search_results[
            :MAX_FETCH_PAGES
        ]:

            try:

                page = _extract_page_text(
                    result["url"],
                    session
                )

            except Exception:

                page = None

            if page:

                sentence = _pick_answer_sentence(
                    current_query,
                    page["content"]
                )

                if not sentence:
                    sentence = result.get(
                        "snippet",
                        ""
                    )

                if sentence:

                    return {
                        "answer": _clean(
                            sentence
                        ),
                        "title": (
                            page.get(
                                "title"
                            )
                            or result["title"]
                        ),
                        "url": (
                            page.get(
                                "url"
                            )
                            or result["url"]
                        ),
                        "source": "web"
                    }

            snippet = _clean(
                result.get(
                    "snippet",
                    ""
                )
            )

            if snippet:
                return {
                    "answer": snippet,
                    "title": result["title"],
                    "url": result["url"],
                    "source": "web_snippet"
                }

    return None
