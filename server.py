from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "autoescuela.sqlite"
SEED_PATH = DATA_DIR / "seed_questions.json"

THEME_LABELS = {
    "senales": "Señales",
    "prioridad": "Prioridad",
    "velocidad": "Velocidad",
    "alumbrado": "Alumbrado",
    "seguridad": "Seguridad vial",
    "documentacion": "Documentación",
    "maniobras": "Maniobras",
    "mecanica": "Mecánica",
    "normas": "Normas generales",
}

THEME_KEYWORDS = {
    "senales": ["señal", "linea", "línea", "marca vial", "stop", "semáforo", "panel", "baliza"],
    "prioridad": ["prioridad", "ceder", "paso", "glorieta", "intersección", "peatón", "peatones"],
    "velocidad": ["velocidad", "km/h", "adelantar", "distancia de frenado"],
    "alumbrado": ["luz", "luces", "alumbrado", "antiniebla", "cruce", "carretera iluminada"],
    "seguridad": ["alcohol", "droga", "cinturón", "casco", "accidente", "airbag", "somnolencia", "seguridad"],
    "documentacion": ["permiso", "licencia", "seguro", "itv", "documento", "documentación", "matrícula"],
    "maniobras": ["estacionar", "parar", "cambio de dirección", "marcha atrás", "giro", "carril", "maniobra"],
    "mecanica": ["neumático", "freno", "motor", "aceite", "batería", "mecánica", "ruedas"],
}


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def classify_theme(title: str, options: list[str] | None = None, fallback: str = "normas") -> str:
    text = " ".join([title, *(options or [])]).lower()
    for theme, keywords in THEME_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return theme
    return fallback


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                theme TEXT NOT NULL DEFAULT 'normas',
                title TEXT NOT NULL,
                image_key TEXT,
                image_url TEXT,
                options_json TEXT NOT NULL,
                correct_index INTEGER NOT NULL,
                explanation TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'demo',
                external_id TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER,
                category TEXT NOT NULL,
                total INTEGER NOT NULL,
                correct INTEGER NOT NULL,
                errors INTEGER NOT NULL,
                score REAL NOT NULL,
                passed INTEGER NOT NULL,
                duration_seconds INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS attempt_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                selected_index INTEGER,
                is_correct INTEGER NOT NULL,
                FOREIGN KEY (attempt_id) REFERENCES attempts(id),
                FOREIGN KEY (question_id) REFERENCES questions(id)
            );

            CREATE TABLE IF NOT EXISTS test_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                question_count INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS test_pool_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                FOREIGN KEY (test_id) REFERENCES test_pool(id),
                FOREIGN KEY (question_id) REFERENCES questions(id)
            );
            """
        )
        ensure_question_columns(conn)
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_source_external
                ON questions(source, external_id)
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_test_pool_source_external
                ON test_pool(source, external_id)
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_test_pool_questions_position
                ON test_pool_questions(test_id, position)
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        if count == 0:
            seed_questions(conn)


def ensure_question_columns(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(questions)").fetchall()
    }
    migrations = {
        "image_url": "ALTER TABLE questions ADD COLUMN image_url TEXT",
        "theme": "ALTER TABLE questions ADD COLUMN theme TEXT NOT NULL DEFAULT 'normas'",
        "source": "ALTER TABLE questions ADD COLUMN source TEXT NOT NULL DEFAULT 'demo'",
        "external_id": "ALTER TABLE questions ADD COLUMN external_id TEXT",
        "updated_at": "ALTER TABLE questions ADD COLUMN updated_at TEXT",
    }
    for column, statement in migrations.items():
        if column not in columns:
            conn.execute(statement)
    conn.execute(
        "UPDATE questions SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL"
    )
    attempt_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(attempts)").fetchall()
    }
    if "test_id" not in attempt_columns:
        conn.execute("ALTER TABLE attempts ADD COLUMN test_id INTEGER")
    rows = conn.execute(
        "SELECT id, title, options_json FROM questions WHERE theme IS NULL OR theme = 'normas'"
    ).fetchall()
    for row in rows:
        options = json.loads(row["options_json"]) if row["options_json"] else []
        conn.execute(
            "UPDATE questions SET theme = ? WHERE id = ?",
            (classify_theme(row["title"], options), row["id"]),
        )


def seed_questions(conn: sqlite3.Connection) -> None:
    questions = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    import_questions(conn, questions, source="demo")


def import_questions(
    conn: sqlite3.Connection, questions: list[dict], source: str = "demo"
) -> int:
    normalized = []
    for index, question in enumerate(questions, start=1):
        external_id = question.get("external_id") or f"{source}-{index}-{question['title'][:60]}"
        normalized.append(
            {
                "category": question.get("category", "general"),
                "theme": question.get("theme")
                or classify_theme(question["title"], question.get("options")),
                "title": question["title"],
                "image_key": question.get("image_key"),
                "image_url": question.get("image_url"),
                "options_json": json.dumps(question["options"], ensure_ascii=False),
                "correct_index": int(question["correct_index"]),
                "explanation": question.get("explanation") or "Pregunta importada desde fuente externa.",
                "source": source,
                "external_id": external_id,
            }
        )

    conn.executemany(
        """
        INSERT INTO questions
            (category, theme, title, image_key, image_url, options_json, correct_index,
             explanation, source, external_id, updated_at)
        VALUES
            (:category, :theme, :title, :image_key, :image_url, :options_json, :correct_index,
             :explanation, :source, :external_id, CURRENT_TIMESTAMP)
        ON CONFLICT(source, external_id) DO UPDATE SET
            category = excluded.category,
            theme = excluded.theme,
            title = excluded.title,
            image_key = excluded.image_key,
            image_url = excluded.image_url,
            options_json = excluded.options_json,
            correct_index = excluded.correct_index,
            explanation = excluded.explanation,
            updated_at = CURRENT_TIMESTAMP
        """,
        normalized,
    )
    return len(normalized)


def question_for_test(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "category": row["category"],
        "theme": row["theme"],
        "themeLabel": THEME_LABELS.get(row["theme"], row["theme"].title()),
        "title": row["title"],
        "imageKey": row["image_key"],
        "imageUrl": row["image_url"],
        "source": row["source"],
        "options": json.loads(row["options_json"]),
    }


def question_for_review(row: sqlite3.Row, selected_index: int | None) -> dict:
    correct_index = row["correct_index"]
    return {
        **question_for_test(row),
        "selectedIndex": selected_index,
        "correctIndex": correct_index,
        "isCorrect": selected_index == correct_index,
        "explanation": row["explanation"],
    }


def json_response(handler: BaseHTTPRequestHandler, data: object, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw)


def max_allowed_errors(total: int) -> int:
    if total >= 30:
        return 3
    return max(1, round(total * 0.1))


class AutoescuelaHandler(BaseHTTPRequestHandler):
    server_version = "AutoescuelaTest/1.0"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/categories":
            self.get_categories()
            return
        if parsed.path == "/api/tests":
            self.get_tests()
            return
        if parsed.path == "/api/dashboard":
            self.get_dashboard()
            return
        if parsed.path == "/api/questions":
            self.get_questions(parsed.query)
            return
        if parsed.path == "/api/results":
            self.get_results()
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/results":
            self.save_result()
            return
        json_response(self, {"error": "Ruta no encontrada"}, 404)

    def get_categories(self) -> None:
        with db_connect() as conn:
            rows = conn.execute(
                """
                SELECT category, COUNT(*) AS total
                FROM questions
                WHERE category != 'simulacro_externo'
                GROUP BY category
                ORDER BY category
                """
            ).fetchall()
            source_rows = conn.execute(
                """
                SELECT source, COUNT(*) AS total, MAX(updated_at) AS updated_at
                FROM questions
                GROUP BY source
                ORDER BY source
                """
            ).fetchall()
            pool_stats = conn.execute(
                """
                SELECT COUNT(*) AS tests, COALESCE(MIN(question_count), 0) AS min_questions,
                       COALESCE(MAX(question_count), 0) AS max_questions
                FROM test_pool
                """
            ).fetchone()
            stats = conn.execute(
                """
                SELECT
                    category,
                    COUNT(*) AS attempts,
                    ROUND(AVG(score), 1) AS average_score,
                    SUM(passed) AS passed
                FROM attempts
                GROUP BY category
                """
            ).fetchall()

        stat_map = {row["category"]: dict(row) for row in stats}
        labels = {
            "general": "Test general",
            "senales": "Señales",
            "prioridad": "Prioridad",
            "seguridad": "Seguridad vial",
            "testsconducir_b": "TestsConducir B",
        }
        categories = [
            {
                "id": row["category"],
                "label": labels.get(row["category"], row["category"].title()),
                "questionCount": row["total"],
                "attempts": stat_map.get(row["category"], {}).get("attempts", 0),
                "averageScore": stat_map.get(row["category"], {}).get("average_score"),
                "passed": stat_map.get(row["category"], {}).get("passed", 0),
            }
            for row in rows
        ]
        categories.insert(
            0,
            {
                "id": "all",
                "label": "Aleatorio",
                "questionCount": sum(row["total"] for row in rows),
                "attempts": 0,
                "averageScore": None,
                "passed": 0,
            },
        )
        if pool_stats["tests"]:
            categories.insert(
                1,
                {
                    "id": "pool",
                    "label": "Pool externo",
                    "questionCount": pool_stats["tests"] * pool_stats["min_questions"],
                    "testCount": pool_stats["tests"],
                    "questionsPerTest": pool_stats["min_questions"],
                    "attempts": 0,
                    "averageScore": None,
                    "passed": 0,
                },
            )
        json_response(
            self,
            {
                "categories": categories,
                "sources": [dict(row) for row in source_rows],
            },
        )

    def get_tests(self) -> None:
        with db_connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    tp.id,
                    tp.title,
                    tp.question_count,
                    tp.source,
                    latest.id AS attempt_id,
                    latest.correct,
                    latest.errors,
                    latest.score,
                    latest.passed,
                    latest.duration_seconds,
                    latest.created_at,
                    (
                        SELECT COUNT(*)
                        FROM attempts a
                        WHERE a.test_id = tp.id
                    ) AS attempt_count
                FROM test_pool tp
                LEFT JOIN attempts latest ON latest.id = (
                    SELECT a2.id
                    FROM attempts a2
                    WHERE a2.test_id = tp.id
                    ORDER BY a2.id DESC
                    LIMIT 1
                )
                ORDER BY tp.id
                """
            ).fetchall()

        tests = []
        for index, row in enumerate(rows, start=1):
            tests.append(
                {
                    "id": row["id"],
                    "number": index,
                    "title": row["title"],
                    "questionCount": row["question_count"],
                    "source": row["source"],
                    "attemptCount": row["attempt_count"],
                    "lastAttempt": None
                    if row["attempt_id"] is None
                    else {
                        "id": row["attempt_id"],
                        "correct": row["correct"],
                        "errors": row["errors"],
                        "score": row["score"],
                        "passed": bool(row["passed"]),
                        "durationSeconds": row["duration_seconds"],
                        "createdAt": row["created_at"],
                    },
                }
            )
        json_response(self, {"tests": tests})

    def get_questions(self, query: str) -> None:
        params = parse_qs(query)
        mode = params.get("mode", [None])[0]
        if mode in {"mistakes", "smart"}:
            limit = min(max(int(params.get("limit", ["30"])[0]), 1), 30)
            self.get_practice_questions(mode, limit)
            return
        test_id = params.get("testId", [None])[0]
        if test_id is not None:
            self.get_test_questions(int(test_id))
            return
        category = params.get("category", ["all"])[0]
        limit = min(max(int(params.get("limit", ["10"])[0]), 1), 30)
        if category == "pool":
            self.get_pool_questions(limit)
            return
        where = ""
        args: list[object] = []
        if category != "all":
            where = "WHERE category = ?"
            args.append(category)

        with db_connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, category, theme, title, image_key, image_url, options_json,
                       correct_index, explanation, source, external_id, updated_at
                FROM questions
                {where}
                ORDER BY RANDOM()
                LIMIT ?
                """,
                [*args, limit],
            ).fetchall()

        json_response(self, {"questions": [question_for_test(row) for row in rows]})

    def get_practice_questions(self, mode: str, limit: int) -> None:
        with db_connect() as conn:
            if mode == "mistakes":
                rows = conn.execute(
                    """
                    SELECT q.id, q.category, q.theme, q.title, q.image_key, q.image_url,
                           q.options_json, q.correct_index, q.explanation, q.source,
                           q.external_id, q.updated_at,
                           COUNT(aa.id) AS wrong_count
                    FROM attempt_answers aa
                    JOIN questions q ON q.id = aa.question_id
                    WHERE aa.is_correct = 0
                    GROUP BY q.id
                    ORDER BY wrong_count DESC, RANDOM()
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    WITH theme_stats AS (
                        SELECT q.theme,
                               AVG(CASE WHEN aa.is_correct = 1 THEN 1.0 ELSE 0.0 END) AS accuracy
                        FROM attempt_answers aa
                        JOIN questions q ON q.id = aa.question_id
                        GROUP BY q.theme
                    ),
                    question_stats AS (
                        SELECT q.id,
                               SUM(CASE WHEN aa.is_correct = 0 THEN 1 ELSE 0 END) AS wrongs,
                               COUNT(aa.id) AS attempts
                        FROM questions q
                        LEFT JOIN attempt_answers aa ON aa.question_id = q.id
                        GROUP BY q.id
                    )
                    SELECT q.id, q.category, q.theme, q.title, q.image_key, q.image_url,
                           q.options_json, q.correct_index, q.explanation, q.source,
                           q.external_id, q.updated_at
                    FROM questions q
                    LEFT JOIN question_stats qs ON qs.id = q.id
                    LEFT JOIN theme_stats ts ON ts.theme = q.theme
                    WHERE q.source = 'testsconducir-pool'
                    ORDER BY
                        (COALESCE(qs.wrongs, 0) * 8)
                        + CASE WHEN COALESCE(ts.accuracy, 1) < 0.8 THEN 4 ELSE 0 END
                        + CASE WHEN COALESCE(qs.attempts, 0) = 0 THEN 2 ELSE 0 END
                        DESC,
                        RANDOM()
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

            if len(rows) < limit:
                existing = {row["id"] for row in rows}
                filler = conn.execute(
                    f"""
                    SELECT id, category, theme, title, image_key, image_url,
                           options_json, correct_index, explanation, source,
                           external_id, updated_at
                    FROM questions
                    WHERE source = 'testsconducir-pool'
                    {"AND id NOT IN (" + ",".join("?" for _ in existing) + ")" if existing else ""}
                    ORDER BY RANDOM()
                    LIMIT ?
                    """,
                    [*existing, limit - len(rows)] if existing else [limit - len(rows)],
                ).fetchall()
                rows = [*rows, *filler]

        title = "Repaso de fallos" if mode == "mistakes" else "Test inteligente"
        json_response(
            self,
            {
                "test": {"id": None, "title": title, "questionCount": len(rows), "mode": mode},
                "questions": [question_for_test(row) for row in rows],
            },
        )

    def get_dashboard(self) -> None:
        with db_connect() as conn:
            totals = conn.execute(
                """
                SELECT COUNT(*) AS attempts,
                       COALESCE(SUM(passed), 0) AS passed,
                       COALESCE(ROUND(AVG(errors), 1), 0) AS avg_errors
                FROM attempts
                WHERE test_id IS NOT NULL
                """
            ).fetchone()
            recent = conn.execute(
                """
                SELECT errors, passed, score, created_at
                FROM attempts
                WHERE test_id IS NOT NULL
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()
            theme_rows = conn.execute(
                """
                SELECT q.theme,
                       COUNT(aa.id) AS answered,
                       COALESCE(SUM(CASE WHEN aa.is_correct = 1 THEN 1 ELSE 0 END), 0) AS correct,
                       COALESCE(SUM(CASE WHEN aa.is_correct = 0 THEN 1 ELSE 0 END), 0) AS wrong
                FROM questions q
                LEFT JOIN attempt_answers aa ON aa.question_id = q.id
                WHERE q.source = 'testsconducir-pool'
                GROUP BY q.theme
                ORDER BY q.theme
                """
            ).fetchall()
            repeated_mistakes = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM (
                    SELECT question_id
                    FROM attempt_answers
                    WHERE is_correct = 0
                    GROUP BY question_id
                    HAVING COUNT(*) >= 2
                )
                """
            ).fetchone()["total"]

        recent_errors = [row["errors"] for row in recent]
        recent_passed = [row["passed"] for row in recent]
        if len(recent_errors) < 5:
            readiness = {
                "level": "pending",
                "label": "Aún faltan datos",
                "detail": "Haz al menos 5 simulacros para medir tu preparación.",
            }
        elif len(recent_errors) >= 10 and max(recent_errors[:10]) <= 2 and sum(recent_passed[:10]) >= 9:
            readiness = {
                "level": "ready",
                "label": "Listo para presentarte",
                "detail": "Tus últimos simulacros están en margen de examen.",
            }
        elif sum(recent_passed[:5]) >= 3:
            readiness = {
                "level": "good",
                "label": "Vas bien",
                "detail": "Sigue bajando fallos hasta moverte en 0-2 de forma constante.",
            }
        else:
            readiness = {
                "level": "not-ready",
                "label": "Aún no estás listo",
                "detail": "Refuerza los temas rojos y repasa tus fallos antes de presentarte.",
            }

        themes = []
        for row in theme_rows:
            accuracy = round((row["correct"] / row["answered"]) * 100, 1) if row["answered"] else None
            themes.append(
                {
                    "id": row["theme"],
                    "label": THEME_LABELS.get(row["theme"], row["theme"].title()),
                    "answered": row["answered"],
                    "correct": row["correct"],
                    "wrong": row["wrong"],
                    "accuracy": accuracy,
                    "status": "pending"
                    if accuracy is None
                    else ("pass" if accuracy >= 80 else "fail"),
                }
            )

        json_response(
            self,
            {
                "summary": {
                    "attempts": totals["attempts"],
                    "passed": totals["passed"],
                    "failed": totals["attempts"] - totals["passed"],
                    "averageErrors": totals["avg_errors"],
                    "repeatedMistakes": repeated_mistakes,
                },
                "readiness": readiness,
                "themes": themes,
                "recent": [dict(row) for row in recent],
            },
        )

    def get_pool_questions(self, limit: int) -> None:
        with db_connect() as conn:
            test = conn.execute(
                """
                SELECT id, title, question_count
                FROM test_pool
                WHERE question_count >= ?
                ORDER BY RANDOM()
                LIMIT 1
                """,
                (limit,),
            ).fetchone()
            if not test:
                json_response(self, {"questions": [], "test": None})
                return
            rows = conn.execute(
                """
                SELECT q.id, q.category, q.theme, q.title, q.image_key, q.image_url,
                       q.options_json, q.correct_index, q.explanation, q.source,
                       q.external_id, q.updated_at
                FROM test_pool_questions tpq
                JOIN questions q ON q.id = tpq.question_id
                WHERE tpq.test_id = ?
                ORDER BY tpq.position
                LIMIT ?
                """,
                (test["id"], limit),
            ).fetchall()

        json_response(
            self,
            {
                "test": {
                    "id": test["id"],
                    "title": test["title"],
                    "questionCount": test["question_count"],
                },
                "questions": [question_for_test(row) for row in rows],
            },
        )

    def get_test_questions(self, test_id: int) -> None:
        with db_connect() as conn:
            test = conn.execute(
                """
                SELECT id, title, question_count
                FROM test_pool
                WHERE id = ?
                """,
                (test_id,),
            ).fetchone()
            if not test:
                json_response(self, {"error": "Test no encontrado"}, 404)
                return
            rows = conn.execute(
                """
                SELECT q.id, q.category, q.theme, q.title, q.image_key, q.image_url,
                       q.options_json, q.correct_index, q.explanation, q.source,
                       q.external_id, q.updated_at
                FROM test_pool_questions tpq
                JOIN questions q ON q.id = tpq.question_id
                WHERE tpq.test_id = ?
                ORDER BY tpq.position
                """,
                (test_id,),
            ).fetchall()

        json_response(
            self,
            {
                "test": {
                    "id": test["id"],
                    "title": test["title"],
                    "questionCount": test["question_count"],
                },
                "questions": [question_for_test(row) for row in rows],
            },
        )

    def get_results(self) -> None:
        with db_connect() as conn:
            rows = conn.execute(
                """
                SELECT id, category, total, correct, errors, score, passed,
                       duration_seconds, created_at, test_id
                FROM attempts
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()
        json_response(self, {"results": [dict(row) for row in rows]})

    def save_result(self) -> None:
        payload = read_json(self)
        answers = payload.get("answers", [])
        category = payload.get("category", "all")
        test_id = payload.get("testId")
        test_id = None if test_id is None else int(test_id)
        duration = int(payload.get("durationSeconds", 0))

        if not answers:
            json_response(self, {"error": "No hay respuestas para guardar"}, 400)
            return

        question_ids = [int(answer["questionId"]) for answer in answers]
        placeholders = ",".join("?" for _ in question_ids)
        with db_connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, category, theme, title, image_key, image_url, options_json,
                       correct_index, explanation, source, external_id, updated_at
                FROM questions
                WHERE id IN ({placeholders})
                """,
                question_ids,
            ).fetchall()
            question_map = {row["id"]: row for row in rows}

            reviewed = []
            correct = 0
            for answer in answers:
                question_id = int(answer["questionId"])
                selected = answer.get("selectedIndex")
                selected = None if selected is None else int(selected)
                row = question_map[question_id]
                item = question_for_review(row, selected)
                reviewed.append(item)
                correct += 1 if item["isCorrect"] else 0

            total = len(reviewed)
            errors = total - correct
            score = round((correct / total) * 10, 1)
            passed = errors <= max_allowed_errors(total)

            cursor = conn.execute(
                """
                INSERT INTO attempts
                    (test_id, category, total, correct, errors, score, passed, duration_seconds)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    test_id,
                    category,
                    total,
                    correct,
                    errors,
                    score,
                    1 if passed else 0,
                    duration,
                ),
            )
            attempt_id = cursor.lastrowid
            conn.executemany(
                """
                INSERT INTO attempt_answers
                    (attempt_id, question_id, selected_index, is_correct)
                VALUES
                    (?, ?, ?, ?)
                """,
                [
                    (
                        attempt_id,
                        item["id"],
                        item["selectedIndex"],
                        1 if item["isCorrect"] else 0,
                    )
                    for item in reviewed
                ],
            )

        json_response(
            self,
            {
                "id": attempt_id,
                "testId": test_id,
                "category": category,
                "total": total,
                "correct": correct,
                "errors": errors,
                "score": score,
                "passed": passed,
                "durationSeconds": duration,
                "review": reviewed,
                "savedAt": int(time.time()),
            },
            201,
        )

    def serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        target = (PUBLIC_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(PUBLIC_DIR.resolve())) or not target.exists():
            json_response(self, {"error": "Archivo no encontrado"}, 404)
            return

        content_type, _ = mimetypes.guess_type(str(target))
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), AutoescuelaHandler)
    print(f"Autoescuela Test listo en http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
