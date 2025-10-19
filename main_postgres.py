import os
import json
import traceback
import html
from datetime import datetime
from multiprocessing import Process, Queue
from typing import Dict, List, Tuple
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    g,
    flash,
    jsonify,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Импорт для работы с PostgreSQL
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from sqlalchemy import create_engine, text
except ImportError:
    print("❌ Ошибка: Не установлены библиотеки для PostgreSQL")
    print("Установите их командой: pip install psycopg2-binary sqlalchemy")
    exit(1)

# Конфигурация PostgreSQL
POSTGRES_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 5432)),
    'database': os.environ.get('DB_NAME', 'coding_platform'),
    'user': os.environ.get('DB_USER', 'admin'),
    'password': os.environ.get('DB_PASSWORD', 'Sserapheam17*'),
    'client_encoding': 'utf8'
}

def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET", "dev-secret-change-me")
    app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "static", "uploads")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # Функция для обработки HTML-сущностей и форматирования
    def clean_html_entities(text):
        if not text:
            return text
        # Декодируем HTML-сущности
        text = html.unescape(text)
        # Заменяем &nbsp; на обычные пробелы
        text = text.replace('&nbsp;', ' ')
        
        # Красивое форматирование для задач
        import re
        
        # Сначала разбиваем текст на части
        parts = []
        current_part = ""
        
        # Разбиваем по ключевым словам
        keywords = ['Example \d+:', 'Input:', 'Output:', 'Explanation:', 'Constraints:', 'Follow-up:']
        pattern = '|'.join(keywords)
        
        # Находим все вхождения ключевых слов
        matches = list(re.finditer(pattern, text))
        
        if not matches:
            # Если нет ключевых слов, возвращаем как есть
            return f'<p class="description-paragraph">{text}</p>'
        
        # Обрабатываем каждую секцию
        last_end = 0
        for match in matches:
            # Добавляем текст до ключевого слова
            if match.start() > last_end:
                before_text = text[last_end:match.start()].strip()
                if before_text:
                    parts.append(('text', before_text))
            
            # Обрабатываем ключевое слово и следующий текст
            keyword = match.group()
            next_match = matches[matches.index(match) + 1] if matches.index(match) + 1 < len(matches) else None
            end_pos = next_match.start() if next_match else len(text)
            
            content = text[match.end():end_pos].strip()
            
            if keyword.startswith('Example'):
                parts.append(('example', keyword, content))
            elif keyword in ['Input:', 'Output:']:
                parts.append(('input_output', keyword, content))
            elif keyword == 'Explanation:':
                parts.append(('explanation', content))
            elif keyword == 'Constraints:':
                parts.append(('constraints', content))
            elif keyword == 'Follow-up:':
                parts.append(('followup', content))
            
            last_end = end_pos
        
        # Создаем HTML
        html_content = []
        in_example = False
        
        for part in parts:
            if part[0] == 'text':
                html_content.append(f'<p class="description-paragraph">{part[1]}</p>')
            elif part[0] == 'example':
                if in_example:
                    html_content.append('</div>')
                html_content.append(f'<div class="example-section"><h4 class="example-title">{part[1]}</h4>')
                in_example = True
            elif part[0] == 'input_output':
                html_content.append(f'<div class="input-output"><strong>{part[1]}</strong> <code class="code-snippet">{part[2]}</code></div>')
            elif part[0] == 'explanation':
                html_content.append(f'<div class="explanation"><strong>Explanation:</strong> {part[1]}</div>')
            elif part[0] == 'constraints':
                if in_example:
                    html_content.append('</div>')
                    in_example = False
                html_content.append(f'<div class="constraints-section"><h4 class="constraints-title">Constraints:</h4>')
                # Разбиваем ограничения по строкам
                constraints = part[1].split('.')
                for constraint in constraints:
                    constraint = constraint.strip()
                    if constraint:
                        html_content.append(f'<div class="constraint-item">{constraint}</div>')
                html_content.append('</div>')
            elif part[0] == 'followup':
                if in_example:
                    html_content.append('</div>')
                    in_example = False
                html_content.append(f'<div class="followup-section"><h4 class="followup-title">Follow-up:</h4><p class="followup-text">{part[1]}</p></div>')
        
        if in_example:
            html_content.append('</div>')
        
        return ''.join(html_content)

    # Регистрируем фильтр для шаблонов
    @app.template_filter('clean_html')
    def clean_html_filter(text):
        return clean_html_entities(text)

    @app.before_request
    def before_request() -> None:
        g.db = get_db()
        if g.db is None:
            from flask import abort
            abort(500, "Ошибка подключения к базе данных")

    @app.teardown_request
    def teardown_request(exception) -> None:
        conn = getattr(g, "db", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass  # Игнорируем ошибки при закрытии

    @app.context_processor
    def inject_sidebars():
        if not session.get("user_id"):
            return {"side_topics": [], "side_top": []}
        try:
            # Получаем уникальные темы из задач
            topics = execute_query(g.db,
                """
                SELECT DISTINCT unnest(topic_tags) as topic, COUNT(*) as problem_count
                FROM real_leetcode_problems 
                WHERE topic_tags IS NOT NULL
                GROUP BY unnest(topic_tags)
                ORDER BY topic
                """
            )
        except Exception:
            topics = []
        try:
            top = execute_query(g.db,
                "SELECT username, display_name, avatar_path, points FROM users ORDER BY points DESC, id ASC LIMIT 10"
            )
        except Exception:
            top = []
        return {"side_topics": topics, "side_top": top}

    @app.route("/")
    def index():
        if session.get("user_id"):
            return redirect(url_for("problems"))
        
        # Получаем популярные темы
        featured_topics = execute_query(g.db,
            """
            SELECT topic, COUNT(*) as problem_count
            FROM real_leetcode_problems 
            WHERE topic IS NOT NULL AND topic != ''
            GROUP BY topic
            ORDER BY problem_count DESC
            LIMIT 6
            """
        )
        
        # Получаем недавние задачи
        recent_problems = execute_query(g.db,
            """
            SELECT problem_id, title, difficulty, topic_tags
            FROM real_leetcode_problems 
            ORDER BY id DESC 
            LIMIT 6
            """
        )
        
        top = execute_query(g.db,
            "SELECT username, display_name, avatar_path, points FROM users ORDER BY points DESC, id ASC LIMIT 5"
        )
        return render_template(
            "index.html",
            featured_topics=featured_topics,
            recent_problems=recent_problems,
            top=top,
        )

    @app.route("/search")
    def search():
        q = (request.args.get("q") or "").strip()
        difficulty = (request.args.get("difficulty") or "").strip()
        topic = (request.args.get("topic") or "").strip()
        
        sql = """
            SELECT problem_id, title, difficulty, topic_tags, topic
            FROM real_leetcode_problems 
            WHERE 1=1
        """
        params = []
        
        if q:
            sql += " AND (title ILIKE %s OR description ILIKE %s)"
            like = f"%{q}%"
            params.extend([like, like])
        
        if difficulty in {"Easy", "Medium", "Hard"}:
            sql += " AND difficulty = %s"
            params.append(difficulty)
            
        if topic:
            sql += " AND %s = ANY(topic_tags)"
            params.append(topic)
            
        sql += " ORDER BY problem_id DESC LIMIT 100"
        results = execute_query(g.db, sql, params)
        return render_template("search.html", q=q, difficulty=difficulty, topic=topic, results=results)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            if not username or not password:
                flash("Введите логин и пароль", "error")
                return render_template("register.html")
            try:
                cursor = get_cursor(g.db)
                cursor.execute(
                    "INSERT INTO users(username, password_hash, created_at) VALUES(%s,%s,NOW())",
                    (username, generate_password_hash(password)),
                )
                g.db.commit()
                cursor.close()
                flash("Регистрация прошла успешно. Войдите в аккаунт.", "success")
                return redirect(url_for("login"))
            except psycopg2.IntegrityError:
                flash("Пользователь с таким именем уже существует", "error")
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            user = execute_one(g.db,
                "SELECT id, password_hash FROM users WHERE username=%s", (username,)
            )
            if user and check_password_hash(user["password_hash"], password):
                session["user_id"] = user["id"]
                session["username"] = username
                return redirect(url_for("problems"))
            flash("Неверный логин или пароль", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/dashboard")
    def dashboard():
        if session.get("user_id"):
            return redirect(url_for("problems"))
        return redirect(url_for("login"))

    @app.route("/problems-list")
    def problems_list():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        problems = execute_query(g.db, "SELECT problem_id, title, difficulty FROM real_leetcode_problems ORDER BY problem_id")
        return render_template("problems_list.html", problems=problems)

    @app.route("/problems")
    def problems():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        
        # Получаем все темы из колонки topic
        topics = execute_query(g.db,
            """
            SELECT topic, COUNT(*) as problem_count
            FROM real_leetcode_problems 
            WHERE topic IS NOT NULL AND topic != ''
            GROUP BY topic
            ORDER BY problem_count DESC, topic
            """
        )
        
        # Проверяем, выбрана ли конкретная тема
        selected_topic = request.args.get('topic')
        difficulty_filter = request.args.get('difficulty', '').strip()
        
        problems = []
        
        if selected_topic:
            # Получаем задачи для выбранной темы
            sql = """
                SELECT problem_id, title, difficulty, topic_tags,
                (SELECT s.passed FROM solutions s WHERE s.task_id = problem_id AND s.user_id = %s 
                 ORDER BY s.created_at DESC, s.id DESC LIMIT 1) AS last_passed 
                FROM real_leetcode_problems 
                WHERE topic = %s
            """
            params = [user_id, selected_topic]
            
            if difficulty_filter in {"Easy", "Medium", "Hard"}:
                sql += " AND difficulty = %s"
                params.append(difficulty_filter)
                
            sql += " ORDER BY problem_id"
            problems = execute_query(g.db, sql, params)
        
        # Если не выбрана конкретная тема, получаем все задачи
        all_problems = []
        if not selected_topic:
            all_problems = execute_query(g.db,
                """
                SELECT problem_id, title, difficulty, topic_tags,
                (SELECT s.passed FROM solutions s WHERE s.task_id = problem_id AND s.user_id = %s 
                 ORDER BY s.created_at DESC, s.id DESC LIMIT 1) AS last_passed 
                FROM real_leetcode_problems 
                ORDER BY problem_id
                """,
                (user_id,)
            )
        
        return render_template("problems.html", 
                             topics=topics, 
                             selected_topic=selected_topic,
                             difficulty_filter=difficulty_filter,
                             problems=problems,
                             all_problems=all_problems)

    @app.route("/api/topics/<topic>/problems")
    def api_topic_problems(topic: str):
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "unauthorized"}), 401
        
        sql = """
            SELECT problem_id, title, difficulty, topic_tags,
            (SELECT s.passed FROM solutions s WHERE s.task_id = problem_id AND s.user_id = %s 
             ORDER BY s.created_at DESC, s.id DESC LIMIT 1) AS last_passed 
            FROM real_leetcode_problems 
            WHERE topic = %s 
            ORDER BY difficulty, problem_id
        """
        rows = execute_query(g.db, sql, (user_id, topic))
        groups = {"Easy": [], "Medium": [], "Hard": []}
        for r in rows:
            difficulty = r["difficulty"] or "Easy"
            if difficulty in groups:
                groups[difficulty].append({
                    "problem_id": r["problem_id"],
                    "title": r["title"],
                    "difficulty": r["difficulty"],
                    "last_passed": r["last_passed"],
                })
        return jsonify(groups)

    @app.route("/profile", methods=["GET", "POST"])
    def profile():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        if request.method == "POST":
            display_name = (request.form.get("display_name") or "").strip()
            bio = (request.form.get("bio") or "").strip()

            avatar_file = request.files.get("avatar")
            avatar_path = None
            if avatar_file and avatar_file.filename:
                filename = secure_filename(avatar_file.filename)
                name, ext = os.path.splitext(filename)
                if ext.lower() in {".png", ".jpg", ".jpeg", ".gif"}:
                    unique_name = f"u{user_id}_{int(datetime.utcnow().timestamp())}{ext.lower()}"
                    save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                    avatar_file.save(save_path)
                    avatar_path = f"uploads/{unique_name}"
                else:
                    flash("Допустимы только изображения: PNG, JPG, GIF", "error")

            cursor = get_cursor(g.db)
            if avatar_path:
                cursor.execute(
                    "UPDATE users SET display_name=%s, bio=%s, avatar_path=%s WHERE id=%s",
                    (display_name or None, bio or None, avatar_path, user_id),
                )
            else:
                cursor.execute(
                    "UPDATE users SET display_name=%s, bio=%s WHERE id=%s",
                    (display_name or None, bio or None, user_id),
                )
            g.db.commit()
            cursor.close()
            flash("Профиль обновлён", "success")
            return redirect(url_for("profile"))

        user = execute_one(g.db,
            "SELECT username, display_name, bio, avatar_path, points FROM users WHERE id=%s",
            (user_id,),
        )
        return render_template("profile.html", user=user)

    @app.route("/leaderboard")
    def leaderboard():
        top = execute_query(g.db, "SELECT username, display_name, avatar_path, points FROM users ORDER BY points DESC, id ASC LIMIT 20"
        )
        return render_template("leaderboard.html", top=top)

    @app.route("/problem/<int:problem_id>", methods=["GET", "POST"])
    def problem_detail(problem_id: int):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))

        problem = execute_one(g.db,
            """
            SELECT problem_id, title, description, difficulty, 
                   topic_tags, topic, code_python3, code_javascript, code_java, 
                   code_c, code_csharp, code_cpp, examples, constraints
            FROM real_leetcode_problems 
            WHERE problem_id=%s
            """,
            (problem_id,),
        )
        if not problem:
            flash("Задача не найдена", "error")
            return redirect(url_for("problems"))

        last_solution = execute_one(g.db,
            "SELECT code FROM solutions WHERE user_id=%s AND task_id=%s ORDER BY created_at DESC LIMIT 1",
            (user_id, problem_id),
        )
        code_prefill = last_solution["code"] if last_solution else (problem["code_python3"] or "")

        results: Dict[str, object] | None = None
        
        # Получаем теги задачи
        tags = problem["topic_tags"] or []

        # Создаем боковое меню для задач той же темы
        sidebar_tree = None
        if problem and problem["topic"]:
            rows = execute_query(g.db,
                "SELECT problem_id, title, difficulty FROM real_leetcode_problems WHERE topic=%s ORDER BY difficulty, problem_id",
                (problem["topic"],),
            )
            groups: Dict[str, List[Dict[str, object]]] = {"Easy": [], "Medium": [], "Hard": []}
            for r in rows:
                difficulty = r["difficulty"] or "Easy"
                if difficulty in groups:
                    groups[difficulty].append({
                        "problem_id": r["problem_id"], 
                        "title": r["title"], 
                        "difficulty": difficulty
                    })
            sidebar_tree = {
                "topic": {"name": problem["topic"]},
                "groups": groups,
                "current_problem_id": problem_id,
            }

        if request.method == "POST":
            user_code = request.form.get("code") or ""
            duration_ms = int(request.form.get("duration_ms") or 0)
            if not user_code.strip():
                flash("Код решения не может быть пустым", "error")
                return render_template(
                    "problem.html", problem=problem, code_prefill=user_code, results=None, tags=tags
                )

            # Для LeetCode задач пока используем простую проверку
            # В будущем можно добавить реальные тест-кейсы
            testcases = [("", "")]  # Заглушка
            tests: List[Tuple[str, str]] = testcases

            judge_report = judge_user_code(user_code, tests, time_limit_sec=2.0)
            passed = int(judge_report["passed"])  # type: ignore[index]

            cursor = get_cursor(g.db)
            cursor.execute(
                """
                INSERT INTO solutions(user_id, task_id, code, passed, result_json, created_at, duration_ms)
                VALUES(%s,%s,%s,%s,%s,NOW(),%s)
                """,
                (
                    user_id,
                    problem_id,
                    user_code,
                    passed,
                    json.dumps(judge_report, ensure_ascii=False),
                    duration_ms,
                ),
            )

            if passed:
                # Проверяем, решалась ли задача ранее
                prev_passed = execute_one(g.db,
                    "SELECT 1 FROM solutions WHERE user_id=%s AND task_id=%s AND passed=1 AND id < (SELECT MAX(id) FROM solutions WHERE user_id=%s AND task_id=%s) LIMIT 1",
                    (user_id, problem_id, user_id, problem_id),
                )
                if not prev_passed:
                    # Начисляем очки за первую сдачу
                    points = 100 if problem["difficulty"] == "Easy" else (200 if problem["difficulty"] == "Medium" else 300)
                    cursor.execute("UPDATE users SET points = COALESCE(points,0) + %s WHERE id=%s", (points, user_id))
                    flash(f"Задача решена! +{points} очков", "success")
            
            g.db.commit()
            cursor.close()

            results = judge_report  # type: ignore[assignment]

        return render_template(
            "problem.html",
            problem=problem,
            code_prefill=code_prefill,
            results=results,
            tags=tags,
            sidebar_tree=sidebar_tree,
        )

    @app.route("/submissions")
    def submissions():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        subs = execute_query(g.db,
            """
            SELECT s.id, p.title AS problem_title, s.passed, s.created_at, p.difficulty
            FROM solutions s JOIN real_leetcode_problems p ON p.problem_id = s.task_id 
            WHERE s.user_id = %s ORDER BY s.created_at DESC, s.id DESC
            """,
            (user_id,),
        )
        return render_template("submissions.html", submissions=subs)

    @app.route("/submission/<int:solution_id>")
    def submission_detail(solution_id: int):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        s = execute_one(g.db,
            """
            SELECT s.id, s.user_id, s.task_id, s.code, s.passed, s.result_json, s.created_at, 
            p.title AS problem_title, p.difficulty
            FROM solutions s JOIN real_leetcode_problems p ON p.problem_id = s.task_id WHERE s.id = %s
            """,
            (solution_id,),
        )
        if not s:
            flash("Отправка не найдена", "error")
            return redirect(url_for("submissions"))
        if s["user_id"] != user_id:
            flash("Нет доступа к этой отправке", "error")
            return redirect(url_for("submissions"))
        try:
            result = json.loads(s["result_json"]) if s["result_json"] else None
        except Exception:  # noqa: BLE001
            result = None
        return render_template("submission_detail.html", submission=s, result=result)

    return app


def get_db():
    """Получение подключения к PostgreSQL"""
    try:
        conn = psycopg2.connect(**POSTGRES_CONFIG)
        conn.autocommit = False
        return conn
    except Exception as e:
        print(f"Ошибка подключения к базе данных: {e}")
        return None

def get_cursor(conn=None):
    """Получение курсора из соединения"""
    if conn is None:
        conn = get_db()
    if conn is None:
        return None
    return conn.cursor(cursor_factory=RealDictCursor)

def execute_query(conn, query, params=None):
    """Выполнение запроса и возврат результата"""
    cursor = get_cursor(conn)
    cursor.execute(query, params or ())
    result = cursor.fetchall()
    cursor.close()
    return result

def execute_one(conn, query, params=None):
    """Выполнение запроса и возврат одного результата"""
    cursor = get_cursor(conn)
    cursor.execute(query, params or ())
    result = cursor.fetchone()
    cursor.close()
    return result


def judge_user_code(
    user_code: str, testcases: List[Tuple[str, str]], time_limit_sec: float = 2.0
) -> Dict[str, object]:
    results: List[Dict[str, object]] = []
    all_passed = True
    for idx, (input_text, expected_output) in enumerate(testcases, start=1):
        outcome = _run_with_timeout(user_code, input_text, time_limit_sec)
        if outcome.get("timeout"):
            case_res = {
                "case": idx,
                "status": "TIMEOUT",
                "message": f"Превышено время {time_limit_sec:.1f}s",
            }
            all_passed = False
        elif not outcome.get("ok"):
            case_res = {
                "case": idx,
                "status": "RUNTIME_ERROR",
                "message": outcome.get("error"),
                "traceback": outcome.get("traceback"),
            }
            all_passed = False
        else:
            actual = (outcome.get("stdout") or "").strip()
            expected = (expected_output or "").strip()
            if actual == expected:
                case_res = {"case": idx, "status": "OK"}
            else:
                case_res = {
                    "case": idx,
                    "status": "WA",
                    "message": f"Ожидалось: {expected!r}, получено: {actual!r}",
                }
                all_passed = False
        results.append(case_res)

    return {"passed": all_passed, "results": results}


def _target_exec(code: str, input_text: str, q: Queue) -> None:
    try:
        import builtins
        import io
        import sys

        captured_stdout = io.StringIO()
        stdin_buf = io.StringIO(input_text)
        original_stdout = sys.stdout
        original_stdin = sys.stdin
        sys.stdout = captured_stdout
        sys.stdin = stdin_buf

        allowed_builtin_names = [
            "abs",
            "all",
            "any",
            "bin",
            "bool",
            "chr",
            "divmod",
            "enumerate",
            "float",
            "format",
            "hash",
            "hex",
            "int",
            "isinstance",
            "len",
            "list",
            "map",
            "max",
            "min",
            "ord",
            "pow",
            "print",
            "range",
            "round",
            "str",
            "sum",
            "zip",
            "input",
        ]
        safe_builtins = {name: getattr(builtins, name) for name in allowed_builtin_names}
        restricted_globals = {"__builtins__": safe_builtins}

        exec(code, restricted_globals, None)

        q.put({"ok": True, "stdout": captured_stdout.getvalue()})
    except Exception as exc:  # noqa: BLE001
        q.put(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
    finally:
        try:
            sys.stdout = original_stdout
            sys.stdin = original_stdin
        except Exception:  # noqa: BLE001
            pass


def _run_with_timeout(code: str, input_text: str, time_limit_sec: float) -> Dict[str, object]:
    q: Queue = Queue()
    p = Process(target=_target_exec, args=(code, input_text, q))
    p.start()
    p.join(time_limit_sec)
    if p.is_alive():
        p.terminate()
        p.join(0.1)
        return {"timeout": True}
    try:
        return q.get_nowait()
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "Не удалось получить результат"}


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
