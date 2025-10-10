import os
import sqlite3
import json
import traceback
from datetime import datetime
from multiprocessing import Process, Queue
from typing import Dict, List, Tuple

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


APP_DB_NAME = "app.db"


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET", "dev-secret-change-me")
    app.config["DATABASE"] = os.path.join(os.path.dirname(__file__), APP_DB_NAME)
    app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "static", "uploads")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    @app.before_request
    def before_request() -> None:
        g.db = get_db(app.config["DATABASE"])

    @app.teardown_request
    def teardown_request(exception) -> None:  # type: ignore[no-redef]
        db = getattr(g, "db", None)
        if db is not None:
            db.close()

    ensure_schema_and_seed(app)
    add_missing_tasks()

    @app.context_processor
    def inject_sidebars():
        if not session.get("user_id"):
            return {"side_categories": [], "side_top": []}
        try:
            cats = g.db.execute(
                (
                    "SELECT c.id, c.name, COUNT(t.id) as task_count "
                    "FROM categories c LEFT JOIN tasks t ON t.category_id = c.id "
                    "GROUP BY c.id, c.name ORDER BY c.name"
                )
            ).fetchall()
        except Exception:
            cats = []
        try:
            top = g.db.execute(
                "SELECT username, display_name, avatar_path, points FROM users ORDER BY points DESC, id ASC LIMIT 10"
            ).fetchall()
        except Exception:
            top = []
        return {"side_categories": cats, "side_top": top}

    @app.route("/")
    def index():
        if session.get("user_id"):
            return redirect(url_for("categories"))
        featured_categories = g.db.execute(
            "SELECT id, name FROM categories ORDER BY name LIMIT 6"
        ).fetchall()
        recent_tasks = g.db.execute(
            "SELECT id, title, points, level FROM tasks ORDER BY id DESC LIMIT 6"
        ).fetchall()
        top = g.db.execute(
            "SELECT username, display_name, avatar_path, points FROM users ORDER BY points DESC, id ASC LIMIT 5"
        ).fetchall()
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
        params: List[str] = []
        if q:
            sql += " AND (title LIKE ? OR description LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like])
        if level in {"easy", "medium", "hard"}:
            sql += " AND level = ?"
            params.append(level)
        sql += " ORDER BY id DESC LIMIT 100"
        results = g.db.execute(sql, params).fetchall()
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
                with g.db:
                    g.db.execute(
                        "INSERT INTO users(username, password_hash, created_at) VALUES(?,?,?)",
                        (username, generate_password_hash(password), now()),
                    )
                flash("Регистрация прошла успешно. Войдите в аккаунт.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("Пользователь с таким именем уже существует", "error")
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            user = g.db.execute(
                "SELECT id, password_hash FROM users WHERE username=?", (username,)
            ).fetchone()
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
        # Страница не нужна — перенаправляем в Задачи
        if session.get("user_id"):
            return redirect(url_for("categories"))
        return redirect(url_for("login"))

    @app.route("/tasks")
    def tasks_list():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        tasks = g.db.execute(
            "SELECT id, title FROM tasks ORDER BY id"
        ).fetchall()
        return render_template("dashboard.html", tasks=tasks, solutions=[])

    @app.route("/categories")
    def categories():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        
        # Получаем все категории
        cats = g.db.execute(
            (
                "SELECT c.id, c.name, COUNT(t.id) as task_count "
                "FROM categories c LEFT JOIN tasks t ON t.category_id = c.id "
                "GROUP BY c.id, c.name ORDER BY c.name"
            )
        ).fetchall()
        
        # Проверяем, выбрана ли конкретная категория
        category_id = request.args.get('category_id')
        selected_category = None
        tasks = []
        category_tree = []
        
        if category_id:
            try:
                category_id = int(category_id)
                selected_category = g.db.execute("SELECT id, name FROM categories WHERE id=?", (category_id,)).fetchone()
                if selected_category:
                    # Получаем задачи для выбранной категории
                    level_filter = (request.args.get("level") or "").strip()
                    sql = (
                        "SELECT t.id, t.title, t.points, t.level, "
                        "(SELECT s.passed FROM solutions s WHERE s.task_id = t.id AND s.user_id = ? "
                        " ORDER BY s.created_at DESC, s.id DESC LIMIT 1) AS last_passed "
                        "FROM tasks t WHERE t.category_id = ?"
                    )
                    params = [user_id, category_id]
                    if level_filter in {"easy", "medium", "hard"}:
                        sql += " AND t.level = ?"
                        params.append(level_filter)
                    sql += " ORDER BY t.id"
                    tasks = g.db.execute(sql, params).fetchall()
                    
                    # Создаем дерево категорий для левого меню
                    for c in cats:
                        cat_tasks_sql = """
                            SELECT t.id, t.title, t.level 
                            FROM tasks t 
                            WHERE t.category_id = ? 
                            ORDER BY t.level, t.id
                        """
                        cat_tasks = g.db.execute(cat_tasks_sql, (c['id'],)).fetchall()
                        
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
        sql = (
            "SELECT t.id, t.title, t.points, t.level, "
            "(SELECT s.passed FROM solutions s WHERE s.task_id = t.id AND s.user_id = ? "
            " ORDER BY s.created_at DESC, s.id DESC LIMIT 1) AS last_passed "
            "FROM tasks t WHERE t.category_id = ? ORDER BY t.level, t.id"
        )
        rows = g.db.execute(sql, (user_id, category_id)).fetchall()
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

            with g.db:
                if avatar_path:
                    g.db.execute(
                        "UPDATE users SET display_name=?, bio=?, avatar_path=? WHERE id=?",
                        (display_name or None, bio or None, avatar_path, user_id),
                    )
                else:
                    g.db.execute(
                        "UPDATE users SET display_name=?, bio=? WHERE id=?",
                        (display_name or None, bio or None, user_id),
                    )
            flash("Профиль обновлён", "success")
            return redirect(url_for("profile"))

        user = g.db.execute(
            "SELECT username, display_name, bio, avatar_path, points FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
        return render_template("profile.html", user=user)

    @app.route("/leaderboard")
    def leaderboard():
        top = g.db.execute(
            "SELECT username, display_name, avatar_path, points FROM users ORDER BY points DESC, id ASC LIMIT 20"
        ).fetchall()
        return render_template("leaderboard.html", top=top)

    @app.route("/task/<int:task_id>", methods=["GET", "POST"])
    def task_detail(task_id: int):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))

        task = g.db.execute(
            (
                "SELECT t.id, t.title, t.description, t.starter_code, t.level, t.category_id, c.name as category_name "
                "FROM tasks t LEFT JOIN categories c ON c.id = t.category_id WHERE t.id=?"
            ),
            (task_id,),
        ).fetchone()
        if not task:
            flash("Задача не найдена", "error")
            return redirect(url_for("dashboard"))

        last_solution = g.db.execute(
            "SELECT code FROM solutions WHERE user_id=? AND task_id=? ORDER BY created_at DESC LIMIT 1",
            (user_id, task_id),
        ).fetchone()
        code_prefill = last_solution["code"] if last_solution else task["starter_code"]

        results: Dict[str, object] | None = None
        tags = g.db.execute(
            "SELECT tg.name FROM tags tg JOIN task_tags tt ON tt.tag_id = tg.id WHERE tt.task_id = ? ORDER BY tg.name",
            (task_id,),
        ).fetchall()

        # Build sidebar tree for this task's category
        sidebar_tree = None
        if task and task["category_id"] is not None:
            rows = g.db.execute(
                "SELECT id, title, level FROM tasks WHERE category_id=? ORDER BY level, id",
                (task["category_id"],),
            ).fetchall()
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

            testcases = g.db.execute(
                "SELECT input_text, expected_output FROM testcases WHERE task_id=? ORDER BY id",
                (task_id,),
            ).fetchall()
            tests: List[Tuple[str, str]] = [
                (row["input_text"], row["expected_output"]) for row in testcases
            ]

            judge_report = judge_user_code(user_code, tests, time_limit_sec=2.0)
            passed = int(judge_report["passed"])  # type: ignore[index]

            with g.db:
                g.db.execute(
                    """
                    INSERT INTO solutions(user_id, task_id, code, passed, result_json, created_at, duration_ms)
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        user_id,
                        task_id,
                        user_code,
                        passed,
                        json.dumps(judge_report, ensure_ascii=False),
                        now(),
                        duration_ms,
                    ),
                )

                if passed:
                    already_passed = g.db.execute(
                        "SELECT 1 FROM solutions WHERE user_id=? AND task_id=? AND passed=1 LIMIT 1",
                        (user_id, task_id),
                    ).fetchone()
                    # Текущее решение уже вставлено, проверим было ли до этого
                    prev_passed = g.db.execute(
                        "SELECT 1 FROM solutions WHERE user_id=? AND task_id=? AND passed=1 AND id < last_insert_rowid() LIMIT 1",
                        (user_id, task_id),
                    ).fetchone()
                    if not prev_passed:
                        # начислить очки за первую сдачу
                        task_row = g.db.execute("SELECT points FROM tasks WHERE id=?", (task_id,)).fetchone()
                        task_points = int(task_row["points"]) if task_row and task_row["points"] is not None else 100
                        g.db.execute("UPDATE users SET points = COALESCE(points,0) + ? WHERE id=?", (task_points, user_id))
                        flash(f"Задача зачтена! +{task_points} очков", "success")

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
        subs = g.db.execute(
            (
                "SELECT s.id, t.title AS task_title, s.passed, s.created_at "
                "FROM solutions s JOIN tasks t ON t.id = s.task_id "
                "WHERE s.user_id = ? ORDER BY s.created_at DESC, s.id DESC"
            ),
            (user_id,),
        ).fetchall()
        return render_template("submissions.html", submissions=subs)

    @app.route("/submission/<int:solution_id>")
    def submission_detail(solution_id: int):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        s = g.db.execute(
            (
                "SELECT s.id, s.user_id, s.task_id, s.code, s.passed, s.result_json, s.created_at, "
                "t.title AS task_title "
                "FROM solutions s JOIN tasks t ON t.id = s.task_id WHERE s.id = ?"
            ),
            (solution_id,),
        ).fetchone()
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


def now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema_and_seed(app: Flask) -> None:
    db_path = app.config["DATABASE"]
    first_time = not os.path.exists(db_path)
    conn = get_db(db_path)
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                display_name TEXT,
                bio TEXT,
                avatar_path TEXT,
                points INTEGER DEFAULT 0,
                email TEXT,
                google_sub TEXT
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                starter_code TEXT NOT NULL,
                created_at TEXT NOT NULL,
                category_id INTEGER,
                points INTEGER DEFAULT 100,
                level TEXT DEFAULT 'easy',
                FOREIGN KEY(category_id) REFERENCES categories(id)
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_tags (
                task_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY(task_id, tag_id),
                FOREIGN KEY(task_id) REFERENCES tasks(id),
                FOREIGN KEY(tag_id) REFERENCES tags(id)
            );

            CREATE TABLE IF NOT EXISTS testcases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                input_text TEXT NOT NULL,
                expected_output TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS solutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                passed INTEGER NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                duration_ms INTEGER DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );
            """
        )

        # Extend/ensure columns for users/tasks/solutions
        ensure_user_extra_columns(conn)
        ensure_task_extra_columns(conn)
        ensure_solutions_extra_columns(conn)
        # Backfill missing category/levels for existing tasks
        cur_misc = conn.execute("SELECT id FROM categories WHERE name=?", ("Разное",)).fetchone()
        if not cur_misc:
            conn.execute("INSERT INTO categories(name) VALUES(?)", ("Разное",))
            cur_misc = conn.execute("SELECT id FROM categories WHERE name=?", ("Разное",)).fetchone()
        misc_id = cur_misc["id"] if cur_misc else None
        if misc_id is not None:
            conn.execute("UPDATE tasks SET category_id=? WHERE category_id IS NULL", (misc_id,))
        conn.execute("UPDATE tasks SET level='easy' WHERE level IS NULL AND (points IS NULL OR points <= 60)")
        conn.execute("UPDATE tasks SET level='medium' WHERE level IS NULL AND points > 60 AND points <= 100")
        conn.execute("UPDATE tasks SET level='hard' WHERE level IS NULL AND points > 100")

    # Seed categories and tasks if empty
    cur = conn.execute("SELECT COUNT(*) as cnt FROM categories")
    cat_count = cur.fetchone()["cnt"]
    if cat_count == 0:
        with conn:
            conn.executemany(
                "INSERT INTO categories(name) VALUES(?)",
                [("Базовый синтаксис",), ("Строки",), ("Циклы",), ("Списки",)],
            )

    cur = conn.execute("SELECT COUNT(*) as cnt FROM tasks")
    count = cur.fetchone()["cnt"]
    if count == 0:
        with conn:
            # Find category ids
            cat_ids = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM categories").fetchall()}

            # Task 1: Sum of two numbers (Базовый синтаксис)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Сумма двух чисел",
                    (
                        "Прочитайте из ввода два целых числа через пробел и выведите их сумму.\n"
                        "Пример: ввод: 1 2 → вывод: 3"
                    ),
                    (
                        "# Введите решение ниже. Ожидается чтение двух чисел и вывод суммы.\n"
                        "# Пример ввода: 1 2\n"
                        "a, b = map(int, input().split())\n"
                        "print(a + b)\n"
                    ),
                    now(),
                    cat_ids.get("Базовый синтаксис"),
                    50,
                    "easy",
                ),
            )
            task1_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Сумма двух чисел",)).fetchone()[
                "id"
            ]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task1_id, "1 2\n", "3\n"),
                    (task1_id, "100 200\n", "300\n"),
                ],
            )

            # Task 2: Факториал (Циклы)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Факториал",
                    "Прочитайте целое n (0≤n≤12) и выведите n!.",
                    (
                        "# Прочитайте n и выведите факториал n.\n"
                        "n = int(input())\n"
                        "res = 1\n"
                        "for i in range(2, n + 1):\n"
                        "    res *= i\n"
                        "print(res)\n"
                    ),
                    now(),
                    cat_ids.get("Циклы"),
                    100,
                    "medium",
                ),
            )
            task2_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Факториал",)).fetchone()[
                "id"
            ]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task2_id, "0\n", "1\n"),
                    (task2_id, "5\n", "120\n"),
                ],
            )

            # Task 3: Разворот строки (Строки)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Разворот строки",
                    "Прочитайте строку и выведите её в обратном порядке.",
                    (
                        "# Прочитайте строку и выведите её задом наперёд.\n"
                        "s = input().rstrip('\n')\n"
                        "print(s[::-1])\n"
                    ),
                    now(),
                    cat_ids.get("Строки"),
                    70,
                    "easy",
                ),
            )
            task3_id = conn.execute(
                "SELECT id FROM tasks WHERE title=?", ("Разворот строки",)
            ).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task3_id, "hello\n", "olleh\n"),
                    (task3_id, "Привет\n", "тевирП\n"),
                ],
            )

            # Task 4: Сумма списка (Списки)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Сумма списка",
                    "Прочитайте n и затем n чисел. Выведите их сумму.",
                    (
                        "# В первой строке n, далее n чисел\n"
                        "n = int(input())\n"
                        "arr = list(map(int, input().split()))\n"
                        "print(sum(arr))\n"
                    ),
                    now(),
                    cat_ids.get("Списки"),
                    80,
                    "easy",
                ),
            )
            task4_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Сумма списка",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task4_id, "5\n1 2 3 4 5\n", "15\n"),
                    (task4_id, "3\n10 10 10\n", "30\n"),
                ],
            )

            # Task 5: Подсчет гласных (Строки)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Подсчет гласных",
                    "Прочитайте строку и выведите количество гласных (aeiouаеёиоуыэюя).",
                    (
                        "s = input().lower()\n"
                        "vowels = set('aeiouаеёиоуыэюя')\n"
                        "print(sum(1 for ch in s if ch in vowels))\n"
                    ),
                    now(),
                    cat_ids.get("Строки"),
                    90,
                    "medium",
                ),
            )
            task5_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Подсчет гласных",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task5_id, "hello\n", "2\n"),
                    (task5_id, "Привет мир\n", "3\n"),
                ],
            )

            # Дополнительные задачи для Базовый синтаксис
            # Task 6: Максимум из двух чисел (Easy)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Максимум из двух чисел",
                    "Прочитайте два целых числа и выведите максимальное из них.",
                    (
                        "# Прочитайте два числа и выведите максимум\n"
                        "a, b = map(int, input().split())\n"
                        "print(max(a, b))\n"
                    ),
                    now(),
                    cat_ids.get("Базовый синтаксис"),
                    40,
                    "easy",
                ),
            )
            task6_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Максимум из двух чисел",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task6_id, "5 3\n", "5\n"),
                    (task6_id, "-1 10\n", "10\n"),
                ],
            )

            # Task 7: Квадрат числа (Easy)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Квадрат числа",
                    "Прочитайте целое число и выведите его квадрат.",
                    (
                        "# Прочитайте число и выведите его квадрат\n"
                        "n = int(input())\n"
                        "print(n * n)\n"
                    ),
                    now(),
                    cat_ids.get("Базовый синтаксис"),
                    30,
                    "easy",
                ),
            )
            task7_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Квадрат числа",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task7_id, "5\n", "25\n"),
                    (task7_id, "-3\n", "9\n"),
                ],
            )

            # Task 8: Четное или нечетное (Easy)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Четное или нечетное",
                    "Прочитайте целое число и выведите 'четное' или 'нечетное'.",
                    (
                        "# Прочитайте число и определите четность\n"
                        "n = int(input())\n"
                        "if n % 2 == 0:\n"
                        "    print('четное')\n"
                        "else:\n"
                        "    print('нечетное')\n"
                    ),
                    now(),
                    cat_ids.get("Базовый синтаксис"),
                    45,
                    "easy",
                ),
            )
            task8_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Четное или нечетное",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task8_id, "4\n", "четное\n"),
                    (task8_id, "7\n", "нечетное\n"),
                ],
            )

            # Task 9: Простой калькулятор (Medium)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Простой калькулятор",
                    "Прочитайте два числа и операцию (+, -, *, /) и выведите результат.",
                    (
                        "# Прочитайте два числа и операцию\n"
                        "a = float(input())\n"
                        "op = input().strip()\n"
                        "b = float(input())\n"
                        "\n"
                        "if op == '+':\n"
                        "    result = a + b\n"
                        "elif op == '-':\n"
                        "    result = a - b\n"
                        "elif op == '*':\n"
                        "    result = a * b\n"
                        "elif op == '/':\n"
                        "    result = a / b if b != 0 else float('inf')\n"
                        "\n"
                        "print(result)\n"
                    ),
                    now(),
                    cat_ids.get("Базовый синтаксис"),
                    120,
                    "medium",
                ),
            )
            task9_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Простой калькулятор",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task9_id, "5\n+\n3\n", "8.0\n"),
                    (task9_id, "10\n/\n2\n", "5.0\n"),
                ],
            )

            # Task 10: Числа Фибоначчи (Hard)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Числа Фибоначчи",
                    "Прочитайте число n и выведите n-е число Фибоначчи (F(0)=0, F(1)=1, F(n)=F(n-1)+F(n-2)).",
                    (
                        "# Вычислите n-е число Фибоначчи\n"
                        "n = int(input())\n"
                        "\n"
                        "if n <= 1:\n"
                        "    print(n)\n"
                        "else:\n"
                        "    a, b = 0, 1\n"
                        "    for i in range(2, n + 1):\n"
                        "        a, b = b, a + b\n"
                        "    print(b)\n"
                    ),
                    now(),
                    cat_ids.get("Базовый синтаксис"),
                    150,
                    "hard",
                ),
            )
            task10_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Числа Фибоначчи",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task10_id, "0\n", "0\n"),
                    (task10_id, "10\n", "55\n"),
                ],
            )

            # Дополнительные задачи для Строки
            # Task 11: Длина строки (Easy)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Длина строки",
                    "Прочитайте строку и выведите её длину.",
                    (
                        "# Прочитайте строку и выведите её длину\n"
                        "s = input().rstrip('\\n')\n"
                        "print(len(s))\n"
                    ),
                    now(),
                    cat_ids.get("Строки"),
                    35,
                    "easy",
                ),
            )
            task11_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Длина строки",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task11_id, "hello\n", "5\n"),
                    (task11_id, "Python\n", "6\n"),
                ],
            )

            # Task 12: Палиндром (Medium)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Палиндром",
                    "Прочитайте строку и выведите 'да', если это палиндром, иначе 'нет'.",
                    (
                        "# Проверьте, является ли строка палиндромом\n"
                        "s = input().lower().replace(' ', '')\n"
                        "if s == s[::-1]:\n"
                        "    print('да')\n"
                        "else:\n"
                        "    print('нет')\n"
                    ),
                    now(),
                    cat_ids.get("Строки"),
                    110,
                    "medium",
                ),
            )
            task12_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Палиндром",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task12_id, "racecar\n", "да\n"),
                    (task12_id, "hello\n", "нет\n"),
                ],
            )

            # Task 13: Поиск подстроки (Hard)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Поиск подстроки",
                    "Прочитайте две строки. Выведите индекс первого вхождения второй строки в первую, или -1 если не найдено.",
                    (
                        "# Найдите первое вхождение подстроки\n"
                        "text = input()\n"
                        "substring = input()\n"
                        "print(text.find(substring))\n"
                    ),
                    now(),
                    cat_ids.get("Строки"),
                    140,
                    "hard",
                ),
            )
            task13_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Поиск подстроки",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task13_id, "hello world\nworld\n", "6\n"),
                    (task13_id, "python\njava\n", "-1\n"),
                ],
            )

            # Дополнительные задачи для Циклы
            # Task 14: Сумма от 1 до n (Easy)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Сумма от 1 до n",
                    "Прочитайте целое число n и выведите сумму всех чисел от 1 до n.",
                    (
                        "# Вычислите сумму от 1 до n\n"
                        "n = int(input())\n"
                        "total = 0\n"
                        "for i in range(1, n + 1):\n"
                        "    total += i\n"
                        "print(total)\n"
                    ),
                    now(),
                    cat_ids.get("Циклы"),
                    55,
                    "easy",
                ),
            )
            task14_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Сумма от 1 до n",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task14_id, "5\n", "15\n"),
                    (task14_id, "10\n", "55\n"),
                ],
            )

            # Task 15: Таблица умножения (Medium)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Таблица умножения",
                    "Прочитайте число n и выведите таблицу умножения на n от 1 до 10.",
                    (
                        "# Выведите таблицу умножения на n\n"
                        "n = int(input())\n"
                        "for i in range(1, 11):\n"
                        "    print(f'{n} x {i} = {n * i}')\n"
                    ),
                    now(),
                    cat_ids.get("Циклы"),
                    95,
                    "medium",
                ),
            )
            task15_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Таблица умножения",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task15_id, "5\n", "5 x 1 = 5\n5 x 2 = 10\n5 x 3 = 15\n5 x 4 = 20\n5 x 5 = 25\n5 x 6 = 30\n5 x 7 = 35\n5 x 8 = 40\n5 x 9 = 45\n5 x 10 = 50\n"),
                    (task15_id, "3\n", "3 x 1 = 3\n3 x 2 = 6\n3 x 3 = 9\n3 x 4 = 12\n3 x 5 = 15\n3 x 6 = 18\n3 x 7 = 21\n3 x 8 = 24\n3 x 9 = 27\n3 x 10 = 30\n"),
                ],
            )

            # Task 16: Простые числа (Hard)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Простые числа",
                    "Прочитайте число n и выведите все простые числа от 2 до n.",
                    (
                        "# Найдите все простые числа от 2 до n\n"
                        "n = int(input())\n"
                        "\n"
                        "def is_prime(num):\n"
                        "    if num < 2:\n"
                        "        return False\n"
                        "    for i in range(2, int(num ** 0.5) + 1):\n"
                        "        if num % i == 0:\n"
                        "            return False\n"
                        "    return True\n"
                        "\n"
                        "primes = []\n"
                        "for i in range(2, n + 1):\n"
                        "    if is_prime(i):\n"
                        "        primes.append(i)\n"
                        "\n"
                        "print(' '.join(map(str, primes)))\n"
                    ),
                    now(),
                    cat_ids.get("Циклы"),
                    180,
                    "hard",
                ),
            )
            task16_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Простые числа",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task16_id, "10\n", "2 3 5 7\n"),
                    (task16_id, "20\n", "2 3 5 7 11 13 17 19\n"),
                ],
            )

            # Дополнительные задачи для Списки
            # Task 17: Максимум в списке (Easy)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Максимум в списке",
                    "Прочитайте n и затем n чисел. Выведите максимальное число.",
                    (
                        "# Найдите максимум в списке\n"
                        "n = int(input())\n"
                        "numbers = list(map(int, input().split()))\n"
                        "print(max(numbers))\n"
                    ),
                    now(),
                    cat_ids.get("Списки"),
                    60,
                    "easy",
                ),
            )
            task17_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Максимум в списке",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task17_id, "5\n3 7 1 9 2\n", "9\n"),
                    (task17_id, "3\n-1 -5 -3\n", "-1\n"),
                ],
            )

            # Task 18: Сортировка списка (Medium)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Сортировка списка",
                    "Прочитайте n и затем n чисел. Выведите их в отсортированном порядке.",
                    (
                        "# Отсортируйте список по возрастанию\n"
                        "n = int(input())\n"
                        "numbers = list(map(int, input().split()))\n"
                        "numbers.sort()\n"
                        "print(' '.join(map(str, numbers)))\n"
                    ),
                    now(),
                    cat_ids.get("Списки"),
                    100,
                    "medium",
                ),
            )
            task18_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Сортировка списка",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task18_id, "5\n3 1 4 1 5\n", "1 1 3 4 5\n"),
                    (task18_id, "3\n5 2 8\n", "2 5 8\n"),
                ],
            )

            # Task 19: Уникальные элементы (Hard)
            conn.execute(
                "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                (
                    "Уникальные элементы",
                    "Прочитайте n и затем n чисел. Выведите количество уникальных элементов.",
                    (
                        "# Подсчитайте количество уникальных элементов\n"
                        "n = int(input())\n"
                        "numbers = list(map(int, input().split()))\n"
                        "unique_count = len(set(numbers))\n"
                        "print(unique_count)\n"
                    ),
                    now(),
                    cat_ids.get("Списки"),
                    130,
                    "hard",
                ),
            )
            task19_id = conn.execute("SELECT id FROM tasks WHERE title=?", ("Уникальные элементы",)).fetchone()["id"]
            conn.executemany(
                "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                [
                    (task19_id, "5\n1 2 2 3 1\n", "3\n"),
                    (task19_id, "4\n5 5 5 5\n", "1\n"),
                ],
            )

    conn.close()


def add_missing_tasks():
    """Добавляет недостающие задачи в существующую базу данных"""
    db_path = os.path.join(os.path.dirname(__file__), APP_DB_NAME)
    conn = get_db(db_path)
    
    with conn:
        # Проверяем, есть ли уже задачи
        existing_tasks = conn.execute("SELECT title FROM tasks").fetchall()
        existing_titles = {row["title"] for row in existing_tasks}
        
        # Получаем ID категорий
        cat_ids = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM categories").fetchall()}
        
        # Список новых задач для добавления
        new_tasks = [
            # Базовый синтаксис - Easy
            ("Максимум из двух чисел", "Прочитайте два целых числа и выведите максимальное из них.", 
             "# Прочитайте два числа и выведите максимум\na, b = map(int, input().split())\nprint(max(a, b))\n",
             cat_ids.get("Базовый синтаксис"), 40, "easy", 
             [("5 3\n", "5\n"), ("-1 10\n", "10\n")]),
            
            ("Квадрат числа", "Прочитайте целое число и выведите его квадрат.",
             "# Прочитайте число и выведите его квадрат\nn = int(input())\nprint(n * n)\n",
             cat_ids.get("Базовый синтаксис"), 30, "easy",
             [("5\n", "25\n"), ("-3\n", "9\n")]),
             
            ("Четное или нечетное", "Прочитайте целое число и выведите 'четное' или 'нечетное'.",
             "# Прочитайте число и определите четность\nn = int(input())\nif n % 2 == 0:\n    print('четное')\nelse:\n    print('нечетное')\n",
             cat_ids.get("Базовый синтаксис"), 45, "easy",
             [("4\n", "четное\n"), ("7\n", "нечетное\n")]),
             
            # Базовый синтаксис - Medium
            ("Простой калькулятор", "Прочитайте два числа и операцию (+, -, *, /) и выведите результат.",
             "# Прочитайте два числа и операцию\na = float(input())\nop = input().strip()\nb = float(input())\n\nif op == '+':\n    result = a + b\nelif op == '-':\n    result = a - b\nelif op == '*':\n    result = a * b\nelif op == '/':\n    result = a / b if b != 0 else float('inf')\n\nprint(result)\n",
             cat_ids.get("Базовый синтаксис"), 120, "medium",
             [("5\n+\n3\n", "8.0\n"), ("10\n/\n2\n", "5.0\n")]),
             
            # Базовый синтаксис - Hard
            ("Числа Фибоначчи", "Прочитайте число n и выведите n-е число Фибоначчи (F(0)=0, F(1)=1, F(n)=F(n-1)+F(n-2)).",
             "# Вычислите n-е число Фибоначчи\nn = int(input())\n\nif n <= 1:\n    print(n)\nelse:\n    a, b = 0, 1\n    for i in range(2, n + 1):\n        a, b = b, a + b\n    print(b)\n",
             cat_ids.get("Базовый синтаксис"), 150, "hard",
             [("0\n", "0\n"), ("10\n", "55\n")]),
             
            # Строки - Easy
            ("Длина строки", "Прочитайте строку и выведите её длину.",
             "# Прочитайте строку и выведите её длину\ns = input().rstrip('\\n')\nprint(len(s))\n",
             cat_ids.get("Строки"), 35, "easy",
             [("hello\n", "5\n"), ("Python\n", "6\n")]),
             
            # Строки - Medium
            ("Палиндром", "Прочитайте строку и выведите 'да', если это палиндром, иначе 'нет'.",
             "# Проверьте, является ли строка палиндромом\ns = input().lower().replace(' ', '')\nif s == s[::-1]:\n    print('да')\nelse:\n    print('нет')\n",
             cat_ids.get("Строки"), 110, "medium",
             [("racecar\n", "да\n"), ("hello\n", "нет\n")]),
             
            # Строки - Hard
            ("Поиск подстроки", "Прочитайте две строки. Выведите индекс первого вхождения второй строки в первую, или -1 если не найдено.",
             "# Найдите первое вхождение подстроки\ntext = input()\nsubstring = input()\nprint(text.find(substring))\n",
             cat_ids.get("Строки"), 140, "hard",
             [("hello world\nworld\n", "6\n"), ("python\njava\n", "-1\n")]),
             
            # Циклы - Easy
            ("Сумма от 1 до n", "Прочитайте целое число n и выведите сумму всех чисел от 1 до n.",
             "# Вычислите сумму от 1 до n\nn = int(input())\ntotal = 0\nfor i in range(1, n + 1):\n    total += i\nprint(total)\n",
             cat_ids.get("Циклы"), 55, "easy",
             [("5\n", "15\n"), ("10\n", "55\n")]),
             
            # Циклы - Medium
            ("Таблица умножения", "Прочитайте число n и выведите таблицу умножения на n от 1 до 10.",
             "# Выведите таблицу умножения на n\nn = int(input())\nfor i in range(1, 11):\n    print(f'{n} x {i} = {n * i}')\n",
             cat_ids.get("Циклы"), 95, "medium",
             [("5\n", "5 x 1 = 5\n5 x 2 = 10\n5 x 3 = 15\n5 x 4 = 20\n5 x 5 = 25\n5 x 6 = 30\n5 x 7 = 35\n5 x 8 = 40\n5 x 9 = 45\n5 x 10 = 50\n"), ("3\n", "3 x 1 = 3\n3 x 2 = 6\n3 x 3 = 9\n3 x 4 = 12\n3 x 5 = 15\n3 x 6 = 18\n3 x 7 = 21\n3 x 8 = 24\n3 x 9 = 27\n3 x 10 = 30\n")]),
             
            # Циклы - Hard
            ("Простые числа", "Прочитайте число n и выведите все простые числа от 2 до n.",
             "# Найдите все простые числа от 2 до n\nn = int(input())\n\ndef is_prime(num):\n    if num < 2:\n        return False\n    for i in range(2, int(num ** 0.5) + 1):\n        if num % i == 0:\n            return False\n    return True\n\nprimes = []\nfor i in range(2, n + 1):\n    if is_prime(i):\n        primes.append(i)\n\nprint(' '.join(map(str, primes)))\n",
             cat_ids.get("Циклы"), 180, "hard",
             [("10\n", "2 3 5 7\n"), ("20\n", "2 3 5 7 11 13 17 19\n")]),
             
            # Списки - Easy
            ("Максимум в списке", "Прочитайте n и затем n чисел. Выведите максимальное число.",
             "# Найдите максимум в списке\nn = int(input())\nnumbers = list(map(int, input().split()))\nprint(max(numbers))\n",
             cat_ids.get("Списки"), 60, "easy",
             [("5\n3 7 1 9 2\n", "9\n"), ("3\n-1 -5 -3\n", "-1\n")]),
             
            # Списки - Medium
            ("Сортировка списка", "Прочитайте n и затем n чисел. Выведите их в отсортированном порядке.",
             "# Отсортируйте список по возрастанию\nn = int(input())\nnumbers = list(map(int, input().split()))\nnumbers.sort()\nprint(' '.join(map(str, numbers)))\n",
             cat_ids.get("Списки"), 100, "medium",
             [("5\n3 1 4 1 5\n", "1 1 3 4 5\n"), ("3\n5 2 8\n", "2 5 8\n")]),
             
            # Списки - Hard
            ("Уникальные элементы", "Прочитайте n и затем n чисел. Выведите количество уникальных элементов.",
             "# Подсчитайте количество уникальных элементов\nn = int(input())\nnumbers = list(map(int, input().split()))\nunique_count = len(set(numbers))\nprint(unique_count)\n",
             cat_ids.get("Списки"), 130, "hard",
             [("5\n1 2 2 3 1\n", "3\n"), ("4\n5 5 5 5\n", "1\n")]),
        ]
        
        # Добавляем только те задачи, которых еще нет
        for title, description, starter_code, category_id, points, level, testcases in new_tasks:
            if title not in existing_titles:
                # Добавляем задачу
                conn.execute(
                    "INSERT INTO tasks(title, description, starter_code, created_at, category_id, points, level) VALUES(?,?,?,?,?,?,?)",
                    (title, description, starter_code, now(), category_id, points, level)
                )
                
                # Получаем ID новой задачи
                task_id = conn.execute("SELECT id FROM tasks WHERE title=?", (title,)).fetchone()["id"]
                
                # Добавляем тест-кейсы
                conn.executemany(
                    "INSERT INTO testcases(task_id, input_text, expected_output) VALUES(?,?,?)",
                    [(task_id, input_text, expected_output) for input_text, expected_output in testcases]
                )
                
                print(f"Добавлена задача: {title}")
    
    conn.close()


def ensure_user_extra_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info('users')").fetchall()}
    if "display_name" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
    if "bio" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN bio TEXT")
    if "avatar_path" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT")
    if "points" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN points INTEGER DEFAULT 0")
    if "email" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "google_sub" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN google_sub TEXT")
    # Unique indexes for non-null values
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL"
    )


def ensure_task_extra_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info('tasks')").fetchall()}
    if "category_id" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN category_id INTEGER")
    if "points" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN points INTEGER DEFAULT 100")
    if "level" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN level TEXT DEFAULT 'easy'")


def ensure_solutions_extra_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info('solutions')").fetchall()}
    if "duration_ms" not in cols:
        conn.execute("ALTER TABLE solutions ADD COLUMN duration_ms INTEGER DEFAULT 0")


    


def make_unique_username(base_username: str, conn: sqlite3.Connection) -> str:
    candidate = base_username
    suffix = 1
    while conn.execute("SELECT 1 FROM users WHERE username = ?", (candidate,)).fetchone():
        suffix += 1
        candidate = f"{base_username}{suffix}"
    return candidate


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
    app.run(host="0.0.0.0", port=5000, debug=True)