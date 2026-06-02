from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from server import db_connect, import_questions, init_db


APIFY_URL = (
    "https://api.apify.com/v2/acts/"
    "alvarolozano~preguntas-dgt/run-sync-get-dataset-items"
)
TESTSCONDUCIR_URL = "https://www.testsconducir.es/permiso-B"
USER_AGENT = "AutoTestPro/0.1 (+local importer)"


def fetch_text(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, "replace")


def clean_html(value: str) -> str:
    value = re.sub(r"<script.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<.*?>", " ", value, flags=re.S)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def strip_answer_prefix(value: str) -> str:
    return re.sub(r"^[A-F]\)\s*", "", value).strip()


def first_value(item: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def normalize_options(raw_options: Any) -> list[str]:
    if isinstance(raw_options, list):
        values = raw_options
    elif isinstance(raw_options, dict):
        values = list(raw_options.values())
    else:
        return []

    options = []
    for option in values:
        if isinstance(option, dict):
            text = first_value(option, ["texto", "text", "respuesta", "answer", "label", "value"])
            if text:
                options.append(str(text).strip())
        elif option is not None:
            options.append(str(option).strip())
    return [option for option in options if option]


def normalize_correct_index(raw_correct: Any, options: list[str]) -> int | None:
    if raw_correct is None or isinstance(raw_correct, bool):
        return None
    if isinstance(raw_correct, int):
        return raw_correct if 0 <= raw_correct < len(options) else raw_correct - 1
    if isinstance(raw_correct, str):
        value = raw_correct.strip()
        if value.isdigit():
            index = int(value)
            return index if 0 <= index < len(options) else index - 1
        if len(value) == 1 and value.upper() in "ABCDEF":
            return ord(value.upper()) - ord("A")
        lowered = value.lower()
        for index, option in enumerate(options):
            if option.lower() == lowered:
                return index
    return None


def normalize_api_item(item: dict[str, Any], index: int) -> dict[str, Any] | None:
    title = first_value(item, ["pregunta", "question", "enunciado", "title", "texto"])
    raw_options = first_value(item, ["respuestas", "answers", "opciones", "options"])
    options = normalize_options(raw_options)
    raw_correct = first_value(
        item,
        [
            "respuestaCorrecta",
            "correctAnswer",
            "correct_index",
            "correctIndex",
            "correcta",
            "solucion",
            "answer",
        ],
    )
    correct_index = normalize_correct_index(raw_correct, options)

    if not title or len(options) < 2 or correct_index is None:
        return None
    if correct_index < 0 or correct_index >= len(options):
        return None

    external = first_value(item, ["id", "questionId", "external_id", "codigo"])
    if not external:
        external = hashlib.sha1(
            json.dumps({"title": title, "options": options}, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    category = first_value(item, ["categoria", "category", "tema", "tipo"]) or "general"
    explanation = first_value(item, ["explicacion", "explanation", "comentario", "ayuda"])
    image_url = first_value(item, ["imagen", "image", "imageUrl", "urlImagen"])

    return {
        "external_id": str(external),
        "category": str(category).lower().replace(" ", "_"),
        "title": str(title).strip(),
        "image_key": "api",
        "image_url": str(image_url).strip() if image_url else None,
        "options": options,
        "correct_index": correct_index,
        "explanation": str(explanation).strip() if explanation else "Sin explicación en la fuente importada.",
    }


def fetch_apify_questions() -> list[dict[str, Any]]:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("Falta APIFY_TOKEN en el entorno.")

    payload = {
        "idPermiso": os.environ.get("DGT_PERMIT_ID", "1"),
        "idIdioma": os.environ.get("DGT_LANGUAGE_ID", "1"),
    }
    url = f"{APIFY_URL}?token={token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def load_json_questions(location: str) -> list[dict[str, Any]]:
    if location.startswith(("http://", "https://")):
        data = json.loads(fetch_text(location, timeout=60))
    else:
        with open(location, "r", encoding="utf-8") as file:
            data = json.load(file)
    if isinstance(data, dict):
        data = data.get("questions", data.get("items", []))
    if not isinstance(data, list):
        raise RuntimeError("El JSON debe ser una lista o tener una clave 'questions'.")
    return data


def testsconducir_links(list_url: str, limit: int) -> list[str]:
    html_text = fetch_text(list_url)
    paths = []
    for path in re.findall(r'href=["\'](/pregunta/B/[^"\']+)["\']', html_text):
        full_url = urllib.parse.urljoin(list_url, path)
        if full_url not in paths:
            paths.append(full_url)
        if len(paths) >= limit:
            break
    return paths


def parse_testsconducir_question(url: str) -> dict[str, Any] | None:
    page = fetch_text(url)
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", page, flags=re.I | re.S)
    correct_match = re.search(
        r'class=["\'][^"\']*option_correct[^"\']*["\'][^>]*>\s*(\d+)\s*<',
        page,
        flags=re.I | re.S,
    )
    option_matches = re.findall(
        r'<li[^>]+id=["\'](\d+)["\'][^>]*>\s*<a[^>]*>(.*?)</a>',
        page,
        flags=re.I | re.S,
    )
    image_match = re.search(
        r'<img[^>]+class=["\'][^"\']*img-responsive[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
        page,
        flags=re.I | re.S,
    )

    if not title_match or not correct_match or len(option_matches) < 2:
        return None

    title = clean_html(title_match.group(1))
    correct_id = correct_match.group(1)
    options = []
    correct_index = None
    for index, (option_id, option_html) in enumerate(option_matches):
        if option_id == correct_id:
            correct_index = index
        options.append(strip_answer_prefix(clean_html(option_html)))

    if correct_index is None:
        return None

    image_url = None
    if image_match:
        candidate = urllib.parse.urljoin(url, html.unescape(image_match.group(1)))
        if not candidate.endswith("/static/test/fotos/logo.webp"):
            image_url = candidate

    external_id = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return {
        "external_id": external_id,
        "category": "testsconducir_b",
        "title": title,
        "image_key": "web",
        "image_url": image_url,
        "options": options,
        "correct_index": correct_index,
        "explanation": "Pregunta importada de TestsConducir. Conviene contrastar la explicación con normativa o temario oficial.",
    }


def parse_testsconducir_test_page(page: str, page_url: str, limit: int) -> list[dict[str, Any]]:
    starts = [
        match.start()
        for match in re.finditer(
            r'<div class=["\']covid-test-wrap test-step["\'] name=["\']question\d+["\']>',
            page,
            flags=re.I,
        )
    ]
    questions = []
    for index, start in enumerate(starts[:limit]):
        end = starts[index + 1] if index + 1 < len(starts) else len(page)
        block = page[start:end]
        position_match = re.search(r'name=["\']question(\d+)["\']', block, flags=re.I)
        if not position_match:
            continue
        position = int(position_match.group(1))

        correct_match = re.search(
            rf'id=["\']correct{position}["\'][^>]*>\s*(\d+)\s*<',
            block,
            flags=re.I | re.S,
        )
        title_match = re.search(r"<h3[^>]*>(.*?)</h3>", block, flags=re.I | re.S)
        option_matches = re.findall(
            rf'id=["\']answer{position}(\d+)["\'][^>]*>(.*?)</label>',
            block,
            flags=re.I | re.S,
        )
        explanation_match = re.search(
            rf'id=["\']explicacion{position}["\'][^>]*>(.*?)</span>',
            block,
            flags=re.I | re.S,
        )

        if not title_match or not correct_match or len(option_matches) < 2:
            continue

        correct_value = int(correct_match.group(1))
        options = []
        correct_index = None
        for option_index, (option_number, option_html) in enumerate(option_matches):
            if int(option_number) == correct_value:
                correct_index = option_index
            options.append(strip_answer_prefix(clean_html(option_html)))
        if correct_index is None:
            continue

        title = clean_html(title_match.group(1))
        explanation = (
            clean_html(explanation_match.group(1))
            if explanation_match
            else "Sin explicación en la fuente importada."
        )

        image_url = None
        after_title = block[title_match.end() :]
        image_match = re.search(
            r'<img[^>]+class=["\'][^"\']*img-responsive[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
            after_title,
            flags=re.I | re.S,
        )
        if image_match:
            candidate = urllib.parse.urljoin(page_url, html.unescape(image_match.group(1)))
            if not candidate.endswith("/static/test/fotos/logo.webp"):
                image_url = candidate

        external_id = hashlib.sha1(
            json.dumps({"title": title, "options": options}, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        questions.append(
            {
                "external_id": external_id,
                "category": "simulacro_externo",
                "title": title,
                "image_key": "web",
                "image_url": image_url,
                "options": options,
                "correct_index": correct_index,
                "explanation": explanation,
            }
        )
    return questions


def testsconducir_shared_code(page: str) -> str:
    match = re.search(r"test-compartido/([A-Za-z0-9]+)", page)
    if match:
        return match.group(1)
    return hashlib.sha1(page.encode("utf-8", "ignore")).hexdigest()[:12]


def fetch_testsconducir_questions(list_url: str, limit: int, delay: float) -> list[dict[str, Any]]:
    links = testsconducir_links(list_url, limit)
    questions = []
    for index, link in enumerate(links, start=1):
        question = parse_testsconducir_question(link)
        if question:
            questions.append(question)
        if index < len(links):
            time.sleep(delay)
    return questions


def import_normalized(questions: list[dict[str, Any]], source: str) -> int:
    with db_connect() as conn:
        return import_questions(conn, questions, source=source)


def question_ids_by_external_id(conn: Any, questions: list[dict[str, Any]], source: str) -> dict[str, int]:
    external_ids = [question["external_id"] for question in questions]
    if not external_ids:
        return {}
    placeholders = ",".join("?" for _ in external_ids)
    rows = conn.execute(
        f"""
        SELECT id, external_id
        FROM questions
        WHERE source = ? AND external_id IN ({placeholders})
        """,
        [source, *external_ids],
    ).fetchall()
    return {row["external_id"]: row["id"] for row in rows}


def import_test_pool(
    tests: list[dict[str, Any]], question_source: str, pool_source: str
) -> tuple[int, int]:
    all_questions = []
    seen = set()
    for test in tests:
        for question in test["questions"]:
            if question["external_id"] not in seen:
                seen.add(question["external_id"])
                all_questions.append(question)

    with db_connect() as conn:
        import_questions(conn, all_questions, source=question_source)
        question_ids = question_ids_by_external_id(conn, all_questions, question_source)

        existing = conn.execute(
            "SELECT id FROM test_pool WHERE source = ?",
            (pool_source,),
        ).fetchall()
        existing_ids = [row["id"] for row in existing]
        if existing_ids:
            placeholders = ",".join("?" for _ in existing_ids)
            conn.execute(
                f"DELETE FROM test_pool_questions WHERE test_id IN ({placeholders})",
                existing_ids,
            )
            conn.execute("DELETE FROM test_pool WHERE source = ?", (pool_source,))

        for index, test in enumerate(tests, start=1):
            cursor = conn.execute(
                """
                INSERT INTO test_pool
                    (source, external_id, title, question_count, updated_at)
                VALUES
                    (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    pool_source,
                    test["external_id"],
                    f"Simulacro externo {index:03d}",
                    len(test["questions"]),
                ),
            )
            test_id = cursor.lastrowid
            conn.executemany(
                """
                INSERT INTO test_pool_questions
                    (test_id, question_id, position)
                VALUES
                    (?, ?, ?)
                """,
                [
                    (test_id, question_ids[question["external_id"]], position)
                    for position, question in enumerate(test["questions"], start=1)
                    if question["external_id"] in question_ids
                ],
            )

    return len(tests), len(all_questions)


def fetch_testsconducir_pool(
    test_url: str, test_count: int, questions_per_test: int, delay: float
) -> list[dict[str, Any]]:
    tests = []
    seen_tests = set()
    attempts = 0
    max_attempts = max(test_count * 2, test_count + 10)
    while len(tests) < test_count and attempts < max_attempts:
        attempts += 1
        page = fetch_text(test_url, timeout=45)
        shared_code = testsconducir_shared_code(page)
        if shared_code in seen_tests:
            time.sleep(delay)
            continue
        questions = parse_testsconducir_test_page(page, test_url, questions_per_test)
        if len(questions) >= questions_per_test:
            seen_tests.add(shared_code)
            tests.append(
                {
                    "external_id": shared_code,
                    "questions": questions[:questions_per_test],
                }
            )
            print(f"Test {len(tests):03d}/{test_count} importable ({len(questions[:questions_per_test])} preguntas)")
        time.sleep(delay)
    return tests


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincroniza preguntas para AutoTest Pro.")
    parser.add_argument(
        "provider",
        choices=["apify", "json", "testsconducir", "testsconducir-pool"],
        nargs="?",
        default="apify",
        help="Fuente de preguntas.",
    )
    parser.add_argument("--source", help="Nombre interno de la fuente en la base de datos.")
    parser.add_argument("--url", help="URL de listado/API/JSON según proveedor.")
    parser.add_argument("--file", help="Archivo JSON local para provider=json.")
    parser.add_argument("--limit", type=int, default=10, help="Máximo de preguntas a importar.")
    parser.add_argument("--tests", type=int, default=100, help="Número de tests del pool.")
    parser.add_argument("--questions-per-test", type=int, default=30, help="Preguntas por test.")
    parser.add_argument("--delay", type=float, default=0.4, help="Pausa entre páginas de scraping.")
    args = parser.parse_args()

    init_db()
    try:
        if args.provider == "apify":
            items = fetch_apify_questions()
            questions = [
                question
                for index, item in enumerate(items, start=1)
                if isinstance(item, dict)
                for question in [normalize_api_item(item, index)]
                if question
            ]
            source = args.source or "apify-dgt"
        elif args.provider == "json":
            location = args.file or args.url
            if not location:
                raise RuntimeError("Indica --file o --url para importar JSON.")
            items = load_json_questions(location)
            questions = [
                question
                for index, item in enumerate(items, start=1)
                if isinstance(item, dict)
                for question in [normalize_api_item(item, index)]
                if question
            ]
            source = args.source or "json"
        elif args.provider == "testsconducir":
            questions = fetch_testsconducir_questions(
                args.url or TESTSCONDUCIR_URL,
                max(1, args.limit),
                max(0, args.delay),
            )
            source = args.source or "testsconducir"
        else:
            tests = fetch_testsconducir_pool(
                args.url or "https://www.testsconducir.es/test/B/",
                max(1, args.tests),
                min(max(1, args.questions_per_test), 30),
                max(0, args.delay),
            )
            if len(tests) < args.tests:
                raise RuntimeError(f"Solo se pudieron preparar {len(tests)} tests.")
            test_total, question_total = import_test_pool(
                tests,
                question_source=args.source or "testsconducir-pool",
                pool_source="external-exam-pool",
            )
            print(
                f"Pool completado: {test_total} tests y {question_total} preguntas únicas."
            )
            return 0
    except urllib.error.HTTPError as error:
        print(f"No se pudo sincronizar: HTTP {error.code}")
        return 1
    except Exception as error:
        print(f"No se pudo sincronizar: {error}")
        return 1

    imported = import_normalized(questions, source=source)
    print(f"Sincronización completada: {imported} preguntas importadas desde {source}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
