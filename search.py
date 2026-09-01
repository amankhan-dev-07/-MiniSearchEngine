import sqlite3
import os
import re
from urllib.parse import urlparse
from difflib import SequenceMatcher


# ============================================================
# DATABASE
# ============================================================

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )
)

DB_PATH = os.path.join(
    PROJECT_ROOT,
    "search_engine.db"
)


# ============================================================
# SETTINGS
# ============================================================

MAX_RESULTS = 20
MIN_RESULT_SCORE = 20
SNIPPET_LENGTH = 240
SUGGESTION_THRESHOLD = 0.55


# ============================================================
# TEXT PROCESSING
# ============================================================

def normalize_text(text):

    if not text:
        return ""

    text = str(text).lower()

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def tokenize(text):

    text = normalize_text(text)

    return re.findall(
        r"\b[a-z0-9]+\b",
        text
    )


def get_query_words(query):

    words = tokenize(query)

    result = []

    for word in words:

        if len(word) < 2:
            continue

        if word not in result:
            result.append(word)

    return result


# ============================================================
# DATABASE
# ============================================================

def get_all_pages():

    connection = None

    try:

        connection = sqlite3.connect(
            DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                url,
                title,
                content
            FROM pages
            """
        )

        return cursor.fetchall()

    finally:

        if connection:
            connection.close()


# ============================================================
# URL HELPERS
# ============================================================

def get_url_text(url):

    if not url:
        return ""

    try:

        parsed = urlparse(url)

        return normalize_text(
            f"{parsed.netloc} {parsed.path}"
        )

    except Exception:

        return normalize_text(url)


def normalize_url(url):

    if not url:
        return ""

    url = str(
        url
    ).strip()

    if url.endswith("/"):
        url = url[:-1]

    return url.lower()


def is_local_page(url):

    try:

        hostname = (
            urlparse(url)
            .netloc
            .lower()
        )

        return hostname in {
            "127.0.0.1:9000",
            "localhost:9000",
            "127.0.0.1",
            "localhost",
        }

    except Exception:

        return False


# ============================================================
# NAVIGATION CLEANING
# ============================================================

COMMON_NAV_PHRASES = {
    "home",
    "about",
    "contact",
    "explore",
    "explore more",
    "more",
    "menu",
    "login",
    "sign in",
    "sign up",
    "privacy",
    "privacy policy",
    "terms",
    "terms of service",
    "next",
    "previous",
    "back",
}


def remove_common_navigation(text):

    if not text:
        return ""

    cleaned = normalize_text(
        text
    )

    phrases = sorted(
        COMMON_NAV_PHRASES,
        key=len,
        reverse=True
    )

    for phrase in phrases:

        cleaned = re.sub(
            rf"\b{re.escape(phrase)}\b",
            " ",
            cleaned,
            flags=re.IGNORECASE
        )

    cleaned = re.sub(
        r"\s+",
        " ",
        cleaned
    )

    return cleaned.strip()


# ============================================================
# SIMILARITY
# ============================================================

def similarity(a, b):

    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0.0

    return SequenceMatcher(
        None,
        a,
        b
    ).ratio()


# ============================================================
# SUGGESTION CANDIDATES
# ============================================================

def build_suggestion_candidates(pages):

    candidates = set()

    for url, title, content in pages:

        for word in tokenize(title):

            if len(word) >= 2:

                candidates.add(
                    word
                )

    return candidates


# ============================================================
# SMART SUGGESTION
# ============================================================

def get_suggestion(query):

    query = normalize_text(
        query
    )

    if not query:
        return None

    pages = get_all_pages()

    if not pages:
        return None


    titles = []

    for url, title, content in pages:

        title = str(
            title or ""
        ).strip()

        if title:

            titles.append(
                title
            )


    normalized_titles = [
        normalize_text(title)
        for title in titles
    ]


    # Already correct.
    if query in normalized_titles:

        return None


    query_words = get_query_words(
        query
    )


    # ========================================================
    # SINGLE WORD
    # ========================================================

    if len(query_words) == 1:

        query_word = query_words[0]

        best_word = None
        best_score = 0.0

        candidates = (
            build_suggestion_candidates(
                pages
            )
        )

        for candidate in candidates:

            score = similarity(
                query_word,
                candidate
            )

            if score >= 0.99:

                return None

            if score > best_score:

                best_score = score
                best_word = candidate


        if (
            best_word
            and best_score >= SUGGESTION_THRESHOLD
        ):

            return best_word


        return None


    # ========================================================
    # MULTI WORD
    # ========================================================

    best_title = None
    best_score = 0.0


    for title in titles:

        title_normalized = normalize_text(
            title
        )

        title_words = tokenize(
            title_normalized
        )

        if not title_words:
            continue


        full_score = similarity(
            query,
            title_normalized
        )


        word_scores = []


        for query_word in query_words:

            best_word_score = 0.0

            for title_word in title_words:

                current_score = similarity(
                    query_word,
                    title_word
                )

                if current_score > best_word_score:

                    best_word_score = (
                        current_score
                    )

            word_scores.append(
                best_word_score
            )


        if word_scores:

            average_score = (
                sum(word_scores)
                /
                len(word_scores)
            )

        else:

            average_score = 0.0


        combined_score = (
            full_score * 0.60
            +
            average_score * 0.40
        )


        if combined_score > best_score:

            best_score = combined_score
            best_title = title


    if (
        best_title
        and best_score >= SUGGESTION_THRESHOLD
    ):

        return best_title


    return None


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    query,
    title,
    content,
    url
):

    query = normalize_text(query)
    title = normalize_text(title)
    content = normalize_text(content)

    if not query:
        return 0

    query_words = get_query_words(query)

    if not query_words:
        return 0

    title_words = tokenize(title)

    real_content = remove_common_navigation(content)
    content_words = tokenize(real_content)
    url_words = tokenize(get_url_text(url))

    score = 0

    # ========================================================
    # 1. EXACT TITLE — strongest possible signal
    # ========================================================

    if title == query:
        score += 300

    # ========================================================
    # 2. EXACT PHRASE IN TITLE
    # ========================================================

    elif query in title:
        score += 180

    # ========================================================
    # 3. TITLE WORD MATCHING
    # ========================================================

    title_matches = 0

    for word in query_words:

        if word in title_words:

            title_matches += 1

            # Earlier/cleaner title matches matter more.
            score += 65

    # All query words present in title.
    if (
        len(query_words) > 1
        and title_matches == len(query_words)
    ):
        score += 100

    # ========================================================
    # 4. CONTENT PHRASE MATCH
    # ========================================================

    if query in real_content:
        score += 35

    # ========================================================
    # 5. CONTENT WORD MATCHING
    # ========================================================

    content_matches = 0

    for word in query_words:

        if word in content_words:

            content_matches += 1

            count = content_words.count(word)

            # Cap repeated-word influence so long pages
            # don't automatically outrank focused pages.
            score += min(
                count * 2,
                12
            )

    # All query words present in content.
    if (
        len(query_words) > 1
        and content_matches == len(query_words)
    ):
        score += 25

    # ========================================================
    # 6. URL MATCH — useful, but intentionally weak
    # ========================================================

    for word in query_words:

        if word in url_words:
            score += 10

    # ========================================================
    # 7. QUERY COVERAGE
    # ========================================================

    matched_words = (
        set(query_words)
        .intersection(
            set(title_words + content_words)
        )
    )

    if matched_words:

        coverage = (
            len(matched_words)
            /
            len(set(query_words))
        )

        score += int(
            coverage * 30
        )

    # ========================================================
    # 8. STRONG MULTI-WORD QUALITY RULE
    # ========================================================

    if len(query_words) > 1:

        # A page matching the complete phrase/title is ideal.
        if title == query:
            score += 100

        # Partial title matches are still useful.
        elif title_matches == len(query_words):
            score += 45

        # Only content matching should not dominate title matches.
        elif title_matches == 0 and content_matches > 0:
            score = int(score * 0.70)

        # Very weak partial matches are heavily reduced.
        elif (
            title_matches < len(query_words)
            and content_matches < len(query_words)
        ):
            score = int(score * 0.45)

    # ========================================================
    # 9. EXTERNAL PAGE PENALTY
    # ========================================================

    # Local indexed pages are our primary search corpus.
    # External pages can still appear when genuinely relevant,
    # but need stronger evidence.
    if not is_local_page(url):

        score = int(
            score * 0.45
        )

        # External pages without title relevance should
        # not beat focused local pages.
        if title_matches == 0:
            score = min(
                score,
                24
            )

    return max(
        score,
        0
    )



# ============================================================
# RELEVANCE FILTER
# ============================================================

def is_relevant(
    query,
    title,
    content,
    url,
    score
):

    if score < MIN_RESULT_SCORE:
        return False


    query_words = get_query_words(
        query
    )

    if not query_words:
        return False


    title_words = set(
        tokenize(title)
    )

    real_content = (
        remove_common_navigation(
            content
        )
    )

    content_words = set(
        tokenize(real_content)
    )


    # ========================================================
    # SINGLE WORD
    # ========================================================

    if len(query_words) == 1:

        word = query_words[0]


        if word in title_words:

            return True


        # External pages only count when
        # the title itself is relevant.
        if not is_local_page(url):

            return False


        if word in content_words:

            return score >= 30


        return False


    # ========================================================
    # MULTI WORD
    # ========================================================

    matched_title = (
        set(query_words)
        .intersection(
            title_words
        )
    )

    matched_content = (
        set(query_words)
        .intersection(
            content_words
        )
    )

    matched_words = (
        matched_title
        |
        matched_content
    )


    total_words = len(
        set(query_words)
    )


    coverage = (
        len(matched_words)
        /
        total_words
    )


    if coverage >= 0.75:

        if not is_local_page(url):

            return (
                len(matched_title)
                == total_words
                or query in normalize_text(title)
            )

        return True


    normalized_title = normalize_text(
        title
    )


    if query in normalized_title:

        return True


    normalized_content = normalize_text(
        content
    )


    if query in normalized_content:

        if is_local_page(url):

            return score >= 30

        return False


    return False


# ============================================================
# SNIPPET
# ============================================================

def create_snippet(
    content,
    query
):

    if not content:
        return ""


    text = str(
        content
    ).strip()


    if not text:
        return ""


    query = query.strip()

    lower_text = text.lower()
    lower_query = query.lower()


    position = lower_text.find(
        lower_query
    )


    # Find individual word
    if position == -1:

        best_position = -1


        for word in get_query_words(
            query
        ):

            current_position = (
                lower_text.find(word)
            )


            if current_position != -1:

                if (
                    best_position == -1
                    or current_position < best_position
                ):

                    best_position = (
                        current_position
                    )


        position = best_position


    # No match
    if position == -1:

        snippet = text[
            :SNIPPET_LENGTH
        ]


        if len(text) > SNIPPET_LENGTH:

            snippet += "..."


        return snippet


    start = max(
        0,
        position - 85
    )

    end = min(
        len(text),
        position + 155
    )


    snippet = text[
        start:end
    ].strip()


    if start > 0:

        first_space = snippet.find(
            " "
        )


        if first_space != -1:

            snippet = snippet[
                first_space + 1:
            ]


        snippet = (
            "..."
            +
            snippet
        )


    if end < len(text):

        snippet = (
            snippet.rstrip()
            +
            "..."
        )


    return snippet


# ============================================================
# NORMAL SEARCH
# ============================================================

def _search_exact(query, pages):

    results = []

    seen_urls = set()


    for url, title, content in pages:

        url = str(
            url or ""
        ).strip()

        title = str(
            title or ""
        ).strip()

        content = str(
            content or ""
        ).strip()


        normalized_url = normalize_url(
            url
        )


        if (
            not normalized_url
            or normalized_url in seen_urls
        ):

            continue


        score = calculate_score(
            query,
            title,
            content,
            url
        )


        if not is_relevant(
            query,
            title,
            content,
            url,
            score
        ):

            continue


        seen_urls.add(
            normalized_url
        )


        results.append({

            "title": title,

            "url": url,

            "score": score,

            "snippet": create_snippet(
                content,
                query
            )

        })


    # ============================================================
    # SMART FINAL RANKING
    # Score remains primary; title relevance breaks ties.
    # ============================================================
    normalized_query = normalize_text(query)
    query_words = set(get_query_words(query))

    def ranking_key(result):
        normalized_title = normalize_text(result["title"])
        title_words = set(tokenize(normalized_title))

        exact_title = 1 if normalized_title == normalized_query else 0
        starts_with_query = 1 if normalized_title.startswith(normalized_query) else 0
        phrase_match = 1 if normalized_query in normalized_title else 0
        title_coverage = len(query_words.intersection(title_words))
        title_length = len(tokenize(normalized_title))

        return (
            result["score"],
            exact_title,
            starts_with_query,
            phrase_match,
            title_coverage,
            -title_length,
            normalized_title
        )

    results.sort(key=ranking_key, reverse=True)


    return results


# ============================================================
# SEARCH ENGINE
# ============================================================

def search_pages(query):

    query = str(
        query or ""
    ).strip()


    if not query:

        return []


    pages = get_all_pages()


    if not pages:

        return []


    # ========================================================
    # FIRST: NORMAL SEARCH
    # ========================================================

    results = _search_exact(
        query,
        pages
    )


    # ========================================================
    # IF RESULTS EXIST, RETURN THEM
    # ========================================================

    if results:

        return results[:MAX_RESULTS]


    # ========================================================
    # TYPO FALLBACK
    # ========================================================

    suggestion = get_suggestion(
        query
    )


    if not suggestion:

        return []


    # ========================================================
    # SEARCH SUGGESTED QUERY
    # ========================================================

    suggested_results = _search_exact(
        suggestion,
        pages
    )


    # ========================================================
    # MARK RESULTS AS SUGGESTED
    # ========================================================

    for result in suggested_results:

        result["suggested"] = True

        result["original_query"] = query

        result["suggestion"] = suggestion


    return suggested_results[:MAX_RESULTS]


# ============================================================
# PAGINATED SEARCH
# ============================================================

def search_paginated(query, limit=5, offset=0):

    query = str(query or "").strip()

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 5

    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0

    # Keep API safe and predictable.
    limit = max(1, min(limit, MAX_RESULTS))
    offset = max(0, offset)

    if not query:
        return {
            "results": [],
            "total_results": 0,
            "limit": limit,
            "offset": offset,
            "has_more": False,
            "suggestion": None,
            "used_suggestion": False
        }

    pages = get_all_pages()

    if not pages:
        return {
            "results": [],
            "total_results": 0,
            "limit": limit,
            "offset": offset,
            "has_more": False,
            "suggestion": None,
            "used_suggestion": False
        }

    # Normal search first.
    all_results = _search_exact(
        query,
        pages
    )

    suggestion = None
    used_suggestion = False

    # Typo fallback only when normal search has no results.
    if not all_results:

        suggestion = get_suggestion(
            query
        )

        if suggestion:

            all_results = _search_exact(
                suggestion,
                pages
            )

            for result in all_results:
                result["suggested"] = True
                result["original_query"] = query
                result["suggestion"] = suggestion

            used_suggestion = bool(all_results)

    total_results = len(all_results)

    page_results = all_results[
        offset: offset + limit
    ]

    return {
        "results": page_results,
        "total_results": total_results,
        "limit": limit,
        "offset": offset,
        "has_more": (
            offset + limit < total_results
        ),
        "suggestion": suggestion,
        "used_suggestion": used_suggestion
    }


# ============================================================
# SEARCH DETAILS
# ============================================================

def search_with_suggestion(query):

    query = str(
        query or ""
    ).strip()


    if not query:

        return {
            "results": [],
            "suggestion": None,
            "used_suggestion": False
        }


    pages = get_all_pages()


    if not pages:

        return {
            "results": [],
            "suggestion": None,
            "used_suggestion": False
        }


    # Normal search
    results = _search_exact(
        query,
        pages
    )


    if results:

        return {
            "results": results,
            "suggestion": None,
            "used_suggestion": False
        }


    # Get typo suggestion
    suggestion = get_suggestion(
        query
    )


    if not suggestion:

        return {
            "results": [],
            "suggestion": None,
            "used_suggestion": False
        }


    # Search suggestion
    suggested_results = _search_exact(
        suggestion,
        pages
    )


    for result in suggested_results:

        result["suggested"] = True

        result["original_query"] = query

        result["suggestion"] = suggestion


    return {
        "results":
            suggested_results,

        "suggestion":
            suggestion,

        "used_suggestion":
            bool(suggested_results)
    }


# ============================================================
# TERMINAL SEARCH
# ============================================================

if __name__ == "__main__":

    query = input(
        "Search: "
    ).strip()


    search_data = search_with_suggestion(
        query
    )


    results = search_data[
        "results"
    ]

    suggestion = search_data[
        "suggestion"
    ]

    used_suggestion = search_data[
        "used_suggestion"
    ]


    print(
        f"\nResults found: "
        f"{len(results)}\n"
    )


    # ========================================================
    # SUGGESTION MESSAGE
    # ========================================================

    if suggestion:

        print(
            f"Did you mean: "
            f"{suggestion}?"
        )

        print()


    # ========================================================
    # NO RESULTS
    # ========================================================

    if not results:

        print(
            "No relevant results found."
        )


    # ========================================================
    # RESULTS
    # ========================================================

    for index, result in enumerate(
        results,
        start=1
    ):

        if used_suggestion:

            print(
                f"{index}. "
                f"{result['title']} "
                f"[Suggested]"
            )

        else:

            print(
                f"{index}. "
                f"{result['title']}"
            )


        print(
            f"   Score: "
            f"{result['score']}"
        )


        print(
            f"   URL: "
            f"{result['url']}"
        )


        print(
            f"   {result['snippet']}"
        )


        print()