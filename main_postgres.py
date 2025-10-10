import os
import json
import traceback
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
            return {"side_categories": [], "side_top": []}
        try:
            cats = execute_query(g.db,
                """
                SELECT c.id, c.name, COUNT(t.id) as task_count 
                FROM categories c LEFT JOIN tasks t ON t.category_id = c.id 
                GROUP BY c.id, c.name ORDER BY c.name
                """
            )
        except Exception:
            cats = []
        try:
            top = execute_query(g.db,
                "SELECT username, display_name, avatar_path, points FROM users ORDER BY points DESC, id ASC LIMIT 10"
            )
        except Exception:
            top = []
        return {"side_categories": cats, "side_top": top}

    @app.route("/")
    def index():
        if session.get("user_id"):
            return redirect(url_for("categories"))
        featured_categories = execute_query(g.db,
            "SELECT id, name FROM categories ORDER BY name LIMIT 6"
        )
        recent_tasks = execute_query(g.db,
            "SELECT id, title, points, level FROM tasks ORDER BY id DESC LIMIT 6"
        )
        top = execute_query(g.db,
            "SELECT username, display_name, avatar_path, points FROM users ORDER BY points DESC, id ASC LIMIT 5"
        )
        return render_template(
            "index.html",
            featured_categories=featured_categories,
            recent_tasks=recent_tasks,
            top=top,
        )

    @app.route("/search")
    def search():
        q = (request.args.get("q") or "").strip()
        level = (request.args.get("level") or "").strip()
        sql = "SELECT id, title, points, level FROM tasks WHERE 1=1"
        params = []
        if q:
            sql += " AND (title ILIKE %s OR description ILIKE %s)"
            like = f"%{q}%"
            params.extend([like, like])
        if level in {"easy", "medium", "hard"}:
            sql += " AND level = %s"
            params.append(level)
        sql += " ORDER BY id DESC LIMIT 100"
        results = execute_query(g.db, sql, params)
        return render_template("search.html", q=q, results=results)

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
                return redirect(url_for("categories"))
            flash("Неверный логин или пароль", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/dashboard")
    def dashboard():
        if session.get("user_id"):
            return redirect(url_for("categories"))
        return redirect(url_for("login"))

    @app.route("/tasks")
    def tasks_list():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        tasks = execute_query(g.db, "SELECT id, title FROM tasks ORDER BY id"
        )
        return render_template("dashboard.html", tasks=tasks, solutions=[])

    @app.route("/categories")
    def categories():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        
        # Получаем все категории
        cats = execute_query(g.db,
            """
            SELECT c.id, c.name, COUNT(t.id) as task_count 
            FROM categories c LEFT JOIN tasks t ON t.category_id = c.id 
            GROUP BY c.id, c.name ORDER BY c.name
            """
        )
        
        # Проверяем, выбрана ли конкретная категория
        category_id = request.args.get('category_id')
        selected_category = None
        tasks = []
        category_tree = []
        
        if category_id:
            try:
                category_id = int(category_id)
                selected_category = execute_one(g.db, "SELECT id, name FROM categories WHERE id=%s", (category_id,))
                if selected_category:
                    # Получаем задачи для выбранной категории
                    level_filter = (request.args.get("level") or "").strip()
                    sql = """
                        SELECT t.id, t.title, t.points, t.level, 
                        (SELECT s.passed FROM solutions s WHERE s.task_id = t.id AND s.user_id = %s 
                         ORDER BY s.created_at DESC, s.id DESC LIMIT 1) AS last_passed 
                        FROM tasks t WHERE t.category_id = %s
                    """
                    params = [user_id, category_id]
                    if level_filter in {"easy", "medium", "hard"}:
                        sql += " AND t.level = %s"
                        params.append(level_filter)
                    sql += " ORDER BY t.id"
                    tasks = execute_query(g.db, sql, params)
                    
                    # Создаем дерево категорий для левого меню
                    for c in cats:
                        cat_tasks_sql = """
                            SELECT t.id, t.title, t.level 
                            FROM tasks t 
                            WHERE t.category_id = %s 
                            ORDER BY t.level, t.id
                        """
                        cat_tasks = execute_query(g.db, cat_tasks_sql, (c['id'],))
                        
                        levels = {'easy': [], 'medium': [], 'hard': []}
                        for task in cat_tasks:
                            level = task['level'] or 'easy'
                            if level in levels:
                                levels[level].append(task)
                        
                        category_tree.append({
                            'id': c['id'],
                            'name': c['name'],
                            'task_count': c['task_count'],
                            'is_current': c['id'] == category_id,
                            'levels': levels
                        })
            except ValueError:
                pass  # Неверный category_id, игнорируем
        
        return render_template("categories.html", 
                             categories=cats, 
                             selected_category=selected_category,
                             tasks=tasks,
                             category_tree=category_tree)

    @app.route("/api/categories/<int:category_id>/tasks")
    def api_category_tasks(category_id: int):
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "unauthorized"}), 401
        sql = """
            SELECT t.id, t.title, t.points, t.level, 
            (SELECT s.passed FROM solutions s WHERE s.task_id = t.id AND s.user_id = %s 
             ORDER BY s.created_at DESC, s.id DESC LIMIT 1) AS last_passed 
            FROM tasks t WHERE t.category_id = %s ORDER BY t.level, t.id
        """
        rows = execute_query(g.db, sql, (user_id, category_id))
        groups = {"easy": [], "medium": [], "hard": [], "other": []}
        for r in rows:
            lvl = (r["level"] or "other").lower()
            key = lvl if lvl in groups else "other"
            groups[key].append({
                "id": r["id"],
                "title": r["title"],
                "points": r["points"],
                "level": r["level"],
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

    @app.route("/task/<int:task_id>", methods=["GET", "POST"])
    def task_detail(task_id: int):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))

        task = execute_one(g.db,
            """
            SELECT t.id, t.title, t.description, t.starter_code, t.level, t.category_id, c.name as category_name 
            FROM tasks t LEFT JOIN categories c ON c.id = t.category_id WHERE t.id=%s
            """,
            (task_id,),
        )
        if not task:
            flash("Задача не найдена", "error")
            return redirect(url_for("dashboard"))

        last_solution = execute_one(g.db,
            "SELECT code FROM solutions WHERE user_id=%s AND task_id=%s ORDER BY created_at DESC LIMIT 1",
            (user_id, task_id),
        )
        code_prefill = last_solution["code"] if last_solution else task["starter_code"]

        results: Dict[str, object] | None = None
        tags = execute_query(g.db,
            "SELECT tg.name FROM tags tg JOIN task_tags tt ON tt.tag_id = tg.id WHERE tt.task_id = %s ORDER BY tg.name",
            (task_id,),
        )

        # Build sidebar tree for this task's category
        sidebar_tree = None
        if task and task["category_id"] is not None:
            rows = execute_query(g.db,
                "SELECT id, title, level FROM tasks WHERE category_id=%s ORDER BY level, id",
                (task["category_id"],),
            )
            groups: Dict[str, List[Dict[str, object]]] = {"easy": [], "medium": [], "hard": [], "other": []}
            for r in rows:
                lvl = (r["level"] or "other").lower()
                key = lvl if lvl in groups else "other"
                groups[key].append({"id": r["id"], "title": r["title"], "level": lvl})
            sidebar_tree = {
                "category": {"id": task["category_id"], "name": task["category_name"]},
                "groups": groups,
                "current_task_id": task_id,
            }

        if request.method == "POST":
            user_code = request.form.get("code") or ""
            duration_ms = int(request.form.get("duration_ms") or 0)
            if not user_code.strip():
                flash("Код решения не может быть пустым", "error")
                return render_template(
                    "task.html", task=task, code_prefill=user_code, results=None, tags=tags
                )

            testcases = execute_query(g.db,
                "SELECT input_text, expected_output FROM testcases WHERE task_id=%s ORDER BY id",
                (task_id,),
            )
            tests: List[Tuple[str, str]] = [
                (row["input_text"], row["expected_output"]) for row in testcases
            ]

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
                    task_id,
                    user_code,
                    passed,
                    json.dumps(judge_report, ensure_ascii=False),
                    duration_ms,
                ),
            )

            if passed:
                already_passed = execute_one(g.db,
                    "SELECT 1 FROM solutions WHERE user_id=%s AND task_id=%s AND passed=1 LIMIT 1",
                    (user_id, task_id),
                )
                # Текущее решение уже вставлено, проверим было ли до этого
                prev_passed = execute_one(g.db,
                    "SELECT 1 FROM solutions WHERE user_id=%s AND task_id=%s AND passed=1 AND id < (SELECT MAX(id) FROM solutions WHERE user_id=%s AND task_id=%s) LIMIT 1",
                    (user_id, task_id, user_id, task_id),
                )
                if not prev_passed:
                    # начислить очки за первую сдачу
                    task_row = execute_one(g.db, "SELECT points FROM tasks WHERE id=%s", (task_id,))
                    task_points = int(task_row["points"]) if task_row and task_row["points"] is not None else 100
                    cursor.execute("UPDATE users SET points = COALESCE(points,0) + %s WHERE id=%s", (task_points, user_id))
                    flash(f"Задача зачтена! +{task_points} очков", "success")
            
            g.db.commit()
            cursor.close()

            results = judge_report  # type: ignore[assignment]

        return render_template(
            "task.html",
            task=task,
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
            SELECT s.id, t.title AS task_title, s.passed, s.created_at 
            FROM solutions s JOIN tasks t ON t.id = s.task_id 
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
            t.title AS task_title 
            FROM solutions s JOIN tasks t ON t.id = s.task_id WHERE s.id = %s
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
