import os
import re
import secrets
import logging
import sqlite3
import json
from datetime import timedelta
from functools import wraps

from flask import (
    Flask, render_template_string, request, redirect, url_for,
    session, flash, Response, jsonify, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("smartpanier")

# --- CONFIGURATION ---
app = Flask(__name__)

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    log.warning(
        "SECRET_KEY absente de l'environnement : une clé temporaire a été générée. "
        "Toutes les sessions seront invalidées à chaque redémarrage. "
        "Définissez la variable d'environnement SECRET_KEY sur Render."
    )
app.secret_key = SECRET_KEY

IS_PROD = os.environ.get("FLASK_ENV", "production") == "production"

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PROD,
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

DB_NAME = os.environ.get("DB_NAME", "courses_multiusers.db")
SITE_URL = os.environ.get("SITE_URL", "https://smart-panier-1.onrender.com")

csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")


@app.after_request
def add_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


# --- BASE DE DONNÉES ---
def get_db():
    """Une connexion par requête, réutilisée via flask.g, fermée automatiquement à la fin."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_NAME, timeout=10)
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS courses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nom TEXT, prix REAL, qte INTEGER, fait BOOLEAN, cat TEXT, date_ajout DATETIME DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS historique (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, total REAL, nb_articles INTEGER, date_achat DATETIME DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS templates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, items_json TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS shares (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, shared_with_id INTEGER, UNIQUE(owner_id, shared_with_id))")
        c.execute("CREATE TABLE IF NOT EXISTS price_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nom TEXT, dernier_prix REAL, cat TEXT, UNIQUE(user_id, nom))")
        conn.commit()


def migrate_db():
    """Ajoute en douceur les nouvelles colonnes sans casser une base existante en prod."""
    with sqlite3.connect(DB_NAME) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(courses)").fetchall()]
        if "added_by" not in cols:
            conn.execute("ALTER TABLE courses ADD COLUMN added_by TEXT")
        if "checked_by" not in cols:
            conn.execute("ALTER TABLE courses ADD COLUMN checked_by TEXT")
        conn.commit()


init_db()
migrate_db()

# --- CONSTANTES ---
CAT_CONFIG = {
    "🥦 Fruits & Légumes": "#10b981",
    "🥩 Protéines": "#ef4444",
    "🥖 Boulangerie": "#f59e0b",
    "🥛 Laitiers": "#3b82f6",
    "🥤 Boissons": "#8b5cf6",
    "✨ Autre": "#64748b"
}

DEVISES = ["FCFA", "EUR (€)", "USD ($)", "CAD ($)", "GBP (£)"]

PRESET_RECIPES = {
    "🍝 Sauce Spaghetti Bolognese": [
        {"nom": "Viande hachée (500g)", "prix": 2500, "qte": 1, "cat": "🥩 Protéines"},
        {"nom": "Spaghetti (1 paquet)", "prix": 800, "qte": 1, "cat": "✨ Autre"},
        {"nom": "Tomates en boîte", "prix": 600, "qte": 2, "cat": "🥦 Fruits & Légumes"},
        {"nom": "Oignon & Ail", "prix": 300, "qte": 1, "cat": "🥦 Fruits & Légumes"},
        {"nom": "Fromage râpé", "prix": 1200, "qte": 1, "cat": "🥛 Laitiers"}
    ],
    "🥗 Salade Fraîcheur": [
        {"nom": "Laitue", "prix": 500, "qte": 1, "cat": "🥦 Fruits & Légumes"},
        {"nom": "Tomates fraîches", "prix": 500, "qte": 1, "cat": "🥦 Fruits & Légumes"},
        {"nom": "Concombre", "prix": 300, "qte": 1, "cat": "🥦 Fruits & Légumes"},
        {"nom": "Blanc de poulet", "prix": 2000, "qte": 1, "cat": "🥩 Protéines"},
        {"nom": "Huile d'olive", "prix": 3500, "qte": 1, "cat": "✨ Autre"}
    ],
    "☕ Petit-Déjeuner Complet": [
        {"nom": "Pains au chocolat / Croissants", "prix": 1500, "qte": 1, "cat": "🥖 Boulangerie"},
        {"nom": "Lait", "prix": 1000, "qte": 1, "cat": "🥛 Laitiers"},
        {"nom": "Café", "prix": 2000, "qte": 1, "cat": "🥤 Boissons"},
        {"nom": "Jus d'orange", "prix": 1200, "qte": 1, "cat": "🥤 Boissons"},
        {"nom": "Œufs (boîte de 10)", "prix": 1200, "qte": 1, "cat": "🥩 Protéines"}
    ]
}

MANIFEST_JSON = """{
  "short_name": "SmartPanier",
  "name": "SmartPanier - Gestion de Courses & Budget",
  "icons": [
    {
      "src": "https://cdn-icons-png.flaticon.com/512/3081/3081986.png",
      "type": "image/png",
      "sizes": "512x512"
    }
  ],
  "start_url": "/",
  "background_color": "#0f172a",
  "theme_color": "#1e293b",
  "display": "standalone"
}"""

SW_JS = """const CACHE_NAME = 'smartpanier-v6';
const urlsToCache = [
  '/',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css',
  'https://cdn.jsdelivr.net/npm/chart.js',
  'https://unpkg.com/html5-qrcode'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache)));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
  );
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});"""

FAVICON_SVG = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%9B%92%3C/text%3E%3C/svg%3E"

# --- VALIDATION ---
def validate_username(u):
    u = (u or "").strip()
    if re.match(r"^[A-Za-z0-9_]{3,20}$", u):
        return u
    return None


def validate_password(p):
    return bool(p) and 6 <= len(p) <= 128


def validate_item_input(nom, qte_raw, prix_raw, cat):
    errors = []
    nom = (nom or "").strip()
    if not nom or len(nom) > 100:
        errors.append("Le nom de l'article doit contenir entre 1 et 100 caractères.")
        nom = nom[:100] if nom else "Article"

    try:
        qte = int(qte_raw)
        if not (1 <= qte <= 9999):
            raise ValueError
    except (ValueError, TypeError):
        errors.append("Quantité invalide (entre 1 et 9999).")
        qte = 1

    try:
        prix = float(prix_raw) if prix_raw not in (None, "") else 0.0
        if not (0 <= prix <= 100_000_000):
            raise ValueError
    except (ValueError, TypeError):
        errors.append("Prix invalide.")
        prix = 0.0

    if cat not in CAT_CONFIG:
        cat = "✨ Autre"

    return nom, qte, prix, cat, errors


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "uid" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Session expirée."}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# --- TEMPLATES HTML ---

BASE_HEAD = """
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" href="{{ favicon }}">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
"""

AUTH_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <title>{{ title }} - SmartPanier</title>
    <style>
        body { background: #0f172a; color: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; font-family: system-ui, -apple-system, sans-serif; }
        .auth-card { background: #1e293b; padding: 30px; border-radius: 20px; width: 100%; max-width: 420px; border: 1px solid #334155; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        .form-control { background: #0f172a; border: 1px solid #334155; color: white; padding: 12px; font-size: 16px !important; }
        .form-control:focus { background: #0f172a; color: white; border-color: #3b82f6; box-shadow: none; }
        .btn-custom { padding: 12px; font-size: 16px; border-radius: 10px; font-weight: bold; }
        .spinner-border-sm { display: none; }
        .btn-loading .spinner-border-sm { display: inline-block; }
        .btn-loading .btn-label { display: none; }
    </style>
</head>
<body>
    <div class="auth-card text-center">
        <h3 class="fw-bold mb-4">🛒 SmartPanier</h3>
        {% with m = get_flashed_messages() %}
            {% if m %}<div class="alert alert-warning py-2 small mb-3">{{ m[0] }}</div>{% endif %}
        {% endwith %}
        <form method="POST" onsubmit="this.querySelector('button[type=submit]').classList.add('btn-loading')">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <input type="text" name="user" class="form-control mb-3" placeholder="Nom d'utilisateur" minlength="3" maxlength="20" pattern="[A-Za-z0-9_]+" title="Lettres, chiffres et underscore uniquement (3-20 caractères)" required autocomplete="username">
            <input type="password" name="pass" class="form-control mb-4" placeholder="Mot de passe (6 caractères min.)" minlength="6" required autocomplete="current-password">
            <button type="submit" class="btn btn-warning w-100 btn-custom mb-3">
                <span class="spinner-border spinner-border-sm me-2"></span><span class="btn-label">{{ btn }}</span>
            </button>
        </form>
        <div class="small">
            {% if title=="Login" %}
                Pas encore de compte ? <a href="/register" class="text-info fw-bold text-decoration-none">Créer un compte</a>
            {% else %}
                Déjà inscrit ? <a href="/login" class="text-info fw-bold text-decoration-none">Se connecter</a>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""

LANDING_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <title>Bienvenue sur SmartPanier</title>
    <style>
        body { background: #0f172a; color: white; text-align: center; font-family: system-ui, -apple-system, sans-serif; }
        .hero { padding: 100px 20px 60px; background: linear-gradient(180deg, #1e293b 0%, #0f172a 100%); }
        .btn-start { background: #f59e0b; color: #0f172a; font-weight: 800; padding: 16px 36px; border-radius: 50px; text-decoration: none; display: inline-block; transition: transform 0.2s; }
        .btn-start:hover { transform: scale(1.05); color: #0f172a; }
    </style>
</head>
<body>
    <div class="hero">
        <h1 class="display-3 fw-bold mb-3">🛒 SmartPanier</h1>
        <p class="lead text-secondary mb-5 max-w-lg mx-auto">Gérez votre budget courses intelligemment, évitez les mauvaises surprises en caisse et partagez vos listes en un clic.</p>
        <a href="/register" class="btn-start shadow-lg">COMMENCER GRATUITEMENT</a>
        <p class="mt-4 small text-secondary">Déjà membre ? <a href="/login" class="text-info fw-bold text-decoration-none">Se connecter</a></p>
    </div>
</body>
</html>
"""

ERROR_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <title>Erreur {{ code }} - SmartPanier</title>
    <style>
        body { background: #0f172a; color: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; font-family: system-ui, -apple-system, sans-serif; text-align: center; padding: 20px; }
        .code { font-size: 5rem; font-weight: 900; color: #f59e0b; }
    </style>
</head>
<body>
    <div>
        <div class="code">{{ code }}</div>
        <p class="lead mb-4">{{ msg }}</p>
        <a href="/" class="btn btn-warning fw-bold px-4">Retour à l'accueil</a>
    </div>
</body>
</html>
"""

PROFILE_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <title>Mon Profil - SmartPanier</title>
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; padding: 30px 15px; }
        .profile-card { background: #1e293b; border: 1px solid #334155; border-radius: 20px; padding: 25px; max-width: 500px; margin: 0 auto; }
        .form-control { background: #0f172a; border: 1px solid #334155; color: white; font-size: 16px !important; }
        .form-control:focus { background: #0f172a; color: white; border-color: #3b82f6; box-shadow: none; }
    </style>
</head>
<body>
    <div class="profile-card">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h4 class="fw-bold mb-0"><i class="fa fa-user-gear text-warning me-2"></i>Mon Profil</h4>
            <a href="/" class="btn btn-sm btn-outline-secondary"><i class="fa fa-arrow-left"></i> Retour</a>
        </div>

        {% with m = get_flashed_messages() %}
            {% if m %}<div class="alert alert-info py-2 small mb-3">{{ m[0] }}</div>{% endif %}
        {% endwith %}

        <div class="mb-4">
            <label class="small text-secondary fw-bold">Nom d'utilisateur</label>
            <input type="text" class="form-control" value="{{ username }}" disabled>
        </div>

        <form action="/share_list" method="POST" class="mb-4 border-top border-secondary pt-3">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <h6 class="fw-bold mb-2 text-info"><i class="fa fa-users me-1"></i> Partager ma liste (Mode Coloc)</h6>
            <div class="input-group input-group-sm mb-2">
                <input type="text" name="share_username" class="form-control" placeholder="Pseudo de l'utilisateur..." maxlength="20" required>
                <button type="submit" class="btn btn-info fw-bold">Partager</button>
            </div>
            {% if shared_users %}
                <div class="small text-secondary mt-2">Partagé avec :
                {% for u in shared_users %}
                    <span class="badge bg-secondary">{{ u[0] }}</span>
                {% endfor %}
                </div>
            {% endif %}
        </form>

        <form action="/change_password" method="POST" class="mb-4 border-top border-secondary pt-3">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <h6 class="fw-bold mb-3 text-warning">🔑 Modifier le mot de passe</h6>
            <input type="password" name="old_pass" class="form-control mb-2" placeholder="Ancien mot de passe" required>
            <input type="password" name="new_pass" class="form-control mb-3" placeholder="Nouveau mot de passe (6 car. min.)" minlength="6" required>
            <button type="submit" class="btn btn-warning w-100 fw-bold">Mettre à jour</button>
        </form>

        <div class="border-top border-secondary pt-3">
            <h6 class="fw-bold mb-3 text-danger">⚠️ Zone de Danger</h6>
            <div class="d-flex flex-column gap-2">
                <form action="/clear_history" method="POST" onsubmit="return confirm('Effacer tout votre historique d\\'achats ?')">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <button type="submit" class="btn btn-outline-warning btn-sm w-100">Vider l'historique d'achats</button>
                </form>
                <form action="/reset_all" method="POST" onsubmit="return confirm('Attention ! Cela va tout supprimer (listes, historique, modèles). Continuer ?')">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <button type="submit" class="btn btn-outline-danger btn-sm w-100">Réinitialiser toutes mes données</button>
                </form>
            </div>
        </div>
    </div>
</body>
</html>
"""

MAIN_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <meta name="csrf-token" content="{{ csrf_token() }}">
    <link rel="manifest" href="/manifest.json">
    <meta name="theme-color" content="#1e293b">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://unpkg.com/html5-qrcode"></script>
    <title>SmartPanier - Dashboard</title>
    <style>
        :root { --bg: #0f172a; --card: #1e293b; --border: #334155; --text: #f8fafc; --input-bg: #0f172a; }
        [data-theme="light"] { --bg: #f1f5f9; --card: #ffffff; --border: #cbd5e1; --text: #0f172a; --input-bg: #f8fafc; }
        body {
            background: var(--bg); color: var(--text); font-family: system-ui, -apple-system, sans-serif; padding-bottom: 40px;
            transition: background 0.3s, color 0.3s; -webkit-tap-highlight-color: transparent; user-select: none; touch-action: manipulation;
        }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 18px; margin-bottom: 16px; padding: 18px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        .form-control, .form-select { background: var(--input-bg) !important; border: 1px solid var(--border) !important; color: var(--text) !important; padding: 10px 14px; font-size: 16px !important; }
        .form-control:focus, .form-select:focus { box-shadow: none; border-color: #3b82f6 !important; }
        .total-display { color: #f59e0b; font-weight: 900; font-size: 2.8rem; line-height: 1.1; }
        .budget-alert-banner { background: #ef4444; color: white; font-weight: bold; text-align: center; padding: 10px; border-radius: 12px; margin-bottom: 15px; animation: pulse 1.5s infinite; }
        .list-group-item { background: var(--card); color: var(--text); border: 1px solid var(--border); margin-bottom: 8px; border-radius: 12px !important; padding: 12px 16px; }
        .done { opacity: 0.4; text-decoration: line-through; }
        .btn, .list-group-item, .cat-filter-btn { transition: transform 0.15s ease, opacity 0.15s ease; }
        .btn:active, .cat-filter-btn:active { transform: scale(0.96); }
        .btn-action { padding: 10px; font-weight: bold; border-radius: 10px; }
        .cat-filter-btn { font-size: 0.82rem; padding: 6px 12px; border-radius: 20px; cursor: pointer; border: 1px solid var(--border); background: var(--card); color: var(--text); }
        .cat-filter-btn.active { background: #3b82f6; color: white; border-color: #3b82f6; }
        .modal-content { background: var(--card); color: var(--text); border: 1px solid var(--border); }
        .item-row.removing { opacity: 0; transform: translateX(30px); }
        .item-row.entering { animation: fadeInUp 0.25s ease; }
        .added-by-badge { font-size: 0.68rem; opacity: 0.7; }
        .spinner-border-sm { display: none; }
        .btn-loading .spinner-border-sm { display: inline-block; }
        .btn-loading .btn-label { display: none; }
        .btn-loading { pointer-events: none; opacity: 0.8; }
        #toastContainer { position: fixed; bottom: 20px; right: 20px; z-index: 2000; display: flex; flex-direction: column; gap: 8px; max-width: 90vw; }
        .app-toast { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 12px; padding: 12px 16px; box-shadow: 0 6px 18px rgba(0,0,0,0.3); display: flex; align-items: center; gap: 10px; animation: fadeInUp 0.2s ease; min-width: 220px; }
        .app-toast.success { border-left: 4px solid #10b981; }
        .app-toast.error { border-left: 4px solid #ef4444; }
        .app-toast.info { border-left: 4px solid #3b82f6; }
        .app-toast button.undo { background: none; border: none; color: #3b82f6; font-weight: bold; }
        ::-webkit-scrollbar { display: none; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.7; } 100% { opacity: 1; } }
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
        @media (max-width: 576px) {
            .modal-dialog { margin: 0; position: fixed; bottom: 0; width: 100%; }
            .modal-content { border-radius: 24px 24px 0 0 !important; border-bottom: none; }
        }
        @media print { .no-print { display: none !important; } body { background: white; color: black; } .card { border: none; } }
    </style>
</head>
<body>
    <div class="container" style="max-width: 980px;">
        <div class="d-flex justify-content-between align-items-center my-3 no-print flex-wrap gap-2">
            <div class="d-flex align-items-center gap-2">
                <h5 class="mb-0 fw-bold">👤 {{ username }}</h5>
                <a href="/profile" class="btn btn-sm btn-outline-warning"><i class="fa fa-user-cog"></i> Profil</a>
            </div>

            <div class="d-flex gap-2 align-items-center flex-wrap">
                <button id="pwaInstallBtn" class="btn btn-sm btn-warning fw-bold d-none"><i class="fa fa-download me-1"></i> Installer</button>
                <button onclick="requestNotificationPermission()" class="btn btn-sm btn-outline-info" title="Rappels"><i class="fa fa-bell"></i></button>

                <form action="/set_devise" method="POST" class="m-0">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <select name="devise" onchange="this.form.submit()" class="form-select form-select-sm" style="width: auto;">
                        {% for d in devises %}
                            <option value="{{d}}" {% if d == devise %}selected{% endif %}>{{d}}</option>
                        {% endfor %}
                    </select>
                </form>

                <button onclick="toggleTheme()" class="btn btn-sm btn-outline-secondary" id="themeBtn"><i class="fa fa-moon"></i></button>
                <a href="/export_csv" class="btn btn-sm btn-outline-success"><i class="fa fa-file-excel"></i> Excel</a>
                <button onclick="invite()" class="btn btn-sm btn-outline-info"><i class="fa fa-gift"></i> Inviter</button>
                <a href="/logout" class="btn btn-sm btn-outline-danger"><i class="fa fa-sign-out-alt"></i></a>
            </div>
        </div>

        {% if total > budget %}
        <div class="budget-alert-banner shadow-lg">
            🚨 ATTENTION : Budget dépassé de {{ fmt(total - budget) }} {{ devise }} ! 🚨
        </div>
        {% endif %}

        <div class="row g-3">
            <div class="col-lg-5 no-print">
                <div class="card">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h6 class="fw-bold mb-0">➕ Ajouter un article</h6>
                        <button class="btn btn-sm btn-outline-warning" data-bs-toggle="modal" data-bs-target="#scannerModal">
                            <i class="fa fa-barcode me-1"></i> Scan
                        </button>
                    </div>

                    <form id="addForm">
                        <input type="text" name="nom" id="itemNomInput" oninput="checkPriceMemory(this.value)" class="form-control mb-2" placeholder="Nom du produit (ex: Pain)" maxlength="100" required autocomplete="off">
                        <div class="row g-2 mb-2">
                            <div class="col-5"><input type="number" name="qte" class="form-control" value="1" min="1" max="9999" placeholder="Qté"></div>
                            <div class="col-7"><input type="number" step="any" min="0" name="prix" id="itemPrixInput" class="form-control" placeholder="Prix ({{ devise }})"></div>
                        </div>
                        <select name="cat" id="itemCatInput" class="form-select mb-3">
                            {% for c in categories %}
                                <option value="{{c}}">{{c}}</option>
                            {% endfor %}
                        </select>
                        <button type="submit" class="btn btn-warning w-100 btn-action">
                            <span class="spinner-border spinner-border-sm me-2"></span><span class="btn-label">AJOUTER AU PANIER</span>
                        </button>
                    </form>
                </div>

                <div class="card">
                    <h6 class="fw-bold mb-2">🍲 Recettes & Modèles Rapides</h6>
                    <form action="/load_recipe" method="POST" class="mb-2">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <div class="input-group input-group-sm">
                            <select name="recipe_name" class="form-select">
                                <option value="">-- Choisir une recette --</option>
                                {% for r_name in preset_recipes.keys() %}
                                    <option value="{{ r_name }}">{{ r_name }}</option>
                                {% endfor %}
                            </select>
                            <button type="submit" class="btn btn-success fw-bold">+ Charger</button>
                        </div>
                    </form>

                    {% if templates %}
                    <hr class="border-secondary my-2">
                    <h6 class="fw-bold mb-2 small text-uppercase text-secondary">Mes Modèles Sauvegardés</h6>
                    <div class="d-flex flex-column gap-1">
                        {% for t in templates %}
                        <div class="d-flex justify-content-between align-items-center bg-dark p-2 rounded border border-secondary">
                            <span class="small fw-bold text-truncate" style="max-width: 180px;">{{ t[2] }}</span>
                            <div class="d-flex gap-1">
                                <form action="/load_template/{{ t[0] }}" method="POST" class="m-0">
                                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                                    <button type="submit" class="btn btn-sm btn-outline-info py-0 px-2">Charger</button>
                                </form>
                                <form action="/del_template/{{ t[0] }}" method="POST" class="m-0" onsubmit="return confirm('Supprimer ce modèle ?')">
                                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                                    <button type="submit" class="btn btn-sm btn-outline-danger py-0 px-1"><i class="fa fa-times"></i></button>
                                </form>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                    {% endif %}

                    <form action="/save_template" method="POST" class="mt-3">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <div class="input-group input-group-sm">
                            <input type="text" name="title" class="form-control" placeholder="Nom de la liste..." maxlength="80" required>
                            <button type="submit" class="btn btn-outline-warning">Sauvegarder</button>
                        </div>
                    </form>
                </div>

                <div class="card">
                    <h6 class="fw-bold mb-2">📊 Budget Max & Rappels</h6>
                    <form action="/set_budget" method="POST" class="mb-3">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <div class="input-group input-group-sm">
                            <input type="number" step="any" min="0" name="val" class="form-control" value="{{ '%.0f'|format(budget) }}" required>
                            <span class="input-group-text bg-secondary text-white border-secondary">{{ devise }}</span>
                            <button type="submit" class="btn btn-primary fw-bold">Modifier</button>
                        </div>
                    </form>

                    <button onclick="scheduleReminder(30, {{ liste|length }})" class="btn btn-sm btn-outline-warning w-100 mb-3">⏰ Rappel dans 30 min</button>

                    <hr class="border-secondary my-2">
                    <div style="max-width: 250px; margin: 0 auto;">
                        <canvas id="categoryChart"></canvas>
                    </div>
                </div>
            </div>

            <div class="col-lg-7">
                <div class="card text-center">
                    <span class="small text-uppercase text-secondary fw-bold">Total Actuel</span>
                    <div class="total-display my-1" id="totalDisplay" data-total="{{ total }}">
                        {{ fmt(total) }} <span style="font-size: 1.5rem;">{{ devise }}</span>
                    </div>
                    <div class="d-flex gap-2 mt-2 no-print">
                        <button onclick="copyWA()" class="btn btn-success flex-grow-1 btn-action"><i class="fab fa-whatsapp me-1"></i> Partager</button>
                        <button onclick="window.print()" class="btn btn-outline-info btn-action"><i class="fa fa-print"></i></button>
                        <form action="/cloturer" method="POST" class="m-0" onsubmit="return confirm('Clôturer la liste actuelle ?')">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                            <button type="submit" class="btn btn-outline-danger btn-action">🏁 Finir</button>
                        </form>
                    </div>
                </div>

                <div class="card no-print mb-3 py-2">
                    <input type="text" id="searchInput" onkeyup="filterItems()" class="form-control mb-2" placeholder="🔍 Rechercher un article...">
                    <div class="d-flex flex-wrap gap-1" id="categoryFilters">
                        <span class="cat-filter-btn active" onclick="setCategoryFilter('ALL', this)">Tous</span>
                        {% for c in categories %}
                            <span class="cat-filter-btn" onclick="setCategoryFilter('{{c}}', this)">{{c}}</span>
                        {% endfor %}
                    </div>
                </div>

                <div class="list-group mb-4" id="itemsList">
                    {% for item in liste %}
                    <div class="list-group-item d-flex justify-content-between align-items-center item-row {{ 'done' if item[5] }}"
                         data-cat="{{ item[6] }}" data-id="{{ item[0] }}" data-nom="{{ item[2] }}"
                         data-qte="{{ item[4] }}" data-prix="{{ item[3] }}" data-fait="{{ 1 if item[5] else 0 }}">
                        <div class="me-2">
                            <span class="fw-bold item-n d-block">{{ item[2] }} <small class="text-secondary">(x{{ item[4] }})</small></span>
                            <span class="badge rounded-pill mt-1" style="background: {{ config[item[6]] }}; font-weight: 500;">{{ item[6] }}</span>
                            {% if item[8] and item[8] != username %}
                            <span class="added-by-badge d-block mt-1"><i class="fa fa-user-plus me-1"></i>Ajouté par {{ item[8] }}</span>
                            {% endif %}
                        </div>
                        <div class="text-end">
                            <span class="fw-bold d-block text-warning item-total" style="font-size: 1.1rem;">{{ fmt(item[3] * item[4]) }} {{ devise }}</span>
                            <div class="no-print mt-1">
                                <a href="#" onclick="return toggleCheck({{ item[0] }})" class="text-success me-2 text-decoration-none"><i class="fa fa-check-circle fa-lg"></i></a>
                                <a href="#" onclick="return openEditModal(this)" class="text-warning me-2 text-decoration-none"><i class="fa fa-pencil fa-lg"></i></a>
                                <a href="#" onclick="return deleteItem({{ item[0] }})" class="text-danger text-decoration-none"><i class="fa fa-trash fa-lg"></i></a>
                            </div>
                        </div>
                    </div>
                    {% else %}
                    <div class="text-center text-secondary py-4" id="emptyState">Votre panier est vide pour l'instant ! 🛒</div>
                    {% endfor %}
                </div>

                {% if histo %}
                <div class="card no-print">
                    <h6 class="fw-bold mb-3"><i class="fa fa-history text-info me-2"></i> Historique & Analyse Mensuelle</h6>
                    <div style="max-height: 180px; margin-bottom: 15px;">
                        <canvas id="monthlyChart"></canvas>
                    </div>
                    <div class="list-group list-group-flush">
                        {% for h in histo %}
                        <div class="d-flex justify-content-between align-items-center py-2 border-bottom border-secondary">
                            <div>
                                <small class="text-secondary d-block">{{ h[3] }}</small>
                                <span class="small">{{ h[2] }} article(s)</span>
                            </div>
                            <span class="fw-bold text-info">{{ fmt(h[1]) }} {{ devise }}</span>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                {% endif %}
            </div>
        </div>
    </div>

    <!-- MODALE SCANNER CODE-BARRES -->
    <div class="modal fade" id="scannerModal" tabindex="-1">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title fw-bold"><i class="fa fa-barcode me-2"></i>Scanner un produit</h5>
                    <button type="button" class="btn-close btn-close-white" onclick="stopScanner()" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body text-center">
                    <div id="reader" style="width: 100%;"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- MODALE DE MODIFICATION UNIQUE (partagée par tous les articles) -->
    <div class="modal fade" id="editModal" tabindex="-1">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title fw-bold">✏️ Modifier l'article</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <form id="editForm">
                    <div class="modal-body text-start">
                        <label class="small text-secondary fw-bold">Nom</label>
                        <input type="text" name="nom" id="editNom" class="form-control mb-2" maxlength="100" required>

                        <div class="row g-2 mb-2">
                            <div class="col-6">
                                <label class="small text-secondary fw-bold">Quantité</label>
                                <input type="number" name="qte" id="editQte" class="form-control" min="1" max="9999">
                            </div>
                            <div class="col-6">
                                <label class="small text-secondary fw-bold">Prix Unitaire</label>
                                <input type="number" step="any" min="0" name="prix" id="editPrix" class="form-control">
                            </div>
                        </div>

                        <label class="small text-secondary fw-bold">Catégorie</label>
                        <select name="cat" id="editCat" class="form-select">
                            {% for c in categories %}
                                <option value="{{c}}">{{c}}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Annuler</button>
                        <button type="submit" class="btn btn-warning btn-sm fw-bold">
                            <span class="spinner-border spinner-border-sm me-2"></span><span class="btn-label">Mettre à jour</span>
                        </button>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <div id="toastContainer"></div>

    <script>
        const CSRF_TOKEN = document.querySelector('meta[name=csrf-token]').content;
        const DEVISE = {{ devise|tojson }};
        let currentCatFilter = 'ALL';
        let html5QrcodeScanner;

        // ---------- TOASTS ----------
        function showToast(message, type, undoFn) {
            const container = document.getElementById('toastContainer');
            const el = document.createElement('div');
            el.className = 'app-toast ' + (type || 'info');
            const icon = type === 'success' ? 'fa-circle-check' : (type === 'error' ? 'fa-circle-exclamation' : 'fa-circle-info');
            el.innerHTML = '<i class="fa ' + icon + '"></i><span class="flex-grow-1 small">' + message + '</span>';
            if (undoFn) {
                const btn = document.createElement('button');
                btn.className = 'undo';
                btn.textContent = 'Annuler';
                btn.onclick = () => { undoFn(); el.remove(); };
                el.appendChild(btn);
            }
            container.appendChild(el);
            setTimeout(() => el.remove(), undoFn ? 5000 : 3000);
        }

        // ---------- API HELPER ----------
        async function apiFetch(url, formData) {
            const resp = await fetch(url, {
                method: 'POST',
                headers: { 'X-CSRFToken': CSRF_TOKEN },
                body: formData
            });
            let data;
            try { data = await resp.json(); } catch (e) { data = { ok: false, error: 'Réponse invalide du serveur.' }; }
            if (!resp.ok || !data.ok) {
                showToast(data.error || 'Une erreur est survenue.', 'error');
                if (resp.status === 401) { window.location.href = '/login'; }
                throw new Error(data.error || 'Erreur API');
            }
            return data;
        }

        function fmtMoney(n) {
            return Math.round(n).toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');
        }

        function updateTotal(newTotal) {
            const el = document.getElementById('totalDisplay');
            el.dataset.total = newTotal;
            el.childNodes[0].textContent = fmtMoney(newTotal) + ' ';
        }

        // ---------- CHECK / DELETE / EDIT (AJAX) ----------
        async function toggleCheck(id) {
            const row = document.querySelector('.item-row[data-id="' + id + '"]');
            row.classList.toggle('done');
            try {
                const fd = new FormData();
                const data = await apiFetch('/api/check/' + id, fd);
                updateTotal(data.total);
            } catch (e) {
                row.classList.toggle('done');
            }
            return false;
        }

        async function deleteItem(id) {
            const row = document.querySelector('.item-row[data-id="' + id + '"]');
            const snapshot = { ...row.dataset };
            row.classList.add('removing');
            try {
                const fd = new FormData();
                const data = await apiFetch('/api/del/' + id, fd);
                setTimeout(() => {
                    row.remove();
                    checkEmptyState();
                }, 180);
                updateTotal(data.total);
                showToast('Article supprimé.', 'info', () => reAddItem(snapshot));
            } catch (e) {
                row.classList.remove('removing');
            }
            return false;
        }

        async function reAddItem(snapshot) {
            const fd = new FormData();
            fd.append('nom', snapshot.nom);
            fd.append('qte', snapshot.qte);
            fd.append('prix', snapshot.prix);
            fd.append('cat', document.querySelector('.item-row[data-cat]')?.dataset.cat || '✨ Autre');
            try {
                const data = await apiFetch('/api/add', fd);
                addItemToDOM(data.item);
                updateTotal(data.total);
            } catch (e) {}
        }

        function openEditModal(linkEl) {
            const row = linkEl.closest('.item-row');
            document.getElementById('editForm').dataset.id = row.dataset.id;
            document.getElementById('editNom').value = row.dataset.nom;
            document.getElementById('editQte').value = row.dataset.qte;
            document.getElementById('editPrix').value = row.dataset.prix;
            document.getElementById('editCat').value = row.dataset.cat;
            new bootstrap.Modal(document.getElementById('editModal')).show();
            return false;
        }

        document.getElementById('editForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            const btn = this.querySelector('button[type=submit]');
            btn.classList.add('btn-loading');
            const id = this.dataset.id;
            const fd = new FormData(this);
            try {
                const data = await apiFetch('/api/edit/' + id, fd);
                const row = document.querySelector('.item-row[data-id="' + id + '"]');
                row.dataset.nom = data.item.nom;
                row.dataset.qte = data.item.qte;
                row.dataset.prix = data.item.prix;
                row.dataset.cat = data.item.cat;
                row.setAttribute('data-cat', data.item.cat);
                row.querySelector('.item-n').innerHTML = data.item.nom + ' <small class="text-secondary">(x' + data.item.qte + ')</small>';
                row.querySelector('.badge').textContent = data.item.cat;
                row.querySelector('.badge').style.background = data.item.color;
                row.querySelector('.item-total').textContent = fmtMoney(data.item.prix * data.item.qte) + ' ' + DEVISE;
                updateTotal(data.total);
                bootstrap.Modal.getInstance(document.getElementById('editModal')).hide();
                showToast('Article mis à jour.', 'success');
            } catch (e) {} finally {
                btn.classList.remove('btn-loading');
            }
        });

        function addItemToDOM(item) {
            const list = document.getElementById('itemsList');
            const empty = document.getElementById('emptyState');
            if (empty) empty.remove();
            const div = document.createElement('div');
            div.className = 'list-group-item d-flex justify-content-between align-items-center item-row entering';
            div.setAttribute('data-cat', item.cat);
            div.setAttribute('data-id', item.id);
            div.setAttribute('data-nom', item.nom);
            div.setAttribute('data-qte', item.qte);
            div.setAttribute('data-prix', item.prix);
            div.setAttribute('data-fait', '0');
            div.innerHTML = `
                <div class="me-2">
                    <span class="fw-bold item-n d-block">${item.nom} <small class="text-secondary">(x${item.qte})</small></span>
                    <span class="badge rounded-pill mt-1" style="background: ${item.color}; font-weight: 500;">${item.cat}</span>
                </div>
                <div class="text-end">
                    <span class="fw-bold d-block text-warning item-total" style="font-size: 1.1rem;">${fmtMoney(item.prix * item.qte)} ${DEVISE}</span>
                    <div class="no-print mt-1">
                        <a href="#" onclick="return toggleCheck(${item.id})" class="text-success me-2 text-decoration-none"><i class="fa fa-check-circle fa-lg"></i></a>
                        <a href="#" onclick="return openEditModal(this)" class="text-warning me-2 text-decoration-none"><i class="fa fa-pencil fa-lg"></i></a>
                        <a href="#" onclick="return deleteItem(${item.id})" class="text-danger text-decoration-none"><i class="fa fa-trash fa-lg"></i></a>
                    </div>
                </div>`;
            list.prepend(div);
            filterItems();
        }

        function checkEmptyState() {
            const list = document.getElementById('itemsList');
            if (!list.querySelector('.item-row')) {
                const div = document.createElement('div');
                div.className = 'text-center text-secondary py-4';
                div.id = 'emptyState';
                div.textContent = 'Votre panier est vide pour l\\'instant ! 🛒';
                list.appendChild(div);
            }
        }

        document.getElementById('addForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            const btn = this.querySelector('button[type=submit]');
            btn.classList.add('btn-loading');
            const fd = new FormData(this);
            try {
                const data = await apiFetch('/api/add', fd);
                addItemToDOM(data.item);
                updateTotal(data.total);
                this.reset();
                document.getElementById('itemNomInput').focus();
                showToast('Article ajouté.', 'success');
            } catch (e) {} finally {
                btn.classList.remove('btn-loading');
            }
        });

        // ---------- SCANNER ----------
        function startScanner() {
            html5QrcodeScanner = new Html5QrcodeScanner("reader", { fps: 10, qrbox: 250 });
            html5QrcodeScanner.render((decodedText) => {
                document.getElementById('itemNomInput').value = "Produit " + decodedText;
                stopScanner();
                let modal = bootstrap.Modal.getInstance(document.getElementById('scannerModal'));
                modal.hide();
            });
        }

        function stopScanner() {
            if (html5QrcodeScanner) { html5QrcodeScanner.clear().catch(() => {}); }
        }

        document.getElementById('scannerModal').addEventListener('shown.bs.modal', startScanner);
        document.getElementById('scannerModal').addEventListener('hidden.bs.modal', stopScanner);

        function checkPriceMemory(val) {
            if (val.length < 2) return;
            fetch('/api/suggest?query=' + encodeURIComponent(val))
                .then(r => r.json())
                .then(data => {
                    if (data.found) {
                        document.getElementById('itemPrixInput').value = data.prix;
                        document.getElementById('itemCatInput').value = data.cat;
                    }
                });
        }

        function requestNotificationPermission() {
            if (!("Notification" in window)) { showToast("Votre navigateur ne prend pas en charge les notifications.", "error"); return; }
            Notification.requestPermission().then(permission => {
                if (permission === "granted") {
                    new Notification("🛒 SmartPanier", { body: "Rappels activés avec succès !" });
                }
            });
        }

        function scheduleReminder(minutes, count) {
            if (Notification.permission === "granted") {
                setTimeout(() => {
                    new Notification("🛒 C'est l'heure des courses !", { body: `Tu as ${count} article(s) dans ta liste.` });
                }, minutes * 60 * 1000);
                showToast(`Rappel programmé dans ${minutes} minutes !`, 'success');
            } else { requestNotificationPermission(); }
        }

        function playAlertBeep() {
            try {
                const ctx = new (window.AudioContext || window.webkitAudioContext)();
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.type = 'sawtooth';
                osc.frequency.setValueAtTime(440, ctx.currentTime);
                gain.gain.setValueAtTime(0.1, ctx.currentTime);
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start();
                osc.stop(ctx.currentTime + 0.3);
            } catch (e) {}
        }

        const isBudgetOver = {{ 'true' if total > budget else 'false' }};
        if (isBudgetOver) {
            window.addEventListener('load', () => setTimeout(playAlertBeep, 500));
        }

        let deferredPrompt;
        const installBtn = document.getElementById('pwaInstallBtn');

        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/sw.js').catch(err => console.log('SW Fail:', err));
            });
        }

        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            deferredPrompt = e;
            installBtn.classList.remove('d-none');
        });

        installBtn.addEventListener('click', () => {
            if (deferredPrompt) {
                deferredPrompt.prompt();
                deferredPrompt.userChoice.then((choiceResult) => {
                    if (choiceResult.outcome === 'accepted') { installBtn.classList.add('d-none'); }
                    deferredPrompt = null;
                });
            }
        });

        function applyTheme(theme) {
            document.documentElement.setAttribute('data-theme', theme);
            const btn = document.getElementById('themeBtn');
            btn.innerHTML = (theme === 'light') ? '<i class="fa fa-sun text-warning"></i>' : '<i class="fa fa-moon"></i>';
            localStorage.setItem('theme', theme);
        }

        function toggleTheme() {
            const current = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
            applyTheme(current);
        }

        applyTheme(localStorage.getItem('theme') || 'dark');

        function filterItems() {
            let search = document.getElementById('searchInput').value.toLowerCase();
            let items = document.querySelectorAll('#itemsList .item-row');
            items.forEach(item => {
                let name = item.querySelector('.item-n').innerText.toLowerCase();
                let cat = item.getAttribute('data-cat');
                let matchesSearch = name.includes(search);
                let matchesCat = (currentCatFilter === 'ALL' || cat === currentCatFilter);
                item.style.display = (matchesSearch && matchesCat) ? "flex" : "none";
            });
        }

        function setCategoryFilter(cat, btnElement) {
            currentCatFilter = cat;
            document.querySelectorAll('#categoryFilters .cat-filter-btn').forEach(b => b.classList.remove('active'));
            btnElement.classList.add('active');
            filterItems();
        }

        function invite() { window.open("https://wa.me/?text=" + encodeURIComponent("Salut ! Gère ton budget courses simplement ici : {{url}}")); }

        function copyWA() {
            let t = "*🛒 MA LISTE SmartPanier*\\n\\n";
            let items = document.querySelectorAll('.item-row:not(.done)');
            if (items.length === 0) { showToast("Votre liste est vide !", "error"); return; }
            items.forEach(i => { t += "🔹 " + i.querySelector('.item-n').innerText + "\\n"; });
            t += "\\n*💰 TOTAL : " + document.querySelector('.total-display').innerText.trim() + "*\\n\\n_Géré avec SmartPanier : {{url}}_";
            navigator.clipboard.writeText(t).then(() => showToast("Liste copiée pour WhatsApp !", "success"));
        }

        document.addEventListener("DOMContentLoaded", function() {
            const chartData = {{ chart_data | tojson }};
            const ctxPie = document.getElementById('categoryChart').getContext('2d');
            new Chart(ctxPie, {
                type: 'doughnut',
                data: { labels: chartData.labels, datasets: [{ data: chartData.data, backgroundColor: chartData.colors, borderWidth: 0 }] },
                options: { responsive: true, plugins: { legend: { display: false } } }
            });

            {% if histo %}
            const histoData = {{ histo_data | tojson }};
            const ctxLine = document.getElementById('monthlyChart').getContext('2d');
            new Chart(ctxLine, {
                type: 'line',
                data: {
                    labels: histoData.labels,
                    datasets: [{ label: 'Dépenses', data: histoData.totals, borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.2)', fill: true, tension: 0.3 }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
                    scales: { x: { ticks: { color: '#94a3b8' } }, y: { ticks: { color: '#94a3b8' } } }
                }
            });
            {% endif %}
        });
    </script>
</body>
</html>
"""

# --- HELPERS JINJA ---
def fmt_number(n):
    try:
        return "{:,.0f}".format(float(n)).replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


app.jinja_env.globals["fmt"] = fmt_number
app.jinja_env.globals["favicon"] = FAVICON_SVG


def current_scope_filter():
    """Retourne la clause SQL + params pour restreindre aux articles visibles par l'utilisateur (siens + partagés)."""
    uid = session["uid"]
    return "(user_id=? OR user_id IN (SELECT owner_id FROM shares WHERE shared_with_id=?))", (uid, uid)


# --- ROUTES STATIQUES / PWA ---
@app.route("/manifest.json")
def manifest():
    return Response(MANIFEST_JSON, mimetype="application/json")


@app.route("/sw.js")
def sw():
    return Response(SW_JS, mimetype="application/javascript")


@app.route("/api/suggest")
@login_required
def suggest():
    q = request.args.get("query", "").strip()[:100]
    conn = get_db()
    r = conn.execute(
        "SELECT dernier_prix, cat FROM price_memory WHERE user_id=? AND nom LIKE ? LIMIT 1",
        (session["uid"], f"%{q}%")
    ).fetchone()
    if r:
        return jsonify({"found": True, "prix": r[0], "cat": r[1]})
    return jsonify({"found": False})


# --- AUTHENTIFICATION ---
@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def register():
    if request.method == "POST":
        u = validate_username(request.form.get("user"))
        p = request.form.get("pass")
        if not u:
            flash("Nom d'utilisateur invalide (3-20 caractères : lettres, chiffres, underscore).")
        elif not validate_password(p):
            flash("Le mot de passe doit contenir au moins 6 caractères.")
        else:
            try:
                with sqlite3.connect(DB_NAME) as conn:
                    conn.execute("INSERT INTO users (username, password) VALUES (?,?)", (u, generate_password_hash(p)))
                    conn.commit()
                flash("Compte créé avec succès ! Connectez-vous.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("Ce nom d'utilisateur est déjà pris.")
            except sqlite3.Error:
                log.exception("Erreur lors de l'inscription")
                flash("Une erreur est survenue, réessayez.")
    return render_template_string(AUTH_HTML, title="Inscription", btn="CRÉER MON COMPTE")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if request.method == "POST":
        u = (request.form.get("user") or "").strip()
        p = request.form.get("pass") or ""
        with sqlite3.connect(DB_NAME) as conn:
            r = conn.execute("SELECT id, password FROM users WHERE username=?", (u,)).fetchone()
        if r and check_password_hash(r[1], p):
            session.clear()
            session["uid"], session["user"] = r[0], u
            session.permanent = True
            return redirect(url_for("home"))
        flash("Identifiants incorrects.")
    return render_template_string(AUTH_HTML, title="Login", btn="SE CONNECTER")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/profile")
@login_required
def profile():
    conn = get_db()
    shared_users = conn.execute(
        "SELECT u.username FROM shares s JOIN users u ON s.shared_with_id = u.id WHERE s.owner_id=?",
        (session["uid"],)
    ).fetchall()
    return render_template_string(PROFILE_HTML, username=session["user"], shared_users=shared_users)


@app.route("/share_list", methods=["POST"])
@login_required
def share_list():
    target_user = validate_username(request.form.get("share_username"))
    conn = get_db()
    if not target_user:
        flash("Nom d'utilisateur invalide.")
        return redirect(url_for("profile"))
    target = conn.execute("SELECT id FROM users WHERE username=?", (target_user,)).fetchone()
    if not target:
        flash("Utilisateur introuvable.")
    elif target[0] == session["uid"]:
        flash("Vous ne pouvez pas partager votre liste avec vous-même.")
    else:
        try:
            conn.execute("INSERT INTO shares (owner_id, shared_with_id) VALUES (?,?)", (session["uid"], target[0]))
            conn.commit()
            flash(f"Liste partagée avec {target_user} !")
        except sqlite3.IntegrityError:
            flash("Déjà partagé avec cet utilisateur.")
    return redirect(url_for("profile"))


@app.route("/change_password", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def change_password():
    old_p = request.form.get("old_pass") or ""
    new_p = request.form.get("new_pass") or ""
    conn = get_db()
    r = conn.execute("SELECT password FROM users WHERE id=?", (session["uid"],)).fetchone()
    if not (r and check_password_hash(r[0], old_p)):
        flash("L'ancien mot de passe est incorrect.")
    elif not validate_password(new_p):
        flash("Le nouveau mot de passe doit contenir au moins 6 caractères.")
    else:
        conn.execute("UPDATE users SET password=? WHERE id=?", (generate_password_hash(new_p), session["uid"]))
        conn.commit()
        flash("Mot de passe mis à jour !")
    return redirect(url_for("profile"))


@app.route("/clear_history", methods=["POST"])
@login_required
def clear_history():
    conn = get_db()
    conn.execute("DELETE FROM historique WHERE user_id=?", (session["uid"],))
    conn.commit()
    flash("Historique effacé.")
    return redirect(url_for("profile"))


@app.route("/reset_all", methods=["POST"])
@login_required
def reset_all():
    uid = session["uid"]
    conn = get_db()
    conn.execute("DELETE FROM courses WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM historique WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM templates WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM price_memory WHERE user_id=?", (uid,))
    conn.commit()
    flash("Toutes vos données ont été réinitialisées.")
    return redirect(url_for("home"))


@app.route("/export_csv")
@login_required
def export_csv():
    devise = session.get("devise", "FCFA")
    conn = get_db()
    items = conn.execute("SELECT nom, qte, prix, cat FROM courses WHERE user_id=?", (session["uid"],)).fetchall()
    csv_data = f"Nom,Quantite,Prix Unitaire ({devise}),Total ({devise}),Categorie\n"
    for item in items:
        nom_safe = item[0].replace('"', '""')
        total = item[1] * item[2]
        csv_data += f'"{nom_safe}",{item[1]},{item[2]},{total},"{item[3]}"\n'
    return Response(csv_data, mimetype="text/csv", headers={"Content-disposition": "attachment; filename=Ma_Liste_SmartPanier.csv"})


# --- PAGE PRINCIPALE ---
@app.route("/")
def home():
    if "uid" not in session:
        return render_template_string(LANDING_HTML)

    uid = session["uid"]
    budget_user = session.get("budget", 50000.0)
    devise_user = session.get("devise", "FCFA")

    conn = get_db()
    scope_clause, scope_params = current_scope_filter()
    liste = conn.execute(
        f"SELECT * FROM courses WHERE {scope_clause} ORDER BY fait ASC, id DESC", scope_params
    ).fetchall()

    total = conn.execute(
        f"SELECT SUM(prix*qte) FROM courses WHERE {scope_clause} AND fait=0", scope_params
    ).fetchone()[0] or 0
    histo = conn.execute(
        "SELECT id, total, nb_articles, date_achat FROM historique WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,)
    ).fetchall()
    templates = conn.execute("SELECT id, user_id, title FROM templates WHERE user_id=?", (uid,)).fetchall()

    stats = {}
    chart_labels, chart_data, chart_colors = [], [], []
    glob = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=?", (uid,)).fetchone()[0] or 1
    for c, color in CAT_CONFIG.items():
        s = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=? AND cat=?", (uid, c)).fetchone()[0] or 0
        stats[c] = {"p": int((s / glob) * 100), "c": color}
        if s > 0:
            chart_labels.append(c)
            chart_data.append(s)
            chart_colors.append(color)

    histo_labels, histo_totals = [], []
    for h in reversed(histo):
        histo_labels.append(str(h[3])[:10])
        histo_totals.append(h[1])

    return render_template_string(
        MAIN_HTML,
        liste=liste,
        total=total,
        budget=budget_user,
        username=session["user"],
        categories=list(CAT_CONFIG.keys()),
        config=CAT_CONFIG,
        stats=stats,
        histo=histo,
        devises=DEVISES,
        devise=devise_user,
        preset_recipes=PRESET_RECIPES,
        templates=templates,
        chart_data={"labels": chart_labels, "data": chart_data, "colors": chart_colors},
        histo_data={"labels": histo_labels, "totals": histo_totals},
        url=SITE_URL
    )


# --- API JSON (AJAX) ---
def _current_total(conn):
    scope_clause, scope_params = current_scope_filter()
    return conn.execute(
        f"SELECT SUM(prix*qte) FROM courses WHERE {scope_clause} AND fait=0", scope_params
    ).fetchone()[0] or 0


@app.route("/api/add", methods=["POST"])
@login_required
def api_add():
    nom, qte, prix, cat, errors = validate_item_input(
        request.form.get("nom"), request.form.get("qte", 1), request.form.get("prix"), request.form.get("cat")
    )
    if errors:
        return jsonify({"ok": False, "error": " ".join(errors)}), 400
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO courses (user_id, nom, prix, qte, fait, cat, added_by) VALUES (?,?,?,?,0,?,?)",
        (session["uid"], nom, prix, qte, cat, session["user"])
    )
    new_id = cur.lastrowid
    if prix > 0:
        conn.execute(
            "INSERT INTO price_memory (user_id, nom, dernier_prix, cat) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, nom) DO UPDATE SET dernier_prix=excluded.dernier_prix, cat=excluded.cat",
            (session["uid"], nom, prix, cat)
        )
    conn.commit()
    return jsonify({
        "ok": True,
        "total": _current_total(conn),
        "item": {"id": new_id, "nom": nom, "prix": prix, "qte": qte, "cat": cat, "color": CAT_CONFIG.get(cat, "#64748b")}
    })


@app.route("/api/edit/<int:item_id>", methods=["POST"])
@login_required
def api_edit(item_id):
    nom, qte, prix, cat, errors = validate_item_input(
        request.form.get("nom"), request.form.get("qte", 1), request.form.get("prix"), request.form.get("cat")
    )
    if errors:
        return jsonify({"ok": False, "error": " ".join(errors)}), 400
    conn = get_db()
    scope_clause, scope_params = current_scope_filter()
    cur = conn.execute(
        f"UPDATE courses SET nom=?, qte=?, prix=?, cat=? WHERE id=? AND {scope_clause}",
        (nom, qte, prix, cat, item_id, *scope_params)
    )
    if cur.rowcount == 0:
        return jsonify({"ok": False, "error": "Article introuvable."}), 404
    if prix > 0:
        conn.execute(
            "INSERT INTO price_memory (user_id, nom, dernier_prix, cat) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, nom) DO UPDATE SET dernier_prix=excluded.dernier_prix, cat=excluded.cat",
            (session["uid"], nom, prix, cat)
        )
    conn.commit()
    return jsonify({
        "ok": True,
        "total": _current_total(conn),
        "item": {"id": item_id, "nom": nom, "prix": prix, "qte": qte, "cat": cat, "color": CAT_CONFIG.get(cat, "#64748b")}
    })


@app.route("/api/check/<int:item_id>", methods=["POST"])
@login_required
def api_check(item_id):
    conn = get_db()
    scope_clause, scope_params = current_scope_filter()
    cur = conn.execute(
        f"UPDATE courses SET fait = NOT fait, checked_by=? WHERE id=? AND {scope_clause}",
        (session["user"], item_id, *scope_params)
    )
    if cur.rowcount == 0:
        return jsonify({"ok": False, "error": "Article introuvable."}), 404
    conn.commit()
    return jsonify({"ok": True, "total": _current_total(conn)})


@app.route("/api/del/<int:item_id>", methods=["POST"])
@login_required
def api_del(item_id):
    conn = get_db()
    scope_clause, scope_params = current_scope_filter()
    cur = conn.execute(f"DELETE FROM courses WHERE id=? AND {scope_clause}", (item_id, *scope_params))
    if cur.rowcount == 0:
        return jsonify({"ok": False, "error": "Article introuvable."}), 404
    conn.commit()
    return jsonify({"ok": True, "total": _current_total(conn)})


# --- LISTES : RECETTES / MODÈLES / BUDGET / DEVISE / CLÔTURE ---
@app.route("/load_recipe", methods=["POST"])
@login_required
def load_recipe():
    r_name = request.form.get("recipe_name")
    if r_name in PRESET_RECIPES:
        conn = get_db()
        for item in PRESET_RECIPES[r_name]:
            conn.execute(
                "INSERT INTO courses (user_id, nom, prix, qte, fait, cat, added_by) VALUES (?,?,?,?,0,?,?)",
                (session["uid"], item["nom"], item["prix"], item["qte"], item["cat"], session["user"])
            )
        conn.commit()
    return redirect(url_for("home"))


@app.route("/save_template", methods=["POST"])
@login_required
def save_template():
    title = (request.form.get("title") or "Ma liste").strip()[:80]
    conn = get_db()
    items = conn.execute("SELECT nom, prix, qte, cat FROM courses WHERE user_id=?", (session["uid"],)).fetchall()
    items_json = json.dumps([{"nom": x[0], "prix": x[1], "qte": x[2], "cat": x[3]} for x in items])
    conn.execute("INSERT INTO templates (user_id, title, items_json) VALUES (?,?,?)", (session["uid"], title, items_json))
    conn.commit()
    return redirect(url_for("home"))


@app.route("/load_template/<int:template_id>", methods=["POST"])
@login_required
def load_template(template_id):
    conn = get_db()
    r = conn.execute("SELECT items_json FROM templates WHERE id=? AND user_id=?", (template_id, session["uid"])).fetchone()
    if r:
        try:
            items = json.loads(r[0])
        except (json.JSONDecodeError, TypeError):
            items = []
        for item in items:
            conn.execute(
                "INSERT INTO courses (user_id, nom, prix, qte, fait, cat, added_by) VALUES (?,?,?,?,0,?,?)",
                (session["uid"], item.get("nom", "Article"), item.get("prix", 0), item.get("qte", 1),
                 item.get("cat", "✨ Autre"), session["user"])
            )
        conn.commit()
    return redirect(url_for("home"))


@app.route("/del_template/<int:template_id>", methods=["POST"])
@login_required
def del_template(template_id):
    conn = get_db()
    conn.execute("DELETE FROM templates WHERE id=? AND user_id=?", (template_id, session["uid"]))
    conn.commit()
    return redirect(url_for("home"))


@app.route("/set_budget", methods=["POST"])
@login_required
def set_budget():
    try:
        val = float(request.form.get("val", 50000))
        session["budget"] = max(0, min(val, 1_000_000_000))
    except ValueError:
        pass
    return redirect(url_for("home"))


@app.route("/set_devise", methods=["POST"])
@login_required
def set_devise():
    devise = request.form.get("devise", "FCFA")
    if devise in DEVISES:
        session["devise"] = devise
    return redirect(url_for("home"))


@app.route("/cloturer", methods=["POST"])
@login_required
def cloturer():
    uid = session["uid"]
    conn = get_db()
    total = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=? AND fait=0", (uid,)).fetchone()[0] or 0
    count = conn.execute("SELECT COUNT(*) FROM courses WHERE user_id=? AND fait=0", (uid,)).fetchone()[0] or 0
    if total > 0 or count > 0:
        conn.execute("INSERT INTO historique (user_id, total, nb_articles) VALUES (?,?,?)", (uid, total, count))
        conn.execute("DELETE FROM courses WHERE user_id=?", (uid,))
        conn.commit()
    return redirect(url_for("home"))


# --- GESTION D'ERREURS ---
@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Session expirée, merci de recharger la page."}), 400
    flash("Votre session a expiré, merci de réessayer.")
    return redirect(url_for("home"))


@app.errorhandler(404)
def not_found(e):
    return render_template_string(ERROR_HTML, code=404, msg="Cette page n'existe pas."), 404


@app.errorhandler(429)
def rate_limited(e):
    return render_template_string(ERROR_HTML, code=429, msg="Trop de tentatives. Merci de réessayer dans quelques instants."), 429


@app.errorhandler(500)
def server_error(e):
    log.exception("Erreur serveur non gérée")
    return render_template_string(ERROR_HTML, code=500, msg="Une erreur inattendue est survenue."), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
