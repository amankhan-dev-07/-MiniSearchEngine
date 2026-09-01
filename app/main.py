from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from app.search.search import (
    search_pages,
    get_all_pages,
    get_suggestion,
)

try:
    from app.wikipedia import wikipedia_fallback
except ImportError:
    wikipedia_fallback = None

try:
    from app.web_retriever import web_fallback
except ImportError:
    web_fallback = None
import os
import re
import logging
import math
import time
from collections import Counter


_LOGGER = logging.getLogger("ameja")
if not _LOGGER.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Ameja Search Engine",
    version="1.0.0"
)

# Browser/frontend access. For local/public deployment, the API remains
# lightweight and does not require any external paid service.
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "AMEJA_ALLOWED_ORIGINS",
        "*"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ============================================================
# FRONTEND
# ============================================================

AMEJA_PUBLIC_BASE_URL = os.getenv(
    "AMEJA_PUBLIC_BASE_URL",
    ""
).rstrip("/")

AMEJA_PAGES_DIR = os.getenv(
    "AMEJA_PAGES_DIR",
    os.path.join(
        os.path.dirname(
            os.path.dirname(
                os.path.abspath(__file__)
            )
        ),
        "test_site",
        "pages"
    )
)


def _public_url(
    url
):
    """
    Convert local development source URLs to same-origin production URLs.
    External URLs (e.g. Wikipedia) remain unchanged.
    """

    value = str(
        url or ""
    )

    if not value:
        return value

    if "127.0.0.1:9000/pages/" in value:
        path = value.split(
            "127.0.0.1:9000",
            1
        )[1]

        if AMEJA_PUBLIC_BASE_URL:
            return (
                AMEJA_PUBLIC_BASE_URL
                + path
            )

        return path

    if "localhost:9000/pages/" in value:
        path = value.split(
            "localhost:9000",
            1
        )[1]

        if AMEJA_PUBLIC_BASE_URL:
            return (
                AMEJA_PUBLIC_BASE_URL
                + path
            )

        return path

    return value


def _publicize_result_urls(
    data
):
    """
    Rewrite source/result URLs without mutating the answer content.
    """

    if not isinstance(
        data,
        dict
    ):
        return data

    result = dict(
        data
    )

    if "url" in result:
        result["url"] = _public_url(
            result.get(
                "url"
            )
        )

    sources = result.get(
        "sources"
    )

    if isinstance(
        sources,
        list
    ):

        result["sources"] = []

        for source in sources:

            if isinstance(
                source,
                dict
            ):

                item = dict(
                    source
                )

                item["url"] = _public_url(
                    item.get(
                        "url"
                    )
                )

                result["sources"].append(
                    item
                )

            else:
                result["sources"].append(
                    source
                )

    results = result.get(
        "results"
    )

    if isinstance(
        results,
        list
    ):

        result["results"] = []

        for item in results:

            if isinstance(
                item,
                dict
            ):

                entry = dict(
                    item
                )

                entry["url"] = _public_url(
                    entry.get(
                        "url"
                    )
                )

                result["results"].append(
                    entry
                )

            else:
                result["results"].append(
                    item
                )

    return result



FRONTEND_PATH = os.path.join(
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    ),
    "frontend",
    "index.html"
)


# ============================================================
# HELPERS
# ============================================================

def normalize_words(text):
    return re.findall(
        r"\b[a-z0-9]+\b",
        str(text or "").lower()
    )


def normalize_text(text):
    return " ".join(
        normalize_words(text)
    )


def clean_sentence(sentence):
    sentence = re.sub(
        r"\s+",
        " ",
        str(sentence or "")
    ).strip()

    return sentence


def split_sentences(text):
    text = clean_sentence(text)

    if not text:
        return []

    # Handles normal English sentence punctuation while also
    # keeping short educational fragments.
    parts = re.split(
        r"(?<=[.!?])\s+",
        text
    )

    return [
        clean_sentence(part)
        for part in parts
        if clean_sentence(part)
    ]


def understand_query(query):
    """
    Normalize common English + Hindi/Hinglish question forms
    into the actual topic represented in AMEJA's indexed pages.
    """

    original = clean_sentence(
        query
    )

    if not original:
        return ""

    text = original.lower().strip()

    text = re.sub(
        r"[?!.]+$",
        "",
        text
    ).strip()

    # --------------------------------------------------------
    # Exact multi-word question phrases first.
    # --------------------------------------------------------

    replacements = [
        # Hinglish / Hindi
        (r"\bke\s+(?:baare|bare)\s+mein\s+(?:batao|bataiye|bata|samjhao)\b", " "),
        (r"\bke\s+(?:baare|bare)\s+mein\b", " "),
        (r"\b(?:baare|bare)\s+mein\s+(?:batao|bataiye|bata|samjhao)\b", " "),
        (r"\b(?:kaun|kon)\s+(?:the|tha|thi|thii|hai|hain)\b", " "),
        (r"\b(?:kaun|kon)\s+hai\b", " "),
        (r"\b(?:baare|bare)\s+mein\b", " "),

        # Hinglish definition
        (r"\bkya\s+(?:hota|hoti|hote)\s+hai(?:n)?\b", " "),
        (r"\bkya\s+hai\b", " "),
        (r"\bkya\s+h\b", " "),

        # Hinglish how/process
        (r"\bkaise\s+kaam\s+karta\s+hai\b", " "),
        (r"\bkaise\s+kaam\s+karte\s+hai(?:n)?\b", " "),
        (r"\bkaise\s+kaam\s+karti\s+hai\b", " "),

        # English how
        (r"\bhow\s+does\s+(.+?)\s+work\b", r" \1 "),
        (r"\bhow\s+do\s+(.+?)\s+work\b", r" \1 "),
        (r"\bhow\s+can\s+(.+?)\s+work\b", r" \1 "),
        (r"\bhow\s+does\s+(.+?)\b", r" \1 "),
        (r"\bhow\s+do\s+(.+?)\b", r" \1 "),

        # English why / purpose
        (r"\bwhy\s+is\s+(.+?)\s+used\b", r" \1 "),
        (r"\bwhy\s+are\s+(.+?)\s+used\b", r" \1 "),
        (r"\bwhy\s+do\s+people\s+use\s+(.+?)\b", r" \1 "),
        (r"\bwhy\s+is\s+(.+?)\b", r" \1 "),
        (r"\bwhy\s+are\s+(.+?)\b", r" \1 "),
        (r"\b(?:what\s+are|what\s+is)\s+the\s+(?:benefits?|advantages?)\s+of\s+(.+?)\b", r" benefits of \1 "),
        (r"\bbenefits?\s+of\s+(.+?)\b", r" benefits of \1 "),
        (r"\badvantages?\s+of\s+(.+?)\b", r" advantages of \1 "),

        # English definitions/explanations
        (r"\bdefinition\s+of\b", " "),
        (r"\bmeaning\s+of\b", " "),
        (r"\bwhat\s+does\b", " "),
        (r"\bwhat\s+is\b", " "),
        (r"\bwhat\s+are\b", " "),
        (r"\bwhat\s+was\b", " "),
        (r"\bwhat\s+were\b", " "),
        (r"\bwho\s+is\b", " "),
        (r"\bwho\s+are\b", " "),
        (r"\bwhere\s+is\b", " "),
        (r"\bwhere\s+are\b", " "),
        (r"\bwhen\s+is\b", " "),
        (r"\bwhen\s+was\b", " "),
        (r"\bplease\s+explain\b", " "),
        (r"\btell\s+me\s+about\b", " "),
        (r"\bexplain\b", " "),
        (r"\bdefine\b", " "),
        (r"\bhow\s+to\b", " "),
    ]

    for pattern, replacement in replacements:
        text = re.sub(
            pattern,
            replacement,
            text,
            flags=re.IGNORECASE
        )

    filler_words = {
        "kya", "hai", "h", "hain",
        "ka", "ke", "ki", "ko",
        "mein", "me",
        "mujhe", "mujhko",
        "mere", "mera", "meri",
        "iske", "is",
        "yeh", "ye",
        "woh", "wo",
        "batao", "bataiye", "bata",
        "samjhao", "samjhaao",
        "please", "pls", "plz",
        "tell", "about", "it",
        "the", "a", "an",
        "par", "pe", "se",
        "kaise", "kis", "kisi",
        "kyun", "kyon", "kyu"
    }

    words = re.findall(
        r"[a-z0-9]+",
        text
    )

    core = [
        word
        for word in words
        if word not in filler_words
        and len(word) >= 2
    ]

    return " ".join(core).strip() or original



def detect_answer_language(query):
    """English stays English; distinctive Hindi/Hinglish words trigger Hinglish."""

    original = clean_sentence(
        query
    )

    if not original:
        return "english"

    words = set(
        normalize_words(
            original
        )
    )

    markers = {
        "kya", "hai", "hain", "h",
        "ke", "ka", "ki", "ko",
        "baare", "bare", "mein", "me",
        "batao", "bataiye", "btao",
        "samjhao", "samjhaao",
        "kaise", "kyun", "kyon",
        "kyu", "kab", "kahan",
        "kaun", "kon",
        "mujhe", "mere", "mera", "meri",
        "yeh", "ye", "woh", "wo",
        "kis", "kisi", "par", "pe", "se",
        "faayda", "fayda", "faayde", "fayde",
        "tha", "thi", "thii",
    }

    return (
        "hinglish"
        if words.intersection(markers)
        else "english"
    )



def detect_question_type(query):
    """
    Detect the broad intent of the user's question.

    This only affects answer sentence ranking/presentation.
    """

    text = clean_sentence(
        query
    ).lower()

    if not text:
        return "definition"

    # How / process questions
    if (
        text.startswith("how does ")
        or text.startswith("how do ")
        or text.startswith("how can ")
        or text.startswith("how to ")
        or "kaise" in text
        or "kaise kaam" in text
    ):
        return "how"

    # Why / purpose / benefits questions
    if (
        text.startswith("why ")
        or "kyun" in text
        or "kyon" in text
        or "kyu" in text
        or "benefit" in text
        or "advantage" in text
        or "faayda" in text
        or "fayda" in text
    ):
        return "why"

    # Definition questions
    if (
        text.startswith("what is ")
        or text.startswith("what are ")
        or text.startswith("what was ")
        or text.startswith("what were ")
        or text.startswith("define ")
        or text.startswith("definition ")
        or "kya hai" in text
        or "kya hota" in text
        or "kya hoti" in text
        or "kya h" in text
    ):
        return "definition"

    # Hinglish fact/person questions
    if (
        text.startswith("kaun ")
        or "kaun hai" in text
        or "kaun the" in text
        or "kaun tha" in text
        or "kaun thi" in text
    ):
        return "fact"

    # Fact-style questions
    if (
        text.startswith("who ")
        or text.startswith("where ")
        or text.startswith("when ")
    ):
        return "fact"

    return "explain"




def to_hinglish_answer(
    answer,
    topic
):
    """
    Render common AMEJA answers in simple natural Hinglish.
    """

    answer = clean_sentence(answer)
    topic = clean_sentence(topic)

    if not answer:
        return answer

    topic_lower = topic.lower()
    lowered = answer.lower()

    if (
        topic_lower == "python"
        and "python is a popular programming language" in lowered
    ):
        return (
            "Python ek popular programming language hai jo "
            "web development, automation, data science aur "
            "artificial intelligence ke liye use hoti hai."
        )

    if (
        topic_lower == "machine learning"
        and "machine learning allows systems" in lowered
    ):
        return (
            "Machine learning mein systems data se patterns "
            "seekhte hain aur predictions ya decisions le sakte hain."
        )

    if (
        topic_lower == "database"
        and (
            "sqlite" in lowered
            or "database" in lowered
        )
    ):
        return (
            "Database ek aisa system hai jahan information ko "
            "store aur organize kiya jata hai. SQLite small "
            "applications aur prototypes ke liye ek lightweight "
            "database hai."
        )

    if (
        topic_lower == "python libraries"
        and "python" in lowered
        and "libraries" in lowered
    ):
        return (
            "Python libraries reusable tools aur functionality "
            "provide karti hain jo programming, web development, "
            "data processing aur machine learning jaise kaamon "
            "mein help karti hain."
        )

    if (
        topic_lower == "fastapi"
        and "fastapi" in lowered
    ):
        return (
            "FastAPI ek modern Python framework hai jo fast aur "
            "scalable web APIs banane ke liye use hota hai."
        )

    if (
        topic_lower == "web development"
        and "web development" in lowered
    ):
        return (
            "Web development mein websites aur web applications "
            "banayi jati hain, jisme HTML, CSS, JavaScript aur "
            "backend technologies ka use hota hai."
        )

    converted = answer

    for pattern, replacement in (
        (r"\bprovides\b", "provide karta hai"),
        (r"\bhelps\b", "help karta hai"),
        (r"\ballows\b", "allow karta hai"),
        (r"\benables\b", "enable karta hai"),
        (r"\binvolves\b", "mein shamil hota hai"),
        (r"\bused for\b", "ke liye use hota hai"),
        (r"\bused to\b", "ke liye use kiya jata hai"),
        (r"\band\b", "aur"),
    ):
        converted = re.sub(
            pattern,
            replacement,
            converted,
            flags=re.IGNORECASE
        )

    return re.sub(
        r"\s+",
        " ",
        converted
    ).strip()



def rank_answer_sentences(
    sentences,
    core_set,
    core_query,
    title_words,
    question_type
):
    """
    Rank source sentences for Answer Engine selection.
    """

    ranked = []

    for index, sentence in enumerate(sentences):

        sentence = clean_sentence(
            sentence
        )

        if not sentence:
            continue

        sentence_words = set(
            normalize_words(
                sentence
            )
        )

        topic_matches = len(
            core_set & sentence_words
        )

        if topic_matches == 0:
            continue

        title_matches = len(
            core_set & title_words
        )

        lowered = sentence.lower()

        score = (
            topic_matches * 60
            + title_matches * 12
            + max(0, 12 - index)
        )

        if core_query.lower() in lowered:
            score += 110

        # Direct definitions are excellent answer candidates.
        if __import__("re").match(
            rf"^{__import__('re').escape(core_query.lower())}\s+(is|are)\b",
            lowered
        ):
            score += 350

        if 35 <= len(sentence) <= 280:
            score += 18

        if question_type == "definition":

            if any(
                signal in f" {lowered} "
                for signal in (
                    " is ",
                    " are ",
                    " refers to ",
                    " means ",
                    " is a ",
                    " is an "
                )
            ):
                score += 55

        elif question_type == "how":

            if any(
                signal in f" {lowered} "
                for signal in (
                    " works ",
                    " working ",
                    " process ",
                    " steps ",
                    " used ",
                    " allows ",
                    " enables ",
                    " helps ",
                    " involves "
                )
            ):
                score += 48

        elif question_type == "why":

            if any(
                signal in f" {lowered} "
                for signal in (
                    " because ",
                    " helps ",
                    " allows ",
                    " enables ",
                    " useful ",
                    " important ",
                    " used "
                )
            ):
                score += 45

        elif question_type == "fact":

            if (
                " is " in f" {lowered} "
                or " are " in f" {lowered} "
            ):
                score += 38

        else:

            if any(
                signal in f" {lowered} "
                for signal in (
                    " is ",
                    " are ",
                    " used ",
                    " provides ",
                    " helps ",
                    " allows "
                )
            ):
                score += 25

        navigation = {
            "home",
            "menu",
            "explore",
            "explore more",
            "next",
            "previous",
            "contact",
            "login",
            "sign in",
            "sign up"
        }

        if lowered in navigation:
            continue

        ranked.append({
            "sentence": sentence,
            "score": score,
            "matches": topic_matches,
            "index": index
        })

    ranked.sort(
        key=lambda item: (
            item["score"],
            item["matches"],
            -item["index"]
        ),
        reverse=True
    )

    return ranked



def extract_best_answer_sentence(
    content,
    core_query,
    question_type
):
    """
    Extract a complete grounded answer sentence.
    """

    cleaned = clean_sentence(
        content
    )

    topic = clean_sentence(
        core_query
    )

    if not cleaned or not topic:
        return None

    topic_lower = topic.lower()

    sentences = split_sentences(
        cleaned
    )

    if not sentences:
        return None

    # Exact definition first.
    for sentence in sentences:

        candidate = clean_sentence(
            sentence
        )

        if re.match(
            rf"^{re.escape(topic_lower)}\s+(is|are)\b",
            candidate.lower()
        ):
            return candidate

    # Complete process/explanation sentence beginning with the topic.
    for sentence in sentences:

        candidate = clean_sentence(
            sentence
        )

        if re.match(
            rf"^{re.escape(topic_lower)}\s+"
            rf"(allows|helps|provides|involves|uses|works|"
            rf"enables|supports|lets)\b",
            candidate.lower()
        ):
            return candidate

    # Generic ranked complete sentence fallback.
    ranked = rank_answer_sentences(
        sentences,
        set(
            normalize_words(
                topic
            )
        ),
        topic,
        set(),
        question_type
    )

    if ranked:
        return clean_sentence(
            ranked[0]["sentence"]
        )

    return None




def _clean_source_content(
    content,
    title
):
    """
    Safely clean crawler text without removing a legitimate
    sentence prefix that happens to equal the page title.
    """

    cleaned = clean_sentence(
        content
    )

    title_clean = clean_sentence(
        title
    )

    if not cleaned:
        return ""

    if not title_clean:
        return cleaned

    # Remove only repeated heading prefixes, e.g.
    # "Python Python Python is ..."
    # but keep "Python is ..."
    prefix = title_clean.lower()
    lower = cleaned.lower()

    while lower.startswith(
        prefix + " " + prefix
    ):
        cleaned = cleaned[
            len(title_clean):
        ].strip()
        lower = cleaned.lower()

    if lower == prefix:
        return ""

    return cleaned




def _get_multi_source_sentences(
    candidates,
    core_query,
    question_type,
    max_sentences=3
):
    """
    Compatibility-safe multi-source hook.

    The local corpus currently contains crawler heading artefacts. Until a
    dedicated sentence extractor is trusted, do not inject secondary text
    into the primary answer.

    Returning an empty list keeps the answer grounded in the selected
    primary page and prevents malformed cross-page sentences.
    """

    return []



def build_answer(query):
    """
    Answer Engine v22.

    Exact/bare topics:
        one clean primary-source answer.

    Explicit how/why/explain questions:
        concise multi-source synthesis, maximum 3 complete sentences.
    """

    original_query = str(
        query or ""
    ).strip()

    answer_language = detect_answer_language(
        original_query
    )

    question_type = detect_question_type(
        original_query
    )

    if (
        ENABLE_NATURAL_QUERY_PARSER
        and "understand_natural_query" in globals()
    ):
        parsed_intent = understand_natural_query(
            original_query
        ).get(
            "intent",
            "search"
        )

        if parsed_intent != "search":
            question_type = parsed_intent

    # Bare one-word topics are definition-style requests.
    bare_topic = (
        len(
            normalize_words(
                original_query
            )
        ) == 1
        and "?" not in original_query
    )

    if bare_topic:
        question_type = "definition"

    empty = {
        "answer": None,
        "title": None,
        "url": None,
        "sources": [],
        "confidence": 0,
        "understood_query": "",
        "answer_language": answer_language,
        "question_type": question_type
    }

    if not original_query:
        return empty

    natural = None

    if (
        ENABLE_NATURAL_QUERY_PARSER
        and "understand_natural_query" in globals()
    ):
        natural = understand_natural_query(
            original_query
        )

    core_query = (
        natural.get(
            "topic",
            ""
        )
        if natural
        else ""
    )

    if not core_query:
        core_query = (
            understand_query(
                original_query
            )
            or original_query
        )

    normalized_core = normalize_text(
        core_query
    )

    pages = get_all_pages()

    if not pages:
        return empty

    # --------------------------------------------------------
    # Find exact/canonical primary page.
    # --------------------------------------------------------

    canonical_titles = {
        "python": "python programming",
        "machine learning": "machine learning",
        "database": "database systems",
        "web development": "web development",
        "fastapi": "fastapi web development",
        "python libraries": "python libraries",
        "artificial intelligence": "artificial intelligence",
        "algorithms": "algorithms",
        "data structures": "data structures",
        "software engineering": "software engineering",
    }

    canonical = canonical_titles.get(
        normalized_core
    )

    exact_page = None

    for url, title, content in pages:

        title_clean = clean_sentence(
            title
        )

        normalized_title = normalize_text(
            title_clean
        )

        if (
            normalized_title == normalized_core
            or (
                canonical
                and normalized_title == canonical
            )
        ):

            exact_page = {
                "url": url,
                "title": title_clean,
                "content": str(
                    content or ""
                ).strip(),
                "score": 10000
            }

            break

    # --------------------------------------------------------
    # Retrieve normal search candidates.
    # --------------------------------------------------------

    results = smart_local_search(
        core_query
    )

    page_lookup = {
        str(url).lower(): {
            "url": url,
            "title": clean_sentence(title),
            "content": str(content or "").strip()
        }
        for url, title, content in pages
    }

    candidates = []

    if exact_page:
        candidates.append(
            exact_page
        )

    for result in results[:10]:

        url = str(
            result.get(
                "url",
                ""
            )
        )

        page = page_lookup.get(
            url.lower()
        )

        if not page:
            continue

        if (
            exact_page
            and url.lower()
            == str(
                exact_page["url"]
            ).lower()
        ):
            continue

        candidates.append({
            **page,
            "score": result.get(
                "score",
                0
            )
        })

    if not candidates:
        return empty

    primary = candidates[0]

    # Ensure canonical page is first when one is known.
    if canonical:

        for index, candidate in enumerate(
            candidates
        ):

            if (
                normalize_text(
                    candidate["title"]
                )
                == canonical
            ):

                primary = candidate

                candidates = [
                    primary
                ] + [
                    item
                    for item in candidates
                    if str(
                        item["url"]
                    ).lower()
                    != str(
                        primary["url"]
                    ).lower()
                ]

                break

    # --------------------------------------------------------
    # Exact/canonical topic + definitions/facts = primary source only.
    # A bare exact-title topic such as "artificial intelligence"
    # must never synthesize unrelated supporting pages.
    # --------------------------------------------------------

    exact_or_canonical_topic = (
        bare_topic
        or (
            canonical
            and normalize_text(
                primary["title"]
            ) == canonical
        )
        or (
            normalize_text(
                primary["title"]
            ) == normalized_core
        )
    )

    if (
        exact_or_canonical_topic
        or question_type in {
            "definition",
            "fact"
        }
    ):

        content = _clean_source_content(
            primary["content"],
            primary["title"]
        )

        answer_sentence = extract_best_answer_sentence(
            content,
            core_query,
            "definition"
        )

        if not answer_sentence:
            return empty

        answer = clean_sentence(
            answer_sentence
        )

        sources = [{
            "title": primary["title"],
            "url": str(
                primary["url"]
            )
        }]

    else:

        # ----------------------------------------------------
        # Explicit how/why/explain = safe multi-source synthesis.
        # ----------------------------------------------------

        primary_content = _clean_source_content(
            primary["content"],
            primary["title"]
        )

        primary_sentence = extract_best_answer_sentence(
            primary_content,
            core_query,
            question_type
        )

        if not primary_sentence:
            ranked_primary = rank_answer_sentences(
                split_sentences(
                    primary_content
                ),
                set(
                    normalize_words(
                        core_query
                    )
                ),
                core_query,
                set(
                    normalize_words(
                        primary["title"]
                    )
                ),
                question_type
            )

            if ranked_primary:
                primary_sentence = clean_sentence(
                    ranked_primary[0]["sentence"]
                )

        chosen = _get_multi_source_sentences(
            candidates,
            core_query,
            question_type,
            max_sentences=3
        )

        selected = []

        if primary_sentence:
            selected.append({
                "sentence": clean_sentence(
                    primary_sentence
                ),
                "title": primary["title"],
                "url": primary["url"],
                "score": 100000
            })

        for item in chosen:

            if (
                str(item["url"]).lower()
                == str(primary["url"]).lower()
            ):
                continue

            duplicate = any(
                _sentence_similarity(
                    item["sentence"],
                    existing["sentence"]
                ) >= 0.62
                for existing in selected
            )

            if duplicate:
                continue

            selected.append(item)

            if len(selected) >= 3:
                break

        if not selected:
            return empty

        answer = " ".join(
            item["sentence"]
            for item in selected
        )

        sources = []
        seen_sources = set()

        for item in selected:

            url = str(
                item["url"]
            )

            key = url.lower()

            if key in seen_sources:
                continue

            sources.append({
                "title": item["title"],
                "url": url
            })

            seen_sources.add(
                key
            )

    # --------------------------------------------------------
    # Confidence.
    # --------------------------------------------------------

    primary_title = normalize_text(
        primary["title"]
    )

    if (
        primary_title == normalized_core
        or (
            canonical
            and primary_title == canonical
        )
    ):
        confidence = 98

    elif normalized_core in primary_title:
        confidence = 95

    else:
        confidence = 90

    if len(sources) > 1 and confidence > 96:
        confidence = 96

    # --------------------------------------------------------
    # Language.
    # --------------------------------------------------------

    if answer_language == "hinglish":
        answer = to_hinglish_answer(
            answer,
            core_query
        )

    return {
        "answer": answer,
        "title": primary["title"],
        "url": primary["url"],
        "sources": sources,
        "confidence": confidence,
        "understood_query": core_query,
        "answer_language": answer_language,
        "question_type": question_type
    }




def _to_hinglish_wikipedia(
    answer,
    topic
):
    """
    Convert common factual Wikipedia-style English sentences into
    natural lightweight Hinglish.

    This is deliberately deterministic and limited to well-known
    sentence patterns so the laptop does not need a local LLM.
    """

    answer = clean_sentence(
        answer
    )

    if not answer:
        return answer

    topic = clean_sentence(
        topic
    )

    # Strong exact template for the most common Wikipedia person fact.
    # This prevents awkward word-by-word conversion.
    if (
        topic.lower() == "albert einstein"
        and "german-born theoretical physicist" in answer.lower()
    ):
        sentences = split_sentences(
            answer
        )

        first = (
            "Albert Einstein ek German-born theoretical physicist "
            "the, jo theory of relativity develop karne ke liye "
            "sabse zyada jaane jaate hain."
        )

        if len(sentences) > 1:
            second = sentences[1]

            if "important contributions to quantum theory" in second.lower():
                second = (
                    "Unhone quantum theory mein bhi important "
                    "contributions diye."
                )
            else:
                second = _simple_hinglish_sentence(
                    second
                )

            return clean_sentence(
                first + " " + second
            )

        return first

    return " ".join(
        _simple_hinglish_sentence(
            sentence
        )
        for sentence in split_sentences(
            answer
        )
    ).strip()


def _simple_hinglish_sentence(
    sentence
):
    """
    Conservative sentence-level conversion for common factual text.
    """

    value = clean_sentence(
        sentence
    )

    if not value:
        return ""

    replacements = [
        (
            r"\bbest known for developing the\b",
            "ke liye sabse zyada jaane jaate hain, jinhone"
        ),
        (
            r"\bbest known for\b",
            "ke liye sabse zyada jaane jaate hain"
        ),
        (
            r"\bwas a\b",
            "ek"
        ),
        (
            r"\bwas an\b",
            "ek"
        ),
        (
            r"\bis a\b",
            "ek"
        ),
        (
            r"\bis an\b",
            "ek"
        ),
        (
            r"\bmade important contributions to\b",
            "mein important contributions diye"
        ),
        (
            r"\bimportant contributions to\b",
            "mein important contributions diye"
        ),
        (
            r"\breceived the\b",
            "ko mila"
        ),
        (
            r"\bknown as\b",
            "ke naam se jaana jata hai"
        ),
        (
            r"\bwas born in\b",
            "mein paida hue the"
        ),
        (
            r"\bdied in\b",
            "mein unki death hui"
        ),
        (
            r"\bdeveloped\b",
            "develop kiya"
        ),
        (
            r"\bused for\b",
            "ke liye use hota hai"
        ),
        (
            r"\bis used for\b",
            "ke liye use kiya jata hai"
        ),
        (
            r"\band\b",
            "aur"
        ),
    ]

    converted = value

    for pattern, replacement in replacements:
        converted = re.sub(
            pattern,
            replacement,
            converted,
            flags=re.IGNORECASE
        )

    # A few safe connectors.
    converted = re.sub(
        r"\bwhich\b",
        "jo",
        converted,
        flags=re.IGNORECASE
    )

    converted = re.sub(
        r"\bfrom\b",
        "se",
        converted,
        flags=re.IGNORECASE
    )

    converted = re.sub(
        r"\bin\b",
        "mein",
        converted,
        flags=re.IGNORECASE
    )

    converted = re.sub(
        r"\s+",
        " ",
        converted
    ).strip()

    return converted



def _clean_local_answer_title_duplication(
    answer,
    title
):
    """
    Remove only actual duplicated crawler headings.

    Never truncate a legitimate multi-word title such as
    "Machine learning".
    """

    answer = clean_sentence(
        answer
    )

    title = clean_sentence(
        title
    )

    if not answer or not title:
        return answer

    lower_answer = answer.lower()
    lower_title = title.lower()

    # Case 1: full heading repeated twice:
    # "Python Programming Python Programming Python is ..."
    doubled = (
        lower_title
        + " "
        + lower_title
        + " "
    )

    while lower_answer.startswith(
        doubled
    ):
        answer = answer[
            len(title) * 2 + 1:
        ].strip()
        lower_answer = answer.lower()

    # Case 2: heading followed by the same title as the beginning
    # of the real sentence:
    # "Machine Learning Machine learning allows ..."
    if lower_answer.startswith(
        lower_title + " "
    ):

        remainder = answer[
            len(title):
        ].strip()

        first_word = (
            title.split()[0]
            if title.split()
            else ""
        )

        if (
            first_word
            and remainder.lower().startswith(
                first_word.lower()
            )
        ):
            return clean_sentence(
                remainder
            )

    return clean_sentence(
        answer
    )



def _compact_wikipedia_answer(
    extract,
    max_sentences=2,
    max_chars=520
):
    """
    Keep Wikipedia fallback lightweight: use at most two useful
    sentences and cap the returned text length.
    """

    extract = clean_sentence(
        extract
    )

    if not extract:
        return ""

    sentences = split_sentences(
        extract
    )

    if not sentences:
        return extract[:max_chars].strip()

    chosen = []

    for sentence in sentences:

        sentence = clean_sentence(
            sentence
        )

        if not sentence:
            continue

        chosen.append(
            sentence
        )

        if len(chosen) >= max_sentences:
            break

    answer = " ".join(
        chosen
    ).strip()

    if len(answer) > max_chars:

        answer = (
            answer[:max_chars - 3]
            .rsplit(" ", 1)[0]
            .rstrip()
            + "..."
        )

    return answer




def _strip_page_heading(
    content,
    title
):
    """
    Remove only duplicated page-heading text from the beginning of a
    crawled page. The real sentence remains intact.
    """

    content = clean_sentence(
        content
    )

    title = clean_sentence(
        title
    )

    if not content:
        return ""

    if not title:
        return content

    # Repeated exact heading:
    # "Python Libraries Python Libraries Python libraries provide..."
    for _ in range(4):

        prefix = (
            title.lower()
            + " "
        )

        if content.lower().startswith(
            prefix
        ):
            content = content[
                len(title):
            ].strip()
        else:
            break

    return clean_sentence(
        content
    )


def _answer_sentence_candidates(
    page_content,
    core_query,
    question_type,
    page_title="",
    limit=6
):
    """Return complete, relevant sentences from cleaned page content."""

    content = _strip_page_heading(
        page_content,
        page_title
    )

    if not content:
        return []

    sentences = split_sentences(
        content
    )

    core_words = set(
        normalize_words(
            core_query
        )
    )

    ranked = []

    for position, sentence in enumerate(
        sentences
    ):

        sentence = clean_sentence(
            sentence
        )

        if len(sentence) < 45:
            continue

        # Never accept a sentence that begins with a lowercase fragment.
        if not sentence[:1].isupper():
            continue

        words = set(
            normalize_words(
                sentence
            )
        )

        matches = len(
            core_words & words
        )

        if matches < max(
            1,
            min(
                2,
                len(core_words)
            )
        ):
            continue

        lowered = sentence.lower()

        # Reject obvious heading/title fragments.
        if page_title and (
            lowered == page_title.lower()
            or lowered.startswith(
                page_title.lower() + " "
            )
        ):
            remainder = sentence[
                len(page_title):
            ].strip()

            if not remainder or not remainder[:1].isupper():
                continue

        # Reject common orphan starts.
        if re.match(
            r"^(?:and|or|but|also|is|are|was|were|provide|"
            r"provides|allows|enables|helps|uses)\b",
            lowered
        ):
            continue

        score = (
            matches * 100
            + max(
                0,
                12 - position
            )
        )

        if 55 <= len(sentence) <= 260:
            score += 30

        if question_type == "why" and any(
            cue in lowered
            for cue in (
                "used",
                "use",
                "because",
                "helps",
                "allows",
                "enables",
                "popular",
                "useful",
                "benefit",
                "advantage",
            )
        ):
            score += 60

        if question_type == "how" and any(
            cue in lowered
            for cue in (
                "works",
                "learn",
                "patterns",
                "process",
                "steps",
                "executes",
                "takes",
            )
        ):
            score += 60

        ranked.append(
            (
                score,
                sentence
            )
        )

    ranked.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return [
        sentence
        for _score, sentence
        in ranked[:limit]
    ]



def _dedupe_sentences(
    sentences
):
    """
    Remove near-duplicate sentences while keeping order.
    """

    selected = []

    for sentence in sentences:

        sentence = clean_sentence(
            sentence
        )

        if not sentence:
            continue

        current_words = set(
            normalize_words(
                sentence
            )
        )

        duplicate = False

        for previous in selected:

            previous_words = set(
                normalize_words(
                    previous
                )
            )

            if not current_words or not previous_words:
                continue

            similarity = (
                len(
                    current_words
                    & previous_words
                )
                /
                max(
                    1,
                    len(
                        current_words
                        | previous_words
                    )
                )
            )

            if similarity >= 0.62:
                duplicate = True
                break

        if not duplicate:
            selected.append(
                sentence
            )

    return selected



def _sentence_similarity(
    a,
    b
):
    """
    Lightweight token-overlap similarity for duplicate-sentence filtering.
    """

    a_words = set(
        normalize_words(
            a
        )
    )

    b_words = set(
        normalize_words(
            b
        )
    )

    if not a_words or not b_words:
        return 0.0

    return (
        len(
            a_words & b_words
        )
        /
        max(
            1,
            len(
                a_words | b_words
            )
        )
    )



def _build_lightweight_multisource_answer(
    query,
    local_result
):
    """
    Conservative multi-source enrichment.

    The primary answer remains authoritative. Supporting sources are added
    only when a complete cleaned sentence contributes a distinct fact.
    """

    question_type = local_result.get(
        "question_type",
        ""
    )

    if question_type not in {
        "how",
        "why",
        "explain"
    }:
        return local_result

    core_query = local_result.get(
        "understood_query",
        ""
    )

    primary_answer = clean_sentence(
        local_result.get(
            "answer",
            ""
        )
    )

    primary_title = clean_sentence(
        local_result.get(
            "title",
            ""
        )
    )

    primary_url = str(
        local_result.get(
            "url",
            ""
        )
    )

    if not primary_answer:
        return local_result

    results = search_pages(
        core_query
    )

    if not results:
        return local_result

    page_map = {
        str(url).lower(): {
            "url": url,
            "title": clean_sentence(title),
            "content": content,
        }
        for url, title, content in get_all_pages()
    }

    selected_support = []

    for result in results[:10]:

        url = str(
            result.get(
                "url",
                ""
            )
        )

        if (
            not url
            or url.lower() == primary_url.lower()
        ):
            continue

        page = page_map.get(
            url.lower()
        )

        if not page:
            continue

        candidates = _answer_sentence_candidates(
            page["content"],
            core_query,
            question_type,
            page_title=page["title"],
            limit=4
        )

        if not candidates:
            continue

        chosen = candidates[0]

        # Skip support sentences that are essentially the same statement
        # as the primary answer.
        if (
            _sentence_similarity(
                primary_answer,
                chosen
            )
            >= 0.55
        ):
            continue

        # Skip support if it duplicates an earlier supporting idea.
        duplicate = False

        for item in selected_support:

            if (
                _sentence_similarity(
                    item["sentence"],
                    chosen
                )
                >= 0.58
            ):
                duplicate = True
                break

        if duplicate:
            continue

        selected_support.append({
            "sentence": chosen,
            "title": page["title"],
            "url": page["url"]
        })

        # Maximum one sentence from each of two supporting pages.
        if len(selected_support) >= 2:
            break

    # If we don't have genuinely distinct support, keep the original
    # primary answer instead of manufacturing an "AI" sounding response.
    if not selected_support:
        return local_result

    answer_parts = [
        primary_answer
    ]

    for item in selected_support:
        if len(answer_parts) >= 3:
            break
        answer_parts.append(
            item["sentence"]
        )

    answer = clean_sentence(
        " ".join(answer_parts)
    )

    # Keep the lightweight answer bounded.
    if len(answer) > 720:
        answer = clean_sentence(
            primary_answer
        )

        selected_support = []

    if not selected_support:
        return local_result

    improved = dict(
        local_result
    )

    improved["answer"] = answer

    sources = [{
        "title": primary_title,
        "url": primary_url
    }]

    for item in selected_support:

        key = str(
            item["url"]
        ).lower()

        if any(
            str(
                source["url"]
            ).lower() == key
            for source in sources
        ):
            continue

        sources.append({
            "title": item["title"],
            "url": item["url"]
        })

    improved["sources"] = sources
    improved["confidence"] = min(
        int(
            local_result.get(
                "confidence",
                90
            )
        ),
        96
    )
    improved["answer_source"] = "ameja_multisource"

    return improved



def _router_topic(query):
    parsed = understand_natural_query(
        query
    )

    topic = parsed.get(
        "topic",
        ""
    )

    intent = parsed.get(
        "intent",
        "search"
    )

    if not topic:
        topic = clean_sentence(
            understand_query(query)
            or query
        )

    return (
        topic,
        intent
    )


def _semantic_tokens(text):
    """
    Small, deterministic tokenizer suitable for an 8 GB RAM laptop.
    """

    return [
        token
        for token in re.findall(
            r"[a-z0-9]+",
            str(text or "").lower()
        )
        if len(token) > 1
        and token not in _STOPWORDS
    ]


def _semantic_query_parts(
    query,
    intent
):
    """
    Normalize the question into weighted topic tokens.
    """

    base = _semantic_tokens(
        query
    )

    expanded = list(base)

    for token in _INTENT_EXPANSIONS.get(
        intent,
        set()
    ):
        expanded.append(
            token
        )

    return Counter(
        expanded
    )


def _semantic_score(
    query_counter,
    title,
    content,
    intent
):
    """
    Lightweight relevance score:
      title hit > body hit
      exact phrase > isolated token
      intent terms give a small boost
    """

    title_tokens = Counter(
        _semantic_tokens(
            title
        )
    )

    body_tokens = Counter(
        _semantic_tokens(
            content
        )
    )

    score = 0.0

    for token, weight in query_counter.items():

        if token in title_tokens:
            score += (
                8.0
                * min(
                    2,
                    title_tokens[token]
                )
                * weight
            )

        if token in body_tokens:
            score += (
                2.0
                * min(
                    3,
                    body_tokens[token]
                )
                * weight
            )

    normalized_query = " ".join(
        query_counter.keys()
    )

    title_norm = normalize_text(
        title
    )

    content_norm = normalize_text(
        content
    )

    if normalized_query and normalized_query in title_norm:
        score += 25.0

    # Intent-specific evidence.
    evidence = _INTENT_EXPANSIONS.get(
        intent,
        set()
    )

    if evidence:

        combined = (
            title_norm
            + " "
            + content_norm
        )

        for term in evidence:
            if term in combined:
                score += 1.5

    return score


def _clean_indexed_content(
    content,
    title
):
    """
    Clean crawler heading duplication without cutting the beginning of
    the actual first sentence.

    Examples:
        "Python Programming Python is ..."
            -> "Python is ..."

        "Python for Data Science Python for data science is ..."
            -> "Python for data science is ..."

        "Artificial Intelligence Artificial intelligence enables ..."
            -> "Artificial intelligence enables ..."
    """

    value = clean_sentence(
        content
    )

    title = clean_sentence(
        title
    )

    if not value or not title:
        return value

    # 1) Remove repeated full heading copies.
    repeated = (
        title.lower()
        + " "
        + title.lower()
        + " "
    )

    while value.lower().startswith(
        repeated
    ):
        value = value[
            len(title) * 2 + 1:
        ].strip()

    # 2) Remove one remaining heading copy only when the following text
    # clearly starts the actual sentence with the title's first word.
    if value.lower().startswith(
        title.lower() + " "
    ):

        remainder = value[
            len(title):
        ].strip()

        first_word = (
            title.split(
                None,
                1
            )[0]
            if title.split()
            else ""
        )

        if (
            first_word
            and remainder.lower().startswith(
                first_word.lower()
            )
        ):
            value = remainder

    return clean_sentence(
        value
    )



def _semantic_sentence_score(
    sentence,
    query_counter,
    intent
):
    """
    Score complete sentences independently so a source cannot leak
    heading fragments into the final answer.
    """

    sentence_tokens = Counter(
        _semantic_tokens(
            sentence
        )
    )

    score = 0.0

    for token, weight in query_counter.items():

        if token in sentence_tokens:
            score += (
                4.0
                * weight
            )

    lowered = sentence.lower()

    if intent == "why":

        for cue in (
            "used", "use", "because",
            "helps", "allows", "enables",
            "popular", "useful", "benefit",
            "advantage", "purpose"
        ):
            if cue in lowered:
                score += 4.0

    elif intent == "how":

        for cue in (
            "works", "learn", "patterns",
            "process", "steps", "takes",
            "executes", "method"
        ):
            if cue in lowered:
                score += 4.0

    elif intent == "explain":

        for cue in (
            "is", "are", "means",
            "refers", "used", "includes",
            "allows", "enables"
        ):
            if re.search(
                rf"\b{re.escape(cue)}\b",
                lowered
            ):
                score += 1.5

    # Prefer compact complete sentences.
    length = len(
        sentence
    )

    if 55 <= length <= 240:
        score += 6.0

    if length > 320:
        score -= 5.0

    return score


def semantic_retrieve(
    query,
    top_k=5
):
    """
    Retrieve semantically related indexed pages using lightweight
    lexical semantics. No external model is required.
    """

    intent = detect_question_type(
        query
    )

    query_counter = _semantic_query_parts(
        query,
        intent
    )

    if not query_counter:
        return []

    retrieved = []

    for url, title, content in get_all_pages():

        clean_content = _clean_indexed_content(
            content,
            title
        )

        if not clean_content:
            continue

        score = _semantic_score(
            query_counter,
            title,
            clean_content,
            intent
        )

        if score <= 0:
            continue

        retrieved.append({
            "url": url,
            "title": clean_sentence(
                title
            ),
            "content": clean_content,
            "semantic_score": score
        })

    retrieved.sort(
        key=lambda item: item["semantic_score"],
        reverse=True
    )

    return retrieved[
        :max(
            1,
            int(top_k)
        )
    ]


def semantic_compose_answer(
    query,
    primary_result,
    max_support=2
):
    """
    Keep the existing primary answer. Add supporting indexed facts only
    when semantic retrieval finds strongly relevant complete sentences.
    """

    if not ENABLE_SEMANTIC_ANSWERER:
        return primary_result

    intent = primary_result.get(
        "question_type",
        ""
    )

    if intent not in {
        "how",
        "why",
        "explain"
    }:
        return primary_result

    primary_answer = clean_sentence(
        primary_result.get(
            "answer",
            ""
        )
    )

    primary_url = str(
        primary_result.get(
            "url",
            ""
        )
    ).lower()

    core_query = (
        primary_result.get(
            "understood_query"
        )
        or query
    )

    if not primary_answer:
        return primary_result

    pages = semantic_retrieve(
        core_query,
        top_k=7
    )

    if not pages:
        return primary_result

    query_counter = _semantic_query_parts(
        core_query,
        intent
    )

    support = []

    for page in pages:

        if str(
            page["url"]
        ).lower() == primary_url:
            continue

        sentences = split_sentences(
            page["content"]
        )

        scored_sentences = []

        for position, sentence in enumerate(
            sentences
        ):

            sentence = clean_sentence(
                sentence
            )

            if len(sentence) < 45:
                continue

            if not sentence[:1].isupper():
                continue

            # Never allow source headings to appear in the answer.
            if normalize_text(
                sentence
            ) == normalize_text(
                page["title"]
            ):
                continue

            score = _semantic_sentence_score(
                sentence,
                query_counter,
                intent
            )

            if score < 12:
                continue

            # Reject likely fragments.
            if re.match(
                r"^(?:and|or|but|also|is|are|was|were|"
                r"provide|provides|allows|enables|helps|uses)\b",
                sentence.lower()
            ):
                continue

            scored_sentences.append(
                (
                    score,
                    -position,
                    sentence
                )
            )

        if not scored_sentences:
            continue

        scored_sentences.sort(
            reverse=True
        )

        best_sentence = scored_sentences[0][2]

        # Don't add a near-duplicate of the primary answer.
        if (
            _sentence_similarity(
                primary_answer,
                best_sentence
            )
            >= 0.52
        ):
            continue

        # Don't add a duplicate supporting idea.
        duplicate = False

        for existing in support:

            if (
                _sentence_similarity(
                    existing["sentence"],
                    best_sentence
                )
                >= 0.58
            ):
                duplicate = True
                break

        if duplicate:
            continue

        support.append({
            "sentence": best_sentence,
            "title": page["title"],
            "url": page["url"]
        })

        if len(support) >= max_support:
            break

    if not support:
        return primary_result

    parts = [
        primary_answer
    ]

    for item in support:
        parts.append(
            item["sentence"]
        )

    answer = clean_sentence(
        " ".join(parts)
    )

    if len(answer) > 720:
        return primary_result

    improved = dict(
        primary_result
    )

    improved["answer"] = answer

    sources = [{
        "title": primary_result["title"],
        "url": primary_result["url"]
    }]

    for item in support:
        sources.append({
            "title": item["title"],
            "url": item["url"]
        })

    improved["sources"] = sources
    improved["confidence"] = min(
        int(
            primary_result.get(
                "confidence",
                90
            )
        ),
        96
    )
    improved["answer_source"] = (
        "ameja_semantic"
    )

    return improved




# ============================================================
# BM25-STYLE LOCAL RETRIEVAL + QUERY EXPANSION v1
# ============================================================

ENABLE_BM25_RANKING = True

_BM25_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for",
    "from", "how", "i", "in", "is", "it", "of", "on", "or",
    "that", "the", "their", "this", "to", "was", "were", "what",
    "when", "where", "which", "who", "why", "with", "do", "does",
    "can", "could", "should", "would", "about"
}

_QUERY_EXPANSIONS = {
    "python": {
        "programming",
        "language",
        "automation",
        "data",
        "science",
        "artificial",
        "intelligence",
        "development",
    },
    "machine": {
        "learning",
        "data",
        "patterns",
        "predictions",
        "model",
    },
    "learning": {
        "machine",
        "data",
        "patterns",
        "predictions",
        "model",
    },
    "database": {
        "data",
        "storage",
        "sql",
        "information",
    },
    "artificial": {
        "intelligence",
        "ai",
        "computers",
        "patterns",
        "learning",
    },
    "intelligence": {
        "artificial",
        "ai",
        "computers",
        "patterns",
    },
}


def _bm25_tokens(text):
    return [
        token
        for token in re.findall(
            r"[a-z0-9]+",
            str(text or "").lower()
        )
        if len(token) > 1
        and token not in _BM25_STOPWORDS
    ]


def _expand_query_tokens(query):
    """
    Expand only with small, deterministic topic associations.
    """

    base = _bm25_tokens(
        query
    )

    expanded = list(
        base
    )

    for token in base:
        expanded.extend(
            _QUERY_EXPANSIONS.get(
                token,
                set()
            )
        )

    return expanded


def _bm25_rank_pages(
    query,
    pages,
    top_k=10
):
    """
    Lightweight BM25-style ranking over the local AMEJA corpus.

    Title tokens receive a strong extra boost. No embeddings or ML model
    are required.
    """

    if not pages:
        return []

    query_tokens = _expand_query_tokens(
        query
    )

    if not query_tokens:
        return []

    query_counter = Counter(
        query_tokens
    )

    documents = []
    document_frequencies = Counter()
    total_length = 0

    for page in pages:

        title_tokens = _bm25_tokens(
            page.get(
                "title",
                ""
            )
        )

        body_tokens = _bm25_tokens(
            page.get(
                "content",
                ""
            )
        )

        # Weight title tokens by duplication for the ranking document.
        tokens = (
            title_tokens * 4
            + body_tokens
        )

        counts = Counter(
            tokens
        )

        documents.append(
            {
                "page": page,
                "counts": counts,
                "length": len(tokens),
                "title_tokens": set(
                    title_tokens
                ),
            }
        )

        total_length += len(tokens)

        for token in set(tokens):
            document_frequencies[token] += 1

    n_docs = len(
        documents
    )

    avgdl = (
        total_length
        / max(
            1,
            n_docs
        )
    )

    k1 = 1.25
    b = 0.72

    ranked = []

    for document in documents:

        counts = document["counts"]
        dl = document["length"]

        score = 0.0

        for token, qf in query_counter.items():

            tf = counts.get(
                token,
                0
            )

            if tf <= 0:
                continue

            df = document_frequencies.get(
                token,
                0
            )

            # BM25 idf with a small floor to avoid negative effects.
            idf = math.log(
                1.0
                + (
                    n_docs
                    - df
                    + 0.5
                )
                / (
                    df
                    + 0.5
                )
            )

            denominator = (
                tf
                + k1
                * (
                    1
                    - b
                    + b
                    * dl
                    / max(
                        1.0,
                        avgdl
                    )
                )
            )

            score += (
                idf
                * (
                    tf
                    * (k1 + 1)
                    / max(
                        1e-9,
                        denominator
                    )
                )
                * max(
                    1,
                    qf
                )
            )

            if token in document["title_tokens"]:
                score += (
                    idf
                    * 5.0
                )

        # Exact normalized phrase in title gets the strongest boost.
        normalized_query = normalize_text(
            query
        )

        normalized_title = normalize_text(
            document["page"].get(
                "title",
                ""
            )
        )

        if (
            normalized_query
            and normalized_query
            == normalized_title
        ):
            score += 28.0

        elif (
            normalized_query
            and normalized_query
            in normalized_title
        ):
            score += 18.0

        page = dict(
            document["page"]
        )

        page["bm25_score"] = score
        ranked.append(
            page
        )

    ranked.sort(
        key=lambda page: page["bm25_score"],
        reverse=True
    )

    return ranked[
        :max(
            1,
            int(top_k)
        )
    ]



# ============================================================
# INTENT-AWARE SEARCH v1
# ============================================================

ENABLE_INTENT_RANKING = True

_INTENT_QUERY_MARKERS = {
    "definition": {
        "what", "define", "meaning", "kya"
    },
    "how": {
        "how", "kaise"
    },
    "why": {
        "why", "kyun", "kyon", "kyu"
    },
    "usage": {
        "use", "uses", "used", "usage", "applications",
        "application", "usecase", "usecases"
    },
    "benefits": {
        "benefit", "benefits", "advantage", "advantages",
        "faayda", "fayda", "faayde", "fayde"
    },
    "comparison": {
        "vs", "versus", "compare", "comparison",
        "difference", "different"
    },
}



# ============================================================
# NATURAL QUERY UNDERSTANDING v1
# ============================================================

ENABLE_NATURAL_QUERY_PARSER = True

_NATURAL_FILLERS = {
    "please", "tell", "me", "can", "you", "could", "would",
    "want", "to", "know", "kindly", "simple", "simply",
    "basically", "really", "actually", "just"
}


def _natural_clean_text(query):
    value = clean_sentence(query)
    value = re.sub(r"[?!.]+$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def understand_natural_query(query):
    """Parse common natural-language queries into intent and topic."""

    original = _natural_clean_text(
        query
    )

    if not original:
        return {
            "intent": "search",
            "topic": "",
            "normalized_query": ""
        }

    lower = normalize_text(
        original
    )

    patterns = [
        ("comparison", r"^(.+?)\s+(?:vs|versus)\s+(.+)$"),
        ("comparison", r"^compare\s+(.+?)\s+(?:and|with|to)\s+(.+)$"),
        ("comparison", r"^what\s+is\s+the\s+difference\s+between\s+(.+?)\s+and\s+(.+)$"),

        ("usage", r"^(.+?)\s+ka\s+use\s+kya\s+hai$"),
        ("usage", r"^(.+?)\s+ke\s+uses?\s+kya\s+hain$"),
        ("usage", r"^what\s+is\s+the\s+use\s+of\s+(.+)$"),
        ("usage", r"^what\s+are\s+the\s+uses\s+of\s+(.+)$"),
        ("usage", r"^what\s+is\s+(.+?)\s+used\s+for$"),
        ("usage", r"^where\s+is\s+(.+?)\s+used$"),

        ("how", r"^how\s+does\s+(.+?)\s+work$"),
        ("how", r"^how\s+do\s+i\s+(.+)$"),
        ("how", r"^how\s+can\s+i\s+(.+)$"),
        ("how", r"^how\s+can\s+(.+)$"),
        ("how", r"^(.+?)\s+se\s+(.+?)\s+kaise\s+(?:karte|karta|karna)\s+(?:hai|hain)$"),
        ("how", r"^(.+?)\s+kaise\s+kaam\s+(?:karta|karti|karte)\s+(?:hai|hain)$"),

        ("benefits", r"^what\s+are\s+the\s+(?:benefits?|advantages?)\s+of\s+(.+)$"),
        ("benefits", r"^(?:benefits?|advantages?)\s+of\s+(.+)$"),
        ("benefits", r"^(.+?)\s+ke\s+kya\s+(?:fayde|faayde|fayda|faayda)\s+(?:hai|hain)$"),
        ("benefits", r"^(.+?)\s+ke\s+(?:fayde|faayde|fayda|faayda)\s+kya\s+(?:hai|hain)$"),
        ("benefits", r"^(.+?)\s+(?:fayde|faayde|fayda|faayda)\s+kya\s+(?:hai|hain)$"),

        ("definition", r"^what\s+is\s+(.+)$"),
        ("definition", r"^what\s+are\s+(.+)$"),
        ("definition", r"^define\s+(.+)$"),
        ("definition", r"^what\s+does\s+(.+?)\s+mean$"),
        ("definition", r"^(.+?)\s+kya\s+hai$"),
        ("definition", r"^(.+?)\s+kya\s+hain$"),

        ("fact", r"^who\s+(?:was|is|were|are)\s+(.+)$"),
        ("fact", r"^(.+?)\s+kaun\s+(?:the|tha|thi|hai|hain)$"),

        ("why", r"^why\s+is\s+(.+)$"),
        ("why", r"^why\s+are\s+(.+)$"),
        ("why", r"^why\s+do\s+people\s+use\s+(.+)$"),
        ("why", r"^why\s+is\s+(.+?)\s+used$"),

        ("explain", r"^explain\s+(.+)$"),
        ("explain", r"^(.+?)\s+samjhao$"),
        ("explain", r"^(.+?)\s+samjhaao$"),
        ("explain", r"^(.+?)\s+ke\s+baare\s+mein\s+(?:batao|bataiye)$"),
    ]

    for intent, pattern in patterns:

        match = re.match(
            pattern,
            lower,
            flags=re.IGNORECASE
        )

        if not match:
            continue

        groups = [
            clean_sentence(group)
            for group in match.groups()
            if group is not None
        ]

        if intent == "comparison" and len(groups) >= 2:
            topic = groups[0] + " vs " + groups[1]

        elif intent == "how" and len(groups) >= 2:
            topic = groups[0] + " " + groups[1]

        else:
            topic = groups[0] if groups else lower

        return {
            "intent": intent,
            "topic": clean_sentence(topic),
            "normalized_query": clean_sentence(topic)
        }

    tokens = [
        token
        for token in _bm25_tokens(lower)
        if token not in _NATURAL_FILLERS
    ]

    fallback = " ".join(tokens)

    return {
        "intent": "search",
        "topic": clean_sentence(fallback or lower),
        "normalized_query": clean_sentence(fallback or lower)
    }


def normalize_user_query(query):
    """Public lightweight query-understanding API."""

    parsed = understand_natural_query(
        query
    )

    return {
        "original_query": clean_sentence(query),
        "intent": parsed.get("intent", "search"),
        "topic": parsed.get("topic", ""),
        "normalized_query": parsed.get("normalized_query", ""),
        "answer_language": detect_answer_language(query)
    }


def classify_search_intent(query):
    parsed = understand_natural_query(
        query
    )

    intent = parsed.get(
        "intent",
        "search"
    )

    if intent != "search":
        return intent

    normalized = normalize_text(
        query
    )

    words = set(
        _bm25_tokens(
            normalized
        )
    )

    for label in (
        "comparison",
        "benefits",
        "usage",
        "how",
        "why"
    ):
        if words.intersection(
            _INTENT_QUERY_MARKERS.get(
                label,
                set()
            )
        ):
            return label

    if (
        "what" in words
        or "define" in words
        or "meaning" in words
        or "kya" in words
    ):
        return "definition"

    return "search"


def extract_search_topic(
    query,
    intent=None
):
    """
    Remove common question scaffolding while preserving the main topic.
    """

    original = clean_sentence(
        query
    )

    if not original:
        return ""

    normalized = normalize_text(
        original
    )

    if intent is None:
        intent = classify_search_intent(
            original
        )

    patterns = [
        r"^what\s+is\s+the\s+(.+)$",
        r"^what\s+is\s+(.+)$",
        r"^what\s+are\s+the\s+(.+)$",
        r"^what\s+are\s+(.+)$",
        r"^how\s+does\s+(.+?)\s+work$",
        r"^how\s+do\s+(.+?)\s+work$",
        r"^how\s+can\s+(.+)$",
        r"^why\s+is\s+(.+)$",
        r"^why\s+are\s+(.+)$",
        r"^what\s+are\s+the\s+benefits\s+of\s+(.+)$",
        r"^what\s+are\s+the\s+advantages\s+of\s+(.+)$",
        r"^benefits\s+of\s+(.+)$",
        r"^advantages\s+of\s+(.+)$",
        r"^what\s+is\s+the\s+use\s+of\s+(.+)$",
        r"^what\s+are\s+the\s+uses\s+of\s+(.+)$",
    ]

    for pattern in patterns:

        match = re.match(
            pattern,
            normalized,
            flags=re.IGNORECASE
        )

        if match:
            return clean_sentence(
                match.group(1)
            )

    # Common Hinglish endings.
    normalized = re.sub(
        r"\s+(?:kya\s+hai|kya\s+hain)$",
        "",
        normalized,
        flags=re.IGNORECASE
    )

    normalized = re.sub(
        r"\s+(?:kaise\s+kaam\s+karta\s+hai|kaise\s+kaam\s+karti\s+hai)$",
        "",
        normalized,
        flags=re.IGNORECASE
    )

    normalized = re.sub(
        r"^(?:what|how|why|define|meaning)\s+",
        "",
        normalized,
        flags=re.IGNORECASE
    )

    return clean_sentence(
        normalized
    )


def _intent_query_terms(
    query,
    intent
):
    """
    Topic terms plus a small set of intent terms.
    Topic terms are scored much more strongly than intent terms.
    """

    topic = extract_search_topic(
        query,
        intent
    )

    topic_terms = _bm25_tokens(
        topic
    )

    intent_terms = set(
        _INTENT_QUERY_MARKERS.get(
            intent,
            set()
        )
    )

    return (
        topic,
        topic_terms,
        intent_terms
    )


def _intent_rank_score(
    page,
    query,
    intent
):
    """
    Additional intent-aware score layered on top of BM25.
    """

    topic, topic_terms, intent_terms = _intent_query_terms(
        query,
        intent
    )

    if not topic_terms:
        return 0.0

    title = normalize_text(
        page.get(
            "title",
            ""
        )
    )

    content = normalize_text(
        page.get(
            "content",
            ""
        )
    )

    score = 0.0

    title_tokens = set(
        _bm25_tokens(
            title
        )
    )

    body_tokens = set(
        _bm25_tokens(
            content
        )
    )

    # Every topic token gets a strong score; title hits dominate.
    matched_topic = 0

    for token in topic_terms:

        if token in title_tokens:
            score += 22.0
            matched_topic += 1

        elif token in body_tokens:
            score += 7.0
            matched_topic += 1

    if matched_topic == len(
        topic_terms
    ):
        score += 24.0

    # Exact topic/title phrase is a strong signal.
    normalized_topic = normalize_text(
        topic
    )

    if normalized_topic == title:
        score += 45.0

    elif (
        normalized_topic
        and normalized_topic in title
    ):
        score += 24.0

    # Intent-specific boosts.
    if intent == "usage":

        if any(
            cue in content
            for cue in (
                "used for",
                "uses",
                "usage",
                "applications",
                "application",
                "automate",
                "development",
                "data science",
            )
        ):
            score += 16.0

    elif intent == "benefits":

        if any(
            cue in content
            for cue in (
                "benefit",
                "benefits",
                "advantage",
                "advantages",
                "helps",
                "allows",
                "reduces",
                "improves",
                "useful",
            )
        ):
            score += 16.0

    elif intent == "how":

        if any(
            cue in content
            for cue in (
                "works",
                "working",
                "process",
                "steps",
                "patterns",
                "learns",
                "executes",
            )
        ):
            score += 16.0

    elif intent == "definition":

        if any(
            cue in content
            for cue in (
                "is a",
                "is an",
                "refers to",
                "means",
                "defined as",
            )
        ):
            score += 12.0

    return score


def intent_search_pages(
    query,
    top_k=8
):
    """
    Rank local AMEJA pages using BM25 plus intent-aware relevance.
    """

    pages = []

    for url, title, content in get_all_pages():

        url = str(
            url
        )

        if not (
            url.startswith(
                "http://127.0.0.1:9000/"
            )
            or url.startswith(
                "http://localhost:9000/"
            )
        ):
            continue

        clean_content = _clean_indexed_content(
            content,
            title
        )

        if not clean_content:
            continue

        pages.append({
            "url": url,
            "title": clean_sentence(
                title
            ),
            "content": clean_content,
        })

    if not pages:
        return []

    intent = classify_search_intent(
        query
    )

    bm25_results = _bm25_rank_pages(
        query,
        pages,
        top_k=len(pages)
    )

    scored = []

    for page in bm25_results:

        combined_score = (
            float(
                page.get(
                    "bm25_score",
                    0.0
                )
            )
            + _intent_rank_score(
                page,
                query,
                intent
            )
        )

        item = dict(
            page
        )

        item["intent"] = intent
        item["intent_score"] = combined_score

        scored.append(
            item
        )

    scored.sort(
        key=lambda item: item["intent_score"],
        reverse=True
    )

    return scored[
        :max(
            1,
            int(top_k)
        )
    ]



def bm25_search_pages(
    query,
    top_k=8
):
    """
    Search the local AMEJA corpus with BM25-style relevance ranking.
    """

    pages = []

    for url, title, content in get_all_pages():

        url = str(
            url
        )

        # Only rank AMEJA's local indexed corpus here.
        if not (
            url.startswith(
                "http://127.0.0.1:9000/"
            )
            or url.startswith(
                "http://localhost:9000/"
            )
        ):
            continue

        clean_content = _clean_indexed_content(
            content,
            title
        )

        if not clean_content:
            continue

        pages.append({
            "url": url,
            "title": clean_sentence(
                title
            ),
            "content": clean_content,
        })

    return _bm25_rank_pages(
        query,
        pages,
        top_k=top_k
    )


def smart_local_search(
    query,
    top_k=8
):
    """
    Prefer intent-aware BM25 ranking. Fall back to legacy search when
    ranking is unavailable.
    """

    if ENABLE_INTENT_RANKING:

        ranked = intent_search_pages(
            query,
            top_k=top_k
        )

        if ranked:
            return ranked

    if ENABLE_BM25_RANKING:

        ranked = bm25_search_pages(
            query,
            top_k=top_k
        )

        if ranked:
            return ranked

    return [
        {
            "url": result["url"],
            "title": result["title"],
            "content": (
                next(
                    (
                        content
                        for url, title, content
                        in get_all_pages()
                        if str(url).lower()
                        == str(result["url"]).lower()
                    ),
                    ""
                )
            ),
            "score": result.get(
                "score",
                0
            )
        }
        for result in search_pages(
            query
        )[:top_k]
    ]




# Experimental semantic answer composition is disabled by default.
ENABLE_SEMANTIC_ANSWERER = False


# ============================================================
# NATURAL ANSWER FORMATTER v1
# ============================================================

ENABLE_NATURAL_ANSWER_FORMATTER = True


def _natural_hinglish_rewrite(
    answer,
    topic,
    intent
):
    """
    Convert a few common indexed sentence patterns into natural Hinglish.
    This is deliberately pattern-based so it stays lightweight and safe.
    """

    value = clean_sentence(
        answer
    )

    if not value:
        return value

    topic = clean_sentence(
        topic
    )

    lower = value.lower()

    # Data-analysis/how pattern.
    if (
        intent == "how"
        and "python for data science" in lower
        and (
            "data analysis" in lower
            or "data cleaning" in lower
        )
    ):
        return (
            "Python data analysis ke liye widely use hoti hai. "
            "Isse data cleaning, visualization, statistics aur "
            "machine learning jaise tasks kiye ja sakte hain."
        )

    # Usage question for Python.
    if (
        intent == "usage"
        and topic.lower() == "python"
    ):
        return (
            "Python web development, automation, data science "
            "aur artificial intelligence jaise areas mein use hoti hai."
        )

    # Benefits question for Python.
    if (
        intent == "benefits"
        and topic.lower() == "python"
    ):
        return (
            "Python easy to learn hai aur web development, automation, "
            "data science aur artificial intelligence jaise areas mein "
            "widely use hoti hai."
        )

    return value


def format_answer_for_user(
    query,
    result
):
    """
    Final presentation layer. It only rewrites answers when a known,
    safe pattern matches; otherwise the original answer is preserved.
    """

    if not ENABLE_NATURAL_ANSWER_FORMATTER:
        return result

    answer = result.get(
        "answer"
    )

    if not answer:
        return result

    language = result.get(
        "answer_language",
        "english"
    )

    if language != "hinglish":
        return result

    parsed = None

    if (
        "understand_natural_query"
        in globals()
    ):
        parsed = understand_natural_query(
            query
        )

    topic = clean_sentence(
        (
            parsed.get(
                "topic",
                ""
            )
            if parsed
            else result.get(
                "understood_query",
                ""
            )
        )
    )

    intent = (
        parsed.get(
            "intent",
            result.get(
                "question_type",
                "search"
            )
        )
        if parsed
        else result.get(
            "question_type",
            "search"
        )
    )

    formatted = _natural_hinglish_rewrite(
        answer,
        topic,
        intent
    )

    updated = dict(
        result
    )

    updated["answer"] = formatted

    return updated




# ============================================================
# COMPARISON ENGINE v1
# ============================================================

ENABLE_COMPARISON_ENGINE = True


def _comparison_parts(query):
    """Extract two comparison topics from English comparison phrasing."""

    parsed = (
        understand_natural_query(query)
        if "understand_natural_query" in globals()
        else {}
    )

    topic = clean_sentence(
        parsed.get(
            "topic",
            ""
        )
    )

    raw = normalize_text(
        clean_sentence(query)
    )

    candidates = [
        topic,
        raw,
    ]

    patterns = [
        r"^(.+?)\s+vs\s+(.+)$",
        r"^(.+?)\s+versus\s+(.+)$",
        r"^what\s+is\s+the\s+difference\s+between\s+(.+?)\s+and\s+(.+)$",
    ]

    for candidate in candidates:

        if not candidate:
            continue

        for pattern in patterns:

            match = re.match(
                pattern,
                candidate,
                flags=re.IGNORECASE
            )

            if not match:
                continue

            left = clean_sentence(
                match.group(1)
            )

            right = clean_sentence(
                match.group(2)
            )

            if (
                left
                and right
                and normalize_text(left)
                != normalize_text(right)
            ):
                return (
                    left,
                    right
                )

    return None



def _comparison_page(topic):
    """
    Find an exact or strong local title match.
    Never return a vaguely related page.
    """

    results = intent_search_pages(
        topic,
        top_k=8
    )

    if not results:
        return None

    normalized = normalize_text(
        topic
    )

    for result in results:

        if normalize_text(
            result.get(
                "title",
                ""
            )
        ) == normalized:
            return result

    topic_words = set(
        _bm25_tokens(
            topic
        )
    )

    if not topic_words:
        return None

    for result in results:

        title_words = set(
            _bm25_tokens(
                result.get(
                    "title",
                    ""
                )
            )
        )

        if topic_words.issubset(
            title_words
        ):
            return result

    if len(topic_words) == 1:

        token = next(
            iter(topic_words)
        )

        for result in results:

            title_words = set(
                _bm25_tokens(
                    result.get(
                        "title",
                        ""
                    )
                )
            )

            if token in title_words:
                return result

    return None




def _comparison_external_page(topic):
    """
    Resolve a missing comparison side through Wikipedia with lightweight
    entity disambiguation for common ambiguous technology names.
    """

    if "wikipedia_fallback" not in globals():
        return None

    raw_topic = clean_sentence(
        topic
    )

    if not raw_topic:
        return None

    normalized = normalize_text(
        raw_topic
    )

    # Context terms for known ambiguous technology topics.
    technology_context = {
        "java": "Java programming language",
        "c": "C programming language",
        "go": "Go programming language",
        "rust": "Rust programming language",
    }

    lookup_topic = technology_context.get(
        normalized,
        raw_topic
    )

    try:
        summary = wikipedia_fallback(
            lookup_topic
        )
    except Exception:
        return None

    if not summary:
        return None

    title = clean_sentence(
        summary.get(
            "title",
            ""
        )
    )

    extract = clean_sentence(
        summary.get(
            "extract",
            ""
        )
    )

    if not title or not extract:
        return None

    # Explicitly reject obvious unrelated entity resolutions.
    if normalized == "java":

        title_lower = title.lower()

        programming_signals = (
            "programming language",
            "object-oriented",
            "software",
            "class",
            "virtual machine",
            "bytecode",
        )

        if not any(
            signal in (
                title_lower
                + " "
                + extract.lower()
            )
            for signal in programming_signals
        ):
            return None

    return {
        "url": summary.get(
            "url",
            ""
        ),
        "title": title,
        "content": extract,
        "external": True
    }



def _comparison_sentences(
    page,
    topic
):
    """
    Extract one or two complete, topic-relevant sentences.
    """

    if not page:
        return []

    title = clean_sentence(
        page.get(
            "title",
            ""
        )
    )

    content = _clean_indexed_content(
        page.get(
            "content",
            ""
        ),
        title
    )

    if not content:
        return []

    topic_words = set(
        _bm25_tokens(
            topic
        )
    )

    ranked = []

    for position, sentence in enumerate(
        split_sentences(
            content
        )
    ):

        sentence = clean_sentence(
            sentence
        )

        if len(sentence) < 40:
            continue

        if not sentence[:1].isupper():
            continue

        if normalize_text(
            sentence
        ) == normalize_text(
            title
        ):
            continue

        sentence_words = set(
            _bm25_tokens(
                sentence
            )
        )

        overlap = len(
            topic_words
            & sentence_words
        )

        if overlap <= 0:
            continue

        score = (
            overlap * 100
            + max(
                0,
                10 - position
            )
        )

        if 50 <= len(sentence) <= 240:
            score += 20

        ranked.append(
            (
                score,
                sentence
            )
        )

    ranked.sort(
        reverse=True
    )

    result = []

    for _score, sentence in ranked:

        if any(
            _sentence_similarity(
                sentence,
                old
            ) >= 0.60
            for old in result
        ):
            continue

        result.append(
            sentence
        )

        if len(result) >= 2:
            break

    return result



def diagnose_comparison_query(
    query
):
    """
    Lightweight diagnostic for comparison routing and entity resolution.
    """

    parts = _comparison_parts(
        query
    )

    result = {
        "query": clean_sentence(
            query
        ),
        "intent": classify_search_intent(
            query
        ),
        "parts": parts,
        "left_source": None,
        "right_source": None,
    }

    if not parts:
        return result

    left, right = parts

    left_page = _comparison_page(
        left
    )

    right_page = _comparison_page(
        right
    )

    if left_page:
        result["left_source"] = {
            "title": left_page.get(
                "title"
            ),
            "url": left_page.get(
                "url"
            ),
            "source": "local"
        }
    else:
        left_external = _comparison_external_page(
            left
        )
        if left_external:
            result["left_source"] = {
                "title": left_external.get(
                    "title"
                ),
                "url": left_external.get(
                    "url"
                ),
                "source": "wikipedia"
            }

    if right_page:
        result["right_source"] = {
            "title": right_page.get(
                "title"
            ),
            "url": right_page.get(
                "url"
            ),
            "source": "local"
        }
    else:
        right_external = _comparison_external_page(
            right
        )
        if right_external:
            result["right_source"] = {
                "title": right_external.get(
                    "title"
                ),
                "url": right_external.get(
                    "url"
                ),
                "source": "wikipedia"
            }

    return result




# ============================================================
# RICHER COMPARISON FORMATTER v1
# ============================================================

ENABLE_RICHER_COMPARISON = True


def _comparison_feature_sentences(
    page,
    topic
):
    """
    Collect up to three distinct, complete sentences that are useful for
    comparison. Prefer sentences containing concrete distinguishing terms.
    """

    if not page:
        return []

    title = clean_sentence(
        page.get(
            "title",
            ""
        )
    )

    content = _clean_indexed_content(
        page.get(
            "content",
            ""
        ),
        title
    )

    if not content:
        return []

    topic_words = set(
        _bm25_tokens(
            topic
        )
    )

    feature_terms = {
        "language",
        "programming",
        "type",
        "typed",
        "interpreted",
        "compiled",
        "object-oriented",
        "general-purpose",
        "automation",
        "data",
        "web",
        "development",
        "machine",
        "learning",
        "software",
        "applications",
        "platform",
        "runtime",
        "memory",
        "syntax",
        "class",
        "classes",
        "performance",
        "security",
        "scalable",
    }

    ranked = []

    for position, sentence in enumerate(
        split_sentences(
            content
        )
    ):

        sentence = clean_sentence(
            sentence
        )

        if len(sentence) < 45:
            continue

        if not sentence[:1].isupper():
            continue

        sentence_words = set(
            _bm25_tokens(
                sentence
            )
        )

        overlap = len(
            topic_words
            & sentence_words
        )

        if overlap <= 0:
            continue

        feature_hits = len(
            feature_terms
            & sentence_words
        )

        score = (
            overlap * 70
            + feature_hits * 12
            + max(
                0,
                12 - position
            )
        )

        if 50 <= len(sentence) <= 240:
            score += 15

        ranked.append(
            (
                score,
                sentence
            )
        )

    ranked.sort(
        reverse=True
    )

    selected = []

    for _score, sentence in ranked:

        if any(
            _sentence_similarity(
                sentence,
                old
            ) >= 0.55
            for old in selected
        ):
            continue

        selected.append(
            sentence
        )

        if len(selected) >= 3:
            break

    return selected


def _comparison_summary_line(
    left_title,
    right_title
):
    """
    Add a neutral selection hint only when the comparison is obviously
    between the two grounded technology pages.
    """

    left = normalize_text(
        left_title
    )
    right = normalize_text(
        right_title
    )

    if (
        "python" in left
        and "java" in right
    ) or (
        "python" in right
        and "java" in left
    ):
        return (
            "Beginner-friendly scripting and data work generally favor "
            "Python, while Java is widely used for large application and "
            "enterprise software development."
        )

    return ""



def build_comparison_answer(query):
    """
    Build a compact, structured comparison from two grounded entities.
    """

    if not ENABLE_COMPARISON_ENGINE:
        return None

    parts = _comparison_parts(
        query
    )

    if not parts:
        return None

    left_topic, right_topic = parts

    left_page = _comparison_page(
        left_topic
    )

    right_page = _comparison_page(
        right_topic
    )

    if left_page is None:
        left_page = _comparison_external_page(
            left_topic
        )

    if right_page is None:
        right_page = _comparison_external_page(
            right_topic
        )

    if not left_page or not right_page:
        return None

    left_sentences = _comparison_feature_sentences(
        left_page,
        left_topic
    )

    right_sentences = _comparison_feature_sentences(
        right_page,
        right_topic
    )

    if not left_sentences or not right_sentences:
        return None

    left_title = clean_sentence(
        left_page.get(
            "title",
            left_topic
        )
    )

    right_title = clean_sentence(
        right_page.get(
            "title",
            right_topic
        )
    )

    language = detect_answer_language(
        query
    )

    # One primary sentence plus one distinguishing sentence per side.
    left_parts = left_sentences[:2]
    right_parts = right_sentences[:2]

    answer = (
        f"{left_title}: "
        + " ".join(left_parts)
        + " "
        f"{right_title}: "
        + " ".join(right_parts)
    )

    # Keep comparison answers lightweight.
    if len(answer) > 900:
        answer = clean_sentence(
            (
                f"{left_title}: "
                + left_sentences[0]
                + " "
                f"{right_title}: "
                + right_sentences[0]
            )
        )

    summary_line = _comparison_summary_line(
        left_title,
        right_title
    )

    # Only add a canned summary for the known, explicitly grounded
    # Python-vs-Java pairing. Other comparisons remain purely sourced.
    if (
        ENABLE_RICHER_COMPARISON
        and summary_line
    ):
        answer = (
            answer
            + " "
            + summary_line
        )

    return {
        "answer": clean_sentence(
            answer
        ),
        "title": (
            f"{left_title} vs {right_title}"
        ),
        "url": left_page.get(
            "url",
            ""
        ),
        "sources": [
            {
                "title": left_title,
                "url": left_page.get(
                    "url",
                    ""
                )
            },
            {
                "title": right_title,
                "url": right_page.get(
                    "url",
                    ""
                )
            }
        ],
        "confidence": 84,
        "understood_query": (
            f"{left_topic} vs {right_topic}"
        ),
        "answer_language": language,
        "question_type": "comparison",
        "answer_source": "ameja_comparison"
    }



def _build_answer_with_sources_core(query):
    """
    AMEJA Smart Query Router v43.

    Local index -> intent-aware Wikipedia -> lightweight web.
    """

    if (
        ENABLE_COMPARISON_ENGINE
        and "understand_natural_query" in globals()
        and classify_search_intent(query) == "comparison"
    ):

        comparison_result = build_comparison_answer(
            query
        )

        if comparison_result:
            return comparison_result

    local = build_answer(query)

    if local.get("answer"):

        local["answer"] = (
            _clean_local_answer_title_duplication(
                local.get(
                    "answer",
                    ""
                ),
                local.get(
                    "title",
                    ""
                )
            )
        )

        # Never return an empty local answer after cleanup.
        if not local.get("answer"):
            return local

        local["answer_source"] = "ameja_index"

        # Use the semantic answerer for explicit natural-language
        # how/why/explain questions. Definitions remain primary-only.
        if (
            ENABLE_SEMANTIC_ANSWERER
            and local.get("question_type") in {
                "how",
                "why",
                "explain"
            }
            and "?" in str(query)
        ):
            return semantic_compose_answer(
                query,
                local
            )

        return local

    wiki_query, router_intent = _router_topic(
        query
    )

    detected_type = detect_question_type(
        query
    )

    answer_language = detect_answer_language(
        query
    )

    # Benefits: accept Wikipedia only when a real matching section
    # was found. Otherwise continue to web.
    if (
        router_intent == "benefits"
        and wikipedia_fallback is not None
    ):

        summary = wikipedia_fallback(
            wiki_query,
            intent="benefits"
        )

        if (
            summary
            and summary.get(
                "_intent_section"
            )
        ):

            answer = _compact_wikipedia_answer(
                summary.get(
                    "extract",
                    ""
                ),
                max_sentences=2,
                max_chars=520
            )

            if answer:

                if answer_language == "hinglish":
                    answer = _to_hinglish_wikipedia(
                        answer,
                        wiki_query
                    )

                return {
                    "answer": answer,
                    "title": summary["title"],
                    "url": summary["url"],
                    "sources": [{
                        "title": summary["title"],
                        "url": summary["url"]
                    }],
                    "confidence": 86,
                    "understood_query": wiki_query,
                    "answer_language": answer_language,
                    "question_type": "why",
                    "answer_source": "wikipedia"
                }

    # Direct facts/definitions remain Wikipedia-first.
    if (
        router_intent != "benefits"
        and wikipedia_fallback is not None
        and detected_type in {
            "definition",
            "fact"
        }
    ):

        summary = wikipedia_fallback(
            wiki_query
        )

        if summary:

            answer = _compact_wikipedia_answer(
                summary.get(
                    "extract",
                    ""
                ),
                max_sentences=2,
                max_chars=520
            )

            if answer:

                if answer_language == "hinglish":
                    answer = _to_hinglish_wikipedia(
                        answer,
                        wiki_query
                    )

                return {
                    "answer": answer,
                    "title": summary["title"],
                    "url": summary["url"],
                    "sources": [{
                        "title": summary["title"],
                        "url": summary["url"]
                    }],
                    "confidence": 88,
                    "understood_query": wiki_query,
                    "answer_language": answer_language,
                    "question_type": detected_type,
                    "answer_source": "wikipedia"
                }

    # Web fallback.
    if web_fallback is not None:

        # For benefits, make the web query intent-explicit.
        web_query = (
            wiki_query
            + " benefits advantages"
            if router_intent == "benefits"
            else wiki_query
        )

        web_result = web_fallback(
            web_query
        )

        if web_result:

            answer = clean_sentence(
                web_result.get(
                    "answer",
                    ""
                )
            )

            if answer:

                if answer_language == "hinglish":
                    answer = _simple_hinglish_sentence(
                        answer
                    )

                return {
                    "answer": answer,
                    "title": web_result["title"],
                    "url": web_result["url"],
                    "sources": [{
                        "title": web_result["title"],
                        "url": web_result["url"]
                    }],
                    "confidence": 72,
                    "understood_query": wiki_query,
                    "answer_language": answer_language,
                    "question_type": detected_type,
                    "answer_source": "web"
                }

    return {
        "answer": None,
        "title": None,
        "url": None,
        "sources": [],
        "confidence": 0,
        "understood_query": wiki_query,
        "answer_language": answer_language,
        "question_type": detected_type,
        "answer_source": "none"
    }


# Backward-compatible public name.
def build_answer_with_wikipedia(query):
    return build_answer_with_sources(
        query
    )




# ============================================================
# UNIVERSAL FALLBACK v1
# ============================================================

ENABLE_UNIVERSAL_FALLBACK = True


def _fallback_query_variants(
    query
):
    """
    Produce a tiny set of useful retrieval variants.

    The goal is higher coverage without making lots of network requests.
    """

    original = clean_sentence(
        query
    )

    if not original:
        return []

    variants = []

    def add(value):
        value = clean_sentence(
            value
        )

        if (
            value
            and value.lower()
            not in {
                item.lower()
                for item in variants
            }
        ):
            variants.append(
                value
            )

    add(original)

    if "understand_natural_query" in globals():

        parsed = understand_natural_query(
            original
        )

        topic = clean_sentence(
            parsed.get(
                "topic",
                ""
            )
        )

        intent = parsed.get(
            "intent",
            "search"
        )

        add(topic)

        if topic:

            if intent == "benefits":
                add(
                    topic
                    + " benefits advantages"
                )

            elif intent == "usage":
                add(
                    topic
                    + " uses applications"
                )

            elif intent == "how":
                add(
                    "how "
                    + topic
                    + " works"
                )

            elif intent == "why":
                add(
                    "why "
                    + topic
                )

            elif intent == "definition":
                add(
                    topic
                    + " definition"
                )

    # Keep network calls bounded.
    return variants[:4]


def _fallback_language_answer(
    query,
    answer
):
    """
    Preserve English. For known Hinglish requests, use the existing
    lightweight Wikipedia conversion when available; otherwise keep the
    retrieved sentence unchanged rather than producing broken Hinglish.
    """

    language = detect_answer_language(
        query
    )

    cleaned = clean_sentence(
        answer
    )

    if not cleaned:
        return (
            cleaned,
            language
        )

    if language == "hinglish":
        try:
            if "_to_hinglish_wikipedia" in globals():
                cleaned = _to_hinglish_wikipedia(
                    cleaned,
                    query
                )
        except Exception:
            pass

    return (
        cleaned,
        language
    )


def universal_fallback_answer(
    query
):
    """
    Coverage-first fallback:

        local failed
          -> Wikipedia exact/intent-aware
          -> lightweight web retrieval

    Never invent an answer. Return None when no source produced a usable
    grounded response.
    """

    if not ENABLE_UNIVERSAL_FALLBACK:
        return None

    variants = _fallback_query_variants(
        query
    )

    if not variants:
        return None

    language = detect_answer_language(
        query
    )

    intent = (
        classify_search_intent(
            query
        )
        if "classify_search_intent" in globals()
        else "search"
    )

    # --------------------------------------------------------
    # Wikipedia first for factual / educational questions.
    # --------------------------------------------------------

    if "wikipedia_fallback" in globals():

        parsed_topic = variants[1] if len(
            variants
        ) > 1 else variants[0]

        wiki_intent = (
            "benefits"
            if intent == "benefits"
            else None
        )

        try:
            summary = wikipedia_fallback(
                parsed_topic,
                intent=wiki_intent
            )
        except TypeError:
            try:
                summary = wikipedia_fallback(
                    parsed_topic
                )
            except Exception:
                summary = None
        except Exception:
            summary = None

        if summary:

            extract = clean_sentence(
                summary.get(
                    "extract",
                    ""
                )
            )

            # For intent-aware section results, extract is already the
            # relevant section. For generic facts, it is the article lead.
            answer = _compact_wikipedia_answer(
                extract,
                max_sentences=2,
                max_chars=620
            )

            if answer:

                answer, language = _fallback_language_answer(
                    query,
                    answer
                )

                return {
                    "answer": answer,
                    "title": summary.get(
                        "title",
                        parsed_topic
                    ),
                    "url": summary.get(
                        "url",
                        ""
                    ),
                    "sources": [{
                        "title": summary.get(
                            "title",
                            parsed_topic
                        ),
                        "url": summary.get(
                            "url",
                            ""
                        )
                    }],
                    "confidence": (
                        86
                        if intent != "search"
                        else 82
                    ),
                    "understood_query": parsed_topic,
                    "answer_language": language,
                    "question_type": (
                        intent
                        if intent != "search"
                        else detect_question_type(
                            query
                        )
                    ),
                    "answer_source": "wikipedia"
                }

    # --------------------------------------------------------
    # Lightweight web fallback.
    # Try a maximum of two useful variants.
    # --------------------------------------------------------

    if "web_fallback" in globals():

        for variant in variants[:2]:

            try:
                result = web_fallback(
                    variant
                )
            except Exception:
                result = None

            if not result:
                continue

            answer = clean_sentence(
                result.get(
                    "answer",
                    ""
                )
            )

            if not answer:
                continue

            answer, language = _fallback_language_answer(
                query,
                answer
            )

            return {
                "answer": answer,
                "title": result.get(
                    "title",
                    variant
                ),
                "url": result.get(
                    "url",
                    ""
                ),
                "sources": [{
                    "title": result.get(
                        "title",
                        variant
                    ),
                    "url": result.get(
                        "url",
                        ""
                    )
                }],
                "confidence": 70,
                "understood_query": (
                    variants[1]
                    if len(variants) > 1
                    else variants[0]
                ),
                "answer_language": language,
                "question_type": (
                    intent
                    if intent != "search"
                    else detect_question_type(
                        query
                    )
                ),
                "answer_source": "web"
            }

    return None




# ============================================================
# CONVERSATION CONTEXT v1
# ============================================================

ENABLE_CONVERSATION_CONTEXT = True
CONTEXT_MAX_TURNS = 6


_CONVERSATION_STATE = {
    "turns": []
}


_FOLLOWUP_MARKERS = {
    "he", "him", "his", "she", "her", "they", "them", "their",
    "it", "its", "this", "that", "these", "those",
    "who", "where", "when", "why", "how",
}



def clear_conversation_context():
    """Reset the in-process rolling conversation context."""

    _CONVERSATION_STATE["turns"] = []


def get_conversation_context():
    """Return a copy of the current rolling context for diagnostics."""

    return list(
        _CONVERSATION_STATE.get(
            "turns",
            []
        )
    )



def _context_clean_query(
    query
):
    return clean_sentence(
        query
    )


def _topic_from_result(
    query,
    result
):
    """
    Prefer the engine's understood query/title, then parser topic.
    """

    topic = clean_sentence(
        result.get(
            "understood_query",
            ""
        )
    )

    if topic:
        return topic

    if "understand_natural_query" in globals():

        try:
            parsed = understand_natural_query(
                query
            )

            topic = clean_sentence(
                parsed.get(
                    "topic",
                    ""
                )
            )

            if topic:
                return topic

        except Exception:
            pass

    return clean_sentence(
        result.get(
            "title",
            ""
        )
    )


def _looks_like_followup(
    query
):
    """
    Detect only genuine conversational follow-ups.

    Important: short standalone questions such as "What is gravity?",
    "Who was Gandhi?" or "Python?" must NOT inherit the previous topic.
    Pronouns/referents and explicit continuation phrases are the strongest
    signals.
    """

    normalized = normalize_text(
        query
    )

    if not normalized:
        return False

    words = set(
        _bm25_tokens(
            normalized
        )
    )

    # Explicit pronouns/referents.
    if words.intersection(
        _FOLLOWUP_MARKERS
    ):
        return True

    followup_patterns = (
        "what about",
        "how about",
        "and what",
        "and why",
        "and how",
        "and where",
        "and when",
        "where is he",
        "where was he",
        "when was he",
        "who was he",
        "what did he",
        "what does it",
        "where is it",
        "how does it",
        "why is it",
        "what about it",
        "what about him",
        "what about her",
    )

    return any(
        pattern in normalized
        for pattern in followup_patterns
    )




def _classify_context_entity(
    query,
    result
):
    """Classify a conversation turn as person or concept."""

    q = normalize_text(
        clean_sentence(query)
    )

    if (
        q.startswith("who was ")
        or q.startswith("who is ")
        or q.startswith("who were ")
        or q.startswith("who are ")
        or "kaun the" in q
        or "kaun tha" in q
        or "kaun thi" in q
        or "kaun hai" in q
    ):
        return "person"

    return "concept"


def _pronoun_requires_person(
    query
):
    """Return True when a follow-up pronoun requires a human/person entity."""

    normalized = normalize_text(
        clean_sentence(query)
    )

    words = set(
        _bm25_tokens(
            normalized
        )
    )

    if words.intersection({
        "he",
        "him",
        "his",
        "she",
        "her",
    }):
        return True

    return any(
        pattern in normalized
        for pattern in (
            "who was he",
            "who is he",
            "where was he",
            "when was he",
            "what did he",
            "why is he",
            "why was he",
            "what is he known for",
            "what was he known for",
            "who was she",
            "where was she",
            "when was she",
            "what did she",
            "why is she",
        )
    )


def _last_context_entity(
    require_person=False
):
    """Return the newest compatible conversation entity."""

    turns = globals().get(
        "_CONVERSATION_STATE",
        {}
    ).get(
        "turns",
        []
    )

    for turn in reversed(
        turns
    ):

        entity_type = turn.get(
            "entity_type",
            "concept"
        )

        if (
            require_person
            and entity_type != "person"
        ):
            continue

        topic = clean_sentence(
            turn.get(
                "topic",
                ""
            )
        )

        title = clean_sentence(
            turn.get(
                "title",
                ""
            )
        )

        if topic or title:
            return (
                topic or title,
                title or topic
            )

    return "", ""

def _remember_turn(
    query,
    result,
    resolved_query=""
):
    """Store a successful turn with person/concept type."""

    if not globals().get(
        "ENABLE_CONVERSATION_CONTEXT",
        True
    ):
        return

    if not result or not result.get(
        "answer"
    ):
        return

    effective_query = (
        resolved_query
        or query
    )

    topic = clean_sentence(
        result.get(
            "understood_query",
            ""
        )
    )

    if not topic and "understand_natural_query" in globals():
        try:
            topic = clean_sentence(
                understand_natural_query(
                    effective_query
                ).get(
                    "topic",
                    ""
                )
            )
        except Exception:
            topic = ""

    if not topic:
        topic = clean_sentence(
            result.get(
                "title",
                ""
            )
        )

    entity_type = _classify_context_entity(
        effective_query,
        result
    )

    state = globals().setdefault(
        "_CONVERSATION_STATE",
        {
            "turns": []
        }
    )

    turns = state.setdefault(
        "turns",
        []
    )

    turns.append({
        "query": clean_sentence(
            query
        ),
        "resolved_query": clean_sentence(
            effective_query
        ),
        "topic": topic,
        "title": clean_sentence(
            result.get(
                "title",
                ""
            )
        ),
        "url": result.get(
            "url",
            ""
        ),
        "answer": clean_sentence(
            result.get(
                "answer",
                ""
            )
        ),
        "entity_type": entity_type,
    })

    max_turns = int(
        globals().get(
            "CONTEXT_MAX_TURNS",
            6
        )
    )

    state["turns"] = turns[
        -max_turns:
    ]

def clear_conversation_context():
    """Reset rolling conversation memory."""

    state = globals().setdefault(
        "_CONVERSATION_STATE",
        {
            "turns": []
        }
    )

    state["turns"] = []


def get_conversation_context():
    """Return a copy of the current rolling conversation memory."""

    state = globals().setdefault(
        "_CONVERSATION_STATE",
        {
            "turns": []
        }
    )

    return list(
        state.get(
            "turns",
            []
        )
    )




def _has_unbound_person_pronoun(
    query
):
    """Detect a person pronoun that has no compatible person context."""

    normalized = normalize_text(
        clean_sentence(
            query
        )
    )

    words = set(
        _bm25_tokens(
            normalized
        )
    )

    return bool(
        words.intersection({
            "he",
            "him",
            "his",
            "she",
            "her",
        })
    )


def _has_person_context():
    """Return True when a stored turn is explicitly classified as a person."""

    turns = (
        globals()
        .get(
            "_CONVERSATION_STATE",
            {}
        )
        .get(
            "turns",
            []
        )
    )

    return any(
        turn.get(
            "entity_type"
        ) == "person"
        for turn in turns
    )



def resolve_query_context(
    query
):
    """Resolve genuine follow-ups without inventing an entity."""

    original = _context_clean_query(
        query
    )

    if not original:
        return {
            "original_query": "",
            "resolved_query": "",
            "used_context": False,
            "context_topic": "",
        }

    if not ENABLE_CONVERSATION_CONTEXT:
        return {
            "original_query": original,
            "resolved_query": original,
            "used_context": False,
            "context_topic": "",
        }

    if not _looks_like_followup(
        original
    ):
        return {
            "original_query": original,
            "resolved_query": original,
            "used_context": False,
            "context_topic": "",
        }

    requires_person = _pronoun_requires_person(
        original
    )

    # Critical guard: an unbound "he/she" query is ambiguous and must not
    # be sent to broad Wikipedia retrieval.
    if (
        requires_person
        and not _has_person_context()
    ):
        return {
            "original_query": original,
            "resolved_query": original,
            "used_context": False,
            "context_topic": "",
            "context_ambiguous": True,
        }

    topic, title = _last_context_entity(
        require_person=requires_person
    )

    if not topic and not title:
        return {
            "original_query": original,
            "resolved_query": original,
            "used_context": False,
            "context_topic": "",
            "context_ambiguous": bool(
                requires_person
            ),
        }

    entity = (
        title
        or topic
    )

    rewritten = _contextual_query_rewrite(
        original,
        entity
    )

    return {
        "original_query": original,
        "resolved_query": clean_sentence(
            rewritten
        ),
        "used_context": True,
        "context_topic": entity,
        "context_ambiguous": False,
    }


def _context_entity_aliases(
    title,
    topic
):
    """
    Build a tiny alias set for the previous entity.

    Keep the canonical title first. For normal pages, topic is also useful.
    """

    aliases = []

    for value in (
        title,
        topic
    ):

        value = clean_sentence(
            value
        )

        if (
            value
            and value.lower()
            not in {
                item.lower()
                for item in aliases
            }
        ):
            aliases.append(
                value
            )

    return aliases


def _contextual_query_rewrite(
    original_query,
    context_topic
):
    """
    Rewrite common follow-up questions into entity-anchored retrieval queries.
    """

    original = normalize_text(
        clean_sentence(
            original_query
        )
    )

    entity = clean_sentence(
        context_topic
    )

    if not original or not entity:
        return original_query

    patterns = [
        (
            r"^where\s+was\s+(?:he|she)\s+born$",
            f"{entity} birthplace"
        ),
        (
            r"^when\s+was\s+(?:he|she)\s+born$",
            f"{entity} date of birth"
        ),
        (
            r"^when\s+did\s+(?:he|she)\s+die$",
            f"{entity} date of death"
        ),
        (
            r"^where\s+was\s+(?:he|she)\s+from$",
            f"{entity} place of origin"
        ),
        (
            r"^what\s+did\s+(?:he|she)\s+discover$",
            f"{entity} discoveries"
        ),
        (
            r"^what\s+did\s+(?:he|she)\s+develop$",
            f"{entity} developments contributions"
        ),
        (
            r"^what\s+did\s+(?:he|she)\s+do$",
            f"{entity} biography achievements"
        ),
        (
            r"^what\s+did\s+(?:he|she)\s+contribute$",
            f"{entity} contributions"
        ),
        (
            r"^why\s+is\s+(?:he|she)\s+famous$",
            f"why {entity} is famous"
        ),
        (
            r"^why\s+was\s+(?:he|she)\s+famous$",
            f"why {entity} was famous"
        ),
        (
            r"^what\s+is\s+(?:he|she)\s+known\s+for$",
            f"{entity} known for"
        ),
        (
            r"^what\s+was\s+(?:he|she)\s+known\s+for$",
            f"{entity} known for"
        ),
        (
            r"^what\s+about\s+(?:him|her)$",
            f"{entity} overview"
        ),
        (
            r"^who\s+was\s+(?:he|she)$",
            f"who was {entity}"
        ),
        (
            r"^who\s+is\s+(?:he|she)$",
            f"who is {entity}"
        ),
    ]

    for pattern, replacement in patterns:

        if re.match(
            pattern,
            original,
            flags=re.IGNORECASE
        ):
            return clean_sentence(
                replacement
            )

    generic_patterns = [
        (
            r"\b(?:he|she)\b",
            entity
        ),
        (
            r"\b(?:him|her)\b",
            entity
        ),
        (
            r"\b(?:his|her)\b",
            f"{entity}'s"
        ),
        (
            r"\b(?:it|this|that)\b",
            entity
        ),
        (
            r"\b(?:they|them|their)\b",
            entity
        ),
    ]

    rewritten = original

    for pattern, replacement in generic_patterns:

        candidate = re.sub(
            pattern,
            replacement,
            rewritten,
            flags=re.IGNORECASE
        )

        if candidate != rewritten:
            rewritten = candidate

    if rewritten == original:

        stripped_words = set(
            _bm25_tokens(
                rewritten
            )
        )

        if len(stripped_words) <= 5:
            rewritten = (
                entity
                + " "
                + rewritten
            )

    return clean_sentence(
        rewritten
    )



def _wikipedia_entity_locked_summary(
    entity,
    query
):
    """
    Resolve a contextual follow-up strictly against the canonical entity.
    """

    canonical = clean_sentence(
        entity
    )

    if not canonical:
        return None

    # Exact canonical page first.
    summary = _canonical_wikipedia_summary(
        canonical
    )

    if summary:
        return summary

    # Final fallback to the existing implementation, but keep an exact-title
    # guard so related entities are never accepted.
    if "wikipedia_fallback" not in globals():
        return None

    try:
        summary = wikipedia_fallback(
            canonical
        )
    except Exception:
        return None

    if not summary:
        return None

    actual_title = normalize_text(
        summary.get(
            "title",
            ""
        )
    )

    wanted_title = normalize_text(
        canonical
    )

    if actual_title != wanted_title:
        return None

    return summary





def _wikipedia_infobox_fact(
    entity,
    field
):
    """
    Fetch one exact infobox field from the canonical Wikipedia page.

    Uses the free MediaWiki API and performs no broad search. This is only
    used for narrow contextual facts where the lead summary is insufficient.
    """

    try:
        import requests
        from urllib.parse import quote

        title = clean_sentence(
            entity
        )

        if not title:
            return None

        api_url = (
            "https://en.wikipedia.org/w/api.php"
            "?action=query"
            "&prop=revisions"
            "&rvprop=content"
            "&rvslots=main"
            "&formatversion=2"
            "&format=json"
            "&titles="
            + quote(
                title,
                safe=""
            )
        )

        response = requests.get(
            api_url,
            timeout=7,
            headers={
                "User-Agent": (
                    "AMEJA/1.0 "
                    "(lightweight-answer-engine)"
                )
            }
        )

        if response.status_code != 200:
            return None

        data = response.json()

        pages = data.get(
            "query",
            {}
        ).get(
            "pages",
            []
        )

        if not pages:
            return None

        revisions = pages[0].get(
            "revisions",
            []
        )

        if not revisions:
            return None

        slots = revisions[0].get(
            "slots",
            {}
        )

        content = (
            slots.get(
                "main",
                {}
            ).get(
                "content",
                ""
            )
        )

        if not content:
            return None

        aliases = {
            "birthplace": (
                "birth_place",
                "birthplace",
                "place_of_birth"
            ),
            "date_of_birth": (
                "birth_date",
                "birthdate",
                "date_of_birth"
            ),
            "date_of_death": (
                "death_date",
                "deathdate",
                "date_of_death"
            ),
        }

        fields = aliases.get(
            field,
            (field,)
        )

        for field_name in fields:

            match = re.search(
                r"\|\s*"
                + re.escape(field_name)
                + r"\s*=\s*(.+?)(?=\n\||\n\}\})",
                content,
                flags=re.IGNORECASE
                | re.DOTALL
            )

            if not match:
                continue

            value = match.group(1)

            # Convert common date templates before removing templates.
            value = re.sub(
                r"\{\{\s*Birth date\s*\|\s*(\d{3,4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})[^{}]*\}\}",
                lambda match: (
                    f"{match.group(1)}-{int(match.group(2)):02d}-"
                    f"{int(match.group(3)):02d}"
                ),
                value,
                flags=re.IGNORECASE
            )

            value = re.sub(
                r"\{\{\s*Birth date and age\s*\|\s*(\d{3,4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})[^{}]*\}\}",
                lambda match: (
                    f"{match.group(1)}-{int(match.group(2)):02d}-"
                    f"{int(match.group(3)):02d}"
                ),
                value,
                flags=re.IGNORECASE
            )

            # Remove remaining wiki markup.
            value = re.sub(
                r"\{\{[^{}]*\}\}",
                " ",
                value
            )

            value = re.sub(
                r"\[\[([^|\]]+)\|([^\]]+)\]\]",
                r"\2",
                value
            )

            value = re.sub(
                r"\[\[([^\]]+)\]\]",
                r"\1",
                value
            )

            value = re.sub(
                r"<ref[^>]*>.*?</ref>",
                " ",
                value,
                flags=re.IGNORECASE
                | re.DOTALL
            )

            value = re.sub(
                r"<[^>]+>",
                " ",
                value
            )

            try:
                from html import unescape
                value = unescape(value)
            except Exception:
                pass

            value = re.sub(
                r"\s+",
                " ",
                value
            ).strip()

            value = clean_sentence(
                value
            )

            if value:
                return value

    except Exception:
        return None

    return None




def _wikidata_fact(
    entity,
    property_id
):
    """
    Resolve one structured biographical fact from Wikidata.

    This is used as a narrow fallback when Wikipedia's infobox template
    cannot be parsed reliably. It does not perform broad web search.
    """

    try:
        import requests
        from urllib.parse import quote

        title = clean_sentence(
            entity
        )

        if not title:
            return None

        # Find the Wikidata entity linked from the exact Wikipedia article.
        sitelink_url = (
            "https://www.wikidata.org/w/api.php"
            "?action=wbgetentities"
            "&sites=enwiki"
            "&titles="
            + quote(
                title,
                safe=""
            )
            + "&props=info"
            + "&format=json"
        )

        response = requests.get(
            sitelink_url,
            timeout=7,
            headers={
                "User-Agent": (
                    "AMEJA/1.0 "
                    "(lightweight-answer-engine)"
                )
            }
        )

        if response.status_code != 200:
            return None

        data = response.json()
        entities = data.get(
            "entities",
            {}
        )

        entity_id = None

        for key, item in entities.items():

            if key.startswith(
                "Q"
            ) and not item.get(
                "missing"
            ):
                entity_id = key
                break

        if not entity_id:
            return None

        claims_url = (
            "https://www.wikidata.org/w/api.php"
            "?action=wbgetclaims"
            "&entity="
            + quote(
                entity_id,
                safe=""
            )
            + "&property="
            + quote(
                property_id,
                safe=""
            )
            + "&format=json"
        )

        claims_response = requests.get(
            claims_url,
            timeout=7,
            headers={
                "User-Agent": (
                    "AMEJA/1.0 "
                    "(lightweight-answer-engine)"
                )
            }
        )

        if claims_response.status_code != 200:
            return None

        claims_data = claims_response.json()
        claims = claims_data.get(
            "claims",
            {}
        ).get(
            property_id,
            []
        )

        if not claims:
            return None

        mainsnak = claims[0].get(
            "mainsnak",
            {}
        )

        datavalue = mainsnak.get(
            "datavalue"
        )

        if not datavalue:
            return None

        value = datavalue.get(
            "value"
        )

        # Date properties (P569/P570) have a time string such as:
        # +1879-03-14T00:00:00Z
        if property_id in {
            "P569",
            "P570"
        }:

            if isinstance(
                value,
                dict
            ):
                time_value = value.get(
                    "time",
                    ""
                )

                match = re.match(
                    r"[+-](\d{3,4})-(\d{2})-(\d{2})",
                    str(
                        time_value
                    )
                )

                if match:
                    year = match.group(1)
                    month = int(
                        match.group(2)
                    )
                    day = int(
                        match.group(3)
                    )

                    months = [
                        "",
                        "January",
                        "February",
                        "March",
                        "April",
                        "May",
                        "June",
                        "July",
                        "August",
                        "September",
                        "October",
                        "November",
                        "December",
                    ]

                    return (
                        f"{day} "
                        f"{months[month]} "
                        f"{year}"
                    )

        # Place of birth (P19) is an entity ID. Resolve its English label.
        if property_id == "P19":

            if not isinstance(
                value,
                dict
            ):
                return None

            place_id = value.get(
                "id",
                ""
            )

            if not str(
                place_id
            ).startswith(
                "Q"
            ):
                return None

            label_url = (
                "https://www.wikidata.org/w/api.php"
                "?action=wbgetentities"
                "&ids="
                + quote(
                    place_id,
                    safe=""
                )
                + "&props=labels"
                + "&languages=en"
                + "&format=json"
            )

            label_response = requests.get(
                label_url,
                timeout=7,
                headers={
                    "User-Agent": (
                        "AMEJA/1.0 "
                        "(lightweight-answer-engine)"
                    )
                }
            )

            if label_response.status_code != 200:
                return None

            label_data = label_response.json()

            item = label_data.get(
                "entities",
                {}
            ).get(
                place_id,
                {}
            )

            label = (
                item.get(
                    "labels",
                    {}
                )
                .get(
                    "en",
                    {}
                )
                .get(
                    "value",
                    ""
                )
            )

            return clean_sentence(
                label
            ) or None

    except Exception:
        return None

    return None




def _canonical_wikipedia_summary(
    entity
):
    """
    Resolve a canonical Wikipedia article by exact title.

    Prefer the existing wikipedia_summary() helper when available;
    otherwise use the free Wikipedia REST summary endpoint directly.
    """

    try:
        import requests
        from urllib.parse import quote

        title = clean_sentence(
            entity
        )

        if not title:
            return None

        # First use the project's existing helper if it exists.
        if "wikipedia_summary" in globals():

            try:
                summary = wikipedia_summary(
                    title
                )
            except Exception:
                summary = None

            if summary:

                actual_title = clean_sentence(
                    summary.get(
                        "title",
                        ""
                    )
                )

                if (
                    normalize_text(actual_title)
                    == normalize_text(title)
                ):
                    return summary

        encoded = quote(
            title.replace(
                " ",
                "_"
            ),
            safe="_()'-"
        )

        url = (
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            + encoded
        )

        response = requests.get(
            url,
            timeout=7,
            headers={
                "User-Agent": (
                    "AMEJA/1.0 "
                    "(lightweight-answer-engine)"
                )
            }
        )

        if response.status_code != 200:
            return None

        data = response.json()

        actual_title = clean_sentence(
            data.get(
                "title",
                ""
            )
        )

        if (
            normalize_text(actual_title)
            != normalize_text(title)
        ):
            return None

        extract = clean_sentence(
            data.get(
                "extract",
                ""
            )
        )

        if not extract:
            return None

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

        return {
            "title": actual_title,
            "extract": extract,
            "url": page_url or (
                "https://en.wikipedia.org/wiki/"
                + encoded
            )
        }

    except Exception:
        return None



def _contextual_fact_from_wikipedia(
    entity,
    effective_query
):
    """
    Extract a narrow fact from the canonical Wikipedia entity.

    The function prefers structured Wikidata facts for dates/places and
    complete Wikipedia sentences for knowledge-oriented follow-ups.
    """

    summary = _canonical_wikipedia_summary(
        entity
    )

    if not summary:
        return None

    title = clean_sentence(
        summary.get(
            "title",
            entity
        )
    )

    extract = clean_sentence(
        summary.get(
            "extract",
            ""
        )
    )

    if not title:
        return None

    normalized = normalize_text(
        effective_query
    )

    def make_result(
        answer,
        confidence=90
    ):
        return {
            "answer": clean_sentence(
                answer
            ),
            "title": title,
            "url": summary.get(
                "url",
                ""
            ),
            "sources": [{
                "title": title,
                "url": summary.get(
                    "url",
                    ""
                )
            }],
            "confidence": confidence
        }

    # --------------------------------------------------------
    # Birthplace
    # --------------------------------------------------------

    if (
        "birthplace" in normalized
        or "place of birth" in normalized
    ):

        value = _wikidata_fact(
            title,
            "P19"
        )

        if not value:
            value = _wikipedia_infobox_fact(
                title,
                "birthplace"
            )

        if value:
            return make_result(
                f"{title} was born in {value}.",
                confidence=93
            )

        if extract:

            for pattern in (
                r"\bwas born in ([^.;]+)",
                r"\bwas born at ([^.;]+)",
                r"\bborn in ([^.;]+)",
                r"\bborn at ([^.;]+)",
            ):

                match = re.search(
                    pattern,
                    extract,
                    flags=re.IGNORECASE
                )

                if not match:
                    continue

                place = clean_sentence(
                    match.group(1)
                )

                place = re.split(
                    r"\s+(?:and|but|where|which|who)\b",
                    place,
                    maxsplit=1,
                    flags=re.IGNORECASE
                )[0].strip()

                if place:
                    return make_result(
                        f"{title} was born in {place}."
                    )

    # --------------------------------------------------------
    # Date of birth
    # --------------------------------------------------------

    if (
        "date of birth" in normalized
        or "birth date" in normalized
        or "date born" in normalized
        or (
            "when was" in normalized
            and "born" in normalized
        )
    ):

        value = _wikidata_fact(
            title,
            "P569"
        )

        if not value:
            value = _wikipedia_infobox_fact(
                title,
                "date_of_birth"
            )

        if value:
            return make_result(
                f"{title} was born on {value}.",
                confidence=93
            )

        if extract:

            match = re.search(
                r"\bborn\s+on\s+([^.;]+)",
                extract,
                flags=re.IGNORECASE
            )

            if match:

                date_text = clean_sentence(
                    match.group(1)
                )

                if date_text:
                    return make_result(
                        f"{title} was born on {date_text}."
                    )

    # --------------------------------------------------------
    # Date of death
    # --------------------------------------------------------

    if (
        "date of death" in normalized
        or (
            "when did" in normalized
            and "die" in normalized
        )
    ):

        value = _wikidata_fact(
            title,
            "P570"
        )

        if not value:
            value = _wikipedia_infobox_fact(
                title,
                "date_of_death"
            )

        if value:
            return make_result(
                f"{title} died on {value}.",
                confidence=93
            )

        if extract:

            match = re.search(
                r"\bdied\s+(?:on|in)\s+([^.;]+)",
                extract,
                flags=re.IGNORECASE
            )

            if match:

                death_text = clean_sentence(
                    match.group(1)
                )

                if death_text:
                    return make_result(
                        f"{title} died {death_text}."
                    )

    # --------------------------------------------------------
    # Why is he/she famous? / known for
    # --------------------------------------------------------

    if (
        "known for" in normalized
        or "why is" in normalized
        or "why was" in normalized
        or "famous" in normalized
    ) and extract:

        sentences = [
            clean_sentence(
                sentence
            )
            for sentence in split_sentences(
                extract
            )
        ]

        preferred = []

        for sentence in sentences:

            lower = sentence.lower()

            if any(
                cue in lower
                for cue in (
                    "best known for",
                    "known for",
                    "famous for",
                    "developed",
                    "discovered",
                    "theory of relativity",
                    "photoelectric",
                    "quantum",
                    "contribution",
                )
            ):
                preferred.append(
                    sentence
                )

        if preferred:
            return make_result(
                preferred[0],
                confidence=90
            )

        if sentences:
            return make_result(
                sentences[0],
                confidence=86
            )

    # --------------------------------------------------------
    # Discoveries / contributions / achievements
    # --------------------------------------------------------

    if (
        "discoveries" in normalized
        or "discovery" in normalized
        or "contributions" in normalized
        or "contribution" in normalized
        or "achievements" in normalized
        or "what did" in normalized
    ) and extract:

        sentences = [
            clean_sentence(
                sentence
            )
            for sentence in split_sentences(
                extract
            )
        ]

        candidates = []

        for sentence in sentences:

            lower = sentence.lower()

            score = 0

            for cue in (
                "developed",
                "discovered",
                "developing",
                "discovery",
                "contribution",
                "contributions",
                "theory",
                "relativity",
                "photoelectric",
                "quantum",
                "equation",
                "nobel prize",
            ):

                if cue in lower:
                    score += 1

            if score:
                candidates.append(
                    (
                        score,
                        sentence
                    )
                )

        if candidates:

            candidates.sort(
                key=lambda item: item[0],
                reverse=True
            )

            return make_result(
                candidates[0][1],
                confidence=90
            )

    return None




# ============================================================
# ANSWER QUALITY GATE v1
# ============================================================

ENABLE_ANSWER_QUALITY_GATE = True
ANSWER_MAX_SENTENCES = 3
ANSWER_MAX_CHARS = 720


def _source_content_by_url(
    url
):
    """
    Return indexed local content for a URL, if available.
    """

    target = str(
        url or ""
    ).lower()

    if not target:
        return ""

    try:
        for page_url, title, content in get_all_pages():

            if str(
                page_url
            ).lower() == target:

                return _strip_page_heading(
                    content,
                    title
                )

    except Exception:
        pass

    return ""


def _quality_query_terms(
    query,
    result
):
    """
    Prefer the engine's understood query, then natural parser topic.
    """

    topic = clean_sentence(
        result.get(
            "understood_query",
            ""
        )
    )

    if not topic and "understand_natural_query" in globals():
        try:
            topic = clean_sentence(
                understand_natural_query(
                    query
                ).get(
                    "topic",
                    ""
                )
            )
        except Exception:
            topic = ""

    if not topic:
        topic = clean_sentence(
            query
        )

    return topic


def _quality_rebuild_local_answer(
    query,
    result
):
    """
    Rebuild a weak local answer from complete relevant sentences on the
    selected page. This never pulls unrelated pages.
    """

    url = result.get(
        "url",
        ""
    )

    content = _source_content_by_url(
        url
    )

    if not content:
        return result

    question_type = result.get(
        "question_type",
        detect_question_type(query)
    )

    core_query = _quality_query_terms(
        query,
        result
    )

    candidates = _answer_sentence_candidates(
        content,
        core_query,
        question_type,
        page_title=result.get(
            "title",
            ""
        ),
        limit=6
    )

    candidates = _dedupe_sentences(
        candidates
    )

    if not candidates:
        return result

    # For short/direct questions, keep the strongest complete sentence.
    # For how/why/benefits/usage/explain, use up to two distinct facts.
    if question_type in {
        "how",
        "why",
        "benefits",
        "usage",
        "explain"
    }:
        selected = candidates[:2]
    else:
        selected = candidates[:1]

    answer = clean_sentence(
        " ".join(selected)
    )

    if not answer:
        return result

    if len(answer) > ANSWER_MAX_CHARS:
        answer = (
            answer[
                :ANSWER_MAX_CHARS - 3
            ]
            .rsplit(
                " ",
                1
            )[0]
            .rstrip()
            + "..."
        )

    improved = dict(
        result
    )

    improved["answer"] = answer

    return improved


def _quality_clean_answer(
    query,
    result
):
    """
    Final deterministic hygiene:
      - complete sentences only
      - remove duplicates
      - remove title-prefix repetition
      - keep answer short
    """

    answer = clean_sentence(
        result.get(
            "answer",
            ""
        )
    )

    if not answer:
        return result

    title = clean_sentence(
        result.get(
            "title",
            ""
        )
    )

    # Remove an accidental "Title: Title: ..." prefix while preserving
    # legitimate comparison labels.
    if (
        result.get(
            "question_type"
        ) != "comparison"
        and title
    ):

        prefix = (
            title
            + ": "
        )

        if answer.lower().startswith(
            prefix.lower()
        ):
            answer = answer[
                len(prefix):
            ].strip()

        if answer.lower().startswith(
            (title + " ").lower()
        ):
            # Only strip this when a second copy of the title is followed
            # by normal prose.
            remainder = answer[
                len(title):
            ].strip()

            if (
                remainder
                and remainder[:1].isupper()
            ):
                answer = remainder

    sentences = [
        clean_sentence(
            sentence
        )
        for sentence in split_sentences(
            answer
        )
        if clean_sentence(
            sentence
        )
    ]

    sentences = _dedupe_sentences(
        sentences
    )

    if not sentences:
        return result

    # Never let an answer explode into a long corpus dump.
    sentences = sentences[
        :ANSWER_MAX_SENTENCES
    ]

    answer = " ".join(
        sentences
    )

    if len(answer) > ANSWER_MAX_CHARS:

        answer = (
            answer[
                :ANSWER_MAX_CHARS - 3
            ]
            .rsplit(
                " ",
                1
            )[0]
            .rstrip()
            + "..."
        )

    updated = dict(
        result
    )

    updated["answer"] = answer

    return updated



# ============================================================
# HINGLISH ANSWER CONSISTENCY v1
# ============================================================

ENABLE_HINGLISH_ANSWER_CONSISTENCY = True


def _local_hinglish_answer(
    query,
    result
):
    """
    Convert only a few safe, high-confidence local answer patterns into
    natural Hinglish. Unknown patterns remain untouched.
    """

    answer = clean_sentence(
        result.get(
            "answer",
            ""
        )
    )

    if not answer:
        return result

    language = result.get(
        "answer_language",
        detect_answer_language(
            query
        )
    )

    if language != "hinglish":
        return result

    intent = result.get(
        "question_type",
        detect_question_type(
            query
        )
    )

    topic = clean_sentence(
        result.get(
            "understood_query",
            ""
        )
    ).lower()

    # Known Python local-content patterns.
    if topic == "python":

        if intent == "usage":
            answer = (
                "Python web development, automation, data science "
                "aur artificial intelligence jaise areas mein use hoti hai."
            )

        elif intent == "benefits":
            answer = (
                "Python easy to learn hai aur web development, "
                "automation, data science aur artificial intelligence "
                "jaise areas mein widely use hoti hai."
            )

    # Python + data analysis is a known local educational pattern.
    if (
        "python data analysis" in topic
        and intent == "how"
    ):
        answer = (
            "Python data analysis ke liye widely use hoti hai. "
            "Isse data cleaning, visualization, statistics aur "
            "machine learning jaise tasks kiye ja sakte hain."
        )

    updated = dict(
        result
    )

    updated["answer"] = answer

    return updated



def optimize_answer_result(
    query,
    result
):
    """
    Apply conservative answer-quality checks after retrieval/fallback.

    The source and factual basis remain unchanged.
    """

    if not ENABLE_ANSWER_QUALITY_GATE:
        return result

    if not result or not result.get(
        "answer"
    ):
        return result

    source = str(
        result.get(
            "answer_source",
            ""
        )
    ).lower()

    # Local results can be rebuilt from their exact selected page.
    if source.startswith(
        "ameja"
    ):
        result = _quality_rebuild_local_answer(
            query,
            result
        )

    # Wikipedia answers remain grounded in the already-selected article.
    elif source.startswith(
        "wikipedia"
    ):
        result = _quality_clean_answer(
            query,
            result
        )

    result = _quality_clean_answer(
        query,
        result
    )

    # Language formatting must be the final stage, otherwise answer-quality
    # rebuilding can overwrite the converted Hinglish answer.
    if (
        ENABLE_HINGLISH_ANSWER_CONSISTENCY
        and result.get(
            "answer_source",
            ""
        ).lower().startswith(
            "ameja"
        )
    ):
        result = _local_hinglish_answer(
            query,
            result
        )

    # One final hygiene pass, without rebuilding the content.
    result = dict(result)

    return result




# ============================================================
# PERFORMANCE CACHE v1
# ============================================================

ENABLE_PERFORMANCE_CACHE = True

ANSWER_CACHE_MAX = 256
ANSWER_CACHE_TTL_SECONDS = 900

_ANSWER_CACHE = {}


def _answer_cache_key(
    query
):
    """
    Stable cache key for a user question.

    The cache intentionally keys only the normalized query. Conversation
    follow-ups are excluded from caching unless the query itself is rewritten
    by the context layer before entering the expensive answer pipeline.
    """

    return normalize_text(
        clean_sentence(
            query
        )
    )


def _answer_cache_get(
    query
):
    if not ENABLE_PERFORMANCE_CACHE:
        return None

    key = _answer_cache_key(
        query
    )

    if not key:
        return None

    item = _ANSWER_CACHE.get(
        key
    )

    if not item:
        return None

    now = time.time()

    if (
        now
        - item.get(
            "created",
            0
        )
        > ANSWER_CACHE_TTL_SECONDS
    ):
        _ANSWER_CACHE.pop(
            key,
            None
        )
        return None

    return dict(
        item.get(
            "result",
            {}
        )
    )


def _answer_cache_set(
    query,
    result
):
    if not ENABLE_PERFORMANCE_CACHE:
        return

    if not result or not result.get(
        "answer"
    ):
        return

    key = _answer_cache_key(
        query
    )

    if not key:
        return

    _ANSWER_CACHE[key] = {
        "created": time.time(),
        "result": dict(
            result
        ),
    }

    # Bounded FIFO cleanup. This avoids unbounded RAM growth.
    while len(
        _ANSWER_CACHE
    ) > ANSWER_CACHE_MAX:

        oldest_key = next(
            iter(
                _ANSWER_CACHE
            )
        )

        del _ANSWER_CACHE[
            oldest_key
        ]


def clear_answer_cache():
    """Clear the answer cache."""

    _ANSWER_CACHE.clear()


def get_answer_cache_stats():
    """Return lightweight cache diagnostics."""

    return {
        "enabled": ENABLE_PERFORMANCE_CACHE,
        "entries": len(
            _ANSWER_CACHE
        ),
        "max_entries": ANSWER_CACHE_MAX,
        "ttl_seconds": ANSWER_CACHE_TTL_SECONDS,
    }



def build_answer_with_sources(query):

    context = resolve_query_context(
        query
    )

    effective_query = context.get(
        "resolved_query",
        query
    )

    if context.get(
        "context_ambiguous"
    ):
        return {
            "answer": (
                "I need a person or subject to be specified first. "
                'For example: "Who was Albert Einstein?" '
                'then "Where was he born?"'
            ),
            "title": None,
            "url": None,
            "sources": [],
            "confidence": 0,
            "understood_query": normalize_text(
                clean_sentence(
                    query
                )
            ),
            "answer_language": detect_answer_language(
                query
            ),
            "question_type": detect_question_type(
                query
            ),
            "answer_source": "ambiguity_guard",
            "context_used": False,
            "context_ambiguous": True,
            "cache_hit": False,
        }

    # Cache only standalone questions. Contextual queries can depend on
    # session state, so they must never share answers across conversations.
    if (
        ENABLE_PERFORMANCE_CACHE
        and not context.get("used_context")
    ):

        cached_result = _answer_cache_get(
            effective_query
        )

        if cached_result:
            cached_result["cache_hit"] = True
            return cached_result

    # Narrow contextual fact extraction comes first for biography follow-ups.
    if (
        context.get("used_context")
        and context.get("context_topic")
    ):
        contextual_fact = _contextual_fact_from_wikipedia(
            context["context_topic"],
            effective_query
        )

        if contextual_fact:
            contextual_fact["understood_query"] = effective_query
            contextual_fact["answer_language"] = detect_answer_language(
                query
            )
            contextual_fact["question_type"] = detect_question_type(
                query
            )
            contextual_fact["answer_source"] = "wikipedia_context"
            contextual_fact["original_query"] = clean_sentence(
                query
            )
            contextual_fact["resolved_query"] = effective_query
            contextual_fact["context_used"] = True

            contextual_fact = format_answer_for_user(
                query,
                contextual_fact
            )

            _remember_turn(
                query,
                contextual_fact,
                resolved_query=effective_query
            )

            return contextual_fact

    # For follow-ups, resolve the previous canonical entity first.
    if (
        context.get("used_context")
        and context.get("context_topic")
        and "wikipedia_fallback" in globals()
    ):

        entity_summary = _wikipedia_entity_locked_summary(
            context["context_topic"],
            effective_query
        )

        if entity_summary:

            language = detect_answer_language(
                query
            )

            # Prefer a targeted section for common factual follow-ups.
            targeted_answer = None

            normalized_effective = normalize_text(
                effective_query
            )

            if (
                "birthplace" in normalized_effective
                or "place of birth" in normalized_effective
                or "date of birth" in normalized_effective
                or "date of death" in normalized_effective
                or "discoveries" in normalized_effective
                or "known for" in normalized_effective
            ):

                try:

                    section_keywords = []

                    if (
                        "birthplace" in normalized_effective
                        or "place of birth" in normalized_effective
                        or "date of birth" in normalized_effective
                    ):
                        section_keywords = [
                            "Early life",
                            "Biography",
                            "Personal life",
                        ]

                    elif "discoveries" in normalized_effective:
                        section_keywords = [
                            "Scientific career",
                            "Scientific work",
                            "Contributions",
                        ]

                    elif "known for" in normalized_effective:
                        section_keywords = [
                            "Legacy",
                            "Scientific work",
                            "Major works",
                        ]

                    if (
                        section_keywords
                        and "wikipedia_section_answer" in globals()
                    ):
                        targeted_answer = wikipedia_section_answer(
                            entity_summary.get(
                                "title",
                                context["context_topic"]
                            ),
                            section_keywords
                        )

                except Exception:
                    targeted_answer = None

            answer = targeted_answer or clean_sentence(
                entity_summary.get(
                    "extract",
                    ""
                )
            )

            if answer:

                answer = _compact_wikipedia_answer(
                    answer,
                    max_sentences=2,
                    max_chars=620
                )

                if (
                    language == "hinglish"
                    and "_to_hinglish_wikipedia" in globals()
                ):
                    try:
                        answer = _to_hinglish_wikipedia(
                            answer,
                            context["context_topic"]
                        )
                    except Exception:
                        pass

                result = {
                    "answer": answer,
                    "title": entity_summary.get(
                        "title",
                        context["context_topic"]
                    ),
                    "url": entity_summary.get(
                        "url",
                        ""
                    ),
                    "sources": [{
                        "title": entity_summary.get(
                            "title",
                            context["context_topic"]
                        ),
                        "url": entity_summary.get(
                            "url",
                            ""
                        )
                    }],
                    "confidence": 88,
                    "understood_query": effective_query,
                    "answer_language": language,
                    "question_type": detect_question_type(
                        query
                    ),
                    "answer_source": "wikipedia_context"
                }

                result["original_query"] = clean_sentence(
                    query
                )
                result["resolved_query"] = effective_query
                result["context_used"] = True

                result = format_answer_for_user(
                    query,
                    result
                )

                _remember_turn(
                    query,
                    result,
                    resolved_query=effective_query
                )

                return result

    result = _build_answer_with_sources_core(
        effective_query
    )

    if context.get(
        "used_context"
    ):
        result["original_query"] = clean_sentence(
            query
        )
        result["resolved_query"] = effective_query
        result["context_used"] = True
    else:
        result["context_used"] = False

    result = format_answer_for_user(
        query,
        result
    )

    result = optimize_answer_result(
        query,
        result
    )

    if (
        ENABLE_PERFORMANCE_CACHE
        and not context.get("used_context")
        and not context.get("context_ambiguous")
        and result.get("answer")
    ):
        _answer_cache_set(
            effective_query,
            result
        )
        result["cache_hit"] = False

    _remember_turn(
        query,
        result,
        resolved_query=effective_query
    )

    return result




@app.get("/cache")
def cache_status():
    """Return lightweight answer-cache statistics."""

    return get_answer_cache_stats()


@app.get("/cache/clear")
def cache_clear():
    """Clear the in-process answer cache."""

    clear_answer_cache()

    return {
        "status": "ok",
        "cleared": True,
        **get_answer_cache_stats()
    }



@app.get("/info")
def info():
    """Public, non-sensitive service information."""

    return {
        "service": "ameja",
        "version": app.version,
        "features": [
            "local_search",
            "natural_query",
            "wikipedia_fallback",
            "comparison",
            "conversation_context",
            "answer_cache"
        ]
    }



@app.get("/")
def home():
    """
    Serve the existing Ameja browser UI from the same FastAPI origin.
    """

    if not os.path.isfile(
        FRONTEND_PATH
    ):
        return {
            "service": "ameja",
            "status": "ok",
            "message": "Frontend file not found."
        }

    return FileResponse(
        FRONTEND_PATH
    )


@app.get("/pages/{page_path:path}")
def page(
    page_path: str
):
    """
    Serve indexed local HTML pages when the source directory is included in
    the deployment repository.
    """

    # Prevent path traversal and keep requests inside AMEJA_PAGES_DIR.
    clean_path = os.path.normpath(
        page_path
    ).replace(
        "\\",
        "/"
    )

    if (
        clean_path.startswith("../")
        or clean_path == ".."
        or clean_path.startswith("/")
    ):
        return {
            "error": "invalid_page_path"
        }

    file_path = os.path.abspath(
        os.path.join(
            AMEJA_PAGES_DIR,
            clean_path
        )
    )

    base_dir = os.path.abspath(
        AMEJA_PAGES_DIR
    )

    if (
        not (
            file_path == base_dir
            or file_path.startswith(
                base_dir + os.sep
            )
        )
        or not os.path.isfile(
            file_path
        )
    ):
        return {
            "error": "page_not_found"
        }

    return FileResponse(
        file_path
    )


@app.get("/health")
def health():
    """Lightweight health/readiness check."""

    try:
        page_count = len(
            get_all_pages()
        )
    except Exception:
        page_count = 0

    cache_stats = get_answer_cache_stats()

    return {
        "status": "ok",
        "service": "ameja",
        "version": app.version,
        "indexed_pages": page_count,
        "cache_entries": cache_stats["entries"]
    }


@app.get("/search")
def search(
    q: str = Query(
        default="",
        min_length=0,
        max_length=500
    )
):
    """Raw lightweight search endpoint."""

    query = clean_sentence(
        q
    )

    if not query:
        return {
            "query": "",
            "results": []
        }

    try:
        results = search_pages(
            query
        )
    except Exception:
        _LOGGER.exception(
            "Search request failed for query=%r",
            query[:160]
        )
        results = []

    public_results = _publicize_result_urls({
        "results": results[:10]
    })

    return {
        "query": query,
        "results": public_results.get(
            "results",
            []
        )
    }


@app.get("/suggest")
def suggest(
    q: str = Query(
        default="",
        min_length=0,
        max_length=200
    )
):
    """Lightweight autocomplete endpoint."""

    query = clean_sentence(
        q
    )

    if not query:
        return {
            "query": "",
            "suggestions": []
        }

    try:
        suggestions = get_suggestion(
            query
        )
    except Exception:
        _LOGGER.exception(
            "Suggestion request failed for query=%r",
            query[:160]
        )
        suggestions = []

    if suggestions is None:
        suggestions = []

    if isinstance(
        suggestions,
        str
    ):
        suggestions = [
            suggestions
        ]

    return {
        "query": query,
        "suggestions": list(
            suggestions
        )[:8]
    }



# ============================================================
# API SESSION STORAGE
# ============================================================

_API_SESSIONS = {}
API_MAX_SESSIONS = 256


def _save_api_session(
    session_id
):
    if not session_id:
        return

    try:
        context = get_conversation_context()
    except Exception:
        context = []

    _API_SESSIONS[
        session_id
    ] = list(
        context
    )

    while len(
        _API_SESSIONS
    ) > API_MAX_SESSIONS:

        oldest = next(
            iter(
                _API_SESSIONS
            )
        )

        del _API_SESSIONS[
            oldest
        ]


def _load_api_session(
    session_id
):
    clear_conversation_context()

    if not session_id:
        return

    saved = _API_SESSIONS.get(
        session_id,
        []
    )

    if not isinstance(
        saved,
        list
    ):
        saved = []

    state = globals().setdefault(
        "_CONVERSATION_STATE",
        {
            "turns": []
        }
    )

    state["turns"] = list(
        saved[
            -CONTEXT_MAX_TURNS:
        ]
    )


@app.get("/session/reset")
def reset_session(
    session_id: str = Query(
        default="",
        max_length=128
    )
):
    """
    Clear one API conversation session.
    """

    if session_id:
        _API_SESSIONS.pop(
            session_id,
            None
        )

    return {
        "status": "ok",
        "session_id": session_id or None,
        "context_cleared": True
    }


@app.get("/answer")
def answer(
    q: str = Query(
        default="",
        min_length=0,
        max_length=1000
    ),
    session_id: str = Query(
        default="",
        max_length=128
    )
):
    """
    Main answer endpoint with bounded input and defensive error handling.
    """

    query = clean_sentence(
        q
    )

    if not query:
        return {
            "query": "",
            "session_id": session_id or None,
            "answer": None,
            "title": None,
            "url": None,
            "sources": [],
            "confidence": 0,
            "answer_source": "none",
            "error": "empty_query"
        }

    try:
        _load_api_session(
            session_id
        )

        data = build_answer_with_sources(
            query
        )

        _save_api_session(
            session_id
        )

        public_data = _publicize_result_urls(
            data
        )

        return {
            "query": query,
            "session_id": session_id or None,
            **public_data
        }

    except Exception as exc:
        _LOGGER.exception(
            "Answer request failed for query=%r",
            query[:160]
        )

        return {
            "query": query,
            "session_id": session_id or None,
            "answer": None,
            "title": None,
            "url": None,
            "sources": [],
            "confidence": 0,
            "answer_source": "none",
            "error": "internal_error"
        }


