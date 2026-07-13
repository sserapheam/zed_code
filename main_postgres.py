import os
import re
import json
import traceback
import html
from datetime import datetime, date, timedelta
from multiprocessing import Process, Queue
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from dotenv import load_dotenv
from string import Template

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
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import bleach

# Импорт для работы с PostgreSQL
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from sqlalchemy import create_engine, text
except ImportError:
    print("ОШИБКА: Не установлены библиотеки для PostgreSQL")
    print("Установите их командой: pip install psycopg2-binary sqlalchemy")
    exit(1)

def _build_postgres_config() -> dict:
    database_url = os.environ.get("DATABASE_URL")

    if database_url:
        parsed = urlparse(database_url)
        return {
            "host": parsed.hostname or os.environ.get("DB_HOST", "localhost"),
            "port": parsed.port or int(os.environ.get("DB_PORT", 5432)),
            "database": (parsed.path or "/coding_platform").lstrip("/"),
            "user": parsed.username or os.environ.get("DB_USER", "admin"),
            "password": parsed.password or os.environ.get("DB_PASSWORD"),
            "client_encoding": "utf8",
        }

    return {
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get("DB_PORT", 5432)),
        "database": os.environ.get("DB_NAME", "coding_platform"),
        "user": os.environ.get("DB_USER", "admin"),
        "password": os.environ.get("DB_PASSWORD", "postgres"),
        "client_encoding": "utf8",
    }


POSTGRES_CONFIG = _build_postgres_config()

def create_app() -> Flask:
    app = Flask(__name__)
    secret = (os.environ.get("FLASK_SECRET") or "").strip()
    if not secret or secret == "dev-secret-change-me":
        if os.environ.get("FLASK_ENV", "").lower() == "production" or os.environ.get("REQUIRE_FLASK_SECRET", "").lower() in (
            "1",
            "true",
            "yes",
        ):
            raise RuntimeError("FLASK_SECRET must be set to a strong random value")
        secret = secret or "dev-secret-change-me"
        print("WARNING: using insecure FLASK_SECRET — set a strong FLASK_SECRET in .env")
    app.config["SECRET_KEY"] = secret
    app.config["WTF_CSRF_TIME_LIMIT"] = None
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB uploads
    app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "static", "uploads")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    csrf = CSRFProtect(app)
    
    # Настройка логирования для Docker операций
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Функция для обработки HTML-сущностей и форматирования
    def clean_html_entities(text):
        if not text:
            return text
        # Декодируем HTML-сущности, затем экранируем для безопасной вставки
        text = html.unescape(text)
        text = text.replace('\xa0', ' ').replace('&nbsp;', ' ')

        def esc(s: str) -> str:
            return html.escape(str(s), quote=True)

        import re
        
        # Сначала разбиваем текст на части
        parts = []
        
        # Разбиваем по ключевым словам
        keywords = ['Example \\d+:', 'Input:', 'Output:', 'Explanation:', 'Constraints:', 'Follow-up:']
        pattern = '|'.join(keywords)
        
        # Находим все вхождения ключевых слов
        matches = list(re.finditer(pattern, text))
        
        if not matches:
            # Если нет ключевых слов, возвращаем как есть
            return f'<p class="description-paragraph">{esc(text)}</p>'
        
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
                html_content.append(f'<p class="description-paragraph">{esc(part[1])}</p>')
            elif part[0] == 'example':
                if in_example:
                    html_content.append('</div>')
                html_content.append(f'<div class="example-section"><h4 class="example-title">{esc(part[1])}</h4>')
                in_example = True
            elif part[0] == 'input_output':
                html_content.append(
                    f'<div class="input-output"><strong>{esc(part[1])}</strong> '
                    f'<code class="code-snippet">{esc(part[2])}</code></div>'
                )
            elif part[0] == 'explanation':
                html_content.append(
                    f'<div class="explanation"><strong>Explanation:</strong> {esc(part[1])}</div>'
                )
            elif part[0] == 'constraints':
                if in_example:
                    html_content.append('</div>')
                    in_example = False
                html_content.append('<div class="constraints-section"><h4 class="constraints-title">Constraints:</h4>')
                # Разбиваем ограничения по строкам
                constraints = part[1].split('.')
                for constraint in constraints:
                    constraint = constraint.strip()
                    if constraint:
                        html_content.append(f'<div class="constraint-item">{esc(constraint)}</div>')
                html_content.append('</div>')
            elif part[0] == 'followup':
                if in_example:
                    html_content.append('</div>')
                    in_example = False
                html_content.append(
                    f'<div class="followup-section"><h4 class="followup-title">Follow-up:</h4>'
                    f'<p class="followup-text">{esc(part[1])}</p></div>'
                )
        
        if in_example:
            html_content.append('</div>')
        
        return ''.join(html_content)

    # Регистрируем фильтр для шаблонов
    @app.template_filter('clean_html')
    def clean_html_filter(text):
        return clean_html_entities(text)

    @app.template_filter('safe_hint')
    def safe_hint_filter(text):
        """Подсказки: только безопасные теги или plain text."""
        if text is None:
            return ""
        raw = str(text)
        return bleach.clean(
            raw,
            tags=["b", "i", "em", "strong", "code", "pre", "br", "p", "ul", "ol", "li"],
            attributes={},
            strip=True,
        )

    @app.context_processor
    def inject_csrf():
        return {"csrf_token": generate_csrf}

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
                FROM leetcode_problems_with_tests 
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
        # Получаем популярные темы (доступна всем пользователям)
        featured_topics = execute_query(g.db,
            """
            SELECT topic, COUNT(*) as problem_count
            FROM leetcode_problems_with_tests 
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
            FROM leetcode_problems_with_tests 
            ORDER BY created_at DESC NULLS LAST, id DESC
            LIMIT 6
            """
        )
        
        top = execute_query(g.db,
            "SELECT username, display_name, avatar_path, points FROM users ORDER BY points DESC, id ASC LIMIT 5"
        )

        # Реальная статистика для hero (раньше показывали размер LIMIT-выборок)
        stats_row = execute_one(
            g.db,
            """
            SELECT
              (SELECT COUNT(*) FROM leetcode_problems_with_tests) AS problems_count,
              (SELECT COUNT(DISTINCT topic) FROM leetcode_problems_with_tests
               WHERE topic IS NOT NULL AND topic <> '') AS topics_count,
              (SELECT COUNT(*) FROM users) AS users_count
            """,
        )
        platform_stats = {
            "problems": int(stats_row["problems_count"] or 0) if stats_row else 0,
            "topics": int(stats_row["topics_count"] or 0) if stats_row else 0,
            "users": int(stats_row["users_count"] or 0) if stats_row else 0,
        }

        # Задача дня — стабильная на календарный день
        daily_challenge = None
        if platform_stats["problems"] > 0:
            from datetime import date

            day_offset = date.today().toordinal() % platform_stats["problems"]
            daily_challenge = execute_one(
                g.db,
                """
                SELECT problem_id, title, difficulty, topic
                FROM leetcode_problems_with_tests
                ORDER BY problem_id
                OFFSET %s
                LIMIT 1
                """,
                (day_offset,),
            )

        return render_template(
            "index.html",
            featured_topics=featured_topics,
            recent_problems=recent_problems,
            top=top,
            platform_stats=platform_stats,
            daily_challenge=daily_challenge,
        )

    @app.route("/search")
    def search():
        q = (request.args.get("q") or "").strip()
        difficulty = (request.args.get("difficulty") or "").strip()
        topic = (request.args.get("topic") or "").strip()
        
        # Строим запрос
        where_clause = "WHERE 1=1"
        params = []
        
        if q:
            where_clause += " AND (title ILIKE %s OR description ILIKE %s)"
            like = f"%{q}%"
            params.extend([like, like])
        
        if difficulty in {"Easy", "Medium", "Hard"}:
            where_clause += " AND difficulty = %s"
            params.append(difficulty)
            
        if topic:
            where_clause += " AND %s = ANY(topic_tags)"
            params.append(topic)
        
        sql = """
            SELECT problem_id, title, difficulty, topic_tags, topic
            FROM leetcode_problems_with_tests 
            """ + where_clause + """
            ORDER BY problem_id DESC
            LIMIT 100
        """
        results = execute_query(g.db, sql, params)
        return render_template("search.html", q=q, difficulty=difficulty, topic=topic, results=results)

    USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{2,31}$")
    PASSWORD_MIN_LEN = 8
    PASSWORD_MAX_LEN = 128
    RESERVED_USERNAMES = frozenset({
        "admin", "administrator", "root", "system", "support",
        "moderator", "mod", "null", "undefined", "api", "www",
        "help", "official", "zedcode", "zed_code",
    })

    def _validate_password(password: str, password2: str, username: str = "") -> Optional[str]:
        if not password:
            return "Введите пароль"
        if password != password2:
            return "Пароли не совпадают"
        if len(password) < PASSWORD_MIN_LEN:
            return f"Пароль: минимум {PASSWORD_MIN_LEN} символов"
        if len(password) > PASSWORD_MAX_LEN:
            return f"Пароль: максимум {PASSWORD_MAX_LEN} символов"
        if username and password.lower() == username.lower():
            return "Пароль не должен совпадать с логином"
        if not re.search(r"[A-Za-zА-Яа-я]", password) or not re.search(r"\d", password):
            return "Пароль: хотя бы одна буква и одна цифра"
        if password.strip() != password:
            return "Пароль не должен начинаться или заканчиваться пробелом"
        return None

    def _validate_registration(username: str, password: str, password2: str) -> Optional[str]:
        if not username or not password:
            return "Введите логин и пароль"
        if len(username) < 3 or len(username) > 32:
            return "Логин: от 3 до 32 символов"
        if not USERNAME_RE.match(username):
            return (
                "Логин: латинские буквы, цифры и _; "
                "должен начинаться с буквы"
            )
        if username.lower() in RESERVED_USERNAMES:
            return "Это имя занято системой, выберите другое"
        return _validate_password(password, password2, username)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            password2 = request.form.get("password2") or ""
            error = _validate_registration(username, password, password2)
            if error:
                flash(error, "error")
                return render_template("register.html", username=username)
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
                try:
                    g.db.rollback()
                except Exception:
                    pass
                flash("Пользователь с таким именем уже существует", "error")
                return render_template("register.html", username=username)
        return render_template("register.html", username="")

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

    @app.route("/logout", methods=["POST"])
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
        return redirect(url_for("problems"))

    @app.route("/problems")
    def problems():
        user_id = session.get("user_id")  # Может быть None для неавторизованных
        
        # Получаем все темы из колонки topic
        topics = execute_query(g.db,
            """
            SELECT topic, COUNT(*) as problem_count
            FROM leetcode_problems_with_tests 
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
            # Получаем задачи для выбранной темы (только первые 50)
            if user_id:
                sql = """
                    SELECT problem_id, title, difficulty, topic_tags,
                    CASE 
                        WHEN EXISTS(
                            SELECT 1 FROM problem_test_results ptr 
                            WHERE ptr.problem_id = leetcode_problems_with_tests.problem_id 
                            AND ptr.user_id = %s AND ptr.status='passed'
                        ) THEN 1
                        WHEN EXISTS(
                            SELECT 1 FROM problem_test_results ptr 
                            WHERE ptr.problem_id = leetcode_problems_with_tests.problem_id 
                            AND ptr.user_id = %s
                        ) THEN 0
                        ELSE NULL
                    END AS last_passed
                    FROM leetcode_problems_with_tests 
                    WHERE topic = %s
                """
                params = [user_id, user_id, selected_topic]
            else:
                sql = """
                    SELECT problem_id, title, difficulty, topic_tags, NULL AS last_passed
                    FROM leetcode_problems_with_tests 
                    WHERE topic = %s
                """
                params = [selected_topic]
            
            if difficulty_filter in {"Easy", "Medium", "Hard"}:
                sql += " AND difficulty = %s"
                params.append(difficulty_filter)
                
            sql += " ORDER BY problem_id LIMIT 50"
            problems = execute_query(g.db, sql, params)
        
        # Если не выбрана конкретная тема, получаем только первые 50 задач
        all_problems = []
        if not selected_topic:
            where_extra = ""
            params_all = []
            if difficulty_filter in {"Easy", "Medium", "Hard"}:
                where_extra = " WHERE difficulty = %s"
                params_all.append(difficulty_filter)
            if user_id:
                all_problems = execute_query(
                    g.db,
                    f"""
                    SELECT problem_id, title, difficulty, topic_tags,
                    CASE
                        WHEN EXISTS(
                            SELECT 1 FROM problem_test_results ptr
                            WHERE ptr.problem_id = leetcode_problems_with_tests.problem_id
                              AND ptr.user_id = %s AND ptr.status='passed'
                        ) THEN 1
                        WHEN EXISTS(
                            SELECT 1 FROM problem_test_results ptr
                            WHERE ptr.problem_id = leetcode_problems_with_tests.problem_id
                              AND ptr.user_id = %s
                        ) THEN 0
                        ELSE NULL
                    END AS last_passed
                    FROM leetcode_problems_with_tests
                    {where_extra}
                    ORDER BY problem_id
                    LIMIT 50
                    """,
                    ([user_id, user_id] + params_all),
                )
            else:
                all_problems = execute_query(
                    g.db,
                    f"""
                    SELECT problem_id, title, difficulty, topic_tags, NULL AS last_passed
                    FROM leetcode_problems_with_tests
                    {where_extra}
                    ORDER BY problem_id
                    LIMIT 50
                    """,
                    params_all,
                )
        
        return render_template("problems.html", 
                             topics=topics, 
                             selected_topic=selected_topic,
                             difficulty_filter=difficulty_filter,
                             problems=problems,
                             all_problems=all_problems)

    @app.route("/api/problems")
    def api_problems():
        """API для получения задач с пагинацией (доступно и гостям)."""
        user_id = session.get("user_id")

        # Получаем параметры пагинации
        try:
            page = max(1, int(request.args.get("page", 1)))
            per_page = min(100, max(1, int(request.args.get("per_page", 50))))
        except (TypeError, ValueError):
            page, per_page = 1, 50
        topic = request.args.get("topic", "")
        difficulty = request.args.get("difficulty", "")

        offset = (page - 1) * per_page

        where_clause = "WHERE 1=1"
        base_params = []

        if topic:
            where_clause += " AND topic = %s"
            base_params.append(topic)

        if difficulty in {"Easy", "Medium", "Hard"}:
            where_clause += " AND difficulty = %s"
            base_params.append(difficulty)

        if user_id:
            status_select = """
                CASE
                    WHEN EXISTS(
                        SELECT 1 FROM problem_test_results ptr
                        WHERE ptr.problem_id = leetcode_problems_with_tests.problem_id
                          AND ptr.user_id = %s AND ptr.status='passed'
                    ) THEN 1
                    WHEN EXISTS(
                        SELECT 1 FROM problem_test_results ptr
                        WHERE ptr.problem_id = leetcode_problems_with_tests.problem_id
                          AND ptr.user_id = %s
                    ) THEN 0
                    ELSE NULL
                END AS last_passed
            """
            status_params = [user_id, user_id]
        else:
            status_select = "NULL AS last_passed"
            status_params = []

        sql = f"""
            SELECT problem_id, title, difficulty, topic_tags,
            {status_select}
            FROM leetcode_problems_with_tests
            {where_clause}
            ORDER BY problem_id
            LIMIT %s OFFSET %s
        """
        params = status_params + base_params + [per_page, offset]

        problems = execute_query(g.db, sql, params) or []

        count_sql = f"""
            SELECT COUNT(*) as total
            FROM leetcode_problems_with_tests
            {where_clause}
        """
        total_row = execute_query(g.db, count_sql, base_params)
        total_count = (total_row[0]["total"] if total_row else 0) or 0

        formatted_problems = []
        for p in problems:
            tags = p.get("topic_tags") or []
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except Exception:
                    tags = []
            formatted_problems.append({
                "problem_id": p["problem_id"],
                "title": p["title"],
                "difficulty": p["difficulty"] or "Easy",
                "topic_tags": tags if isinstance(tags, list) else [],
                "last_passed": p["last_passed"],
            })

        return jsonify({
            "problems": formatted_problems,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total_count,
                "total_pages": (total_count + per_page - 1) // per_page if total_count else 0,
                "has_next": page * per_page < total_count,
                "has_prev": page > 1,
            },
        })

    @app.route("/api/topics/<topic>/problems")
    def api_topic_problems(topic: str):
        user_id = session.get("user_id")
        if user_id:
            sql = """
                SELECT problem_id, title, difficulty, topic_tags,
                CASE
                    WHEN EXISTS(
                        SELECT 1 FROM problem_test_results ptr
                        WHERE ptr.problem_id = leetcode_problems_with_tests.problem_id
                          AND ptr.user_id = %s AND ptr.status='passed'
                    ) THEN 1
                    WHEN EXISTS(
                        SELECT 1 FROM problem_test_results ptr
                        WHERE ptr.problem_id = leetcode_problems_with_tests.problem_id
                          AND ptr.user_id = %s
                    ) THEN 0
                    ELSE NULL
                END AS last_passed
                FROM leetcode_problems_with_tests
                WHERE topic = %s
                ORDER BY difficulty, problem_id
            """
            rows = execute_query(g.db, sql, (user_id, user_id, topic)) or []
        else:
            sql = """
                SELECT problem_id, title, difficulty, topic_tags, NULL AS last_passed
                FROM leetcode_problems_with_tests
                WHERE topic = %s
                ORDER BY difficulty, problem_id
            """
            rows = execute_query(g.db, sql, (topic,)) or []
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
            form_action = (request.form.get("form_action") or "update_profile").strip()

            if form_action == "change_password":
                current_password = request.form.get("current_password") or ""
                new_password = request.form.get("new_password") or ""
                new_password2 = request.form.get("new_password2") or ""
                user_row = execute_one(
                    g.db,
                    "SELECT username, password_hash FROM users WHERE id=%s",
                    (user_id,),
                )
                if not user_row:
                    flash("Пользователь не найден", "error")
                    return redirect(url_for("login"))
                if not current_password or not check_password_hash(
                    user_row["password_hash"], current_password
                ):
                    flash("Неверный текущий пароль", "error")
                    return redirect(url_for("profile"))
                pwd_error = _validate_password(
                    new_password, new_password2, user_row.get("username") or ""
                )
                if pwd_error:
                    flash(pwd_error, "error")
                    return redirect(url_for("profile"))
                if check_password_hash(user_row["password_hash"], new_password):
                    flash("Новый пароль должен отличаться от текущего", "error")
                    return redirect(url_for("profile"))
                cursor = get_cursor(g.db)
                try:
                    cursor.execute(
                        "UPDATE users SET password_hash=%s WHERE id=%s",
                        (generate_password_hash(new_password), user_id),
                    )
                    g.db.commit()
                except Exception:
                    try:
                        g.db.rollback()
                    except Exception:
                        pass
                    flash("Не удалось сменить пароль", "error")
                    return redirect(url_for("profile"))
                finally:
                    cursor.close()
                flash("Пароль успешно изменён", "success")
                return redirect(url_for("profile"))

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
        stats = execute_one(
            g.db,
            """
            SELECT
              (SELECT COUNT(*) FROM problem_test_results WHERE user_id=%s) AS submissions_count,
              (SELECT COUNT(DISTINCT problem_id) FROM problem_test_results
               WHERE user_id=%s AND status='passed') AS solved_count
            """,
            (user_id, user_id),
        )
        activity = _build_activity_calendar(user_id)
        return render_template(
            "profile.html",
            user=user,
            submissions_count=(stats or {}).get("submissions_count") or 0,
            solved_count=(stats or {}).get("solved_count") or 0,
            activity_weeks=activity["weeks"],
            activity_months=activity["months"],
            activity_total=activity["total"],
            activity_total_label=activity["total_label"],
            activity_weekday_labels=activity["weekday_labels"],
        )

    def _activity_level(count: int) -> int:
        if count <= 0:
            return 0
        if count == 1:
            return 1
        if count <= 3:
            return 2
        if count <= 6:
            return 3
        return 4

    def _plural_submissions(n: int) -> str:
        n_abs = abs(n) % 100
        n1 = n_abs % 10
        if 11 <= n_abs <= 14:
            word = "отправок"
        elif n1 == 1:
            word = "отправка"
        elif 2 <= n1 <= 4:
            word = "отправки"
        else:
            word = "отправок"
        return f"{n} {word}"

    def _build_activity_calendar(user_id: int) -> dict:
        """GitHub-like heat map: last 52 Mon–Sun weeks of submission counts."""
        month_names = (
            "янв", "фев", "мар", "апр", "май", "июн",
            "июл", "авг", "сен", "окт", "ноя", "дек",
        )
        day_names = (
            "понедельник", "вторник", "среда", "четверг",
            "пятница", "суббота", "воскресенье",
        )
        today = date.today()
        current_week_start = today - timedelta(days=today.weekday())
        start_date = current_week_start - timedelta(weeks=51)
        end_date = current_week_start + timedelta(days=6)

        counts: Dict[date, int] = {}
        rows = execute_query(
            g.db,
            """
            SELECT created_at::date AS day, COUNT(*)::int AS cnt
            FROM problem_test_results
            WHERE user_id = %s
              AND created_at::date >= %s
              AND created_at::date <= %s
            GROUP BY created_at::date
            """,
            (user_id, start_date, today),
        ) or []
        for row in rows:
            day_val = row.get("day")
            if isinstance(day_val, datetime):
                day_val = day_val.date()
            if day_val:
                counts[day_val] = int(row.get("cnt") or 0)

        weeks: List[List[dict]] = []
        months: List[dict] = []
        total = 0
        cursor_day = start_date
        week_index = 0
        prev_month = None
        while cursor_day <= end_date:
            week: List[dict] = []
            for _ in range(7):
                cnt = counts.get(cursor_day, 0) if cursor_day <= today else 0
                if cursor_day <= today:
                    total += cnt
                    level = _activity_level(cnt)
                    future = False
                else:
                    level = -1
                    future = True
                label = (
                    f"{cursor_day.day} {month_names[cursor_day.month - 1]}: "
                    f"{_plural_submissions(cnt)}"
                    if not future
                    else f"{cursor_day.day} {month_names[cursor_day.month - 1]}"
                )
                week.append({
                    "date": cursor_day.isoformat(),
                    "count": cnt,
                    "level": level,
                    "label": label,
                    "future": future,
                    "weekday": day_names[cursor_day.weekday()],
                })
                if cursor_day.day == 1 or (week_index == 0 and cursor_day == start_date):
                    if cursor_day.month != prev_month:
                        months.append({
                            "label": month_names[cursor_day.month - 1],
                            "week_index": week_index,
                        })
                        prev_month = cursor_day.month
                cursor_day += timedelta(days=1)
            weeks.append(week)
            week_index += 1

        return {
            "weeks": weeks,
            "months": months,
            "total": total,
            "weekday_labels": [
                {"text": "Пн", "show": True},
                {"text": "Вт", "show": False},
                {"text": "Ср", "show": True},
                {"text": "Чт", "show": False},
                {"text": "Пт", "show": True},
                {"text": "Сб", "show": False},
                {"text": "Вс", "show": False},
            ],
            "total_label": _plural_submissions(total),
        }

    @app.route("/leaderboard")
    def leaderboard():
        top = execute_query(
            g.db,
            """
            SELECT
              u.username,
              u.display_name,
              u.avatar_path,
              u.points,
              COALESCE((
                SELECT COUNT(DISTINCT ptr.problem_id)
                FROM problem_test_results ptr
                WHERE ptr.user_id = u.id AND ptr.status = 'passed'
              ), 0) AS solved_count
            FROM users u
            ORDER BY u.points DESC, u.id ASC
            LIMIT 20
            """,
        )
        return render_template("leaderboard.html", top=top)
    
    @app.route("/api/docker/stats")
    def docker_stats():
        """API эндпоинт для получения статистики Docker пула"""
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "unauthorized"}), 401
        
        use_docker = os.environ.get("USE_DOCKER", "false").lower() in ("true", "1", "yes")
        if not use_docker:
            return jsonify({"error": "Docker режим отключен"}), 400
        
        try:
            from docker_executor_pool import get_executor_pool
            pool = get_executor_pool()
            stats = pool.get_stats()
            return jsonify({
                "success": True,
                "stats": stats
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    def _load_problem_tests(problem_row):
        tests_raw = problem_row.get("tests") if problem_row else None
        if isinstance(tests_raw, str):
            return json.loads(tests_raw)
        if isinstance(tests_raw, (list, dict)):
            return tests_raw
        return []

    def _build_chart_stats(problem_id: int, results: dict):
        try:
            cursor = get_cursor(g.db)
            cursor.execute(
                """
                SELECT execution_time, memory_used
                FROM problem_test_results
                WHERE problem_id = %s AND status = 'passed' AND memory_used IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (problem_id,),
            )
            stats_data = cursor.fetchall()
            cursor.close()
        except Exception:
            return None

        if not stats_data:
            return None

        others_data = [
            {
                "execution_time": float(row["execution_time"] or 0),
                "memory_used": float(row["memory_used"] or 0),
            }
            for row in stats_data
        ]
        current_time = float(results.get("execution_time", 0.0) or 0.0)
        current_memory = float(
            results.get("memory_used", results.get("max_memory_mb", 0.0)) or 0.0
        )

        time_values = sorted([o["execution_time"] * 1000 for o in others_data])
        memory_values = sorted([o["memory_used"] for o in others_data])
        current_time_ms = current_time * 1000
        worse_time_count = sum(1 for t in time_values if t > current_time_ms)
        time_percentile = int((worse_time_count / len(time_values)) * 100) if time_values else 0
        worse_memory_count = sum(1 for m in memory_values if m > current_memory)
        memory_percentile = (
            int((worse_memory_count / len(memory_values)) * 100) if memory_values else 0
        )

        def _bins(values):
            if not values:
                return None
            vmin, vmax = min(values), max(values)
            if vmax - vmin <= 0:
                return {"min": vmin, "max": vmax, "bin_width": 1, "counts": [len(values)]}
            bin_width = (vmax - vmin) / 10
            counts = [0] * 10
            for v in values:
                idx = min(int((v - vmin) / bin_width), 9)
                counts[idx] += 1
            return {"min": vmin, "max": vmax, "bin_width": bin_width, "counts": counts}

        return {
            "current": {"execution_time": current_time, "memory_used": current_memory},
            "others": others_data,
            "time_percentile": time_percentile,
            "memory_percentile": memory_percentile,
            "time_bins": _bins(time_values),
            "memory_bins": _bins(memory_values),
            "others_count": len(others_data),
        }

    @app.route("/api/problem/<int:problem_id>/run", methods=["POST"])
    def api_problem_run(problem_id: int):
        """Быстрый прогон: первые тесты, без сохранения и без очков."""
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        user_code = (payload.get("code") or "").strip()
        if not user_code:
            return jsonify({"error": "Код решения не может быть пустым"}), 400

        problem_row = execute_one(
            g.db,
            """
            SELECT problem_id, tests
            FROM leetcode_problems_with_tests
            WHERE problem_id=%s
            """,
            (problem_id,),
        )
        if not problem_row:
            return jsonify({"error": "Задача не найдена"}), 404

        try:
            tests_data = _load_problem_tests(problem_row)
        except Exception as e:
            return jsonify({"error": f"Ошибка парсинга тестов: {e}"}), 500

        if not tests_data:
            return jsonify({"error": "Для этой задачи нет тестов"}), 400

        sample = tests_data[: min(3, len(tests_data))]
        report = judge_user_code_with_tests(
            user_code, sample, language="python3", time_limit_sec=2.0
        )
        report["mode"] = "run"
        report["sample_only"] = True
        report["points_awarded"] = 0
        report["results"] = convert_special_floats_for_json(report.get("results", []))
        session[f"code_{problem_id}"] = user_code
        return jsonify({"success": True, "results": report})

    @app.route("/api/problem/<int:problem_id>/submit", methods=["POST"])
    def api_problem_submit(problem_id: int):
        """Полная отправка: все тесты, сохранение и начисление очков."""
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        user_code = (payload.get("code") or "").strip()
        if not user_code:
            return jsonify({"error": "Код решения не может быть пустым"}), 400

        problem_row = execute_one(
            g.db,
            """
            SELECT problem_id, difficulty, tests
            FROM leetcode_problems_with_tests
            WHERE problem_id=%s
            """,
            (problem_id,),
        )
        if not problem_row:
            return jsonify({"error": "Задача не найдена"}), 404

        try:
            tests_data = _load_problem_tests(problem_row)
        except Exception as e:
            return jsonify({"error": f"Ошибка парсинга тестов: {e}"}), 500

        if not tests_data:
            return jsonify({"error": "Для этой задачи нет тестов"}), 400

        session[f"code_{problem_id}"] = user_code

        try:
            report = judge_user_code_with_tests(
                user_code, tests_data, language="python3", time_limit_sec=2.0
            )
        except Exception as e:
            try:
                cursor = get_cursor(g.db)
                cursor.execute(
                    """
                    INSERT INTO problem_test_results(
                        problem_id, user_id, solution_code, status, error_message, created_at
                    )
                    VALUES(%s, %s, %s, %s, %s, NOW())
                    """,
                    (problem_id, user_id, user_code, "error", str(e)),
                )
                g.db.commit()
                cursor.close()
            except Exception:
                try:
                    g.db.rollback()
                except Exception:
                    pass
            return jsonify({"success": False, "error": str(e), "results": {
                "passed": False, "status": "error", "error": str(e), "results": []
            }}), 500

        memory_used = report.get("memory_used", report.get("max_memory_mb", 0.0)) or 0.0
        results_for_json = convert_special_floats_for_json(report.get("results", []))
        points_awarded = 0
        cursor = get_cursor(g.db)
        try:
            cursor.execute(
                """
                INSERT INTO problem_test_results(
                    problem_id, user_id, solution_code, test_results,
                    passed_tests, total_tests, execution_time, memory_used, status, created_at
                )
                VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    problem_id,
                    user_id,
                    user_code,
                    json.dumps(results_for_json, ensure_ascii=False),
                    report.get("passed_tests", 0),
                    report.get("total_tests", 0),
                    report.get("execution_time", 0.0),
                    memory_used,
                    report.get("status", "failed"),
                ),
            )

            if report.get("passed"):
                prev_passed = execute_one(
                    g.db,
                    """
                    SELECT 1 FROM problem_test_results
                    WHERE user_id=%s AND problem_id=%s AND status='passed'
                      AND id < (SELECT MAX(id) FROM problem_test_results
                                WHERE user_id=%s AND problem_id=%s)
                    LIMIT 1
                    """,
                    (user_id, problem_id, user_id, problem_id),
                )
                if not prev_passed:
                    difficulty = problem_row.get("difficulty") or "Easy"
                    points_awarded = (
                        100 if difficulty == "Easy"
                        else (200 if difficulty == "Medium" else 300)
                    )
                    cursor.execute(
                        "UPDATE users SET points = COALESCE(points,0) + %s WHERE id=%s",
                        (points_awarded, user_id),
                    )

            g.db.commit()
        except Exception as e:
            try:
                g.db.rollback()
            except Exception:
                pass
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            cursor.close()

        report["mode"] = "submit"
        report["sample_only"] = False
        report["points_awarded"] = points_awarded
        report["memory_used"] = memory_used
        report["results"] = results_for_json
        stats_for_chart = None
        if report.get("passed"):
            stats_for_chart = _build_chart_stats(problem_id, report)

        return jsonify({
            "success": True,
            "results": report,
            "stats_for_chart": stats_for_chart,
        })

    @app.route("/problem/<int:problem_id>", methods=["GET", "POST"])
    def problem(problem_id: int):
        user_id = session.get("user_id")  # Может быть None для неавторизованных

        # Получаем задачу из новой таблицы
        problem = execute_one(g.db,
            """
            SELECT problem_id, title, description, difficulty, 
                   topic_tags, topic, code_python3, examples, constraints, tests, hints
            FROM leetcode_problems_with_tests 
            WHERE problem_id=%s
            """,
            (problem_id,),
        )
        
        if not problem:
            flash("Задача не найдена", "error")
            return redirect(url_for("problems"))
        
        # Парсим JSON поля если они есть
        if problem.get("examples"):
            try:
                if isinstance(problem["examples"], str):
                    problem["examples"] = json.loads(problem["examples"])
                elif not isinstance(problem["examples"], list):
                    problem["examples"] = []
            except Exception:
                problem["examples"] = []
        
        # Парсим подсказки
        hints_data = problem.get("hints")
        if hints_data:
            try:
                if isinstance(hints_data, str):
                    # Пробуем распарсить как JSON
                    if hints_data.strip().startswith('[') or hints_data.strip().startswith('{'):
                        problem["hints"] = json.loads(hints_data)
                    else:
                        # Если это просто строка, делаем из неё массив
                        problem["hints"] = [hints_data] if hints_data.strip() else []
                elif isinstance(hints_data, list):
                    # Уже список
                    problem["hints"] = hints_data
                elif isinstance(hints_data, dict):
                    # Если это словарь, преобразуем в список значений
                    problem["hints"] = list(hints_data.values()) if hints_data else []
                else:
                    problem["hints"] = []
            except Exception as e:
                print(f"Ошибка парсинга подсказок: {e}")
                # Если не удалось распарсить, пробуем как строку
                problem["hints"] = [str(hints_data)] if hints_data else []
        else:
            problem["hints"] = []
        
        # Фильтруем пустые подсказки
        if problem["hints"]:
            problem["hints"] = [h for h in problem["hints"] if h and str(h).strip()]
        
        # Отладочная информация
        print(f"DEBUG: Problem {problem_id} hints: {problem.get('hints')}, type: {type(problem.get('hints'))}, length: {len(problem.get('hints', []))}")
        
        # constraints уже должна быть строкой, но проверим
        if problem.get("constraints") and isinstance(problem["constraints"], dict):
            problem["constraints"] = str(problem["constraints"])

        # Используем только Python 3
        starter_code = problem.get('code_python3', '')
        language = "python3"
        
        # Получаем код из сессии для этой задачи, если есть
        session_key = f"code_{problem_id}"
        code_prefill = session.get(session_key, "")

        # Если в сессии нет кода, берем из БД или стартовый код (только для авторизованных)
        if not code_prefill and user_id:
            last_solution = execute_one(g.db,
                "SELECT solution_code as code FROM problem_test_results WHERE user_id=%s AND problem_id=%s ORDER BY created_at DESC LIMIT 1",
                (user_id, problem_id),
            )
            code_prefill = last_solution["code"] if last_solution else starter_code
        elif not code_prefill:
            code_prefill = starter_code

        results: Dict[str, object] | None = None
        
        # Получаем теги задачи
        tags = problem["topic_tags"] or []

        # Создаем боковое меню для задач той же темы
        sidebar_tree = None
        if problem and problem["topic"]:
            rows = execute_query(g.db,
                """
                SELECT problem_id, title, difficulty
                FROM leetcode_problems_with_tests 
                WHERE topic=%s
                ORDER BY difficulty, problem_id
                """,
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
            # Проверяем авторизацию для отправки решений
            if not user_id:
                flash("Для отправки решений необходимо войти в систему", "error")
                return redirect(url_for("login"))
            
            user_code = request.form.get("code") or ""
            duration_ms = int(request.form.get("duration_ms") or 0)
            language = "python3"  # Только Python 3
            
            # Сохраняем код в сессии для этой задачи
            session_key = f"code_{problem_id}"
            session[session_key] = user_code
            
            if not user_code.strip():
                flash("Код решения не может быть пустым", "error")
                return render_template(
                    "problem.html", problem=problem, code_prefill=user_code, results=None, tags=tags, sidebar_tree=sidebar_tree
                )

            cursor = get_cursor(g.db)
            judge_report = None
            passed = 0
            
            # Проверяем, есть ли тесты в новой таблице
            if problem.get("tests"):
                try:
                    # Парсим JSON тесты (PostgreSQL возвращает jsonb как dict или список)
                    tests_raw = problem["tests"]
                    if isinstance(tests_raw, str):
                        tests_data = json.loads(tests_raw)
                    elif isinstance(tests_raw, (list, dict)):
                        tests_data = tests_raw
                    else:
                        tests_data = []
                    
                    # Выполняем тестирование с новой функцией
                    judge_report = judge_user_code_with_tests(
                        user_code, 
                        tests_data, 
                        language=language,
                        time_limit_sec=2.0
                    )
                    
                    passed = 1 if judge_report.get("passed") else 0
                    
                    # Сохраняем результаты в новую таблицу
                    # Конвертируем специальные float значения для JSON
                    results_for_json = convert_special_floats_for_json(judge_report.get("results", []))
                    
                    cursor.execute(
                        """
                        INSERT INTO problem_test_results(
                            problem_id, user_id, solution_code, test_results, 
                            passed_tests, total_tests, execution_time, memory_used, status, created_at
                        )
                        VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        """,
                        (
                            problem_id,
                            user_id,
                            user_code,
                            json.dumps(results_for_json, ensure_ascii=False),
                            judge_report.get("passed_tests", 0),
                            judge_report.get("total_tests", 0),
                            judge_report.get("execution_time", 0.0),
                            judge_report.get("memory_used", 0.0),
                            judge_report.get("status", "failed"),
                        ),
                    )
                    
                except Exception as e:
                    # Откатываем транзакцию при ошибке
                    try:
                        g.db.rollback()
                    except Exception:
                        pass
                    cursor.close()
                    
                    # В случае ошибки формируем отчет об ошибке
                    error_msg = str(e)
                    judge_report = {
                        "passed": False,
                        "results": [],
                        "error": error_msg,
                        "status": "error",
                    }
                    
                    # Пытаемся сохранить информацию об ошибке в новую транзакцию
                    try:
                        cursor = get_cursor(g.db)
                        cursor.execute(
                            """
                            INSERT INTO problem_test_results(
                                problem_id, user_id, solution_code, status, error_message, created_at
                            )
                            VALUES(%s, %s, %s, %s, %s, NOW())
                            """,
                            (problem_id, user_id, user_code, "error", error_msg),
                        )
                        g.db.commit()
                        cursor.close()
                        flash(f"Ошибка при тестировании: {error_msg}", "error")
                    except Exception as e2:
                        # Если даже сохранение ошибки не удалось, откатываем и показываем сообщение
                        try:
                            g.db.rollback()
                        except Exception:
                            pass
                        if cursor:
                            cursor.close()
                        flash(f"Критическая ошибка при сохранении результатов: {str(e2)}. Исходная ошибка: {error_msg}", "error")
            else:
                # Если нет тестов, просто показываем ошибку
                flash("Для этой задачи нет тестов. Невозможно проверить решение.", "error")
                cursor.close()
                return render_template(
                    "problem.html", problem=problem, code_prefill=user_code, results=None, tags=tags, sidebar_tree=sidebar_tree
                )

            points_awarded = 0
            try:
                if passed:
                    # Проверяем, решалась ли задача ранее (используем problem_test_results)
                    prev_passed = execute_one(g.db,
                        """
                        SELECT 1 FROM problem_test_results 
                        WHERE user_id=%s AND problem_id=%s AND status='passed' 
                        AND id < (SELECT MAX(id) FROM problem_test_results WHERE user_id=%s AND problem_id=%s) 
                        LIMIT 1
                        """,
                        (user_id, problem_id, user_id, problem_id),
                    )
                    if not prev_passed:
                        # Начисляем очки за первую сдачу
                        points_awarded = 100 if problem["difficulty"] == "Easy" else (200 if problem["difficulty"] == "Medium" else 300)
                        cursor.execute("UPDATE users SET points = COALESCE(points,0) + %s WHERE id=%s", (points_awarded, user_id))
                        flash(f"Задача решена! +{points_awarded} очков", "success")
                
                g.db.commit()
            except Exception as e:
                # Откатываем транзакцию при ошибке
                g.db.rollback()
                flash(f"Ошибка при сохранении результатов: {str(e)}", "error")
            finally:
                cursor.close()

            # Добавляем информацию о начисленных очках в результаты
            if judge_report:
                judge_report["points_awarded"] = points_awarded
            results = judge_report  # type: ignore[assignment]
            # После POST используем код, который только что отправили
            code_prefill = user_code
            
            # Если решение успешное, получаем статистику других решений для графика
            stats_for_chart = None
            if results and results.get("passed"):
                try:
                    cursor = get_cursor(g.db)
                    cursor.execute("""
                        SELECT execution_time, memory_used
                        FROM problem_test_results
                        WHERE problem_id = %s AND status = 'passed' AND memory_used IS NOT NULL
                        ORDER BY created_at DESC
                        LIMIT 100
                    """, (problem_id,))
                    stats_data = cursor.fetchall()
                    cursor.close()
                    
                    if stats_data:
                        others_data = [
                            {
                                "execution_time": float(row["execution_time"] or 0),
                                "memory_used": float(row["memory_used"] or 0)
                            }
                            for row in stats_data
                        ]
                        current_time = results.get("execution_time", 0.0)
                        current_memory = results.get("memory_used", 0.0)
                        
                        # Вычисляем процентили и гистограммы
                        time_percentile = 0
                        memory_percentile = 0
                        time_bins = []
                        memory_bins = []
                        
                        if others_data:
                            time_values = sorted([o["execution_time"] * 1000 for o in others_data])  # в ms
                            memory_values = sorted([o["memory_used"] for o in others_data])
                            
                            # Вычисляем процентиль (сколько процентов решений хуже нашего)
                            current_time_ms = current_time * 1000
                            worse_time_count = sum(1 for t in time_values if t > current_time_ms)
                            time_percentile = int((worse_time_count / len(time_values)) * 100) if time_values else 0
                            
                            worse_memory_count = sum(1 for m in memory_values if m > current_memory)
                            memory_percentile = int((worse_memory_count / len(memory_values)) * 100) if memory_values else 0
                            
                            # Создаем bins для гистограмм (10 интервалов)
                            if time_values:
                                time_min, time_max = min(time_values), max(time_values)
                                time_range = time_max - time_min
                                if time_range > 0:
                                    bin_width = time_range / 10
                                    time_bins = [0] * 10
                                    for t in time_values:
                                        bin_idx = min(int((t - time_min) / bin_width), 9)
                                        time_bins[bin_idx] += 1
                                    time_bins_data = {
                                        "min": time_min,
                                        "max": time_max,
                                        "bin_width": bin_width,
                                        "counts": time_bins
                                    }
                                else:
                                    time_bins_data = {"min": time_min, "max": time_max, "bin_width": 1, "counts": [len(time_values)]}
                            else:
                                time_bins_data = None
                            
                            if memory_values:
                                mem_min, mem_max = min(memory_values), max(memory_values)
                                mem_range = mem_max - mem_min
                                if mem_range > 0:
                                    bin_width = mem_range / 10
                                    memory_bins = [0] * 10
                                    for m in memory_values:
                                        bin_idx = min(int((m - mem_min) / bin_width), 9)
                                        memory_bins[bin_idx] += 1
                                    memory_bins_data = {
                                        "min": mem_min,
                                        "max": mem_max,
                                        "bin_width": bin_width,
                                        "counts": memory_bins
                                    }
                                else:
                                    memory_bins_data = {"min": mem_min, "max": mem_max, "bin_width": 1, "counts": [len(memory_values)]}
                            else:
                                memory_bins_data = None
                        else:
                            time_bins_data = None
                            memory_bins_data = None
                        
                        stats_for_chart = {
                            "current": {
                                "execution_time": current_time,
                                "memory_used": current_memory
                            },
                            "others": others_data,
                            "time_percentile": time_percentile,
                            "memory_percentile": memory_percentile,
                            "time_bins": time_bins_data,
                            "memory_bins": memory_bins_data,
                            "others_count": len(others_data)
                        }
                except Exception as e:
                    print(f"Ошибка при получении статистики для графика: {e}")
                    stats_for_chart = None
        else:
            stats_for_chart = None

        # Получаем комментарии к задаче с реакциями
        comments_raw = execute_query(g.db,
            """
            SELECT c.id, c.user_id, c.content, c.created_at, c.updated_at,
                   u.username, u.display_name, u.avatar_path,
                   COALESCE(SUM(CASE WHEN cr.reaction_type = 'like' THEN 1 ELSE 0 END)::int, 0) as likes_count,
                   COALESCE(SUM(CASE WHEN cr.reaction_type = 'dislike' THEN 1 ELSE 0 END)::int, 0) as dislikes_count
            FROM problem_comments c
            JOIN users u ON u.id = c.user_id
            LEFT JOIN comment_reactions cr ON cr.comment_id = c.id
            WHERE c.problem_id = %s
            GROUP BY c.id, c.user_id, c.content, c.created_at, c.updated_at,
                     u.username, u.display_name, u.avatar_path
            ORDER BY c.created_at ASC
            """,
            (problem_id,),
        )
        
        # Получаем реакции текущего пользователя для каждого комментария
        comments = []
        for comment in comments_raw:
            user_reaction = None
            if user_id:
                reaction = execute_one(g.db,
                    "SELECT reaction_type FROM comment_reactions WHERE comment_id = %s AND user_id = %s",
                    (comment["id"], user_id),
                )
                if reaction:
                    user_reaction = reaction["reaction_type"]
            
            comment_dict = dict(comment)
            comment_dict["user_reaction"] = user_reaction
            comments.append(comment_dict)
        
        return render_template(
            "problem.html",
            problem=problem,
            code_prefill=code_prefill,
            results=results,
            tags=tags,
            sidebar_tree=sidebar_tree,
            stats_for_chart=stats_for_chart,
            comments=comments,
            user_id=user_id,  # Передаем user_id для проверки авторизации в шаблоне
        )

    @app.route("/comment/add", methods=["POST"])
    def add_comment():
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Требуется авторизация"}), 401
        
        problem_id = request.form.get("problem_id")
        content = request.form.get("content", "").strip()
        
        if not problem_id or not content:
            flash("Комментарий не может быть пустым", "error")
            return redirect(url_for("problem", problem_id=problem_id))
        
        try:
            cursor = get_cursor(g.db)
            cursor.execute(
                """
                INSERT INTO problem_comments (problem_id, user_id, content, created_at, updated_at)
                VALUES (%s, %s, %s, NOW(), NOW())
                """,
                (problem_id, user_id, content),
            )
            g.db.commit()
            cursor.close()
            flash("Комментарий добавлен", "success")
        except Exception as e:
            g.db.rollback()
            flash(f"Ошибка при добавлении комментария: {str(e)}", "error")
        
        return redirect(url_for("problem", problem_id=problem_id))
    
    @app.route("/comment/<int:comment_id>/delete", methods=["POST"])
    def delete_comment(comment_id):
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Требуется авторизация"}), 401
        
        # Проверяем, что комментарий принадлежит пользователю
        comment = execute_one(g.db,
            "SELECT problem_id, user_id FROM problem_comments WHERE id = %s",
            (comment_id,),
        )
        
        if not comment:
            flash("Комментарий не найден", "error")
            return redirect(url_for("problems"))
        
        if comment["user_id"] != user_id:
            flash("Вы не можете удалить этот комментарий", "error")
            return redirect(url_for("problem", problem_id=comment["problem_id"]))
        
        try:
            cursor = get_cursor(g.db)
            cursor.execute("DELETE FROM problem_comments WHERE id = %s", (comment_id,))
            g.db.commit()
            cursor.close()
            flash("Комментарий удален", "success")
        except Exception as e:
            g.db.rollback()
            flash(f"Ошибка при удалении комментария: {str(e)}", "error")
        
        return redirect(url_for("problem", problem_id=comment["problem_id"]))
    
    @app.route("/comment/<int:comment_id>/edit", methods=["POST"])
    def edit_comment(comment_id):
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Требуется авторизация"}), 401
        
        # Проверяем, что комментарий принадлежит пользователю
        comment = execute_one(g.db,
            "SELECT problem_id, user_id FROM problem_comments WHERE id = %s",
            (comment_id,),
        )
        
        if not comment:
            return jsonify({"error": "Комментарий не найден"}), 404
        
        if comment["user_id"] != user_id:
            return jsonify({"error": "Вы не можете редактировать этот комментарий"}), 403
        
        content = request.form.get("content", "").strip()
        if not content:
            return jsonify({"error": "Комментарий не может быть пустым"}), 400
        
        try:
            cursor = get_cursor(g.db)
            cursor.execute(
                "UPDATE problem_comments SET content = %s, updated_at = NOW() WHERE id = %s",
                (content, comment_id),
            )
            g.db.commit()
            cursor.close()
            return jsonify({"success": True, "content": content})
        except Exception as e:
            g.db.rollback()
            return jsonify({"error": str(e)}), 500
    
    @app.route("/comment/<int:comment_id>/reaction", methods=["POST"])
    def toggle_reaction(comment_id):
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Требуется авторизация"}), 401
        
        reaction_type = request.form.get("reaction_type")
        if reaction_type not in ["like", "dislike"]:
            return jsonify({"error": "Неверный тип реакции"}), 400
        
        # Проверяем существующую реакцию
        existing = execute_one(g.db,
            "SELECT reaction_type FROM comment_reactions WHERE comment_id = %s AND user_id = %s",
            (comment_id, user_id),
        )
        
        try:
            cursor = get_cursor(g.db)
            if existing:
                if existing["reaction_type"] == reaction_type:
                    # Удаляем реакцию, если пользователь нажал на ту же кнопку
                    cursor.execute(
                        "DELETE FROM comment_reactions WHERE comment_id = %s AND user_id = %s",
                        (comment_id, user_id),
                    )
                else:
                    # Меняем реакцию
                    cursor.execute(
                        "UPDATE comment_reactions SET reaction_type = %s WHERE comment_id = %s AND user_id = %s",
                        (reaction_type, comment_id, user_id),
                    )
            else:
                # Добавляем новую реакцию
                cursor.execute(
                    "INSERT INTO comment_reactions (comment_id, user_id, reaction_type) VALUES (%s, %s, %s)",
                    (comment_id, user_id, reaction_type),
                )
            
            g.db.commit()
            cursor.close()
            
            # Получаем обновленные счетчики
            likes = execute_one(g.db,
                "SELECT COUNT(*) as count FROM comment_reactions WHERE comment_id = %s AND reaction_type = 'like'",
                (comment_id,),
            )
            dislikes = execute_one(g.db,
                "SELECT COUNT(*) as count FROM comment_reactions WHERE comment_id = %s AND reaction_type = 'dislike'",
                (comment_id,),
            )
            
            # Проверяем текущую реакцию пользователя
            current_reaction = execute_one(g.db,
                "SELECT reaction_type FROM comment_reactions WHERE comment_id = %s AND user_id = %s",
                (comment_id, user_id),
            )
            
            return jsonify({
                "likes": likes["count"] if likes else 0,
                "dislikes": dislikes["count"] if dislikes else 0,
                "user_reaction": current_reaction["reaction_type"] if current_reaction else None,
            })
        except Exception as e:
            g.db.rollback()
            return jsonify({"error": str(e)}), 500
    
    @app.route("/submissions")
    def submissions():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        per_page = 30
        offset = (page - 1) * per_page
        total_row = execute_one(
            g.db,
            "SELECT COUNT(*) AS total FROM problem_test_results WHERE user_id=%s",
            (user_id,),
        )
        total = int((total_row or {}).get("total") or 0)
        subs = execute_query(
            g.db,
            """
            SELECT ptr.id, p.problem_id, p.title AS problem_title,
                   CASE WHEN ptr.status='passed' THEN 1 ELSE 0 END as passed,
                   ptr.created_at, p.difficulty
            FROM problem_test_results ptr
            JOIN leetcode_problems_with_tests p ON p.problem_id = ptr.problem_id
            WHERE ptr.user_id = %s
            ORDER BY ptr.created_at DESC, ptr.id DESC
            LIMIT %s OFFSET %s
            """,
            (user_id, per_page, offset),
        )
        total_pages = (total + per_page - 1) // per_page if total else 1
        return render_template(
            "submissions.html",
            submissions=subs,
            page=page,
            total_pages=total_pages,
            total=total,
        )

    @app.route("/u/<username>")
    def public_profile(username: str):
        username = (username or "").strip()
        user = execute_one(
            g.db,
            """
            SELECT id, username, display_name, bio, avatar_path, points
            FROM users WHERE username=%s
            """,
            (username,),
        )
        if not user:
            flash("Пользователь не найден", "error")
            return redirect(url_for("leaderboard"))
        stats = execute_one(
            g.db,
            """
            SELECT
              (SELECT COUNT(*) FROM problem_test_results WHERE user_id=%s) AS submissions_count,
              (SELECT COUNT(DISTINCT problem_id) FROM problem_test_results
               WHERE user_id=%s AND status='passed') AS solved_count
            """,
            (user["id"], user["id"]),
        )
        activity = _build_activity_calendar(user["id"])
        is_own = session.get("user_id") == user["id"]
        return render_template(
            "public_profile.html",
            user=user,
            submissions_count=(stats or {}).get("submissions_count") or 0,
            solved_count=(stats or {}).get("solved_count") or 0,
            activity_weeks=activity["weeks"],
            activity_months=activity["months"],
            activity_total=activity["total"],
            activity_total_label=activity["total_label"],
            activity_weekday_labels=activity["weekday_labels"],
            is_own=is_own,
        )

    @app.route("/submission/<int:solution_id>")
    def submission_detail(solution_id: int):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        s = execute_one(g.db,
            """
            SELECT ptr.id, ptr.user_id, ptr.problem_id as task_id, ptr.solution_code as code, 
                   CASE WHEN ptr.status='passed' THEN 1 ELSE 0 END as passed, 
                   ptr.test_results::text as result_json, ptr.created_at, 
                   p.title AS problem_title, p.difficulty
            FROM problem_test_results ptr 
            JOIN leetcode_problems_with_tests p ON p.problem_id = ptr.problem_id 
            WHERE ptr.id = %s
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
            # test_results может быть jsonb или строкой
            result_json = s.get("result_json")
            if result_json:
                if isinstance(result_json, str):
                    result = json.loads(result_json)
                else:
                    # Если это уже dict (jsonb возвращается как dict)
                    result = result_json
            else:
                result = None
        except Exception:  # noqa: BLE001
            result = None
        return render_template("submission_detail.html", submission=s, result=result)

    @app.route("/sandbox", methods=["GET", "POST"])
    def sandbox():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))

        results = None
        language = "python3"

        if request.method == "POST":
            code = request.form.get("code", "")
            if code.strip():
                try:
                    result = _run_with_timeout(code, 5.0)
                    results = {
                        "success": result.get("ok", False),
                        "output": result.get("output", ""),
                        "error": result.get("error", ""),
                        "execution_time": result.get("execution_time", 0),
                    }
                except Exception as e:
                    results = {
                        "success": False,
                        "output": "",
                        "error": f"Ошибка выполнения: {str(e)}",
                        "execution_time": 0,
                    }

        return render_template("sandbox.html", results=results, selected_language=language)

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


def convert_special_floats_for_json(obj):
    """
    Рекурсивно конвертирует специальные float значения (inf, -inf, nan) 
    в строковое представление для корректной сериализации в JSON
    """
    import math
    
    if isinstance(obj, float):
        if math.isnan(obj):
            return "NaN"
        elif math.isinf(obj):
            return "Infinity" if obj > 0 else "-Infinity"
        else:
            return obj
    elif isinstance(obj, dict):
        return {key: convert_special_floats_for_json(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_special_floats_for_json(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_special_floats_for_json(item) for item in obj)
    else:
        return obj


def judge_user_code_with_tests(
    user_code: str, tests: List[Dict], language: str = "python3", time_limit_sec: float = 2.0
) -> Dict[str, object]:
    """
    Выполняет тестирование кода пользователя с использованием тестов из новой таблицы.

    Args:
        user_code: Код пользователя
        tests: Список тестов в формате [{"input": "...", "output": ...}, ...]
        language: Язык программирования
        time_limit_sec: Лимит времени на выполнение одного теста

    Returns:
        Словарь с результатами тестирования
    """
    import ast
    import math
    import json
    import re

    def _normalize_value(value):
        if isinstance(value, str):
            text = value.strip()
            if text == "":
                return text
            try:
                return ast.literal_eval(text)
            except Exception:
                try:
                    return json.loads(text)
                except Exception:
                    return text
        return value

    if language == "python3":
        method_match = re.search(r'def\s+(\w+)\s*\(', user_code)
        method_name = method_match.group(1) if method_match else "solve"

        tests_python_literal = repr(tests)

        template_body = Template(
            """
def __zedcode_parse_input(raw_input):
    import ast
    import json

    if raw_input is None:
        return None

    if isinstance(raw_input, (list, tuple, dict, int, float, bool)):
        return raw_input

    text = str(raw_input).strip()
    if text == "":
        return text

    try:
        return ast.literal_eval(text)
    except Exception:
        try:
            return json.loads(text)
        except Exception:
            return text


def __zedcode_call_method(solution, method_name, raw_input):
    parsed = __zedcode_parse_input(raw_input)
    target = getattr(solution, method_name)

    if isinstance(parsed, dict):
        return target(**parsed)
    if isinstance(parsed, (list, tuple)):
        return target(parsed)
    return target(parsed)


def __zedcode_serialize(obj):
    if isinstance(obj, (list, tuple)):
        return [__zedcode_serialize(item) for item in obj]
    if isinstance(obj, dict):
        normalized = {}
        for key, val in obj.items():
            normalized[str(key)] = __zedcode_serialize(val)
        return normalized
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return str(obj)


if __name__ == "__main__":
    import json
    import time
    import traceback

    tests = $tests_python_literal
    output = []
    solution = Solution()

    for index, case in enumerate(tests, start=1):
        test_input = case.get("input", "")
        expected = case.get("output")
        record = {"case": index, "input": test_input, "expected": expected}
        start_time = time.perf_counter()
        try:
            result = __zedcode_call_method(solution, "$method_name", test_input)
            record["actual"] = __zedcode_serialize(result)
            record["execution_time"] = time.perf_counter() - start_time
        except Exception as exc:
            record["error"] = str(exc)
            record["traceback"] = traceback.format_exc()
        output.append(record)

    print(json.dumps(output, ensure_ascii=False))
"""
        )

        harness = "\n".join(
            [
                user_code,
                "",
                template_body.safe_substitute(
                    tests_python_literal=tests_python_literal,
                    method_name=method_name,
                ).strip(),
            ]
        )

        outcome = _run_with_timeout(harness, time_limit_sec * max(len(tests), 1))
        memory_mb = outcome.get("memory_mb", 0.0)
        raw_output = (outcome.get("output") or "").strip()

        results: List[Dict[str, object]] = []
        all_passed = True
        total_execution_time = 0.0

        if not outcome.get("ok"):
            results.append({
                "case": 1,
                "status": "RUNTIME_ERROR",
                "message": outcome.get("error", "Неизвестная ошибка"),
                "traceback": outcome.get("traceback"),
            })
            all_passed = False
        elif not raw_output:
            results.append({
                "case": 1,
                "status": "RUNTIME_ERROR",
                "message": "Пустой вывод тестового стенда",
            })
            all_passed = False
        else:
            try:
                batch_results = json.loads(raw_output)
            except json.JSONDecodeError as exc:
                results.append({
                    "case": 1,
                    "status": "RUNTIME_ERROR",
                    "message": f"Ошибка парсинга вывода тестового стенда: {str(exc)}",
                    "raw_output": raw_output[:500],
                })
                all_passed = False
            else:
                for idx, test in enumerate(tests, start=1):
                    case_info = batch_results[idx - 1] if idx - 1 < len(batch_results) else {}
                    test_input = test.get("input", "")
                    expected_output = test.get("output")

                    if case_info.get("error"):
                        results.append({
                            "case": idx,
                            "status": "RUNTIME_ERROR",
                            "message": case_info.get("error"),
                            "traceback": case_info.get("traceback"),
                            "input": test_input,
                            "expected": expected_output,
                        })
                        all_passed = False
                        continue

                    execution_time_case = float(case_info.get("execution_time", 0.0) or 0.0)
                    total_execution_time += execution_time_case

                    if execution_time_case > time_limit_sec:
                        results.append({
                            "case": idx,
                            "status": "TIMEOUT",
                            "message": f"Превышено время {time_limit_sec:.1f}s",
                            "input": test_input,
                            "expected": expected_output,
                        })
                        all_passed = False
                        continue

                    actual_value = case_info.get("actual")
                    actual_normalized = _normalize_value(actual_value)
                    expected_normalized = _normalize_value(expected_output)

                    is_equal = False
                    if isinstance(actual_normalized, float) and isinstance(expected_normalized, float):
                        if math.isnan(actual_normalized) and math.isnan(expected_normalized):
                            is_equal = True
                        elif math.isinf(actual_normalized) and math.isinf(expected_normalized):
                            is_equal = (actual_normalized > 0) == (expected_normalized > 0)
                        else:
                            is_equal = actual_normalized == expected_normalized
                    else:
                        is_equal = actual_normalized == expected_normalized

                    if is_equal:
                        results.append({
                            "case": idx,
                            "status": "OK",
                            "input": test_input,
                            "expected": expected_output,
                            "actual": actual_value,
                            "execution_time": execution_time_case,
                            "memory_mb": memory_mb,
                        })
                    else:
                        results.append({
                            "case": idx,
                            "status": "WA",
                            "message": f"Ожидалось: {expected_output!r}, получено: {actual_value!r}",
                            "input": test_input,
                            "expected": expected_output,
                            "actual": actual_value,
                            "execution_time": execution_time_case,
                            "memory_mb": memory_mb,
                        })
                        all_passed = False

        return {
            "passed": all_passed,
            "results": results,
            "passed_tests": sum(1 for r in results if r.get("status") == "OK"),
            "total_tests": len(results),
            "execution_time": total_execution_time,
            "max_memory_mb": memory_mb,
            "status": "passed" if all_passed else "failed",
        }

    # Обработка всех остальных языков (старый подход на каждый тест отдельно)
    results: List[Dict[str, object]] = []
    all_passed = True
    total_execution_time = 0.0
    max_memory_used = 0.0

    for idx, test in enumerate(tests, start=1):
        test_input = test.get("input", "")
        expected_output = test.get("output")

        if not isinstance(test_input, str):
            test_input = str(test_input)

        test_input = test_input.strip()

        try:
            input_array = ast.literal_eval(test_input)
        except (ValueError, SyntaxError):
            try:
                input_array = json.loads(test_input)
            except json.JSONDecodeError:
                input_array = test_input

        test_code = user_code

        outcome = _run_with_timeout(test_code, time_limit_sec)
        execution_time = outcome.get("execution_time", 0) / 1000.0
        memory_mb = outcome.get("memory_mb", 0.0)
        total_execution_time += execution_time
        if memory_mb > max_memory_used:
            max_memory_used = memory_mb

        if execution_time > time_limit_sec or "Превышено время" in outcome.get("error", ""):
            case_res = {
                "case": idx,
                "status": "TIMEOUT",
                "message": f"Превышено время {time_limit_sec:.1f}s",
                "input": test_input,
                "expected": expected_output,
            }
            all_passed = False
        elif not outcome.get("ok"):
            case_res = {
                "case": idx,
                "status": "RUNTIME_ERROR",
                "message": outcome.get("error", "Неизвестная ошибка"),
                "traceback": outcome.get("traceback"),
                "input": test_input,
                "expected": expected_output,
            }
            all_passed = False
        else:
            actual_output_str = (outcome.get("output") or "").strip()

            if not actual_output_str:
                case_res = {
                    "case": idx,
                    "status": "WA",
                    "message": f"Пустой вывод. Ожидалось: {expected_output!r}",
                    "input": test_input,
                    "expected": expected_output,
                    "actual": "",
                    "execution_time": execution_time,
                }
                all_passed = False
            else:
                try:
                    if actual_output_str.lower() in ("true", "false"):
                        actual_output = actual_output_str.lower() == "true"
                    elif actual_output_str.lower() in ("none", "null"):
                        actual_output = None
                    elif actual_output_str.lower() in ("inf", "infinity"):
                        actual_output = float("inf")
                    elif actual_output_str.lower() in ("-inf", "-infinity"):
                        actual_output = float("-inf")
                    elif actual_output_str.lower() == "nan":
                        actual_output = float("nan")
                    else:
                        actual_output = ast.literal_eval(actual_output_str)

                    if isinstance(actual_output, float) and isinstance(expected_output, float):
                        if math.isnan(actual_output) and math.isnan(expected_output):
                            is_equal = True
                        elif math.isinf(actual_output) and math.isinf(expected_output):
                            is_equal = (actual_output > 0) == (expected_output > 0)
                        else:
                            is_equal = actual_output == expected_output
                    else:
                        is_equal = actual_output == expected_output

                    if is_equal:
                        case_res = {
                            "case": idx,
                            "status": "OK",
                            "input": test_input,
                            "expected": expected_output,
                            "actual": actual_output,
                            "execution_time": execution_time,
                            "memory_mb": memory_mb,
                        }
                    else:
                        case_res = {
                            "case": idx,
                            "status": "WA",
                            "message": f"Ожидалось: {expected_output!r}, получено: {actual_output!r}",
                            "input": test_input,
                            "expected": expected_output,
                            "actual": actual_output,
                            "execution_time": execution_time,
                            "memory_mb": memory_mb,
                        }
                        all_passed = False
                except Exception as e:
                    case_res = {
                        "case": idx,
                        "status": "WA",
                        "message": f"Ошибка парсинга результата: {str(e)}. Получено: {actual_output_str!r}",
                        "input": test_input,
                        "expected": expected_output,
                        "actual": actual_output_str,
                        "execution_time": execution_time,
                    }
                    all_passed = False

        results.append(case_res)

    return {
        "passed": all_passed,
        "results": results,
        "passed_tests": sum(1 for r in results if r.get("status") == "OK"),
        "total_tests": len(results),
        "execution_time": total_execution_time,
        "max_memory_mb": max_memory_used,
        "status": "passed" if all_passed else "failed",
    }


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


def _run_javascript_with_timeout(code: str, time_limit_sec: float) -> Dict[str, object]:
    """Выполнение JavaScript кода с таймаутом"""
    import subprocess
    import tempfile
    import time
    
    try:
        # Создаем временный файл для JavaScript кода с кодировкой UTF-8
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(code)
            temp_file = f.name
        
        start_time = time.time()
        
        # Выполняем код через Node.js с правильной кодировкой
        env = os.environ.copy()
        env['NODE_OPTIONS'] = '--max-old-space-size=4096'
        
        # Выполняем код через Node.js
        # Используем полный путь к Node.js для Windows
        node_path = r'C:\Program Files\nodejs\node.exe'
        if not os.path.exists(node_path):
            # Если полный путь не найден, пытаемся использовать node из PATH
            node_path = 'node'
        
        result = subprocess.run(
            [node_path, temp_file],
            capture_output=True,
            text=True,
            timeout=time_limit_sec,
            encoding='utf-8',
            errors='replace',
            env=env
        )
        
        execution_time = int((time.time() - start_time) * 1000)
        
        # Удаляем временный файл
        os.unlink(temp_file)
        
        return {
            "ok": result.returncode == 0,
            "output": result.stdout,
            "error": result.stderr if result.returncode != 0 else "",
            "execution_time": execution_time
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Превышено время выполнения", "execution_time": int(time_limit_sec * 1000)}
    except Exception as e:
        return {"ok": False, "error": f"Ошибка выполнения: {str(e)}", "execution_time": 0}

def _run_compiled_with_timeout(code: str, language: str, time_limit_sec: float) -> Dict[str, object]:
    """Выполнение кода компилируемых языков с таймаутом"""
    import subprocess
    import tempfile
    import time
    
    try:
        # Определяем расширение файла и команды компиляции/выполнения
        if language == "java":
            ext = ".java"
            compile_cmd = ["javac"]
            run_cmd = ["java", "Main"]
        elif language == "c":
            ext = ".c"
            compile_cmd = ["gcc", "-o"]
            run_cmd = ["./main"]
        elif language == "cpp":
            ext = ".cpp"
            compile_cmd = ["g++", "-o"]
            run_cmd = ["./main"]
        elif language == "csharp":
            ext = ".cs"
            compile_cmd = ["mcs"]
            run_cmd = ["mono", "main.exe"]
        else:
            return {"ok": False, "error": "Неподдерживаемый язык программирования"}
        
        # Создаем временные файлы с кодировкой UTF-8
        with tempfile.NamedTemporaryFile(mode='w', suffix=ext, delete=False, encoding='utf-8') as f:
            f.write(code)
            source_file = f.name
        
        if language == "java":
            # Для Java создаем файл Main.java
            with tempfile.NamedTemporaryFile(mode='w', suffix='.java', delete=False, encoding='utf-8') as f:
                f.write(code)
                java_file = f.name
            source_file = java_file
        
        start_time = time.time()
        
        # Компилируем код с правильной кодировкой
        env = os.environ.copy()
        env['LANG'] = 'en_US.UTF-8'
        env['LC_ALL'] = 'en_US.UTF-8'
        
        if language == "java":
            compile_result = subprocess.run(
                ["javac", java_file],
                capture_output=True,
                text=True,
                timeout=time_limit_sec,
                encoding='utf-8',
                errors='replace',
                env=env
            )
        else:
            compile_result = subprocess.run(
                compile_cmd + ["main", source_file],
                capture_output=True,
                text=True,
                timeout=time_limit_sec,
                encoding='utf-8',
                errors='replace',
                env=env
            )
        
        if compile_result.returncode != 0:
            return {
                "ok": False,
                "error": f"Ошибка компиляции: {compile_result.stderr}",
                "execution_time": int((time.time() - start_time) * 1000)
            }
        
        # Выполняем скомпилированный код
        run_result = subprocess.run(
            run_cmd,
            capture_output=True,
            text=True,
            timeout=time_limit_sec,
            cwd=os.path.dirname(source_file) if language == "java" else None,
            encoding='utf-8',
            errors='replace',
            env=env
        )
        
        execution_time = int((time.time() - start_time) * 1000)
        
        # Удаляем временные файлы
        os.unlink(source_file)
        if language == "java":
            os.unlink(java_file)
            # Удаляем .class файлы
            for file in os.listdir(os.path.dirname(java_file)):
                if file.endswith('.class'):
                    os.unlink(os.path.join(os.path.dirname(java_file), file))
        else:
            if os.path.exists("main"):
                os.unlink("main")
            if os.path.exists("main.exe"):
                os.unlink("main.exe")
        
        return {
            "ok": run_result.returncode == 0,
            "output": run_result.stdout,
            "error": run_result.stderr if run_result.returncode != 0 else "",
            "execution_time": execution_time
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Превышено время выполнения", "execution_time": int(time_limit_sec * 1000)}
    except Exception as e:
        return {"ok": False, "error": f"Ошибка выполнения: {str(e)}", "execution_time": 0}

def _run_with_timeout_docker(code: str, time_limit_sec: float) -> Dict[str, object]:
    """Выполнение Python кода в Docker контейнере с ограничениями (использует пул контейнеров)"""
    import logging
    
    # Настройка логирования
    logger = logging.getLogger(__name__)
    
    try:
        # ЯВНЫЙ переключатель: использовать ли пул
        use_pool = os.environ.get("DOCKER_USE_POOL", "false").lower() in ("true", "1", "yes")
        if not use_pool:
            # Запускаем одноразовый контейнер (без пула)
            return _run_with_timeout_docker_legacy(code, time_limit_sec)

        # Используем пул контейнеров для переиспользования
        from docker_executor_pool import get_executor_pool
        
        pool = get_executor_pool()
        logger.debug("🔄 Используем пул контейнеров для выполнения кода")
        
        return pool.execute_code(code, time_limit_sec)
        
    except ImportError:
        # Если пул недоступен, используем старый метод
        logger.warning("⚠️ Пул контейнеров недоступен, используем старый метод создания контейнеров")
        return _run_with_timeout_docker_legacy(code, time_limit_sec)

def _run_with_timeout_docker_legacy(code: str, time_limit_sec: float) -> Dict[str, object]:
    """Выполнение Python кода в Docker контейнере с ограничениями (старый метод - создает новый контейнер)"""
    from docker import DockerClient
    from docker import errors as docker_errors
    import json
    import time
    import logging
    
    # Настройка логирования
    logger = logging.getLogger(__name__)
    
    start_time = time.time()
    
    try:
        # Подключаемся к Docker
        client = DockerClient.from_env()
        logger.info("🔌 Подключение к Docker...")
        
        # Используем volumes для передачи кода через файл (более надежно)
        import tempfile
        temp_dir = tempfile.mkdtemp()
        code_file = os.path.join(temp_dir, "code.py")
        with open(code_file, 'w', encoding='utf-8', newline='\n') as f:
            f.write(code)
        
        # Логируем для отладки
        logger.info(f"📝 Код для выполнения (длина: {len(code)}, первые 300 символов):\n{code[:300]}")
        logger.info(f"📁 Временный файл создан: {code_file}, размер: {os.path.getsize(code_file)} байт")
        
        # Проверяем содержимое файла перед отправкой
        with open(code_file, 'r', encoding='utf-8') as f:
            file_content_check = f.read()
            if file_content_check != code:
                logger.error(f"❌ Содержимое файла не совпадает с кодом! Файл: {len(file_content_check)} байт, Код: {len(code)} байт")
        
        # Проверяем, нужно ли оставлять контейнеры для отладки
        auto_remove_env = os.environ.get("DOCKER_AUTO_REMOVE", "true").strip().lower()
        auto_remove = auto_remove_env not in ("false", "0", "no")
        
        logger.info(f"📦 Запуск контейнера zedcode-python:latest (timeout: {time_limit_sec}с, auto_remove: {auto_remove})...")
        logger.info(f"   DOCKER_AUTO_REMOVE={auto_remove_env} -> auto_remove={auto_remove}")
        
        container_id = None
        container = None
        try:
            # Используем create()+start(), загружаем код через put_archive и выполняем runner в exec_run
            timeout_seconds = int(time_limit_sec) + 2

            container = client.containers.create(
                image="zedcode-python:latest",
                command=["sh", "-c", "tail -f /dev/null"],
                mem_limit="256m",
                cpu_period=100000,
                cpu_quota=int(50000 * time_limit_sec),
                network_disabled=True,
                read_only=False,
                auto_remove=auto_remove
            )
            container_id = container.id
            logger.info(f"📋 Контейнер создан (ID: {container_id[:12]})")

            container.start()

            # Готовим tar-архив с code.py
            import tarfile, io
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode='w') as tar:
                data = code.encode('utf-8')
                ti = tarfile.TarInfo(name='code.py')
                ti.size = len(data)
                ti.mtime = int(time.time())
                ti.mode = 0o644
                tar.addfile(ti, io.BytesIO(data))
            tar_stream.seek(0)
            container.put_archive('/app', tar_stream.read())

            # Выполняем runner
            exec_res = container.exec_run(
                ["sh", "-c", "cat /app/code.py | python -u /app/runner.py"],
                stdout=True, stderr=True
            )
            exit_code = getattr(exec_res, 'exit_code', 1)
            container_output = exec_res.output
            logger.info("Контейнер успешно выполнен (legacy via exec_run)")

            # Останавливаем и удаляем контейнер в любом случае, чтобы не накапливался
            try:
                container.stop(timeout=1)
            except Exception:
                pass
            try:
                container.remove(force=True)
            except Exception:
                pass
        except Exception as container_error:
            # Если контейнер создался, но произошла ошибка, очищаем его
            if container is not None:
                try:
                    container.stop(timeout=1)
                except Exception:
                    pass
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            raise container_error
        finally:
            # Чистим временный файл/каталог, если создавали
            try:
                if 'code_file' in locals() and os.path.exists(code_file):
                    os.unlink(code_file)
                if 'temp_dir' in locals() and os.path.isdir(temp_dir):
                    os.rmdir(temp_dir)
            except Exception:
                pass
        
        execution_time = int((time.time() - start_time) * 1000)
        logger.info(f"⏱️ Время выполнения: {execution_time}ms")
        
        # Если контейнеры не удаляются автоматически, логируем информацию
        if not auto_remove:
            try:
                # Пытаемся найти последние контейнеры zedcode
                containers = client.containers.list(all=True, filters={"ancestor": "zedcode-python:latest"}, limit=3)
                if containers:
                    logger.info(f"📋 Найдено контейнеров zedcode: {len(containers)}")
                    for c in containers[:3]:
                        logger.info(f"   - ID: {c.id[:12]}, Status: {c.status}, Created: {c.attrs.get('Created', 'N/A')}")
                else:
                    logger.warning("⚠️  Контейнеры не найдены (возможно, были удалены)")
            except Exception as e:
                logger.debug(f"Не удалось получить список контейнеров: {e}")
        
        # Парсим JSON ответ от runner.py
        try:
            output_text = container_output.decode('utf-8') if isinstance(container_output, bytes) else str(container_output)
            # Убираем возможные лишние символы в начале/конце
            output_text = output_text.strip()
            
            # Логируем перед парсингом
            logger.info(f"📋 Текст для парсинга JSON (длина: {len(output_text)}, первые 500 символов): {output_text[:500]}")
            
            if not output_text or len(output_text.strip()) == 0:
                logger.error(f"❌ Пустой вывод контейнера!")
                raise Exception("Пустой вывод контейнера")
            
            result = json.loads(output_text)
            logger.info(f"📊 Распарсенный результат: ok={result.get('ok')}, stdout_len={len(result.get('stdout', ''))}, error={result.get('error')}")
            
            # Извлекаем stdout из результата runner.py
            stdout = result.get("stdout", "") or ""
            # Если stdout пустой, пробуем получить из output
            if not stdout:
                stdout = result.get("output", "")
            
            # Логируем для отладки если stdout пустой
            if not stdout:
                logger.error(f"❌ stdout пустой! Полный результат runner.py: {result}")
                logger.error(f"   Код, который выполнялся (первые 300 символов): {code[:300]}")
                logger.error(f"   ok={result.get('ok')}, error={result.get('error')}, stderr={result.get('stderr')}")
                logger.error(f"   Сырой вывод: {output_text[:500]}")
            
            # Контейнер уже удалён выше; дополнительных действий не требуется
            
            return {
                "ok": result.get("ok", False),
                "output": stdout,  # Это будет выводиться в judge_user_code_with_tests
                "error": result.get("error", "") or result.get("stderr", ""),
                "execution_time": int(result.get("execution_time", 0) * 1000) if result.get("execution_time") else execution_time,
                "memory_mb": round(result.get("memory_used", 0), 2)
            }
        except (json.JSONDecodeError, AttributeError) as e:
            # Если не удалось распарсить JSON, возвращаем как есть
            output_text = container_output.decode('utf-8') if isinstance(container_output, bytes) else str(container_output)
            logger.error(f"❌ Ошибка парсинга JSON: {str(e)}")
            logger.error(f"   Сырой вывод: {output_text}")
            
            # Удаляем контейнер после ошибки
            if auto_remove and container is not None:
                try:
                    container.remove()
                except Exception:
                    pass
            
            return {
                "ok": False,
                "output": output_text,
                "error": f"Ошибка парсинга ответа: {str(e)}. Ответ: {output_text[:200]}",
                "execution_time": execution_time,
                "memory_mb": 0.0
            }
            
    except docker_errors.ImageNotFound:
        # Если образ не найден, возвращаем ошибку
        execution_time = int((time.time() - start_time) * 1000)
        logger.error("❌ Docker образ zedcode-python:latest не найден")
        return {
            "ok": False,
            "error": "Docker образ zedcode-python:latest не найден. Выполните: docker build -t zedcode-python:latest docker/executor/",
            "execution_time": execution_time,
            "memory_mb": 0.0
        }
    except docker_errors.ContainerError as e:
        execution_time = int((time.time() - start_time) * 1000)
        logger.error(f"❌ Ошибка контейнера: {str(e)}")
        return {
            "ok": False,
            "error": f"Ошибка контейнера: {str(e)}",
            "execution_time": execution_time,
            "memory_mb": 0.0
        }
    except Exception as e:
        execution_time = int((time.time() - start_time) * 1000) if 'start_time' in locals() else 0
        logger.error(f"❌ Ошибка Docker: {str(e)}", exc_info=True)
        return {
            "ok": False,
            "error": f"Ошибка Docker: {str(e)}",
            "execution_time": execution_time,
            "memory_mb": 0.0
        }


def _run_with_timeout(code: str, time_limit_sec: float) -> Dict[str, object]:
    """Выполнение Python кода с таймаутом и измерением памяти
    
    Автоматически использует Docker если доступен и USE_DOCKER=true,
    иначе использует обычный subprocess режим.
    """
    # Проверяем, нужно ли использовать Docker
    use_docker = os.environ.get("USE_DOCKER", "false").lower() in ("true", "1", "yes")
    
    if use_docker:
        try:
            from docker import DockerClient
            # Проверяем доступность Docker
            client = DockerClient.from_env()
            client.ping()
            # Используем Docker
            return _run_with_timeout_docker(code, time_limit_sec)
        except Exception as e:
            # Если Docker недоступен, используем обычный режим
            # В лог можно добавить информацию, но не прерываем выполнение
            pass
    
    # Обычный режим выполнения (subprocess)
    import subprocess
    import tempfile
    import time
    
    try:
        # Пытаемся импортировать psutil для измерения памяти
        try:
            import psutil
            use_psutil = True
        except ImportError:
            use_psutil = False
        
        # Создаем временный файл для Python кода с кодировкой UTF-8
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(code)
            temp_file = f.name
        
        start_time = time.time()
        max_memory_mb = 0.0
        
        # Выполняем код через Python с правильной кодировкой
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8'] = '1'
        
        # Выполняем код через Python
        process = subprocess.Popen(
            ['python', '-u', temp_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env
        )
        
        # Мониторим использование памяти во время выполнения
        if use_psutil:
            try:
                proc = psutil.Process(process.pid)
                while process.poll() is None:
                    try:
                        mem_info = proc.memory_info()
                        memory_mb = mem_info.rss / 1024 / 1024  # RSS в MB
                        if memory_mb > max_memory_mb:
                            max_memory_mb = memory_mb
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        break
                    time.sleep(0.01)  # Проверяем каждые 10ms
            except Exception:
                pass
        
        try:
            stdout, stderr = process.communicate(timeout=time_limit_sec)
            return_code = process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return_code = -1
        
        execution_time = int((time.time() - start_time) * 1000)
        
        # Удаляем временный файл
        try:
            os.unlink(temp_file)
        except Exception:
            pass
        
        return {
            "ok": return_code == 0,
            "output": stdout,
            "error": stderr if return_code != 0 else "",
            "execution_time": execution_time,
            "memory_mb": round(max_memory_mb, 2) if max_memory_mb > 0 else 0.0
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Превышено время выполнения", "execution_time": int(time_limit_sec * 1000), "memory_mb": 0.0}
    except Exception as e:
        return {"ok": False, "error": f"Ошибка выполнения: {str(e)}", "execution_time": 0, "memory_mb": 0.0}


app = create_app()


if __name__ == "__main__":
    import sys
    
    # Устанавливаем UTF-8 кодировку для консоли (Windows)
    try:
        import io
        if sys.platform == 'win32':
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass  # Игнорируем ошибки настройки кодировки
    
    # Проверяем настройки Docker при запуске
    use_docker = os.environ.get("USE_DOCKER", "false").lower() in ("true", "1", "yes")
    
    print("=" * 60)
    print("🚀 Запуск zedcode")
    print("=" * 60)
    print(f"Python: {sys.executable}")
    print(f"USE_DOCKER: {use_docker}")
    
    if use_docker:
        try:
            # Используем прямой импорт для избежания проблем с атрибутами
            from docker import DockerClient
            from docker import errors as docker_errors
            
            try:
                client = DockerClient.from_env()
                client.ping()
                print("✅ Docker режим: ВКЛЮЧЕН")
                print("   Код будет выполняться в изолированных Docker контейнерах")
                
                # Инициализируем пул контейнеров
                try:
                    from docker_executor_pool import get_executor_pool
                    pool = get_executor_pool()
                    pool_size = pool.pool_size
                    print(f"✅ Пул контейнеров инициализирован: {pool_size} контейнеров")
                    print("   Контейнеры будут переиспользоваться для ускорения выполнения")
                except Exception as e:
                    print(f"⚠️  Не удалось инициализировать пул контейнеров: {e}")
                    print("   Будет использован старый метод (создание нового контейнера каждый раз)")
                
                # Проверяем наличие образа
                try:
                    client.images.get("zedcode-python:latest")
                    print("✅ Docker образ найден: zedcode-python:latest")
                except docker_errors.ImageNotFound:
                    print("⚠️  Docker образ НЕ найден: zedcode-python:latest")
                    print("   Выполните: cd docker\\executor && docker build -t zedcode-python:latest .")
            except docker_errors.DockerException as e:
                print(f"⚠️  Docker режим: ВКЛЮЧЕН, но Docker недоступен: {str(e)}")
                print("   Приложение будет использовать обычный режим (subprocess)")
                print("   Убедитесь, что Docker Desktop запущен")
            except Exception as e:
                print(f"⚠️  Docker режим: ВКЛЮЧЕН, но произошла ошибка: {str(e)}")
                print("   Приложение будет использовать обычный режим (subprocess)")
                import traceback
                traceback.print_exc()
            
        except ImportError as import_err:
            print("⚠️  Docker режим: ВКЛЮЧЕН, но библиотека docker не установлена")
            print(f"   Ошибка импорта: {str(import_err)}")
            print(f"   Python: {sys.executable}")
            print("   Установите: pip install docker")
            print("   Приложение будет использовать обычный режим (subprocess)")
        except Exception as e:
            print(f"⚠️  Docker режим: ВКЛЮЧЕН, но произошла ошибка при импорте: {str(e)}")
            print(f"   Python: {sys.executable}")
            print("   Приложение будет использовать обычный режим (subprocess)")
            import traceback
            traceback.print_exc()
    else:
        print("ℹ️  Docker режим: ВЫКЛЮЧЕН")
        print("   Код будет выполняться через обычный subprocess (без изоляции)")
        print("   Для включения Docker установите USE_DOCKER=true в .env файле")
    
    auto_remove = os.environ.get("DOCKER_AUTO_REMOVE", "true").lower() not in ("false", "0", "no")
    if use_docker:
        print(f"   Автоудаление контейнеров: {'ВКЛЮЧЕНО' if auto_remove else 'ВЫКЛЮЧЕНО'}")
        if not auto_remove:
            print("   Контейнеры будут оставаться для отладки (docker ps -a)")
    
    print("=" * 60)
    print("🌐 Приложение доступно по адресу: http://localhost:8080")
    print("=" * 60)
    print()
    
    app.run(host="0.0.0.0", port=8080, debug=True)
