from pathlib import Path

BASE = Path("test_site/pages")
BASE.mkdir(parents=True, exist_ok=True)

pages = {
    "python.html": (
        "Python Programming",
        "Python is a popular programming language used for web development, automation, data science and artificial intelligence."
    ),
    "fastapi.html": (
        "FastAPI Web Development",
        "FastAPI is a modern Python framework for building fast and scalable web APIs."
    ),
    "database.html": (
        "Database Systems",
        "Databases store and organize information. SQLite is a lightweight database useful for small applications and prototypes."
    ),
    "security.html": (
        "Cyber Security",
        "Cyber security focuses on protecting computers, networks, applications and data from attacks and unauthorized access."
    ),
    "web-development.html": (
        "Web Development",
        "Web development involves creating websites and web applications using HTML, CSS, JavaScript and backend technologies."
    ),
    "artificial-intelligence.html": (
        "Artificial Intelligence",
        "Artificial intelligence enables computers to perform tasks that normally require human intelligence, such as understanding language and recognizing patterns."
    ),
    "machine-learning.html": (
        "Machine Learning",
        "Machine learning allows systems to learn patterns from data and make predictions or decisions."
    ),
    "data-structures.html": (
        "Data Structures",
        "Data structures organize data efficiently. Arrays, linked lists, stacks, queues, trees and graphs are common examples."
    ),
    "algorithms.html": (
        "Algorithms",
        "Algorithms are step-by-step procedures used to solve computational problems efficiently."
    ),
    "software-engineering.html": (
        "Software Engineering",
        "Software engineering applies systematic methods to designing, developing, testing and maintaining reliable software."
    ),
}

# Create individual pages
for filename, (title, content) in pages.items():

    links = "\n".join(
        f'<li><a href="{other}">{pages[other][0]}</a></li>'
        for other in pages
        if other != filename
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
</head>

<body>

    <h1>{title}</h1>

    <p>{content}</p>

    <h2>Explore More</h2>

    <ul>
        {links}
    </ul>

</body>
</html>
"""

    (BASE / filename).write_text(
        html,
        encoding="utf-8"
    )

print("Ameja test website created!")
print(f"Pages created: {len(pages)}")