"""
AMEJA Wikipedia fallback.

Uses Wikimedia's public REST API. The endpoint family is documented by
MediaWiki as /api/rest_v1/ and the page summary endpoint returns a short
plain-text extract suitable for a lightweight answer fallback.
"""

import re
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup


WIKIPEDIA_BASE = "https://en.wikipedia.org/api/rest_v1"
WIKIPEDIA_SEARCH = "https://en.wikipedia.org/w/rest.php/v1/search/page"

WIKIPEDIA_TIMEOUT = 7

WIKIPEDIA_HEADERS = {
    "User-Agent": (
        "AMEJA/1.0 "
        "https://example.invalid/ameja"
    ),
    "Accept": "application/json",
}


def _clean(text):
    return re.sub(
        r"\s+",
        " ",
        str(text or "")
    ).strip()



def _question_topic(query):
    """
    Convert a natural-language fact question into its likely entity/topic.
    Examples:
        "Who was Albert Einstein?" -> "Albert Einstein"
        "What is Python?" -> "Python"
    """
    value = _clean(query)

    value = re.sub(
        r"[?!.]+$",
        "",
        value
    ).strip()

    patterns = (
        r"^who\s+(?:was|is|were|are)\s+(.+)$",
        r"^what\s+(?:is|are|was|were)\s+(.+)$",
        r"^where\s+(?:is|was|are|were)\s+(.+)$",
        r"^when\s+(?:was|is|were|are)\s+(.+)$",
        r"^define\s+(.+)$",
        r"^tell\s+me\s+about\s+(.+)$",
    )

    for pattern in patterns:

        match = re.match(
            pattern,
            value,
            flags=re.IGNORECASE
        )

        if match:
            return _clean(
                match.group(1)
            )

    return value


def _entity_key(value):
    value = _clean(value).lower()

    value = re.sub(
        r"[^a-z0-9\s-]",
        " ",
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    ).strip()

    return value


def _exact_entity_result(
    query,
    results
):
    """
    Prefer an exact Wikipedia title matching the extracted entity.
    """

    topic = _question_topic(
        query
    )

    topic_key = _entity_key(
        topic
    )

    if not topic_key:
        return None

    for result in results:

        title_key = _entity_key(
            result.get(
                "title",
                ""
            )
        )

        if title_key == topic_key:
            return result

    return None



def wikipedia_search(query, limit=3):
    """
    Search English Wikipedia for pages relevant to the query.
    """

    query = _clean(query)

    if not query:
        return []

    try:

        response = requests.get(
            WIKIPEDIA_SEARCH,
            params={
                "q": query,
                "limit": max(
                    1,
                    min(
                        int(limit),
                        5
                    )
                )
            },
            headers=WIKIPEDIA_HEADERS,
            timeout=WIKIPEDIA_TIMEOUT
        )

        if response.status_code != 200:
            return []

        data = response.json()

    except (
        requests.RequestException,
        ValueError,
        TypeError
    ):
        return []

    pages = data.get(
        "pages",
        []
    )

    results = []

    for page in pages:

        key = page.get(
            "key"
        )

        title = page.get(
            "title"
        )

        if not key or not title:
            continue

        results.append({
            "title": _clean(
                title
            ),
            "key": key,
            "description": _clean(
                page.get(
                    "description",
                    ""
                )
            ),
            "excerpt": _clean(
                page.get(
                    "excerpt",
                    ""
                )
            ),
        })

    return results


def wikipedia_summary(title):
    """
    Retrieve a compact first-paragraph summary.
    """

    title = _clean(title)

    if not title:
        return None

    url = (
        WIKIPEDIA_BASE
        + "/page/summary/"
        + quote(
            title,
            safe=""
        )
    )

    try:

        response = requests.get(
            url,
            headers=WIKIPEDIA_HEADERS,
            timeout=WIKIPEDIA_TIMEOUT
        )

        if response.status_code != 200:
            return None

        data = response.json()

    except (
        requests.RequestException,
        ValueError,
        TypeError
    ):
        return None

    extract = _clean(
        data.get(
            "extract",
            ""
        )
    )

    page_title = _clean(
        data.get(
            "title",
            title
        )
    )

    page_url = (
        data.get(
            "content_urls",
            {}
        )
        .get(
            "desktop",
            {}
        )
        .get(
            "page",
            ""
        )
    )

    if not extract:
        return None

    return {
        "title": page_title,
        "extract": extract,
        "url": page_url
            or (
                "https://en.wikipedia.org/wiki/"
                + quote(
                    page_title.replace(
                        " ",
                        "_"
                    ),
                    safe=""
                )
            ),
    }


def _normalize_title_key(value):
    value = _clean(value).lower()
    value = re.sub(
        r"[^a-z0-9\s-]",
        " ",
        value
    )
    value = re.sub(
        r"\s+",
        " ",
        value
    ).strip()

    return value


def _title_match_score(
    query,
    title,
    description="",
    excerpt=""
):
    """
    Score a Wikipedia candidate using title/entity relevance.
    Exact title matches dominate related-person/name results.
    """

    q = _normalize_title_key(
        query
    )

    t = _normalize_title_key(
        title
    )

    if not q or not t:
        return 0

    if t == q:
        return 1000

    q_words = set(
        q.split()
    )

    t_words = set(
        t.split()
    )

    overlap = len(
        q_words & t_words
    )

    score = overlap * 100

    if t.startswith(q):
        score += 180

    elif q.startswith(t):
        score += 120

    elif q in t:
        score += 90

    # Penalize titles that add a person's relationship/name qualifier
    # when the exact entity title is missing.
    extra_words = t_words - q_words

    relationship_words = {
        "son", "daughter", "father", "mother",
        "wife", "husband", "brother", "sister",
        "jr", "junior", "sr", "senior",
        "ii", "iii", "iv"
    }

    if extra_words & relationship_words:
        score -= 220

    desc = _normalize_title_key(
        description
    )

    excerpt_key = _normalize_title_key(
        excerpt
    )

    if q in desc:
        score += 25

    if q in excerpt_key:
        score += 20

    # Exact phrase words appearing consecutively are useful.
    if q and q in t:
        score += 60

    return score



def wikipedia_section_answer(title, section_keywords):
    """
    Extract text from a matching Wikipedia article section using the
    MediaWiki parse API. Returns a compact paragraph or None.
    """

    title = _clean(title)

    if not title:
        return None

    try:
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "parse",
                "page": title,
                "prop": "text",
                "format": "json",
                "formatversion": "2",
            },
            headers=WIKIPEDIA_HEADERS,
            timeout=WIKIPEDIA_TIMEOUT,
        )

        if response.status_code != 200:
            return None

        data = response.json()

    except (
        requests.RequestException,
        ValueError,
        TypeError,
    ):
        return None

    html = (
        data.get(
            "parse",
            {}
        ).get(
            "text",
            ""
        )
    )

    if not html:
        return None

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    normalized_keywords = [
        _normalize_title_key(
            keyword
        )
        for keyword in section_keywords
    ]

    heading = None

    for tag in soup.find_all(
        re.compile(r"^h[1-6]$")
    ):

        heading_text = _normalize_title_key(
            tag.get_text(
                " ",
                strip=True
            )
        )

        if any(
            keyword
            and keyword in heading_text
            for keyword in normalized_keywords
        ):
            heading = tag
            break

    if heading is None:
        return None

    parts = []
    node = heading.find_next_sibling()

    while node:

        if (
            getattr(node, "name", None)
            and re.match(
                r"^h[1-6]$",
                node.name
            )
        ):
            break

        if getattr(
            node,
            "name",
            None
        ) in {"p", "ul", "ol"}:

            value = _clean(
                node.get_text(
                    " ",
                    strip=True
                )
            )

            if value:
                parts.append(
                    value
                )

        if len(parts) >= 4:
            break

        node = node.find_next_sibling()

    if not parts:
        return None

    sentences = re.split(
        r"(?<=[.!?])\s+",
        _clean(
            " ".join(parts)
        )
    )

    selected = []

    for sentence in sentences:

        sentence = _clean(
            sentence
        )

        if len(sentence) < 35:
            continue

        selected.append(
            sentence
        )

        if len(selected) >= 2:
            break

    if not selected:
        return None

    return _clean(
        " ".join(selected)
    )



def wikipedia_section_answer(
    title,
    section_keywords
):
    """
    Find a matching Wikipedia section via MediaWiki's section index,
    then parse that exact section. This is more reliable than relying
    on rendered HTML sibling relationships.
    """

    title = _clean(title)

    if not title:
        return None

    api = "https://en.wikipedia.org/w/api.php"

    try:

        response = requests.get(
            api,
            params={
                "action": "parse",
                "page": title,
                "prop": "sections",
                "format": "json",
                "formatversion": "2",
            },
            headers=WIKIPEDIA_HEADERS,
            timeout=WIKIPEDIA_TIMEOUT
        )

        if response.status_code != 200:
            return None

        sections = response.json().get(
            "parse",
            {}
        ).get(
            "sections",
            []
        )

    except (
        requests.RequestException,
        ValueError,
        TypeError
    ):
        return None

    normalized_keywords = [
        _normalize_title_key(
            keyword
        )
        for keyword in section_keywords
    ]

    matching_section = None

    for section in sections:

        line = _normalize_title_key(
            section.get(
                "line",
                ""
            )
        )

        if any(
            keyword
            and (
                keyword in line
                or line in keyword
            )
            for keyword in normalized_keywords
        ):
            matching_section = section
            break

    if matching_section is None:
        return None

    section_index = matching_section.get(
        "index"
    )

    if section_index is None:
        return None

    try:

        response = requests.get(
            api,
            params={
                "action": "parse",
                "page": title,
                "prop": "text",
                "section": section_index,
                "format": "json",
                "formatversion": "2",
            },
            headers=WIKIPEDIA_HEADERS,
            timeout=WIKIPEDIA_TIMEOUT
        )

        if response.status_code != 200:
            return None

        html = response.json().get(
            "parse",
            {}
        ).get(
            "text",
            ""
        )

    except (
        requests.RequestException,
        ValueError,
        TypeError
    ):
        return None

    if not html:
        return None

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    for tag in soup.find_all(
        [
            "script",
            "style",
            "sup",
            "table",
            "figure"
        ]
    ):
        tag.decompose()

    blocks = []

    for tag in soup.find_all(
        [
            "p",
            "li"
        ]
    ):

        value = _clean(
            tag.get_text(
                " ",
                strip=True
            )
        )

        if value:
            blocks.append(
                value
            )

    if not blocks:
        return None

    sentences = re.split(
        r"(?<=[.!?])\s+",
        _clean(
            " ".join(blocks)
        )
    )

    selected = []

    for sentence in sentences:

        sentence = _clean(
            sentence
        )

        if len(sentence) < 35:
            continue

        # Skip citation/reference debris.
        if sentence.startswith(
            "["
        ):
            continue

        selected.append(
            sentence
        )

        if len(selected) >= 2:
            break

    if not selected:
        return None

    return _clean(
        " ".join(selected)
    )



def wikipedia_fallback(
    query,
    intent=None
):
    """
    Wikipedia fallback with exact entity matching.

    Benefits/advantages requests ONLY use a matching section.
    They never silently fall back to the generic article lead.
    """

    results = wikipedia_search(
        query,
        limit=5
    )

    if not results:
        return None

    exact = _exact_entity_result(
        query,
        results
    )

    ordered = (
        [exact]
        if exact
        else []
    )

    for result in results:

        if (
            exact
            and result.get("title")
            == exact.get("title")
        ):
            continue

        ordered.append(
            result
        )

    if intent in {
        "benefits",
        "advantages"
    }:

        for result in ordered:

            section = wikipedia_section_answer(
                result.get(
                    "title",
                    ""
                ),
                [
                    "value proposition",
                    "advantages",
                    "benefits",
                    "applications",
                    "uses"
                ]
            )

            if section:

                summary = wikipedia_summary(
                    result.get(
                        "title",
                        ""
                    )
                )

                if summary:

                    summary["extract"] = section
                    summary["_intent_section"] = True

                    return summary

        return None

    for result in ordered:

        summary = wikipedia_summary(
            result.get(
                "title",
                ""
            )
        )

        if summary:
            return summary

    return None



