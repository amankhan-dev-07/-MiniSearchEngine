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

# Smart Search aliases for the topics already present in AMEJA.
# These are intentionally conservative and do not invent new topics.
SMART_QUERY_ALIASES = {
    "python": (
        "python programming",
    ),
    "python programming": (
        "python",
    ),
    "database": (
        "database systems",
    ),
    "database systems": (
        "database",
    ),
    "fastapi": (
        "fastapi web development",
    ),
    "fastapi web development": (
        "fastapi",
    ),
    "web development": (
        "fastapi web development",
    ),
}

SEARCH_QUESTION_PHRASES = (
    "tell me about",
    "what is",
    "what are",
    "what was",
    "what were",
    "why is",
    "why are",
    "how does",
    "how do",
    "how did",
    "how can",
    "how to",
    "explain",
    "define",
    "definition of",
    "meaning of",
    "please explain",
)

SEARCH_FILLER_WORDS = {
    "kya", "hai", "hain", "h", "ka", "ke", "ki", "ko",
    "mein", "me", "mujhe", "mujhko", "mere", "mera", "meri",
    "batao", "bataiye", "bata", "samjhao", "samjhaao",
    "ke", "baare", "bare", "about", "it", "the", "a", "an",
}


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


def normalize_search_topic(query):
    """
    Reduce common English/Hinglish question wording to its core topic.
    This is used only to create an additional search variant; the
    original query is always searched first.
    """

    text = normalize_text(query)

    if not text:
        return ""

    for phrase in SEARCH_QUESTION_PHRASES:
        text = re.sub(
            rf"\b{re.escape(phrase)}\b",
            " ",
            text,
            flags=re.IGNORECASE
        )

    # Common Hinglish process/definition endings.
    patterns = (
        r"\bkaise\s+kaam\s+karta\s+hai\b",
        r"\bkaise\s+kaam\s+karte\s+hain\b",
        r"\bkaise\s+kaam\s+karti\s+hai\b",
        r"\bkya\s+(?:hota|hoti|hote)\s+hai(?:n)?\b",
        r"\bkya\s+hai\b",
        r"\bke\s+(?:baare|bare)\s+mein(?:\s+(?:batao|bataiye|samjhao))?\b",
        r"\b(?:baare|bare)\s+mein(?:\s+(?:batao|bataiye|samjhao))?\b",
    )

    for pattern in patterns:
        text = re.sub(
            pattern,
            " ",
            text,
            flags=re.IGNORECASE
        )

    words = tokenize(text)
    words = [
        word
        for word in words
        if word not in SEARCH_FILLER_WORDS
        and len(word) >= 2
    ]

    return " ".join(words).strip()


def build_smart_query_variants(query):
    """
    Build a small set of safe alternate queries.
    Original query is first, then normalized topic, then aliases.
    """

    original = normalize_text(query)

    if not original:
        return []

    variants = [original]

    topic = normalize_search_topic(original)

    if topic and topic not in variants:
        variants.append(topic)

    for variant in SMART_QUERY_ALIASES.get(
        topic,
        ()
    ):
        variant = normalize_text(variant)
        if variant and variant not in variants:
            variants.append(variant)

    # Also allow an alias for the original query when it is already
    # one of the known canonical topics.
    for variant in SMART_QUERY_ALIASES.get(
        original,
        ()
    ):
        variant = normalize_text(variant)
        if variant and variant not in variants:
            variants.append(variant)

    return variants[:4]


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

QUESTION_WORDS = {
    "what", "is", "are", "was", "were",
    "who", "whom", "whose", "which",
    "where", "when", "why", "how",
    "does", "do", "did", "can", "could",
    "would", "should",
    "explain", "define", "definition",
    "meaning", "tell", "about",
    "kya", "hai", "h", "hain",
    "ka", "ke", "ki", "ko",
    "mein", "me", "batao", "bataiye",
    "samjhao", "kaise", "kyun", "kyon",
    "kyu", "mujhe"
}


def is_question_query(query):
    """
    Return True for natural-language questions/requests.
    Such queries should not trigger typo suggestions.
    """

    original = normalize_text(
        query
    )

    if not original:
        return False

    words = tokenize(
        original
    )

    if "?" in str(query):
        return True

    if any(
        word in QUESTION_WORDS
        for word in words
    ):
        return True

    # Common Hinglish multi-word question patterns.
    question_patterns = (
        "kya hai",
        "kya h",
        "kaise kaam",
        "ke baare mein",
        "ke bare mein",
        "batao",
        "samjhao"
    )

    return any(
        pattern in original
        for pattern in question_patterns
    )


def clean_question_topic(query):
    """
    Remove common question/filler words while preserving the
    actual topic words. Used only for typo checking.
    """

    original = normalize_text(
        query
    )

    if not original:
        return ""

    # Remove common multi-word question phrases first.
    phrases = (
        "ke baare mein batao",
        "ke bare mein batao",
        "ke baare mein",
        "ke bare mein",
        "what is",
        "what are",
        "what was",
        "what were",
        "tell me about",
        "please explain",
        "definition of",
        "meaning of",
        "how does",
        "how do",
        "how can",
        "why is",
        "why are",
        "explain"
    )

    cleaned = original

    for phrase in phrases:
        cleaned = re.sub(
            rf"\b{re.escape(phrase)}\b",
            " ",
            cleaned,
            flags=re.IGNORECASE
        )

    words = tokenize(
        cleaned
    )

    core = [
        word
        for word in words
        if word not in QUESTION_WORDS
        and len(word) >= 2
    ]

    return " ".join(core).strip()



def get_suggestion(query):
    """
    Smart typo suggestion.

    Valid natural-language questions such as
    "python kya hai" are NOT treated as typos.
    Actual misspellings such as "pyhton" still get corrected.
    """

    original_query = str(
        query or ""
    ).strip()

    query = normalize_text(
        original_query
    )

    if not query:
        return None

    pages = get_all_pages()

    if not pages:
        return None

    # --------------------------------------------------------
    # Never offer a typo suggestion for a natural-language
    # question/request. The Answer Engine can normalize it.
    # --------------------------------------------------------

    if is_question_query(
        original_query
    ):
        return None

    # If query exactly matches a title, no suggestion.
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

    if query in normalized_titles:
        return None

    query_words = get_query_words(
        query
    )

    if not query_words:
        return None

    # --------------------------------------------------------
    # Single-word typo detection.
    # --------------------------------------------------------

    if len(query_words) == 1:

        query_word = query_words[0]

        candidates = (
            build_suggestion_candidates(
                pages
            )
        )

        best_word = None
        best_score = 0.0

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

    # --------------------------------------------------------
    # Multi-word non-question typo detection.
    # --------------------------------------------------------

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
                    best_word_score = current_score

            word_scores.append(
                best_word_score
            )

        average_score = (
            sum(word_scores)
            / len(word_scores)
            if word_scores
            else 0.0
        )

        combined_score = (
            full_score * 0.60
            + average_score * 0.40
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

    title_set = set(title_words)
    content_set = set(content_words)

    score = 0

    # Exact title: strongest signal
    if title == query:
        score += 1000

    # Exact phrase in title
    elif query in title:
        score += 700

    # Title word matching
    title_matches = sum(
        1 for word in query_words
        if word in title_set
    )
    score += title_matches * 120

    # All query words in title
    if (
        len(query_words) > 1
        and title_matches == len(query_words)
    ):
        score += 250

    # Exact phrase in content
    if query in real_content:
        score += 70

    # Content word matching
    content_matches = sum(
        1 for word in query_words
        if word in content_set
    )

    for word in query_words:
        if word in content_set:
            count = content_words.count(word)
            score += min(count * 3, 18)

    if (
        len(query_words) > 1
        and content_matches == len(query_words)
    ):
        score += 35

    # URL match: weak supporting signal
    for word in query_words:
        if word in url_words:
            score += 8

    # Query coverage
    matched_words = (
        title_set | content_set
    ).intersection(query_words)

    if matched_words:
        coverage = (
            len(matched_words)
            / len(set(query_words))
        )
        score += int(coverage * 30)

    # Multi-word quality
    if len(query_words) > 1:

        if title == query:
            score += 150

        elif title_matches == len(query_words):
            score += 80

        elif title_matches == 0 and content_matches > 0:
            score = int(score * 0.55)

        elif (
            title_matches < len(query_words)
            and content_matches < len(query_words)
        ):
            score = int(score * 0.35)

    # External pages need strong title evidence
    if not is_local_page(url):

        if title_matches == 0:
            return 0

        if title != query and query not in title:
            score = int(score * 0.35)
        else:
            score = int(score * 0.55)

    return max(score, 0)
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

    query_words = get_query_words(query)
    if not query_words:
        return False

    title_words = set(tokenize(title))

    real_content = remove_common_navigation(content)
    content_words = set(tokenize(real_content))

    # Single-word query
    if len(query_words) == 1:

        word = query_words[0]

        if word in title_words:
            return True

        # External body-only matches are noise.
        if not is_local_page(url):
            return False

        if word in content_words:
            return score >= 25

        return False

    # Multi-word query
    query_set = set(query_words)

    matched_title = query_set.intersection(title_words)
    matched_content = query_set.intersection(content_words)
    matched_words = matched_title | matched_content

    total_words = len(query_set)

    coverage = (
        len(matched_words) / total_words
        if total_words
        else 0
    )

    normalized_title = normalize_text(title)

    # Exact phrase in title
    if query in normalized_title:
        return True

    # Every query word is in title
    if len(matched_title) == total_words:
        return True

    # External pages require complete title relevance
    if not is_local_page(url):
        return False

    # Strong local topical match
    if coverage >= 0.75:
        return True

    # Exact phrase in local content
    normalized_content = normalize_text(content)

    if query in normalized_content:
        return score >= 30

    # Mixed title/content match
    if matched_title and matched_content:
        return score >= 45

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


    results.sort(
        key=lambda result: (
            result["score"],
            result["title"].lower()
        ),
        reverse=True
    )


    return results[
        :MAX_RESULTS
    ]


# ============================================================
# SEARCH ENGINE
# ============================================================

def rank_suggested_results(
    results,
    suggestion
):
    """
    Re-rank typo-corrected results.

    Prefer:
      1. exact title
      2. canonical topic title
      3. title starting with the corrected topic
      4. title containing the corrected topic
      5. retrieval score
    """

    normalized_suggestion = normalize_text(
        suggestion
    )

    canonical_titles = {
        "python": "python programming",
        "machine learning": "machine learning",
        "database": "database systems",
        "web development": "web development",
        "fastapi": "fastapi web development"
    }

    canonical_title = canonical_titles.get(
        normalized_suggestion
    )

    def score_result(result):

        title = normalize_text(
            result.get(
                "title",
                ""
            )
        )

        exact = (
            title == normalized_suggestion
        )

        canonical = (
            bool(canonical_title)
            and title == canonical_title
        )

        starts = title.startswith(
            normalized_suggestion
        )

        contains = (
            normalized_suggestion
            in title
        )

        # Large explicit bonus ensures "Python Programming"
        # beats related pages such as "Python Libraries".
        canonical_bonus = (
            200000
            if canonical
            else 0
        )

        exact_bonus = (
            300000
            if exact
            else 0
        )

        starts_bonus = (
            2000
            if starts
            else 0
        )

        contains_bonus = (
            500
            if contains
            else 0
        )

        return (
            exact_bonus + canonical_bonus
            + starts_bonus
            + contains_bonus
            + result.get(
                "score",
                0
            )
        )

    return sorted(
        results,
        key=score_result,
        reverse=True
    )



def search_pages(query):

    query = str(
        query or ""
    ).strip()

    if not query:
        return []

    pages = get_all_pages()

    if not pages:
        return []

    # --------------------------------------------------------
    # SMART SEARCH: search the original query first, then a
    # small number of grounded variants and merge results.
    # --------------------------------------------------------

    variants = build_smart_query_variants(
        query
    )

    merged = {}
    original_normalized = normalize_text(query)

    for variant_index, variant in enumerate(variants):

        variant_results = _search_exact(
            variant,
            pages
        )

        for result in variant_results:

            url = normalize_url(
                result["url"]
            )

            if not url:
                continue

            # Original query is strongest. Variants are supporting
            # retrieval paths and receive a small score adjustment.
            score = int(
                result["score"] * (
                    1.0
                    if variant_index == 0
                    else 0.78
                )
            )

            existing = merged.get(url)

            if existing is None:

                merged[url] = {
                    **result,
                    "score": score,
                    "matched_query": variant
                }

            elif score > existing["score"]:

                existing["score"] = score
                existing["matched_query"] = variant

    results = list(
        merged.values()
    )

    results.sort(
        key=lambda result: (
            result["score"],
            result["title"].lower()
        ),
        reverse=True
    )

    if results:
        return results[:MAX_RESULTS]

    # --------------------------------------------------------
    # TYPO FALLBACK
    # --------------------------------------------------------

    suggestion = (
        None
        if is_question_query(query)
        else get_suggestion(query)
    )

    if not suggestion:
        return []

    suggested_results = _search_exact(
        suggestion,
        pages
    )

    suggested_results = rank_suggested_results(
        suggested_results,
        suggestion
    )

    for result in suggested_results:

        result["suggested"] = True

        result["original_query"] = query

        result["suggestion"] = suggestion

    return suggested_results[:MAX_RESULTS]


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

    # Use the public smart search first.
    results = search_pages(
        query
    )

    # search_pages may already have used typo fallback. Preserve a
    # clean response contract here and only label an explicit typo
    # correction when the suggestion matches the returned results.
    if results:

        suggestion = get_suggestion(
            query
        )

        if suggestion:

            normalized_query = normalize_text(query)
            normalized_suggestion = normalize_text(
                suggestion
            )

            if normalized_query != normalized_suggestion:
                return {
                    "results": results,
                    "suggestion": suggestion,
                    "used_suggestion": True
                }

        return {
            "results": results,
            "suggestion": None,
            "used_suggestion": False
        }

    suggestion = get_suggestion(
        query
    )

    if not suggestion:
        return {
            "results": [],
            "suggestion": None,
            "used_suggestion": False
        }

    suggested_results = _search_exact(
        suggestion,
        pages
    )

    suggested_results = rank_suggested_results(
        suggested_results,
        suggestion
    )

    for result in suggested_results:

        result["suggested"] = True
        result["original_query"] = query
        result["suggestion"] = suggestion

    return {
        "results": suggested_results[:MAX_RESULTS],
        "suggestion": suggestion,
        "used_suggestion": bool(
            suggested_results
        )
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