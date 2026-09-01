import requests

from bs4 import BeautifulSoup

from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlparse,
    urldefrag
)

from urllib.robotparser import RobotFileParser

from collections import deque

import sys
import os
import time


# ============================================================
# PATH SETUP
# ============================================================

sys.path.append(
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )
)

from database.database import get_connection


# ============================================================
# CONFIGURATION
# ============================================================

MAX_PAGES = 50

REQUEST_TIMEOUT = 10

CRAWL_DELAY = 0.5

USER_AGENT = "AmejaBot/1.0"


# ============================================================
# START PAGES
# ============================================================
START_URLS = [

    # Existing pages
    "http://127.0.0.1:9000/pages/python.html",
    "http://127.0.0.1:9000/pages/fastapi.html",
    "http://127.0.0.1:9000/pages/machine-learning.html",
    "http://127.0.0.1:9000/pages/security.html",

    # New Python pages
    "http://127.0.0.1:9000/pages/python-basics.html",
    "http://127.0.0.1:9000/pages/python-functions.html",
    "http://127.0.0.1:9000/pages/python-oop.html",
    "http://127.0.0.1:9000/pages/python-libraries.html",
    "http://127.0.0.1:9000/pages/python-automation.html",
    "http://127.0.0.1:9000/pages/python-data-science.html",

]

# ============================================================
# URL NORMALIZATION
# ============================================================

def normalize_url(url):
    if not url:
        return None

    url = str(url).strip()
    url, _ = urldefrag(url)

    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    if scheme not in ("http", "https"):
        return None

    if not netloc:
        return None

    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    path = parsed.path or "/"

    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # Drop common tracking-only parameters and sort the remainder.
    tracking = {
        "utm_source", "utm_medium", "utm_campaign",
        "utm_term", "utm_content", "gclid", "fbclid",
        "msclkid"
    }

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(
            parsed.query,
            keep_blank_values=True
        )
        if key.lower() not in tracking
    ]

    result = f"{scheme}://{netloc}{path}"

    if query_pairs:
        result += "?" + urlencode(
            sorted(query_pairs)
        )

    return result


# ============================================================
# ROBOTS.TXT
# ============================================================

def get_robot_parser(start_url):

    parsed = urlparse(
        start_url
    )

    robots_url = (
        f"{parsed.scheme}://"
        f"{parsed.netloc}/robots.txt"
    )

    robot_parser = RobotFileParser()

    robot_parser.set_url(
        robots_url
    )

    try:

        robot_parser.read()

        print(
            f"robots.txt loaded: "
            f"{robots_url}"
        )

        return robot_parser

    except Exception as error:

        print(
            "Could not load robots.txt"
        )

        print(
            f"Reason: {error}"
        )

        return None


# ============================================================
# CRAWL PAGE
# ============================================================

def crawl_page(url):

    try:

        response = requests.get(

            url,

            timeout=REQUEST_TIMEOUT,

            headers={
                "User-Agent":
                    USER_AGENT
            }

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

        # Only crawl HTML pages
        if "text/html" not in content_type:

            print(
                f"Skipped non-HTML page: "
                f"{url}"
            )

            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )


        # ====================================================
        # TITLE
        # ====================================================

        title = ""

        if (
            soup.title
            and soup.title.string
        ):

            title = (
                soup.title.string
                .strip()
            )


        # ====================================================
        # COLLECT LINKS
        # ====================================================

        links = []

        for link in soup.find_all(
            "a",
            href=True
        ):

            full_url = urljoin(
                url,
                link["href"]
            )

            normalized = normalize_url(
                full_url
            )

            if normalized:

                links.append(
                    normalized
                )


        # Remove duplicate links

        links = list(
            dict.fromkeys(
                links
            )
        )


        # ====================================================
        # REMOVE NON-CONTENT
        # ====================================================

        for tag in soup(
            [
                "script",
                "style",
                "noscript",
                "iframe",
                "nav",
                "footer",
                "header",
                "aside",
                "form"
            ]
        ):

            tag.decompose()


        # ====================================================
        # REMOVE LINK TEXT
        # ====================================================

        for link in soup.find_all(
            "a"
        ):

            link.decompose()


        # ====================================================
        # CONTENT EXTRACTION
        # ====================================================

        main_content = soup.find(
            "main"
        )

        if main_content:

            content = (
                main_content
                .get_text(
                    " ",
                    strip=True
                )
            )

        else:

            article = soup.find(
                "article"
            )

            if article:

                content = (
                    article
                    .get_text(
                        " ",
                        strip=True
                    )
                )

            else:

                content = (
                    soup.get_text(
                        " ",
                        strip=True
                    )
                )


        return {

            "url": url,

            "title": title,

            "content": content,

            "links": links

        }


    except requests.RequestException as error:

        print(
            f"Crawling failed: "
            f"{url}"
        )

        print(
            f"Error: {error}"
        )

        return None


    except Exception as error:

        print(
            f"Unexpected error: "
            f"{url}"
        )

        print(
            f"Error: {error}"
        )

        return None


# ============================================================
# CRAWL METADATA
# ============================================================

def _content_hash(content):
    import hashlib
    return hashlib.sha256(
        str(content or "").encode(
            "utf-8",
            errors="ignore"
        )
    ).hexdigest()


def ensure_crawl_metadata():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_meta (
            url TEXT PRIMARY KEY,
            content_hash TEXT,
            status TEXT,
            http_status INTEGER,
            last_crawled REAL,
            title TEXT
        )
        """
    )

    connection.commit()
    connection.close()


def update_crawl_metadata(
    url,
    status,
    http_status=None,
    title="",
    content_hash_value=""
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO crawl_meta (
            url,
            content_hash,
            status,
            http_status,
            last_crawled,
            title
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            content_hash = excluded.content_hash,
            status = excluded.status,
            http_status = excluded.http_status,
            last_crawled = excluded.last_crawled,
            title = excluded.title
        """,
        (
            url,
            content_hash_value,
            status,
            http_status,
            time.time(),
            title
        )
    )

    connection.commit()
    connection.close()



# ============================================================
# SAVE PAGE
# ============================================================

def save_page(page):

    url = normalize_url(
        page.get("url")
    )

    if not url:
        return "invalid_url"

    title = str(
        page.get("title", "")
    ).strip()

    content = str(
        page.get("content", "")
    ).strip()

    new_hash = _content_hash(
        content
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT title, content
        FROM pages
        WHERE url = ?
        """,
        (url,)
    )

    existing = cursor.fetchone()

    if existing is None:

        cursor.execute(
            """
            INSERT INTO pages
            (url, title, content)
            VALUES (?, ?, ?)
            """,
            (url, title, content)
        )

        connection.commit()
        connection.close()

        update_crawl_metadata(
            url,
            "new",
            page.get("http_status"),
            title,
            new_hash
        )

        return "new"

    old_title = existing[0] or ""
    old_content = existing[1] or ""

    if (
        old_title == title
        and _content_hash(old_content) == new_hash
    ):

        connection.commit()
        connection.close()

        update_crawl_metadata(
            url,
            "unchanged",
            page.get("http_status"),
            title,
            new_hash
        )

        return "unchanged"

    cursor.execute(
        """
        UPDATE pages
        SET title = ?, content = ?
        WHERE url = ?
        """,
        (title, content, url)
    )

    connection.commit()
    connection.close()

    update_crawl_metadata(
        url,
        "updated",
        page.get("http_status"),
        title,
        new_hash
    )

    return "updated"


# ============================================================
# WEBSITE CRAWLER
# ============================================================

def crawl_website(start_urls):

    ensure_crawl_metadata()

    normalized_starts = []

    for url in start_urls:

        normalized = normalize_url(
            url
        )

        if normalized:

            normalized_starts.append(
                normalized
            )


    if not normalized_starts:

        print(
            "No valid start URLs."
        )

        return


    # ========================================================
    # DOMAIN SET
    # ========================================================

    allowed_domains = set()

    for url in normalized_starts:

        allowed_domains.add(
            urlparse(
                url
            ).netloc.lower()
        )


    # ========================================================
    # QUEUE
    # ========================================================

    queue = deque(
        normalized_starts
    )

    visited = set()


    print(
        "\n============================"
    )

    print(
        "       AMEJA CRAWLER"
    )

    print(
        "============================"
    )

    print(
        f"Start pages: "
        f"{len(normalized_starts)}"
    )

    print(
        f"Maximum pages: "
        f"{MAX_PAGES}"
    )


    # ========================================================
    # ROBOTS CACHE
    # ========================================================

    robots_cache = {}


    # ========================================================
    # CRAWL LOOP
    # ========================================================

    while (

        queue

        and len(visited)
        < MAX_PAGES

    ):

        current_url = queue.popleft()


        if current_url in visited:

            continue


        current_domain = (
            urlparse(
                current_url
            )
            .netloc
            .lower()
        )


        # ====================================================
        # SAME DOMAIN ONLY
        # ====================================================

        if (
            current_domain
            not in allowed_domains
        ):

            visited.add(
                current_url
            )

            continue


        # ====================================================
        # ROBOTS CACHE
        # ====================================================

        if current_domain not in robots_cache:

            robots_cache[
                current_domain
            ] = get_robot_parser(
                current_url
            )


        robot_parser = robots_cache[
            current_domain
        ]


        # ====================================================
        # ROBOTS CHECK
        # ====================================================

        if robot_parser:

            try:

                if not robot_parser.can_fetch(
                    USER_AGENT,
                    current_url
                ):

                    print(
                        f"Blocked by robots.txt: "
                        f"{current_url}"
                    )

                    visited.add(
                        current_url
                    )

                    continue

            except Exception:

                print(
                    "robots.txt check failed."
                )


        # ====================================================
        # CRAWL
        # ====================================================

        print(
            f"\nCrawling: "
            f"{current_url}"
        )


        page = crawl_page(
            current_url
        )


        visited.add(
            current_url
        )


        if page is None:

            continue


        # ====================================================
        # SAVE
        # ====================================================

        save_page(
            page
        )


        print(
            f"Saved: "
            f"{page['title'] or 'Untitled'}"
        )


        print(
            f"Links found: "
            f"{len(page['links'])}"
        )


        # ====================================================
        # QUEUE LINKS
        # ====================================================

        for link in page["links"]:

            if link in visited:

                continue


            link_domain = (
                urlparse(
                    link
                )
                .netloc
                .lower()
            )


            if (
                link_domain
                not in allowed_domains
            ):

                continue


            if link not in queue:

                queue.append(
                    link
                )


        time.sleep(
            CRAWL_DELAY
        )


    # ========================================================
    # FINAL REPORT
    # ========================================================

    print(
        "\n----------------------------"
    )

    print(
        "Crawling complete!"
    )

    print(
        f"Pages visited: "
        f"{len(visited)}"
    )

    print(
        f"Pages limit: "
        f"{MAX_PAGES}"
    )

    print(
        f"Queue remaining: "
        f"{len(queue)}"
    )

    print(
        "----------------------------"
    )


# ============================================================
# START CRAWLER
# ============================================================

if __name__ == "__main__":

    crawl_website(
        START_URLS
    )
