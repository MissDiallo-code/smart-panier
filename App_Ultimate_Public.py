import os
import re
import secrets
import logging
import sqlite3
import json
import threading
import hashlib
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import timedelta, datetime, timezone
from functools import wraps

import requests
from flask import (
    Flask, render_template_string, request, redirect, url_for,
    session, flash, Response, jsonify, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

try:
    from flask_socketio import SocketIO, emit as socketio_emit, join_room
    SOCKETIO_AVAILABLE = True
except ImportError:
    SOCKETIO_AVAILABLE = False

try:
    from authlib.integrations.flask_client import OAuth
    AUTHLIB_AVAILABLE = True
except ImportError:
    AUTHLIB_AVAILABLE = False

try:
    import stripe
    STRIPE_LIB_AVAILABLE = True
except ImportError:
    STRIPE_LIB_AVAILABLE = False

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

# --- NOTIFICATIONS PUSH (OneSignal) ---
# Facultatif : l'app fonctionne sans, avec un repli sur les notifications locales du navigateur.
# Pour activer le vrai push (qui marche même app/onglet fermé), créer un compte gratuit sur
# onesignal.com et définir ONESIGNAL_APP_ID (publique) et ONESIGNAL_API_KEY (secrète) sur Render.
ONESIGNAL_APP_ID = os.environ.get("ONESIGNAL_APP_ID", "")
ONESIGNAL_API_KEY = os.environ.get("ONESIGNAL_API_KEY", "")

csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["300 per hour"], storage_uri="memory://")

# --- EMAIL (mot de passe oublié) ---
# Facultatif : sans configuration SMTP, le formulaire "mot de passe oublié" reste affiché
# mais n'enverra pas d'e-mail réel (juste un log serveur). Fonctionne avec Gmail (mot de passe
# d'application), SendGrid, Mailgun, ou tout autre fournisseur SMTP standard.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER or "no-reply@smartpanier.local")
EMAIL_CONFIGURED = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)

# --- TEMPS RÉEL (collaboration live sur les listes partagées) ---
# Interrupteur de sécurité : si le temps réel pose problème en prod, mettre ENABLE_REALTIME=false
# sur Render pour revenir instantanément au fonctionnement précédent (AJAX classique), sans rien casser.
ENABLE_REALTIME = os.environ.get("ENABLE_REALTIME", "true").lower() != "false"
if ENABLE_REALTIME and SOCKETIO_AVAILABLE:
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
else:
    socketio = None
    if ENABLE_REALTIME and not SOCKETIO_AVAILABLE:
        log.warning("flask-socketio non installé : le temps réel est désactivé (fonctionnement AJAX classique).")


# --- CONNEXION GOOGLE / FACEBOOK (OAuth) ---
# Facultatif : sans ces variables, les boutons "Continuer avec Google/Facebook" n'apparaissent
# simplement pas, et l'inscription classique (utilisateur/mot de passe) reste inchangée.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
FACEBOOK_CLIENT_ID = os.environ.get("FACEBOOK_CLIENT_ID", "")
FACEBOOK_CLIENT_SECRET = os.environ.get("FACEBOOK_CLIENT_SECRET", "")

oauth = OAuth(app) if AUTHLIB_AVAILABLE else None
if oauth and GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
if oauth and FACEBOOK_CLIENT_ID and FACEBOOK_CLIENT_SECRET:
    oauth.register(
        name="facebook",
        client_id=FACEBOOK_CLIENT_ID,
        client_secret=FACEBOOK_CLIENT_SECRET,
        access_token_url="https://graph.facebook.com/oauth/access_token",
        authorize_url="https://www.facebook.com/dialog/oauth",
        api_base_url="https://graph.facebook.com/",
        client_kwargs={"scope": "email"},
    )

GOOGLE_ENABLED = bool(oauth and GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
FACEBOOK_ENABLED = bool(oauth and FACEBOOK_CLIENT_ID and FACEBOOK_CLIENT_SECRET)
if (GOOGLE_CLIENT_ID or FACEBOOK_CLIENT_ID) and not AUTHLIB_AVAILABLE:
    log.warning("Identifiants OAuth définis mais le paquet Authlib n'est pas installé : connexion Google/Facebook désactivée.")


def find_or_create_oauth_user(provider, oauth_id, email, name_hint):
    """Retrouve un compte lié à ce provider OAuth, le relie à un compte existant par email,
    ou en crée un nouveau (sans mot de passe, connexion uniquement via ce provider)."""
    conn = get_db()
    row = conn.execute("SELECT id, username FROM users WHERE oauth_provider=? AND oauth_id=?", (provider, oauth_id)).fetchone()
    if row:
        return row[0], row[1]

    if email:
        row = conn.execute("SELECT id, username FROM users WHERE email=?", (email,)).fetchone()
        if row:
            conn.execute("UPDATE users SET oauth_provider=?, oauth_id=? WHERE id=?", (provider, oauth_id, row[0]))
            conn.commit()
            return row[0], row[1]

    base = re.sub(r"[^A-Za-z0-9_]", "", (name_hint or (email or "user").split("@")[0]))[:15] or "user"
    username = base
    suffix = 0
    while conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        suffix += 1
        username = f"{base}{suffix}"

    cur = conn.execute(
        "INSERT INTO users (username, password, email, oauth_provider, oauth_id, referral_code) VALUES (?,?,?,?,?,?)",
        (username, None, email, provider, oauth_id, generate_referral_code(conn))
    )
    new_uid = cur.lastrowid
    conn.execute("INSERT INTO lists (user_id, nom) VALUES (?,?)", (new_uid, "Ma liste 🛒"))
    conn.commit()
    track_event("signup", new_uid, {"provider": provider})
    if email:
        send_email(
            email, "Bienvenue sur SmartPanier 🛒",
            f"Bonjour {username},\n\nVotre compte SmartPanier (connecté via {provider.title()}) est prêt !\n\n{SITE_URL}/",
            html_body=render_email_html(
                "Bienvenue !",
                f"Bonjour <b>{username}</b>,<br><br>Votre compte SmartPanier (connecté via {provider.title()}) est prêt.",
                button_label="Ouvrir SmartPanier", button_url=f"{SITE_URL}/",
                footer_note="Vous recevez cet email car un compte a été créé avec cette adresse sur SmartPanier."
            )
        )
    return new_uid, username


# --- PAIEMENT (Stripe) ---
# Facultatif : sans ces variables, la page /pricing affiche les plans mais les boutons
# d'abonnement redirigent avec un message clair au lieu de planter.
try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_ENABLED = bool(STRIPE_AVAILABLE and STRIPE_SECRET_KEY and STRIPE_PRICE_ID)
if STRIPE_ENABLED:
    stripe.api_key = STRIPE_SECRET_KEY
elif (STRIPE_SECRET_KEY or STRIPE_PRICE_ID) and not STRIPE_AVAILABLE:
    log.warning("Clés Stripe définies mais le paquet stripe n'est pas installé : paiement désactivé.")

PREMIUM_PRICE_LABEL = os.environ.get("PREMIUM_PRICE_LABEL", "2 000 FCFA / mois")

# --- ADMIN ---
# Le compte dont le nom d'utilisateur correspond a accès au tableau de bord /admin.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")

# --- ANALYTICS (Plausible, respectueux de la vie privée, optionnel) ---
PLAUSIBLE_DOMAIN = os.environ.get("PLAUSIBLE_DOMAIN", "")


def generate_referral_code(conn):
    code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()
    while conn.execute("SELECT id FROM users WHERE referral_code=?", (code,)).fetchone():
        code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()
    return code


def user_is_premium(row):
    """row: tuple (stripe_subscription_status, premium_until) ou dict-like avec ces clés."""
    status, premium_until = row
    if status == "active":
        return True
    if premium_until:
        try:
            return datetime.fromisoformat(premium_until) > datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return False
    return False


def get_user_premium_status(uid):
    conn = get_db()
    row = conn.execute("SELECT stripe_subscription_status, premium_until FROM users WHERE id=?", (uid,)).fetchone()
    return user_is_premium(row) if row else False


def grant_bonus_days(conn, uid, days):
    row = conn.execute("SELECT premium_until FROM users WHERE id=?", (uid,)).fetchone()
    now = datetime.now(timezone.utc)
    base = now
    if row and row[0]:
        try:
            existing = datetime.fromisoformat(row[0])
            if existing > now:
                base = existing
        except (TypeError, ValueError):
            pass
    new_until = (base + timedelta(days=days)).isoformat()
    conn.execute("UPDATE users SET premium_until=? WHERE id=?", (new_until, uid))


def track_event(event_type, user_id=None, meta=None):
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO analytics_events (event_type, user_id, meta) VALUES (?,?,?)",
            (event_type, user_id, json.dumps(meta) if meta else None)
        )
        conn.commit()
    except Exception:
        log.exception("Échec de l'enregistrement d'un évènement analytics")


def premium_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "uid" not in session:
            return redirect(url_for("login"))
        if not get_user_premium_status(session["uid"]):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Fonctionnalité réservée aux membres Premium.", "upsell": True}), 402
            flash("Cette fonctionnalité est réservée aux membres Premium.")
            return redirect(url_for("pricing"))
        return f(*args, **kwargs)
    return wrapper


# --- IA (Claude, optionnel) ---
# Facultatif : sans ANTHROPIC_API_KEY, l'assistant IA, la reconnaissance photo, le scan de
# tickets et le générateur de menus restent simplement masqués dans l'interface.
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_ENABLED = bool(ANTHROPIC_AVAILABLE and ANTHROPIC_API_KEY)
AI_MODEL = os.environ.get("AI_MODEL", "claude-sonnet-5")
_anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if AI_ENABLED else None
if ANTHROPIC_API_KEY and not ANTHROPIC_AVAILABLE:
    log.warning("ANTHROPIC_API_KEY définie mais le paquet anthropic n'est pas installé : fonctions IA désactivées.")


def ask_claude_text(system_prompt, user_prompt, max_tokens=800):
    """Appelle Claude en texte simple. Retourne None si l'IA n'est pas configurée ou en cas d'échec."""
    if not AI_ENABLED:
        return None
    try:
        resp = _anthropic_client.messages.create(
            model=AI_MODEL,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    except Exception:
        log.exception("Échec de l'appel à l'API Claude (texte)")
        return None


def ask_claude_vision(system_prompt, user_prompt, image_base64, media_type="image/jpeg", max_tokens=800):
    """Appelle Claude avec une image. Retourne None si l'IA n'est pas configurée ou en cas d'échec."""
    if not AI_ENABLED:
        return None
    try:
        resp = _anthropic_client.messages.create(
            model=AI_MODEL,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_base64}},
                    {"type": "text", "text": user_prompt},
                ],
            }],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    except Exception:
        log.exception("Échec de l'appel à l'API Claude (vision)")
        return None


def extract_json_from_ai_response(text):
    """Extrait un objet JSON d'une réponse IA (au cas où elle serait entourée de texte ou de ```)."""
    if not text:
        return None
    match = re.search(r"\{.*\}|\[.*\]", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


# --- SAUVEGARDE CLOUD (S3-compatible) ---
# Facultatif : nécessite un bucket S3, Backblaze B2 ou DigitalOcean Spaces.
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_REGION = os.environ.get("S3_REGION", "auto")
CLOUD_BACKUP_ENABLED = bool(S3_BUCKET and S3_ACCESS_KEY and S3_SECRET_KEY)

try:
    import boto3
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    if CLOUD_BACKUP_ENABLED:
        log.warning("Identifiants S3 définis mais boto3 n'est pas installé : sauvegarde cloud désactivée.")


def backup_to_cloud():
    """Sauvegarde un instantané cohérent de la base SQLite vers le stockage cloud configuré."""
    if not (CLOUD_BACKUP_ENABLED and BOTO3_AVAILABLE):
        return False, "Sauvegarde cloud non configurée."
    tmp_path = None
    try:
        import tempfile as _tempfile
        fd, tmp_path = _tempfile.mkstemp(suffix=".db")
        os.close(fd)
        src = sqlite3.connect(DB_NAME)
        dst = sqlite3.connect(tmp_path)
        src.backup(dst)  # API de sauvegarde SQLite : instantané cohérent même avec WAL actif
        dst.close()
        src.close()

        s3 = boto3.client(
            "s3", endpoint_url=S3_ENDPOINT or None, region_name=S3_REGION,
            aws_access_key_id=S3_ACCESS_KEY, aws_secret_access_key=S3_SECRET_KEY,
        )
        key = f"smartpanier-backups/backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.db"
        s3.upload_file(tmp_path, S3_BUCKET, key)
        return True, key
    except Exception:
        log.exception("Échec de la sauvegarde cloud")
        return False, "Échec de la sauvegarde, voir les logs serveur."
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def rt_emit(event, payload, room):
    """Émet un évènement temps réel si la fonctionnalité est active. No-op sinon."""
    if socketio:
        try:
            socketio.emit(event, payload, room=str(room))
        except Exception:
            log.exception("Échec de l'émission temps réel")


def render_email_html(title, intro, button_label=None, button_url=None, footer_note=None):
    button_html = ""
    if button_label and button_url:
        button_html = (
            f'<div style="text-align:center;margin:28px 0;">'
            f'<a href="{button_url}" style="background:#f59e0b;color:#0f172a;padding:14px 28px;'
            f'border-radius:8px;text-decoration:none;font-weight:bold;display:inline-block;">{button_label}</a>'
            f'</div>'
        )
    footer = footer_note or ""
    return f"""
    <div style="background:#0f172a;padding:30px 15px;font-family:Arial,Helvetica,sans-serif;">
      <div style="max-width:480px;margin:0 auto;background:#1e293b;border-radius:16px;padding:30px;color:#f8fafc;">
        <div style="text-align:center;font-size:28px;margin-bottom:10px;">🛒</div>
        <h2 style="text-align:center;color:#f59e0b;margin:0 0 20px;">{title}</h2>
        <p style="line-height:1.6;color:#f8fafc;">{intro}</p>
        {button_html}
        <p style="font-size:12px;color:#94a3b8;margin-top:24px;">{footer}</p>
      </div>
    </div>
    """


def send_email(to_addr, subject, body, html_body=None):
    """Envoie un e-mail (texte, ou texte + HTML si fourni). No-op silencieux (avec log) si le SMTP n'est pas configuré."""
    if not EMAIL_CONFIGURED:
        log.warning("SMTP non configuré : e-mail à %s non envoyé (sujet: %s).", to_addr, subject)
        return False
    try:
        if html_body:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))
        else:
            msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = MAIL_FROM
        msg["To"] = to_addr
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(MAIL_FROM, [to_addr], msg.as_string())
        return True
    except Exception:
        log.exception("Échec de l'envoi d'e-mail à %s", to_addr)
        return False


def validate_email(email):
    email = (email or "").strip().lower()
    if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) and len(email) <= 254:
        return email
    return None


# --- MONITORING D'ERREURS (Sentry) ---
# Facultatif : sans SENTRY_DSN, ce bloc ne fait rien. Avec, chaque erreur serveur en prod
# est automatiquement remontée sur ton tableau de bord Sentry (compte gratuit disponible).
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.1,
            send_default_pii=False,
        )
        log.info("Sentry activé : les erreurs serveur seront remontées automatiquement.")
    except ImportError:
        log.warning("SENTRY_DSN défini mais le paquet sentry-sdk n'est pas installé.")


@app.after_request
def add_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


def send_push_notification(player_id, title, body):
    """Envoie une notification push réelle via OneSignal. No-op silencieux si non configuré."""
    if not (ONESIGNAL_APP_ID and ONESIGNAL_API_KEY and player_id):
        return False
    try:
        requests.post(
            "https://onesignal.com/api/v1/notifications",
            headers={"Authorization": f"Basic {ONESIGNAL_API_KEY}", "Content-Type": "application/json"},
            json={
                "app_id": ONESIGNAL_APP_ID,
                "include_player_ids": [player_id],
                "headings": {"en": title, "fr": title},
                "contents": {"en": body, "fr": body},
            },
            timeout=5,
        )
        return True
    except requests.RequestException:
        log.exception("Échec de l'envoi de la notification push OneSignal")
        return False


def schedule_push_in_background(delay_seconds, player_id, title, body):
    """Programme un envoi push différé. Simple et suffisant pour une petite app,
    mais non-durable si le serveur redémarre entre-temps (limite connue, cf. CHANGEMENTS.md)."""
    timer = threading.Timer(delay_seconds, send_push_notification, args=(player_id, title, body))
    timer.daemon = True
    timer.start()


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
        c.execute("CREATE TABLE IF NOT EXISTS lists (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, nom TEXT NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS courses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nom TEXT, prix REAL, qte INTEGER, fait BOOLEAN, cat TEXT, date_ajout DATETIME DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS historique (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, total REAL, nb_articles INTEGER, date_achat DATETIME DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS templates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, items_json TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS shares (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, shared_with_id INTEGER, UNIQUE(owner_id, shared_with_id))")
        c.execute("CREATE TABLE IF NOT EXISTS price_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nom TEXT, dernier_prix REAL, cat TEXT, UNIQUE(user_id, nom))")
        c.execute("""
            CREATE TABLE IF NOT EXISTS households (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                nom TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS household_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                household_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT DEFAULT 'membre',
                UNIQUE(household_id, user_id)
            )
        """)
        conn.commit()


def _add_col_if_missing(conn, table, col, coldef):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")


def migrate_db():
    """Ajoute en douceur les nouvelles colonnes/tables sans casser une base existante en prod,
    et assure qu'une liste par défaut existe pour chaque utilisateur (migration multi-listes)."""
    with sqlite3.connect(DB_NAME) as conn:
        _add_col_if_missing(conn, "courses", "added_by", "added_by TEXT")
        _add_col_if_missing(conn, "courses", "checked_by", "checked_by TEXT")
        _add_col_if_missing(conn, "courses", "list_id", "list_id INTEGER")
        _add_col_if_missing(conn, "shares", "list_id", "list_id INTEGER")
        _add_col_if_missing(conn, "users", "onesignal_player_id", "onesignal_player_id TEXT")
        _add_col_if_missing(conn, "users", "email", "email TEXT")
        _add_col_if_missing(conn, "users", "reset_token_hash", "reset_token_hash TEXT")
        _add_col_if_missing(conn, "users", "reset_token_expiry", "reset_token_expiry TEXT")
        _add_col_if_missing(conn, "historique", "list_id", "list_id INTEGER")
        _add_col_if_missing(conn, "historique", "list_nom", "list_nom TEXT")
        _add_col_if_missing(conn, "price_memory", "frequency", "frequency INTEGER DEFAULT 1")
        _add_col_if_missing(conn, "lists", "household_id", "household_id INTEGER")
        _add_col_if_missing(conn, "users", "oauth_provider", "oauth_provider TEXT")
        _add_col_if_missing(conn, "users", "oauth_id", "oauth_id TEXT")
        _add_col_if_missing(conn, "users", "stripe_customer_id", "stripe_customer_id TEXT")
        _add_col_if_missing(conn, "users", "stripe_subscription_status", "stripe_subscription_status TEXT")
        _add_col_if_missing(conn, "users", "premium_until", "premium_until TEXT")
        _add_col_if_missing(conn, "users", "referral_code", "referral_code TEXT")
        _add_col_if_missing(conn, "users", "referred_by", "referred_by INTEGER")
        _add_col_if_missing(conn, "users", "created_at", "created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                user_id INTEGER,
                meta TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        # Attribue un code de parrainage à tous les comptes qui n'en ont pas encore.
        for (uid,) in conn.execute("SELECT id FROM users WHERE referral_code IS NULL").fetchall():
            code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()
            while conn.execute("SELECT id FROM users WHERE referral_code=?", (code,)).fetchone():
                code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()
            conn.execute("UPDATE users SET referral_code=? WHERE id=?", (code, uid))
        conn.commit()
        _add_col_if_missing(conn, "users", "created_at", "created_at TEXT")
        _add_col_if_missing(conn, "users", "is_premium", "is_premium INTEGER DEFAULT 0")
        _add_col_if_missing(conn, "users", "premium_until", "premium_until TEXT")
        _add_col_if_missing(conn, "users", "stripe_customer_id", "stripe_customer_id TEXT")
        _add_col_if_missing(conn, "users", "stripe_subscription_id", "stripe_subscription_id TEXT")
        _add_col_if_missing(conn, "users", "referral_code", "referral_code TEXT")
        _add_col_if_missing(conn, "users", "referred_by", "referred_by INTEGER")
        conn.commit()
        # Génère un code de parrainage pour les comptes existants qui n'en ont pas encore.
        for (uid,) in conn.execute("SELECT id FROM users WHERE referral_code IS NULL").fetchall():
            conn.execute("UPDATE users SET referral_code=? WHERE id=?", (generate_referral_code(conn), uid))
        conn.commit()

        default_list_name = "Ma liste 🛒"
        users_all = conn.execute("SELECT id FROM users").fetchall()
        for (uid,) in users_all:
            row = conn.execute("SELECT id FROM lists WHERE user_id=? ORDER BY id LIMIT 1", (uid,)).fetchone()
            if row:
                default_list_id = row[0]
            else:
                cur = conn.execute("INSERT INTO lists (user_id, nom) VALUES (?,?)", (uid, default_list_name))
                default_list_id = cur.lastrowid
            conn.execute("UPDATE courses SET list_id=? WHERE user_id=? AND list_id IS NULL", (default_list_id, uid))
            conn.execute("UPDATE shares SET list_id=? WHERE owner_id=? AND list_id IS NULL", (default_list_id, uid))
            conn.execute(
                "UPDATE historique SET list_id=?, list_nom=? WHERE user_id=? AND list_id IS NULL",
                (default_list_id, default_list_name, uid)
            )
        conn.commit()

        # Corrige la contrainte d'unicité de `shares` : à l'origine UNIQUE(owner_id, shared_with_id)
        # ne permettait qu'UNE SEULE liste partagée par personne. Le mode famille a besoin d'en
        # partager plusieurs -> on reconstruit la table avec UNIQUE(owner_id, shared_with_id, list_id)
        # si ce n'est pas déjà fait (migration idempotente, sans perte de données).
        existing_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='shares'"
        ).fetchone()
        if existing_sql and existing_sql[0]:
            normalized = re.sub(r"\s+", "", existing_sql[0]).lower()
            if "unique(owner_id,shared_with_id,list_id)" not in normalized:
                conn.execute("""
                    CREATE TABLE shares_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_id INTEGER,
                        shared_with_id INTEGER,
                        list_id INTEGER,
                        UNIQUE(owner_id, shared_with_id, list_id)
                    )
                """)
                conn.execute(
                    "INSERT INTO shares_new (owner_id, shared_with_id, list_id) "
                    "SELECT owner_id, shared_with_id, list_id FROM shares"
                )
                conn.execute("DROP TABLE shares")
                conn.execute("ALTER TABLE shares_new RENAME TO shares")
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

SW_JS = """const CACHE_NAME = 'smartpanier-v7';
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


def validate_list_name(nom):
    nom = (nom or "").strip()
    if not nom:
        return None
    return nom[:60]


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "uid" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Session expirée."}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# --- LISTES : HELPERS DE VISIBILITÉ ---
def get_visible_lists(uid):
    """Retourne (mes_listes, listes_partagees_avec_moi) — inclut le partage individuel ET les listes de famille."""
    conn = get_db()
    own = conn.execute("SELECT id, nom FROM lists WHERE user_id=? ORDER BY id", (uid,)).fetchall()
    shared = conn.execute("""
        SELECT l.id, l.nom, u.username FROM lists l
        JOIN shares s ON s.owner_id = l.user_id AND s.list_id = l.id
        JOIN users u ON u.id = l.user_id
        WHERE s.shared_with_id = ?
        UNION
        SELECT l.id, l.nom, u.username FROM lists l
        JOIN household_members hm ON hm.household_id = l.household_id
        JOIN users u ON u.id = l.user_id
        WHERE hm.user_id = ? AND l.user_id != ?
        ORDER BY 1
    """, (uid, uid, uid)).fetchall()
    return own, shared


def list_is_visible(uid, list_id):
    own, shared = get_visible_lists(uid)
    ids = {r[0] for r in own} | {r[0] for r in shared}
    return list_id in ids


def get_user_household(uid):
    conn = get_db()
    row = conn.execute("""
        SELECT h.id, h.nom, hm.role FROM household_members hm
        JOIN households h ON h.id = hm.household_id
        WHERE hm.user_id = ? LIMIT 1
    """, (uid,)).fetchone()
    return row


def list_is_owned(uid, list_id):
    conn = get_db()
    return conn.execute("SELECT id FROM lists WHERE id=? AND user_id=?", (list_id, uid)).fetchone() is not None


def get_current_list_id():
    """Renvoie l'id de la liste actuellement affichée pour l'utilisateur connecté,
    en s'assurant qu'elle existe et lui est bien visible (sécurité)."""
    uid = session["uid"]
    lid = session.get("current_list_id")
    if lid and list_is_visible(uid, lid):
        return lid
    own, shared = get_visible_lists(uid)
    if own:
        session["current_list_id"] = own[0][0]
    elif shared:
        session["current_list_id"] = shared[0][0]
    else:
        conn = get_db()
        cur = conn.execute("INSERT INTO lists (user_id, nom) VALUES (?,?)", (uid, "Ma liste 🛒"))
        conn.commit()
        session["current_list_id"] = cur.lastrowid
    return session["current_list_id"]


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
        {% if google_enabled or facebook_enabled %}
        <div class="d-flex flex-column gap-2 mb-3">
            {% if google_enabled %}<a href="/auth/google" class="btn btn-outline-light w-100 fw-bold"><i class="fab fa-google me-2"></i>Continuer avec Google</a>{% endif %}
            {% if facebook_enabled %}<a href="/auth/facebook" class="btn btn-outline-light w-100 fw-bold"><i class="fab fa-facebook me-2"></i>Continuer avec Facebook</a>{% endif %}
        </div>
        <div class="text-center small text-secondary mb-3">— ou avec un compte classique —</div>
        {% endif %}
        <form method="POST" onsubmit="this.querySelector('button[type=submit]').classList.add('btn-loading')">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            {% if title=="Inscription" and ref_code %}<input type="hidden" name="ref" value="{{ ref_code }}">{% endif %}
            <input type="text" name="user" class="form-control mb-3" placeholder="Nom d'utilisateur" minlength="3" maxlength="20" pattern="[A-Za-z0-9_]+" title="Lettres, chiffres et underscore uniquement (3-20 caractères)" required autocomplete="username">
            {% if title=="Inscription" %}
            <input type="email" name="email" class="form-control mb-3" placeholder="Email (pour récupérer votre compte)" maxlength="254" required autocomplete="email">
            {% endif %}
            <input type="password" name="pass" class="form-control mb-2" placeholder="Mot de passe (6 caractères min.)" minlength="6" required autocomplete="current-password">
            {% if title=="Login" %}
            <div class="text-end small mb-3"><a href="/forgot_password" class="text-secondary text-decoration-none">Mot de passe oublié ?</a></div>
            {% else %}
            <div class="mb-3"></div>
            {% endif %}
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
        <div class="small text-secondary mt-3"><a href="/mentions-legales" class="text-secondary text-decoration-underline">Mentions légales</a> · <a href="/confidentialite" class="text-secondary text-decoration-underline">Confidentialité</a></div>
    </div>
</body>
</html>
"""

FORGOT_PASSWORD_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <title>Mot de passe oublié - SmartPanier</title>
    <style>
        body { background: #0f172a; color: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; font-family: system-ui, -apple-system, sans-serif; }
        .auth-card { background: #1e293b; padding: 30px; border-radius: 20px; width: 100%; max-width: 420px; border: 1px solid #334155; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        .form-control { background: #0f172a; border: 1px solid #334155; color: white; padding: 12px; font-size: 16px !important; }
        .form-control:focus { background: #0f172a; color: white; border-color: #3b82f6; box-shadow: none; }
    </style>
</head>
<body>
    <div class="auth-card text-center">
        <h3 class="fw-bold mb-3">🔑 Mot de passe oublié</h3>
        <p class="small text-secondary mb-4">Indiquez l'email associé à votre compte : si un compte existe, un lien de réinitialisation vous sera envoyé.</p>
        {% with m = get_flashed_messages() %}
            {% if m %}<div class="alert alert-info py-2 small mb-3">{{ m[0] }}</div>{% endif %}
        {% endwith %}
        <form method="POST">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <input type="email" name="email" class="form-control mb-3" placeholder="Votre email" required autofocus>
            <button type="submit" class="btn btn-warning w-100 fw-bold mb-3">Envoyer le lien</button>
        </form>
        <a href="/login" class="text-info small text-decoration-none">Retour à la connexion</a>
    </div>
</body>
</html>
"""

RESET_PASSWORD_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <title>Réinitialiser le mot de passe - SmartPanier</title>
    <style>
        body { background: #0f172a; color: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; font-family: system-ui, -apple-system, sans-serif; }
        .auth-card { background: #1e293b; padding: 30px; border-radius: 20px; width: 100%; max-width: 420px; border: 1px solid #334155; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        .form-control { background: #0f172a; border: 1px solid #334155; color: white; padding: 12px; font-size: 16px !important; }
        .form-control:focus { background: #0f172a; color: white; border-color: #3b82f6; box-shadow: none; }
    </style>
</head>
<body>
    <div class="auth-card text-center">
        <h3 class="fw-bold mb-4">🔑 Nouveau mot de passe</h3>
        {% with m = get_flashed_messages() %}
            {% if m %}<div class="alert alert-warning py-2 small mb-3">{{ m[0] }}</div>{% endif %}
        {% endwith %}
        <form method="POST">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <input type="password" name="new_pass" class="form-control mb-3" placeholder="Nouveau mot de passe (6 car. min.)" minlength="6" required autofocus>
            <button type="submit" class="btn btn-warning w-100 fw-bold">Réinitialiser</button>
        </form>
    </div>
</body>
</html>
"""

LEGAL_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <title>{{ page_title }} - SmartPanier</title>
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; padding: 30px 15px; line-height: 1.6; }
        .legal-card { background: #1e293b; border: 1px solid #334155; border-radius: 20px; padding: 30px; max-width: 720px; margin: 0 auto; }
        h5 { color: #f59e0b; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="legal-card">
        <a href="/" class="btn btn-sm btn-outline-secondary mb-3"><i class="fa fa-arrow-left"></i> Retour</a>
        <h3 class="fw-bold mb-3">{{ page_title }}</h3>
        <div class="small text-secondary mb-4">Dernière mise à jour : {{ update_date }}</div>
        {{ content | safe }}
    </div>
</body>
</html>
"""

LANDING_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <title>SmartPanier — Gérez vos courses et votre budget en famille</title>
    <meta name="description" content="SmartPanier : gérez votre budget courses, créez plusieurs listes, partagez-les avec vos proches en temps réel et évitez les mauvaises surprises en caisse. Gratuit pour commencer.">
    <link rel="canonical" href="{{ site_url }}/">
    <meta property="og:title" content="SmartPanier — Gérez vos courses et votre budget">
    <meta property="og:description" content="Listes de courses partagées, budget maîtrisé, notifications en temps réel. Gratuit pour commencer.">
    <meta property="og:type" content="website">
    <meta property="og:url" content="{{ site_url }}/">
    <meta name="twitter:card" content="summary">
    <meta name="twitter:title" content="SmartPanier — Gérez vos courses et votre budget">
    <meta name="twitter:description" content="Listes de courses partagées, budget maîtrisé, notifications en temps réel.">
    {% if plausible_domain %}<script defer data-domain="{{ plausible_domain }}" src="https://plausible.io/js/script.js"></script>{% endif %}
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"SoftwareApplication","name":"SmartPanier","applicationCategory":"LifestyleApplication","operatingSystem":"Web","offers":{"@type":"Offer","price":"0","priceCurrency":"XOF"}}
    </script>
    <style>
        body { background: #0f172a; color: white; text-align: center; font-family: system-ui, -apple-system, sans-serif; }
        .hero { padding: 100px 20px 60px; background: linear-gradient(180deg, #1e293b 0%, #0f172a 100%); }
        .btn-start { background: #f59e0b; color: #0f172a; font-weight: 800; padding: 16px 36px; border-radius: 50px; text-decoration: none; display: inline-block; transition: transform 0.2s; }
        .btn-start:hover { transform: scale(1.05); color: #0f172a; }
        .feat { max-width: 720px; margin: 0 auto; }
        .feature-row { max-width: 780px; margin: 50px auto 0; }
        .feature-card { background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 20px; }
    </style>
</head>
<body>
    <div class="hero">
        <h1 class="display-3 fw-bold mb-3">🛒 SmartPanier</h1>
        <p class="lead text-secondary mb-4 feat">Gérez votre budget courses intelligemment, créez plusieurs listes (courses, ménage, anniversaire...), partagez-les en temps réel avec vos proches et recevez de vraies alertes, même app fermée.</p>
        <a href="/register" class="btn-start shadow-lg">COMMENCER GRATUITEMENT</a>
        <p class="mt-4 small text-secondary">Déjà membre ? <a href="/login" class="text-info fw-bold text-decoration-none">Se connecter</a></p>

        <div class="row g-3 feature-row">
            <div class="col-md-4"><div class="feature-card h-100"><div class="fs-3 mb-2">📋</div><div class="fw-bold">Multi-listes</div><div class="small text-secondary">Courses, ménage, anniversaire : une liste par besoin.</div></div></div>
            <div class="col-md-4"><div class="feature-card h-100"><div class="fs-3 mb-2">👥</div><div class="fw-bold">Partage en direct</div><div class="small text-secondary">Toi et ton coloc voyez les changements en temps réel.</div></div></div>
            <div class="col-md-4"><div class="feature-card h-100"><div class="fs-3 mb-2">💰</div><div class="fw-bold">Budget maîtrisé</div><div class="small text-secondary">Alerte automatique en cas de dépassement.</div></div></div>
        </div>

        <p class="mt-5 small"><a href="/pricing" class="text-warning fw-bold text-decoration-none">Voir les tarifs</a></p>
        <p class="mt-2 small text-secondary"><a href="/mentions-legales" class="text-secondary text-decoration-underline">Mentions légales</a> · <a href="/confidentialite" class="text-secondary text-decoration-underline">Confidentialité</a></p>
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
        .profile-card { background: #1e293b; border: 1px solid #334155; border-radius: 20px; padding: 25px; max-width: 560px; margin: 0 auto; }
        .form-control, .form-select { background: #0f172a; border: 1px solid #334155; color: white; font-size: 16px !important; }
        .form-control:focus, .form-select:focus { background: #0f172a; color: white; border-color: #3b82f6; box-shadow: none; }
        .list-row { background: #0f172a; border: 1px solid #334155; border-radius: 10px; padding: 8px 10px; }
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

        <div class="mb-4 border-top border-secondary pt-3">
            <h6 class="fw-bold mb-2 text-warning"><i class="fa fa-star me-1"></i> Mon abonnement</h6>
            {% if is_premium %}
                <div class="alert alert-success py-2 small mb-2">Vous êtes membre Premium ✅</div>
                <form action="/billing/portal" method="POST" class="m-0">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <button type="submit" class="btn btn-outline-warning btn-sm w-100">Gérer mon abonnement</button>
                </form>
            {% else %}
                <a href="/pricing" class="btn btn-warning btn-sm w-100 fw-bold">Découvrir Premium</a>
            {% endif %}
        </div>

        <div class="mb-4 border-top border-secondary pt-3">
            <h6 class="fw-bold mb-2 text-info"><i class="fa fa-gift me-1"></i> Parraine tes amis</h6>
            <div class="small text-secondary mb-2">Chaque ami inscrit avec ton lien vous offre 30 jours Premium à toi, et 14 jours à lui. {{ referral_count }} parrainage(s) réussi(s) jusqu'ici.</div>
            <div class="input-group input-group-sm mb-2">
                <input type="text" id="referralLink" class="form-control" value="{{ site_url }}/register?ref={{ referral_code }}" readonly>
                <button type="button" class="btn btn-outline-info" onclick="navigator.clipboard.writeText(document.getElementById('referralLink').value); this.textContent='Copié !'">Copier</button>
            </div>
            <a href="/leaderboard" class="small text-warning text-decoration-none"><i class="fa fa-trophy me-1"></i>Voir le classement des parrains</a>
        </div>

        <div class="mb-4 border-top border-secondary pt-3">
            <h6 class="fw-bold mb-2 text-secondary"><i class="fa fa-download me-1"></i> Mes données</h6>
            <a href="/export_data" class="btn btn-outline-secondary btn-sm w-100">Télécharger toutes mes données (JSON)</a>
        </div>

        <div class="mb-4 border-top border-secondary pt-3">
            <h6 class="fw-bold mb-2 text-info"><i class="fa fa-people-roof me-1"></i> Compte famille</h6>
            {% if household %}
                <div class="small text-secondary mb-2">« {{ household[1] }} » — {{ household_members|length }} membre(s)</div>
                <ul class="list-unstyled small mb-2">
                    {% for m in household_members %}
                    <li>👤 {{ m[0] }} {% if m[1] == 'owner' %}<span class="badge bg-warning text-dark">créateur</span>{% endif %}</li>
                    {% endfor %}
                </ul>
                {% if household[2] == 'owner' %}
                <form action="/household/invite" method="POST" class="input-group input-group-sm mb-2">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <input type="text" name="username" class="form-control" placeholder="Pseudo à inviter" required>
                    <button type="submit" class="btn btn-info fw-bold">Inviter</button>
                </form>
                {% endif %}
                <form action="/household/leave" method="POST" onsubmit="return confirm('{% if household[2] == "owner" %}Dissoudre la famille pour tout le monde ?{% else %}Quitter la famille ?{% endif %}')">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <button type="submit" class="btn btn-outline-danger btn-sm w-100">{% if household[2] == 'owner' %}Dissoudre la famille{% else %}Quitter la famille{% endif %}</button>
                </form>
            {% else %}
                <div class="small text-secondary mb-2">Créez une famille pour partager automatiquement vos listes avec vos proches (plan gratuit : 2 membres max).</div>
                <form action="/household/create" method="POST" class="input-group input-group-sm">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <input type="text" name="nom" class="form-control" placeholder="Nom de la famille" maxlength="60">
                    <button type="submit" class="btn btn-info fw-bold">Créer</button>
                </form>
            {% endif %}
        </div>

        <form action="/update_email" method="POST" class="mb-4 border-top border-secondary pt-3">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <h6 class="fw-bold mb-2 text-info"><i class="fa fa-envelope me-1"></i> Email de récupération</h6>
            <div class="input-group input-group-sm">
                <input type="email" name="email" class="form-control" placeholder="votre@email.com" maxlength="254" value="{{ email or '' }}" required>
                <button type="submit" class="btn btn-outline-info fw-bold">Enregistrer</button>
            </div>
            <div class="small text-secondary mt-1">{% if email %}Sert à récupérer votre compte en cas de mot de passe oublié.{% else %}Aucun email enregistré : le mot de passe oublié ne fonctionnera pas tant que vous n'en ajoutez pas un.{% endif %}</div>
        </form>

        <div class="mb-4 border-top border-secondary pt-3">
            <h6 class="fw-bold mb-2 text-warning"><i class="fa fa-list me-1"></i> Mes listes</h6>
            <div class="d-flex flex-column gap-2 mb-2">
                {% for l in my_lists %}
                <div class="list-row d-flex justify-content-between align-items-center gap-2">
                    <form action="/lists/rename/{{ l[0] }}" method="POST" class="d-flex gap-1 flex-grow-1 m-0">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <input type="text" name="nom" value="{{ l[1] }}" maxlength="60" class="form-control form-control-sm">
                        <button type="submit" class="btn btn-sm btn-outline-info">OK</button>
                    </form>
                    {% if household %}
                    <form action="/lists/toggle_household/{{ l[0] }}" method="POST" class="m-0">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <button type="submit" class="btn btn-sm btn-outline-warning" title="Partager avec la famille"><i class="fa fa-people-roof"></i></button>
                    </form>
                    {% endif %}
                    <form action="/lists/delete/{{ l[0] }}" method="POST" class="m-0" onsubmit="return confirm('Supprimer la liste « {{ l[1] }} » et tous ses articles ?')">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <button type="submit" class="btn btn-sm btn-outline-danger" {% if my_lists|length <= 1 %}disabled title="Il vous faut au moins une liste"{% endif %}><i class="fa fa-trash"></i></button>
                    </form>
                </div>
                {% endfor %}
            </div>
            <form action="/lists/create" method="POST" class="input-group input-group-sm">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <input type="text" name="nom" class="form-control" placeholder="Nom de la nouvelle liste (ex: Ménage)" maxlength="60" required>
                <button type="submit" class="btn btn-warning fw-bold">+ Créer</button>
            </form>
        </div>

        <form action="/share_list" method="POST" class="mb-4 border-top border-secondary pt-3">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <h6 class="fw-bold mb-2 text-info"><i class="fa fa-users me-1"></i> Partager une liste (Mode Coloc)</h6>
            <div class="d-flex flex-column gap-2">
                <select name="list_id" class="form-select form-select-sm">
                    {% for l in my_lists %}
                        <option value="{{ l[0] }}">{{ l[1] }}</option>
                    {% endfor %}
                </select>
                <div class="input-group input-group-sm">
                    <input type="text" name="share_username" class="form-control" placeholder="Pseudo de l'utilisateur..." maxlength="20" required>
                    <button type="submit" class="btn btn-info fw-bold">Partager</button>
                </div>
            </div>
            {% if shared_users %}
                <div class="small text-secondary mt-2">Partagées avec :
                {% for u in shared_users %}
                    <span class="badge bg-secondary">{{ u[0] }} → {{ u[1] }}</span>
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
                <form action="/reset_all" method="POST" onsubmit="return confirm('Attention ! Cela va tout supprimer (listes, articles, historique, modèles). Continuer ?')">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <button type="submit" class="btn btn-outline-danger btn-sm w-100">Réinitialiser toutes mes données</button>
                </form>
            </div>
        </div>
    </div>
</body>
</html>
"""

PRICING_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <title>Tarifs - SmartPanier</title>
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; padding: 40px 15px; }
        .plan { background: #1e293b; border: 1px solid #334155; border-radius: 20px; padding: 30px; max-width: 340px; }
        .plan.premium { border: 2px solid #f59e0b; }
        .price { font-size: 2.2rem; font-weight: 900; color: #f59e0b; }
        .plans-wrap { max-width: 760px; margin: 0 auto; }
        ul.feat { list-style: none; padding: 0; margin: 20px 0; }
        ul.feat li { padding: 6px 0; }
        ul.feat li i { color: #10b981; margin-right: 8px; }
    </style>
</head>
<body>
    <div class="text-center mb-5">
        <a href="/" class="btn btn-sm btn-outline-secondary mb-3"><i class="fa fa-arrow-left"></i> Retour</a>
        <h2 class="fw-bold">Des tarifs simples</h2>
        <p class="text-secondary">Commence gratuitement, passe Premium quand tu en as besoin.</p>
    </div>
    <div class="plans-wrap d-flex flex-wrap gap-4 justify-content-center">
        <div class="plan">
            <h5 class="fw-bold">Gratuit</h5>
            <div class="price">0 FCFA</div>
            <ul class="feat">
                <li><i class="fa fa-check"></i>1 liste de courses</li>
                <li><i class="fa fa-check"></i>Ajout/édition/coche illimités</li>
                <li><i class="fa fa-check"></i>Partage avec un proche</li>
                <li><i class="fa fa-check"></i>Historique d'achats</li>
            </ul>
            {% if not logged_in %}<a href="/register" class="btn btn-outline-light w-100 fw-bold">Créer un compte</a>{% endif %}
        </div>
        <div class="plan premium">
            <h5 class="fw-bold text-warning">Premium <i class="fa fa-star"></i></h5>
            <div class="price">{{ price_label }}</div>
            <ul class="feat">
                <li><i class="fa fa-check"></i>Listes illimitées</li>
                <li><i class="fa fa-check"></i>Export Excel/CSV</li>
                <li><i class="fa fa-check"></i>Scanner code-barres (reconnaissance produit)</li>
                <li><i class="fa fa-check"></i>Tout ce qui est dans Gratuit</li>
            </ul>
            {% if is_premium %}
                <div class="alert alert-success py-2 small text-center mb-2">Vous êtes déjà Premium ✅</div>
                <form action="/billing/portal" method="POST" class="m-0">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <button type="submit" class="btn btn-outline-warning w-100 fw-bold">Gérer mon abonnement</button>
                </form>
            {% elif logged_in %}
                <form action="/billing/checkout" method="POST" class="m-0">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <button type="submit" class="btn btn-warning w-100 fw-bold" {% if not stripe_enabled %}disabled{% endif %}>
                        {% if stripe_enabled %}Passer Premium{% else %}Bientôt disponible{% endif %}
                    </button>
                </form>
                <div class="small text-secondary mt-2 text-center">Astuce : parraine un ami pour gagner 30 jours Premium gratuits, depuis ton profil.</div>
            {% else %}
                <a href="/register" class="btn btn-warning w-100 fw-bold">Créer un compte</a>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""

ADMIN_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <title>Admin - SmartPanier</title>
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; padding: 30px 15px; }
        .stat-card { background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 20px; text-align: center; }
        .stat-num { font-size: 2rem; font-weight: 900; color: #f59e0b; }
        table { color: #f8fafc; }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="btn btn-sm btn-outline-secondary mb-3"><i class="fa fa-arrow-left"></i> Retour</a>
        <h3 class="fw-bold mb-4">📊 Tableau de bord</h3>
        {% with m = get_flashed_messages() %}
            {% if m %}<div class="alert alert-info py-2 small mb-3">{{ m[0] }}</div>{% endif %}
        {% endwith %}
        <div class="row g-3 mb-4">
            <div class="col-6 col-md-3"><div class="stat-card"><div class="stat-num">{{ total_users }}</div><div class="small text-secondary">Utilisateurs</div></div></div>
            <div class="col-6 col-md-3"><div class="stat-card"><div class="stat-num">{{ total_premium }}</div><div class="small text-secondary">Premium actifs</div></div></div>
            <div class="col-6 col-md-3"><div class="stat-card"><div class="stat-num">{{ signups_7j }}</div><div class="small text-secondary">Inscriptions (7j)</div></div></div>
            <div class="col-6 col-md-3"><div class="stat-card"><div class="stat-num">{{ total_referrals }}</div><div class="small text-secondary">Parrainages réussis</div></div></div>
            <div class="col-6 col-md-3"><div class="stat-card"><div class="stat-num">{{ total_lists }}</div><div class="small text-secondary">Listes créées</div></div></div>
            <div class="col-6 col-md-3"><div class="stat-card"><div class="stat-num">{{ total_items }}</div><div class="small text-secondary">Articles ajoutés</div></div></div>
            <div class="col-6 col-md-3"><div class="stat-card"><div class="stat-num">{{ fmt(total_premium * 2000) }}</div><div class="small text-secondary">Revenu mensuel estimé (FCFA)</div></div></div>
        </div>

        <div class="mb-4">
            <h5 class="fw-bold mb-2">☁️ Sauvegarde cloud</h5>
            {% if cloud_backup_enabled %}
                <form action="/admin/backup" method="POST" class="m-0">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <button type="submit" class="btn btn-outline-info btn-sm">Sauvegarder maintenant</button>
                </form>
            {% else %}
                <div class="small text-secondary">Non configurée (variables S3_BUCKET / S3_ACCESS_KEY / S3_SECRET_KEY manquantes).</div>
            {% endif %}
        </div>

        <h5 class="fw-bold mb-3">Évènements récents</h5>
        <table class="table table-dark table-sm">
            <thead><tr><th>Type</th><th>Utilisateur</th><th>Date</th></tr></thead>
            <tbody>
            {% for e in recent_events %}
                <tr><td>{{ e[0] }}</td><td>{{ e[1] or '—' }}</td><td>{{ e[2] }}</td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
"""

LEADERBOARD_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <title>Classement des parrains - SmartPanier</title>
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; padding: 30px 15px; }
        .board { background: #1e293b; border: 1px solid #334155; border-radius: 20px; padding: 25px; max-width: 480px; margin: 0 auto; }
        .row-rank { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid #334155; }
        .rank-badge { width: 28px; height: 28px; border-radius: 50%; background: #334155; display: inline-flex; align-items: center; justify-content: center; font-weight: bold; margin-right: 10px; }
        .rank-badge.gold { background: #f59e0b; color: #0f172a; }
        .rank-badge.silver { background: #cbd5e1; color: #0f172a; }
        .rank-badge.bronze { background: #b45309; color: white; }
    </style>
</head>
<body>
    <div class="board">
        <a href="/profile" class="btn btn-sm btn-outline-secondary mb-3"><i class="fa fa-arrow-left"></i> Retour</a>
        <h4 class="fw-bold mb-1">🏆 Classement des parrains</h4>
        <p class="small text-secondary mb-4">Vous avez parrainé {{ my_count }} personne(s).</p>
        {% for u, nb in top %}
        <div class="row-rank">
            <span>
                <span class="rank-badge {% if loop.index==1 %}gold{% elif loop.index==2 %}silver{% elif loop.index==3 %}bronze{% endif %}">{{ loop.index }}</span>
                {{ u }}{% if u == username %} (vous){% endif %}
            </span>
            <span class="fw-bold text-warning">{{ nb }}</span>
        </div>
        {% else %}
        <p class="text-secondary text-center">Personne n'a encore parrainé quelqu'un — soyez le premier !</p>
        {% endfor %}
    </div>
</body>
</html>
"""

STATS_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    """ + BASE_HEAD + """
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <title>Statistiques - SmartPanier</title>
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; padding: 30px 15px; }
        .card { background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 20px; margin-bottom: 16px; max-width: 640px; margin-left: auto; margin-right: auto; }
        .tip { background: #0f172a; border-left: 3px solid #f59e0b; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; font-size: 0.9rem; }
        .item-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #334155; font-size: 0.9rem; }
    </style>
</head>
<body>
    <div style="max-width:640px;margin:0 auto;">
        <a href="/" class="btn btn-sm btn-outline-secondary mb-3"><i class="fa fa-arrow-left"></i> Retour</a>
        <h3 class="fw-bold mb-4">📊 Mes statistiques</h3>
    </div>

    <div class="card">
        <h6 class="fw-bold mb-3">💡 Conseils personnalisés</h6>
        {% for t in tips %}<div class="tip">{{ t }}</div>{% endfor %}
    </div>

    {% if monthly_data.labels %}
    <div class="card">
        <h6 class="fw-bold mb-3">Évolution mensuelle</h6>
        <canvas id="monthlyStatsChart" height="120"></canvas>
    </div>
    {% endif %}

    {% if top_items %}
    <div class="card">
        <h6 class="fw-bold mb-3">Vos articles les plus achetés</h6>
        {% for it in top_items %}
        <div class="item-row"><span>{{ it[0] }}</span><span class="text-secondary">{{ it[1] }}x · {{ fmt(it[2] or 0) }}</span></div>
        {% endfor %}
    </div>
    {% endif %}

    {% if not has_data %}
    <div class="card text-center text-secondary">Pas encore assez de données. Faites quelques courses et clôturez vos listes pour voir vos statistiques ici !</div>
    {% endif %}

    <script>
        {% if monthly_data.labels %}
        new Chart(document.getElementById('monthlyStatsChart').getContext('2d'), {
            type: 'bar',
            data: { labels: {{ monthly_data.labels | tojson }}, datasets: [{ label: 'Dépenses', data: {{ monthly_data.totals | tojson }}, backgroundColor: '#f59e0b' }] },
            options: { plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#94a3b8' } }, y: { ticks: { color: '#94a3b8' } } } }
        });
        {% endif %}
    </script>
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
    {% if onesignal_app_id %}
    <script src="https://cdn.onesignal.com/sdk/OneSignalSDK.page.js" defer></script>
    {% endif %}
    {% if realtime_enabled %}
    <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
    {% endif %}
    {% if plausible_domain %}<script defer data-domain="{{ plausible_domain }}" src="https://plausible.io/js/script.js"></script>{% endif %}
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
        .list-switcher { max-width: 220px; }
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
                {% if is_premium %}
                <span class="badge bg-warning text-dark fw-bold"><i class="fa fa-star me-1"></i>Premium</span>
                {% else %}
                <a href="/pricing" class="badge bg-secondary text-decoration-none">Passer Premium</a>
                {% endif %}
                <a href="/profile" class="btn btn-sm btn-outline-warning"><i class="fa fa-user-cog"></i> Profil</a>
            </div>

            <div class="d-flex gap-2 align-items-center flex-wrap">
                <button id="pwaInstallBtn" class="btn btn-sm btn-warning fw-bold d-none"><i class="fa fa-download me-1"></i> Installer</button>
                <button onclick="requestNotificationPermission()" class="btn btn-sm btn-outline-info" title="Activer les rappels"><i class="fa fa-bell"></i></button>

                <form action="/set_devise" method="POST" class="m-0">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <select name="devise" onchange="this.form.submit()" class="form-select form-select-sm" style="width: auto;">
                        {% for d in devises %}
                            <option value="{{d}}" {% if d == devise %}selected{% endif %}>{{d}}</option>
                        {% endfor %}
                    </select>
                </form>

                <button onclick="toggleTheme()" class="btn btn-sm btn-outline-secondary" id="themeBtn"><i class="fa fa-moon"></i></button>
                <a href="/stats" class="btn btn-sm btn-outline-info"><i class="fa fa-chart-line"></i> Stats</a>
                <a href="/export_csv" class="btn btn-sm btn-outline-success"><i class="fa fa-file-excel"></i> Excel</a>
                <button onclick="invite()" class="btn btn-sm btn-outline-info"><i class="fa fa-gift"></i> Inviter</button>
                <a href="/logout" class="btn btn-sm btn-outline-danger"><i class="fa fa-sign-out-alt"></i></a>
            </div>
        </div>

        <div class="d-flex align-items-center gap-2 mb-3 no-print flex-wrap">
            <i class="fa fa-list text-warning"></i>
            <form action="/lists/switch" method="POST" class="m-0" id="listSwitchForm">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <select name="list_id" onchange="this.form.submit()" class="form-select form-select-sm list-switcher">
                    {% if my_lists %}
                    <optgroup label="🏠 Mes listes">
                        {% for l in my_lists %}
                        <option value="{{ l[0] }}" {% if l[0] == current_list_id %}selected{% endif %}>{{ l[1] }}</option>
                        {% endfor %}
                    </optgroup>
                    {% endif %}
                    {% if shared_lists %}
                    <optgroup label="👥 Partagées avec moi">
                        {% for l in shared_lists %}
                        <option value="{{ l[0] }}" {% if l[0] == current_list_id %}selected{% endif %}>{{ l[1] }} ({{ l[2] }})</option>
                        {% endfor %}
                    </optgroup>
                    {% endif %}
                </select>
            </form>
            <button class="btn btn-sm btn-outline-warning" data-bs-toggle="modal" data-bs-target="#newListModal" title="Nouvelle liste"><i class="fa fa-plus"></i></button>
            <span class="small text-secondary">{{ liste|length }} article(s)</span>
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
                        <div class="d-flex gap-1">
                            <button type="button" id="voiceBtn" onclick="startVoiceInput()" class="btn btn-sm btn-outline-info" title="Ajouter à la voix">
                                <i class="fa fa-microphone"></i>
                            </button>
                            {% if ai_enabled %}
                            <button type="button" class="btn btn-sm btn-outline-warning" data-bs-toggle="modal" data-bs-target="#photoAiModal" title="Reconnaître un produit par photo">
                                <i class="fa fa-camera"></i>
                            </button>
                            {% endif %}
                            <button class="btn btn-sm btn-outline-warning" data-bs-toggle="modal" data-bs-target="#scannerModal">
                                <i class="fa fa-barcode me-1"></i> Scan
                            </button>
                        </div>
                    </div>

                    <form id="addForm">
                        <div id="suggestionsRow" class="d-flex flex-wrap gap-1 mb-2"></div>
                        <div class="input-group mb-2">
                            <input type="text" name="nom" id="itemNomInput" oninput="checkPriceMemory(this.value)" class="form-control" placeholder="Nom du produit (ex: Pain)" maxlength="100" required autocomplete="off">
                            <button type="button" class="btn btn-outline-warning" id="voiceBtn" onclick="startVoiceInput()" title="Ajouter par la voix"><i class="fa fa-microphone"></i></button>
                        </div>
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
                            <input type="text" name="title" class="form-control" placeholder="Nom du modèle..." maxlength="80" required>
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

                    <button onclick="scheduleReminder(30, {{ liste|length }})" class="btn btn-sm btn-outline-warning w-100 mb-2">⏰ Rappel dans 30 min</button>
                    {% if onesignal_app_id %}
                    <div class="small text-center text-success mb-2"><i class="fa fa-circle-check me-1"></i>Push réel activé</div>
                    {% else %}
                    <div class="small text-center text-secondary mb-2">Rappels locaux (onglet ouvert)</div>
                    {% endif %}

                    <hr class="border-secondary my-2">
                    <div style="max-width: 250px; margin: 0 auto;">
                        <canvas id="categoryChart"></canvas>
                    </div>
                </div>

                {% if ai_enabled %}
                <div class="card">
                    <h6 class="fw-bold mb-2"><i class="fa fa-robot text-warning me-1"></i> Assistant IA</h6>
                    <div id="aiChatLog" class="small mb-2" style="max-height: 160px; overflow-y: auto;"></div>
                    <div class="input-group input-group-sm mb-2">
                        <input type="text" id="aiQuestionInput" class="form-control" placeholder="Une question sur tes courses ?">
                        <button type="button" onclick="askAiAssistant()" class="btn btn-warning fw-bold">Demander</button>
                    </div>
                    <div class="d-flex gap-2">
                        {% if is_premium %}
                        <button type="button" class="btn btn-sm btn-outline-info flex-fill" data-bs-toggle="modal" data-bs-target="#receiptAiModal"><i class="fa fa-receipt me-1"></i>Scanner un ticket</button>
                        <button type="button" class="btn btn-sm btn-outline-success flex-fill" data-bs-toggle="modal" data-bs-target="#menuAiModal"><i class="fa fa-utensils me-1"></i>Générer un menu</button>
                        {% else %}
                        <a href="/pricing" class="btn btn-sm btn-outline-secondary flex-fill">🔒 Scan ticket & menu (Premium)</a>
                        {% endif %}
                    </div>
                </div>
                {% endif %}
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
                    <div class="text-center text-secondary py-4" id="emptyState">Cette liste est vide pour l'instant ! 🛒</div>
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
                                <small class="text-secondary d-block">{{ h[3] }} {% if h[4] %}· {{ h[4] }}{% endif %}</small>
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

        <div class="text-center small text-secondary no-print py-3">
            <a href="/mentions-legales" class="text-secondary text-decoration-underline">Mentions légales</a> · <a href="/confidentialite" class="text-secondary text-decoration-underline">Confidentialité</a>
        </div>
    </div>

    <!-- MODALE NOUVELLE LISTE -->
    <div class="modal fade" id="newListModal" tabindex="-1">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title fw-bold">📋 Nouvelle liste</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <form action="/lists/create" method="POST">
                    <div class="modal-body">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <input type="text" name="nom" class="form-control" placeholder="Ex: Ménage, Anniversaire, Bureau..." maxlength="60" required autofocus>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Annuler</button>
                        <button type="submit" class="btn btn-warning btn-sm fw-bold">Créer</button>
                    </div>
                </form>
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

    {% if ai_enabled %}
    <!-- MODALE RECONNAISSANCE PHOTO -->
    <div class="modal fade" id="photoAiModal" tabindex="-1">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title fw-bold"><i class="fa fa-camera me-2"></i>Photo d'un produit</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body text-center">
                    <input type="file" accept="image/*" capture="environment" id="photoAiInput" class="form-control mb-3" onchange="recognizePhoto(this.files[0])">
                    <div id="photoAiStatus" class="small text-secondary"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- MODALE SCAN DE TICKET -->
    <div class="modal fade" id="receiptAiModal" tabindex="-1">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title fw-bold"><i class="fa fa-receipt me-2"></i>Scanner un ticket de caisse</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body text-center">
                    <input type="file" accept="image/*" capture="environment" id="receiptAiInput" class="form-control mb-3" onchange="scanReceipt(this.files[0])">
                    <div id="receiptAiStatus" class="small text-secondary"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- MODALE GÉNÉRATEUR DE MENU -->
    <div class="modal fade" id="menuAiModal" tabindex="-1">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title fw-bold"><i class="fa fa-utensils me-2"></i>Générer un menu</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <div class="row g-2 mb-3">
                        <div class="col-4"><label class="small text-secondary">Jours</label><input type="number" id="menuJours" class="form-control" value="3" min="1" max="14"></div>
                        <div class="col-4"><label class="small text-secondary">Personnes</label><input type="number" id="menuPersonnes" class="form-control" value="4" min="1" max="20"></div>
                        <div class="col-4"><label class="small text-secondary">Budget</label><input type="number" id="menuBudget" class="form-control" value="15000" min="0"></div>
                    </div>
                    <button type="button" class="btn btn-warning w-100 fw-bold mb-3" onclick="generateMenu()">Générer</button>
                    <div id="menuAiResult"></div>
                </div>
            </div>
        </div>
    </div>
    {% endif %}

    <script>
        const CSRF_TOKEN = document.querySelector('meta[name=csrf-token]').content;
        const DEVISE = {{ devise|tojson }};
        const ONESIGNAL_APP_ID = {{ onesignal_app_id|tojson }};
        const REALTIME_ENABLED = {{ 'true' if realtime_enabled else 'false' }};
        const CURRENT_LIST_ID = {{ current_list_id }};
        const CLIENT_ID = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : ('c' + Math.random().toString(36).slice(2));
        let currentCatFilter = 'ALL';
        let html5QrcodeScanner;

        // ---------- TEMPS RÉEL (collaboration live sur listes partagées) ----------
        if (REALTIME_ENABLED && window.io) {
            const socket = io();
            socket.on('connect', () => socket.emit('join', { list_id: CURRENT_LIST_ID }));

            socket.on('item_added', (payload) => {
                if (payload.client_id === CLIENT_ID) return;
                addItemToDOM(payload.item, true);
                updateTotal(payload.total);
                showToast((payload.by || 'Un membre') + ' a ajouté un article.', 'info');
            });
            socket.on('item_updated', (payload) => {
                if (payload.client_id === CLIENT_ID) return;
                const row = document.querySelector('.item-row[data-id="' + payload.item.id + '"]');
                if (row) {
                    row.dataset.nom = payload.item.nom; row.dataset.qte = payload.item.qte;
                    row.dataset.prix = payload.item.prix; row.dataset.cat = payload.item.cat;
                    row.setAttribute('data-cat', payload.item.cat);
                    row.querySelector('.item-n').innerHTML = payload.item.nom + ' <small class="text-secondary">(x' + payload.item.qte + ')</small>';
                    row.querySelector('.badge').textContent = payload.item.cat;
                    row.querySelector('.badge').style.background = payload.item.color;
                    row.querySelector('.item-total').textContent = fmtMoney(payload.item.prix * payload.item.qte) + ' ' + DEVISE;
                }
                updateTotal(payload.total);
            });
            socket.on('item_checked', (payload) => {
                if (payload.client_id === CLIENT_ID) return;
                const row = document.querySelector('.item-row[data-id="' + payload.item_id + '"]');
                if (row) row.classList.toggle('done');
                updateTotal(payload.total);
            });
            socket.on('item_deleted', (payload) => {
                if (payload.client_id === CLIENT_ID) return;
                const row = document.querySelector('.item-row[data-id="' + payload.item_id + '"]');
                if (row) { row.classList.add('removing'); setTimeout(() => { row.remove(); checkEmptyState(); }, 180); }
                updateTotal(payload.total);
                showToast((payload.by || 'Un membre') + ' a supprimé un article.', 'info');
            });
        }

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
            if (!formData.has('client_id')) formData.append('client_id', CLIENT_ID);
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
                loadSuggestions();
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
            fd.append('cat', snapshot.cat || '✨ Autre');
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
                div.textContent = 'Cette liste est vide pour l\\'instant ! 🛒';
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
            html5QrcodeScanner.render(async (decodedText) => {
                document.getElementById('itemNomInput').value = "Produit " + decodedText;
                stopScanner();
                let modal = bootstrap.Modal.getInstance(document.getElementById('scannerModal'));
                modal.hide();
                try {
                    const resp = await fetch('/api/product/' + encodeURIComponent(decodedText));
                    const data = await resp.json();
                    if (data.found) {
                        document.getElementById('itemNomInput').value = data.nom;
                        if (data.cat) document.getElementById('itemCatInput').value = data.cat;
                        showToast('Produit reconnu : ' + data.nom, 'success');
                    } else {
                        showToast('Produit non reconnu, complétez le nom manuellement.', 'info');
                    }
                } catch (e) {}
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

        // ---------- SAISIE VOCALE (gratuite, native au navigateur) ----------
        function startVoiceInput() {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {
                showToast("La reconnaissance vocale n'est pas prise en charge par ce navigateur.", "error");
                return;
            }
            const btn = document.getElementById('voiceBtn');
            const recognition = new SpeechRecognition();
            recognition.lang = 'fr-FR';
            recognition.interimResults = false;
            recognition.maxAlternatives = 1;

            btn.classList.add('btn-danger');
            btn.classList.remove('btn-outline-warning');
            btn.innerHTML = '<i class="fa fa-circle-notch fa-spin"></i>';

            recognition.onresult = (event) => {
                const transcript = event.results[0][0].transcript;
                document.getElementById('itemNomInput').value = transcript.charAt(0).toUpperCase() + transcript.slice(1);
                checkPriceMemory(transcript);
                showToast('Entendu : "' + transcript + '"', 'success');
            };
            recognition.onerror = () => {
                showToast("Je n'ai rien entendu, réessayez.", "error");
            };
            recognition.onend = () => {
                btn.classList.remove('btn-danger');
                btn.classList.add('btn-outline-warning');
                btn.innerHTML = '<i class="fa fa-microphone"></i>';
            };
            recognition.start();
        }

        // ---------- SUGGESTIONS INTELLIGENTES ----------
        async function loadSuggestions() {
            try {
                const resp = await fetch('/api/suggestions');
                const data = await resp.json();
                const row = document.getElementById('suggestionsRow');
                row.innerHTML = '';
                (data.suggestions || []).forEach(s => {
                    const chip = document.createElement('button');
                    chip.type = 'button';
                    chip.className = 'btn btn-sm btn-outline-warning rounded-pill py-1 px-2';
                    chip.style.fontSize = '0.78rem';
                    chip.innerHTML = '<i class="fa fa-plus me-1"></i>' + s.nom;
                    chip.onclick = async () => {
                        const fd = new FormData();
                        fd.append('nom', s.nom); fd.append('qte', '1'); fd.append('prix', s.prix || 0); fd.append('cat', s.cat || '✨ Autre');
                        try {
                            const data2 = await apiFetch('/api/add', fd);
                            addItemToDOM(data2.item);
                            updateTotal(data2.total);
                            showToast('Article ajouté.', 'success');
                            loadSuggestions();
                        } catch (e) {}
                    };
                    row.appendChild(chip);
                });
            } catch (e) {}
        }
        document.addEventListener('DOMContentLoaded', loadSuggestions);

        // ---------- SAISIE VOCALE ----------
        function startVoiceInput() {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {
                showToast("La reconnaissance vocale n'est pas supportée par ce navigateur.", "error");
                return;
            }
            const recognition = new SpeechRecognition();
            recognition.lang = 'fr-FR';
            recognition.interimResults = false;
            const btn = document.getElementById('voiceBtn');
            btn.classList.add('btn-danger');
            btn.classList.remove('btn-outline-info');
            recognition.onresult = (event) => {
                const text = event.results[0][0].transcript;
                document.getElementById('itemNomInput').value = text;
                showToast('Entendu : "' + text + '"', 'success');
            };
            recognition.onerror = () => showToast("Je n'ai pas compris, réessayez.", "error");
            recognition.onend = () => { btn.classList.remove('btn-danger'); btn.classList.add('btn-outline-info'); };
            recognition.start();
        }

        // ---------- ASSISTANT IA ----------
        async function askAiAssistant() {
            const input = document.getElementById('aiQuestionInput');
            const question = input.value.trim();
            if (!question) return;
            const log = document.getElementById('aiChatLog');
            log.innerHTML += '<div class="mb-1"><b>Vous :</b> ' + question + '</div>';
            input.value = '';
            log.scrollTop = log.scrollHeight;
            try {
                const fd = new FormData();
                fd.append('question', question);
                const data = await apiFetch('/api/ai/assistant', fd);
                log.innerHTML += '<div class="mb-2 text-warning"><b>🤖 :</b> ' + data.answer + '</div>';
            } catch (e) {
                log.innerHTML += '<div class="mb-2 text-danger">L\\'assistant est indisponible pour le moment.</div>';
            }
            log.scrollTop = log.scrollHeight;
        }
        document.getElementById('aiQuestionInput')?.addEventListener('keypress', (e) => { if (e.key === 'Enter') askAiAssistant(); });

        // ---------- RECONNAISSANCE PHOTO ----------
        async function recognizePhoto(file) {
            if (!file) return;
            const status = document.getElementById('photoAiStatus');
            status.textContent = 'Analyse en cours...';
            const fd = new FormData();
            fd.append('photo', file);
            fd.append('client_id', CLIENT_ID);
            try {
                const resp = await fetch('/api/ai/recognize_photo', { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN }, body: fd });
                const data = await resp.json();
                if (data.ok) {
                    document.getElementById('itemNomInput').value = data.nom;
                    document.getElementById('itemCatInput').value = data.cat;
                    status.textContent = 'Reconnu : ' + data.nom;
                    bootstrap.Modal.getInstance(document.getElementById('photoAiModal')).hide();
                    showToast('Produit reconnu : ' + data.nom, 'success');
                } else {
                    status.textContent = data.error || 'Produit non reconnu.';
                }
            } catch (e) {
                status.textContent = "Erreur lors de l'analyse.";
            }
        }

        // ---------- SCAN DE TICKET ----------
        async function scanReceipt(file) {
            if (!file) return;
            const status = document.getElementById('receiptAiStatus');
            status.textContent = 'Lecture du ticket en cours...';
            const fd = new FormData();
            fd.append('photo', file);
            fd.append('client_id', CLIENT_ID);
            try {
                const resp = await fetch('/api/ai/scan_receipt', { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN }, body: fd });
                const data = await resp.json();
                if (data.ok && data.added.length) {
                    status.textContent = data.added.length + ' article(s) ajouté(s) : ' + data.added.join(', ');
                    updateTotal(data.total);
                    showToast(data.added.length + ' articles ajoutés depuis le ticket !', 'success');
                    setTimeout(() => window.location.reload(), 1500);
                } else {
                    status.textContent = data.error || 'Ticket illisible.';
                }
            } catch (e) {
                status.textContent = "Erreur lors de l'analyse.";
            }
        }

        // ---------- GÉNÉRATEUR DE MENU ----------
        async function generateMenu() {
            const resultDiv = document.getElementById('menuAiResult');
            resultDiv.innerHTML = '<div class="text-secondary small">Génération en cours...</div>';
            const fd = new FormData();
            fd.append('jours', document.getElementById('menuJours').value);
            fd.append('personnes', document.getElementById('menuPersonnes').value);
            fd.append('budget', document.getElementById('menuBudget').value);
            try {
                const data = await apiFetch('/api/ai/generate_menu', fd);
                let html = '';
                (data.menu || []).forEach(j => { html += '<div class="mb-1"><b>' + j.jour + ':</b> ' + j.plats + '</div>'; });
                html += '<hr class="border-secondary"><div class="small fw-bold mb-2">Ingrédients à ajouter :</div>';
                (data.ingredients || []).forEach((ing, i) => {
                    html += '<div class="form-check"><input class="form-check-input" type="checkbox" checked id="ing' + i + '"><label class="form-check-label small" for="ing' + i + '">' + ing.nom + ' (' + ing.prix + ')</label></div>';
                });
                html += '<button type="button" class="btn btn-warning btn-sm w-100 mt-3" onclick=\\'addMenuIngredients(' + JSON.stringify(data.ingredients || []).replace(/'/g, "&#39;") + ')\\'>Ajouter les ingrédients cochés à ma liste</button>';
                resultDiv.innerHTML = html;
            } catch (e) {
                resultDiv.innerHTML = '<div class="text-danger small">Impossible de générer un menu pour le moment.</div>';
            }
        }

        async function addMenuIngredients(ingredients) {
            let count = 0;
            for (let i = 0; i < ingredients.length; i++) {
                const checkbox = document.getElementById('ing' + i);
                if (checkbox && !checkbox.checked) continue;
                const ing = ingredients[i];
                const fd = new FormData();
                fd.append('nom', ing.nom); fd.append('qte', ing.qte || 1); fd.append('prix', ing.prix || 0); fd.append('cat', ing.cat || '✨ Autre');
                try {
                    const data = await apiFetch('/api/add', fd);
                    addItemToDOM(data.item);
                    updateTotal(data.total);
                    count++;
                } catch (e) {}
            }
            showToast(count + ' ingrédient(s) ajoutés à la liste !', 'success');
            bootstrap.Modal.getInstance(document.getElementById('menuAiModal')).hide();
        }

        // ---------- NOTIFICATIONS (push réel via OneSignal si configuré, sinon local) ----------
        async function savePushId(playerId) {
            if (!playerId) return;
            const fd = new FormData();
            fd.append('player_id', playerId);
            try { await apiFetch('/api/save_push_id', fd); } catch (e) {}
        }

        if (ONESIGNAL_APP_ID) {
            window.OneSignalDeferred = window.OneSignalDeferred || [];
            OneSignalDeferred.push(async function(OneSignal) {
                await OneSignal.init({ appId: ONESIGNAL_APP_ID, allowLocalhostAsSecureOrigin: true });
                OneSignal.User.PushSubscription.addEventListener('change', (e) => {
                    if (e.current && e.current.id) savePushId(e.current.id);
                });
                if (OneSignal.User.PushSubscription.id) savePushId(OneSignal.User.PushSubscription.id);
            });
        }

        function requestNotificationPermission() {
            if (ONESIGNAL_APP_ID && window.OneSignalDeferred) {
                OneSignalDeferred.push(async function(OneSignal) {
                    await OneSignal.Notifications.requestPermission();
                    showToast("Notifications push activées !", "success");
                });
                return;
            }
            if (!("Notification" in window)) { showToast("Votre navigateur ne prend pas en charge les notifications.", "error"); return; }
            Notification.requestPermission().then(permission => {
                if (permission === "granted") {
                    new Notification("🛒 SmartPanier", { body: "Rappels activés avec succès !" });
                }
            });
        }

        function scheduleReminder(minutes, count) {
            // Repli local : marche tant que l'onglet reste ouvert.
            if (Notification.permission === "granted") {
                setTimeout(() => {
                    new Notification("🛒 C'est l'heure des courses !", { body: `Tu as ${count} article(s) dans ta liste.` });
                }, minutes * 60 * 1000);
            }
            // Si le push réel est configuré, on programme aussi côté serveur (marche même app fermée).
            if (ONESIGNAL_APP_ID) {
                const fd = new FormData();
                fd.append('minutes', minutes);
                fd.append('count', count);
                apiFetch('/api/schedule_reminder', fd).catch(() => {});
            }
            showToast(`Rappel programmé dans ${minutes} minutes !`, 'success');
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
            {% with messages = get_flashed_messages() %}
            {% for m in messages %}
            showToast({{ m|tojson }}, 'info');
            {% endfor %}
            {% endwith %}

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


def record_purchase_memory(conn, uid, nom, prix, cat):
    """Mémorise le dernier prix/catégorie d'un article et incrémente sa fréquence d'achat
    (utilisé pour le dernier prix pré-rempli ET pour les suggestions intelligentes)."""
    if prix and prix > 0:
        conn.execute(
            "INSERT INTO price_memory (user_id, nom, dernier_prix, cat, frequency) VALUES (?,?,?,?,1) "
            "ON CONFLICT(user_id, nom) DO UPDATE SET dernier_prix=excluded.dernier_prix, cat=excluded.cat, frequency=frequency+1",
            (uid, nom, prix, cat)
        )
    else:
        conn.execute(
            "INSERT INTO price_memory (user_id, nom, dernier_prix, cat, frequency) VALUES (?,?,?,?,1) "
            "ON CONFLICT(user_id, nom) DO UPDATE SET cat=excluded.cat, frequency=frequency+1",
            (uid, nom, 0, cat)
        )


def generate_savings_tips(uid):
    """Conseils génériques basés sur les données réelles de l'utilisateur (pas d'IA, pas de
    conseil financier personnalisé — juste des observations factuelles sur ses habitudes)."""
    conn = get_db()
    tips = []

    histo = conn.execute(
        "SELECT total FROM historique WHERE user_id=? ORDER BY id DESC LIMIT 2", (uid,)
    ).fetchall()
    if len(histo) == 2 and histo[1][0] > 0:
        variation = (histo[0][0] - histo[1][0]) / histo[1][0] * 100
        if variation > 15:
            tips.append(f"Votre dernière liste clôturée coûte {variation:.0f}% de plus que la précédente — un coup d'œil aux gros postes peut aider.")
        elif variation < -15:
            tips.append(f"Bravo, votre dernière liste clôturée coûte {abs(variation):.0f}% de moins que la précédente !")

    top_items = conn.execute(
        "SELECT nom, frequency, dernier_prix FROM price_memory WHERE user_id=? AND frequency >= 3 ORDER BY frequency DESC LIMIT 3",
        (uid,)
    ).fetchall()
    for nom, freq, prix in top_items:
        tips.append(f"Vous achetez « {nom} » très régulièrement ({freq} fois) — comparer les prix ou acheter en plus grande quantité peut faire baisser le coût unitaire.")

    list_id = session.get("current_list_id")
    if list_id:
        autre_count = conn.execute(
            "SELECT COUNT(*) FROM courses WHERE list_id=? AND cat='✨ Autre'", (list_id,)
        ).fetchone()[0]
        total_count = conn.execute("SELECT COUNT(*) FROM courses WHERE list_id=?", (list_id,)).fetchone()[0]
        if total_count > 0 and autre_count / total_count > 0.4:
            tips.append("Beaucoup d'articles sont classés « Autre » — les catégoriser précisément rendra vos statistiques plus utiles.")

    budget = session.get("budget", 50000.0)
    total = conn.execute(
        "SELECT SUM(prix*qte) FROM courses WHERE list_id=? AND fait=0", (list_id,)
    ).fetchone()[0] or 0 if list_id else 0
    if budget > 0 and total > budget * 0.9:
        tips.append("Vous approchez (ou dépassez) votre budget sur la liste actuelle — c'est le bon moment pour retirer les articles les moins prioritaires.")

    if not tips:
        tips.append("Continuez à utiliser SmartPanier régulièrement : plus vous avez d'historique, plus ces conseils deviennent pertinents.")

    return tips[:4]


def generate_free_menu(budget_max, devise="FCFA"):
    """Génère une combinaison de recettes prédéfinies qui rentre dans le budget donné
    (sans IA — sélection gloutonne simple parmi PRESET_RECIPES)."""
    recipe_costs = {}
    for name, items in PRESET_RECIPES.items():
        recipe_costs[name] = sum(i["prix"] * i["qte"] for i in items)

    sorted_recipes = sorted(recipe_costs.items(), key=lambda x: x[1])
    selected, total = [], 0
    for name, cost in sorted_recipes:
        if total + cost <= budget_max:
            selected.append(name)
            total += cost
    return selected, total


app.jinja_env.globals["fmt"] = fmt_number
app.jinja_env.globals["favicon"] = FAVICON_SVG


def maybe_trigger_budget_alert(total):
    """Envoie un vrai push si le budget est dépassé (une seule fois par dépassement)."""
    budget = session.get("budget", 50000.0)
    if total > budget and not session.get("_budget_alert_sent"):
        session["_budget_alert_sent"] = True
        conn = get_db()
        r = conn.execute("SELECT onesignal_player_id FROM users WHERE id=?", (session["uid"],)).fetchone()
        if r and r[0]:
            send_push_notification(
                r[0], "🚨 Budget dépassé",
                f"Vous avez dépassé votre budget de {fmt_number(total - budget)} {session.get('devise', 'FCFA')}."
            )
    elif total <= budget:
        session["_budget_alert_sent"] = False


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


@app.route("/api/suggestions")
@login_required
def api_suggestions():
    """Suggère les articles fréquemment achetés qui ne sont pas déjà dans la liste active."""
    list_id = get_current_list_id()
    conn = get_db()
    current_names = {
        r[0].strip().lower() for r in conn.execute("SELECT nom FROM courses WHERE list_id=?", (list_id,)).fetchall()
    }
    rows = conn.execute(
        "SELECT nom, dernier_prix, cat FROM price_memory WHERE user_id=? AND frequency > 1 "
        "ORDER BY frequency DESC, nom LIMIT 20",
        (session["uid"],)
    ).fetchall()
    suggestions = [
        {"nom": r[0], "prix": r[1] or 0, "cat": r[2] or "✨ Autre"}
        for r in rows if r[0].strip().lower() not in current_names
    ][:6]
    return jsonify({"suggestions": suggestions})


# --- AUTHENTIFICATION ---
@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def register():
    if request.method == "POST":
        u = validate_username(request.form.get("user"))
        p = request.form.get("pass")
        email = validate_email(request.form.get("email"))
        if not u:
            flash("Nom d'utilisateur invalide (3-20 caractères : lettres, chiffres, underscore).")
        elif not email:
            flash("Email invalide.")
        elif not validate_password(p):
            flash("Le mot de passe doit contenir au moins 6 caractères.")
        else:
            with sqlite3.connect(DB_NAME) as conn:
                if conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
                    flash("Un compte existe déjà avec cet email.")
                    return render_template_string(AUTH_HTML, title="Inscription", btn="CRÉER MON COMPTE", google_enabled=GOOGLE_ENABLED, facebook_enabled=FACEBOOK_ENABLED)
                try:
                    ref_code = (request.form.get("ref") or request.args.get("ref") or "").strip().upper()[:8]
                    referrer = conn.execute("SELECT id FROM users WHERE referral_code=?", (ref_code,)).fetchone() if ref_code else None

                    new_referral_code = generate_referral_code(conn)
                    cur = conn.execute(
                        "INSERT INTO users (username, password, email, referral_code, referred_by) VALUES (?,?,?,?,?)",
                        (u, generate_password_hash(p), email, new_referral_code, referrer[0] if referrer else None)
                    )
                    new_uid = cur.lastrowid
                    conn.execute("INSERT INTO lists (user_id, nom) VALUES (?,?)", (new_uid, "Ma liste 🛒"))

                    if referrer:
                        grant_bonus_days(conn, new_uid, 14)
                        grant_bonus_days(conn, referrer[0], 30)

                    conn.commit()
                    track_event("signup", new_uid, {"referred": bool(referrer)})
                    send_email(
                        email, "Bienvenue sur SmartPanier 🛒",
                        f"Bonjour {u},\n\nVotre compte SmartPanier est prêt ! Connectez-vous pour créer votre première liste de courses.\n\n{SITE_URL}/login",
                        html_body=render_email_html(
                            "Bienvenue !",
                            f"Bonjour <b>{u}</b>,<br><br>Votre compte SmartPanier est prêt. Créez votre première liste, invitez vos proches, et gardez votre budget sous contrôle.",
                            button_label="Se connecter", button_url=f"{SITE_URL}/login",
                            footer_note="Vous recevez cet email car un compte a été créé avec cette adresse sur SmartPanier."
                        )
                    )
                    flash("Compte créé avec succès ! Connectez-vous.")
                    return redirect(url_for("login"))
                except sqlite3.IntegrityError:
                    flash("Ce nom d'utilisateur est déjà pris.")
                except sqlite3.Error:
                    log.exception("Erreur lors de l'inscription")
                    flash("Une erreur est survenue, réessayez.")
    return render_template_string(
        AUTH_HTML, title="Inscription", btn="CRÉER MON COMPTE",
        google_enabled=GOOGLE_ENABLED, facebook_enabled=FACEBOOK_ENABLED, ref_code=request.args.get("ref", "")
    )


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if request.method == "POST":
        u = (request.form.get("user") or "").strip()
        p = request.form.get("pass") or ""
        with sqlite3.connect(DB_NAME) as conn:
            r = conn.execute("SELECT id, password FROM users WHERE username=?", (u,)).fetchone()
        if r and r[1] and check_password_hash(r[1], p):
            session.clear()
            session["uid"], session["user"] = r[0], u
            session.permanent = True
            get_current_list_id()
            return redirect(url_for("home"))
        if r and not r[1]:
            flash("Ce compte a été créé via Google ou Facebook : utilisez ce mode de connexion.")
        else:
            flash("Identifiants incorrects.")
    return render_template_string(AUTH_HTML, title="Login", btn="SE CONNECTER", google_enabled=GOOGLE_ENABLED, facebook_enabled=FACEBOOK_ENABLED)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/auth/google")
def auth_google():
    if not GOOGLE_ENABLED:
        flash("La connexion Google n'est pas configurée sur ce site.")
        return redirect(url_for("login"))
    redirect_uri = url_for("auth_google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def auth_google_callback():
    if not GOOGLE_ENABLED:
        return redirect(url_for("login"))
    try:
        token = oauth.google.authorize_access_token()
        userinfo = token.get("userinfo") or oauth.google.userinfo(token=token)
    except Exception:
        log.exception("Échec de l'authentification Google")
        flash("La connexion avec Google a échoué, réessayez.")
        return redirect(url_for("login"))
    email = validate_email(userinfo.get("email"))
    name_hint = userinfo.get("given_name") or userinfo.get("name")
    uid, uname = find_or_create_oauth_user("google", userinfo.get("sub"), email, name_hint)
    session.clear()
    session["uid"], session["user"] = uid, uname
    session.permanent = True
    get_current_list_id()
    return redirect(url_for("home"))


@app.route("/auth/facebook")
def auth_facebook():
    if not FACEBOOK_ENABLED:
        flash("La connexion Facebook n'est pas configurée sur ce site.")
        return redirect(url_for("login"))
    redirect_uri = url_for("auth_facebook_callback", _external=True)
    return oauth.facebook.authorize_redirect(redirect_uri)


@app.route("/auth/facebook/callback")
def auth_facebook_callback():
    if not FACEBOOK_ENABLED:
        return redirect(url_for("login"))
    try:
        token = oauth.facebook.authorize_access_token()
        profile = oauth.facebook.get("me?fields=id,name,email", token=token).json()
    except Exception:
        log.exception("Échec de l'authentification Facebook")
        flash("La connexion avec Facebook a échoué, réessayez.")
        return redirect(url_for("login"))
    email = validate_email(profile.get("email"))
    uid, uname = find_or_create_oauth_user("facebook", profile.get("id"), email, profile.get("name"))
    session.clear()
    session["uid"], session["user"] = uid, uname
    session.permanent = True
    get_current_list_id()
    return redirect(url_for("home"))


@app.route("/forgot_password", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def forgot_password():
    if request.method == "POST":
        email = validate_email(request.form.get("email"))
        if email:
            conn_ = sqlite3.connect(DB_NAME)
            row = conn_.execute("SELECT id, username FROM users WHERE email=?", (email,)).fetchone()
            if row:
                uid, uname = row
                token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                expiry = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
                conn_.execute(
                    "UPDATE users SET reset_token_hash=?, reset_token_expiry=? WHERE id=?",
                    (token_hash, expiry, uid)
                )
                conn_.commit()
                reset_link = f"{SITE_URL}/reset_password/{token}"
                send_email(
                    email, "Réinitialisation de votre mot de passe SmartPanier",
                    f"Bonjour {uname},\n\nUn lien de réinitialisation a été demandé pour votre compte SmartPanier.\n"
                    f"Ce lien est valable 30 minutes :\n{reset_link}\n\n"
                    "Si vous n'êtes pas à l'origine de cette demande, ignorez cet email.",
                    html_body=render_email_html(
                        "Réinitialisation du mot de passe",
                        f"Bonjour <b>{uname}</b>,<br><br>Un lien de réinitialisation a été demandé pour votre compte. Ce lien est valable 30 minutes.",
                        button_label="Réinitialiser mon mot de passe", button_url=reset_link,
                        footer_note="Si vous n'êtes pas à l'origine de cette demande, ignorez simplement cet email."
                    )
                )
            conn_.close()
        # Message générique dans tous les cas (ne pas confirmer si l'email existe ou non).
        flash("Si un compte existe avec cet email, un lien de réinitialisation vient d'être envoyé.")
        return redirect(url_for("login"))
    return render_template_string(FORGOT_PASSWORD_HTML)


@app.route("/reset_password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def reset_password(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn_ = sqlite3.connect(DB_NAME)
    row = conn_.execute(
        "SELECT id, reset_token_expiry FROM users WHERE reset_token_hash=?", (token_hash,)
    ).fetchone()
    valid = False
    if row:
        try:
            expiry = datetime.fromisoformat(row[1])
            valid = datetime.now(timezone.utc) < expiry
        except (TypeError, ValueError):
            valid = False

    if not valid:
        conn_.close()
        flash("Ce lien de réinitialisation est invalide ou expiré. Recommencez la demande.")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_p = request.form.get("new_pass") or ""
        if not validate_password(new_p):
            conn_.close()
            flash("Le mot de passe doit contenir au moins 6 caractères.")
            return render_template_string(RESET_PASSWORD_HTML)
        conn_.execute(
            "UPDATE users SET password=?, reset_token_hash=NULL, reset_token_expiry=NULL WHERE id=?",
            (generate_password_hash(new_p), row[0])
        )
        conn_.commit()
        conn_.close()
        flash("Mot de passe réinitialisé avec succès ! Connectez-vous.")
        return redirect(url_for("login"))

    conn_.close()
    return render_template_string(RESET_PASSWORD_HTML)


@app.route("/update_email", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def update_email():
    email = validate_email(request.form.get("email"))
    if not email:
        flash("Email invalide.")
        return redirect(url_for("profile"))
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email=? AND id!=?", (email, session["uid"])).fetchone()
    if existing:
        flash("Cet email est déjà utilisé par un autre compte.")
    else:
        conn.execute("UPDATE users SET email=? WHERE id=?", (email, session["uid"]))
        conn.commit()
        flash("Email mis à jour.")
    return redirect(url_for("profile"))


@app.route("/mentions-legales")
def mentions_legales():
    content = """
    <p>Ce site est édité à titre personnel dans le cadre du projet SmartPanier.</p>
    <h5>Éditeur</h5>
    <p>[À compléter par l'éditeur du site : nom, statut (particulier/entreprise), contact.]</p>
    <h5>Hébergement</h5>
    <p>Ce site est hébergé par Render Services, Inc.</p>
    <h5>Contact</h5>
    <p>[À compléter : adresse email de contact.]</p>
    <p class="small text-secondary mt-4">Ce texte est un modèle de départ et ne constitue pas un conseil juridique. Faites-le relire/adapter selon votre statut et votre pays avant une mise en production publique.</p>
    """
    return render_template_string(LEGAL_HTML, page_title="Mentions légales", update_date="Juillet 2026", content=content)


@app.route("/confidentialite")
def confidentialite():
    content = """
    <p>SmartPanier collecte uniquement les données nécessaires au fonctionnement du service : nom d'utilisateur, email, mot de passe (chiffré), vos listes de courses et votre historique d'achats.</p>
    <h5>Données collectées</h5>
    <ul>
        <li>Compte : nom d'utilisateur, email, mot de passe (haché, jamais stocké en clair)</li>
        <li>Contenu : articles, listes, historique d'achats, modèles sauvegardés</li>
        <li>Technique : identifiant de notification push (si activé), cookies de session</li>
    </ul>
    <h5>Utilisation</h5>
    <p>Ces données servent uniquement à faire fonctionner l'application (affichage de vos listes, envoi de rappels si activés, récupération de compte). Elles ne sont ni vendues ni partagées avec des tiers à des fins publicitaires.</p>
    <h5>Partage entre utilisateurs</h5>
    <p>Une liste que vous partagez explicitement (mode coloc) est visible par les utilisateurs que vous avez choisis, et par eux seuls.</p>
    <h5>Suppression</h5>
    <p>Vous pouvez supprimer vos données à tout moment depuis votre profil ("Réinitialiser toutes mes données").</p>
    <p class="small text-secondary mt-4">Ce texte est un modèle de départ et ne constitue pas un conseil juridique. Faites-le relire/adapter selon votre statut et votre pays avant une mise en production publique.</p>
    """
    return render_template_string(LEGAL_HTML, page_title="Politique de confidentialité", update_date="Juillet 2026", content=content)


@app.route("/pricing")
def pricing():
    is_premium = get_user_premium_status(session["uid"]) if "uid" in session else False
    return render_template_string(
        PRICING_HTML, stripe_enabled=STRIPE_ENABLED, price_label=PREMIUM_PRICE_LABEL,
        is_premium=is_premium, logged_in=("uid" in session)
    )


@app.route("/billing/checkout", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def billing_checkout():
    if not STRIPE_ENABLED:
        flash("Le paiement en ligne n'est pas encore configuré sur ce site.")
        return redirect(url_for("pricing"))
    conn = get_db()
    row = conn.execute("SELECT email, stripe_customer_id FROM users WHERE id=?", (session["uid"],)).fetchone()
    try:
        customer_id = row[1]
        if not customer_id:
            customer = stripe.Customer.create(email=row[0], metadata={"smartpanier_uid": session["uid"]})
            customer_id = customer.id
            conn.execute("UPDATE users SET stripe_customer_id=? WHERE id=?", (customer_id, session["uid"]))
            conn.commit()
        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            success_url=f"{SITE_URL}/billing/success",
            cancel_url=f"{SITE_URL}/billing/cancel",
            metadata={"smartpanier_uid": session["uid"]},
        )
        return redirect(checkout_session.url, code=303)
    except Exception:
        log.exception("Échec de la création de la session Stripe Checkout")
        flash("Impossible de démarrer le paiement pour le moment, réessayez plus tard.")
        return redirect(url_for("pricing"))


@app.route("/billing/portal", methods=["POST"])
@login_required
def billing_portal():
    if not STRIPE_ENABLED:
        flash("Le paiement en ligne n'est pas encore configuré sur ce site.")
        return redirect(url_for("pricing"))
    conn = get_db()
    row = conn.execute("SELECT stripe_customer_id FROM users WHERE id=?", (session["uid"],)).fetchone()
    if not row or not row[0]:
        flash("Aucun abonnement actif trouvé.")
        return redirect(url_for("pricing"))
    try:
        portal_session = stripe.billing_portal.Session.create(customer=row[0], return_url=f"{SITE_URL}/profile")
        return redirect(portal_session.url, code=303)
    except Exception:
        log.exception("Échec de la création de la session Stripe Portal")
        flash("Impossible d'ouvrir la gestion d'abonnement pour le moment.")
        return redirect(url_for("profile"))


@app.route("/billing/success")
@login_required
def billing_success():
    flash("Merci ! Ton abonnement Premium sera actif dans quelques instants.")
    return redirect(url_for("home"))


@app.route("/billing/cancel")
@login_required
def billing_cancel():
    flash("Paiement annulé, aucune somme n'a été prélevée.")
    return redirect(url_for("pricing"))


@app.route("/billing/webhook", methods=["POST"])
@csrf.exempt
def billing_webhook():
    if not STRIPE_ENABLED:
        return jsonify({"ok": False}), 404
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, Exception):
        log.exception("Webhook Stripe invalide")
        return jsonify({"ok": False}), 400

    conn = get_db()
    event_type = event.get("type")
    data_obj = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        uid = data_obj.get("metadata", {}).get("smartpanier_uid")
        customer_id = data_obj.get("customer")
        if uid:
            conn.execute(
                "UPDATE users SET stripe_customer_id=?, stripe_subscription_status='active' WHERE id=?",
                (customer_id, uid)
            )
            conn.commit()
            track_event("premium_start", int(uid))
    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        customer_id = data_obj.get("customer")
        status = data_obj.get("status", "canceled") if event_type == "customer.subscription.updated" else "canceled"
        conn.execute("UPDATE users SET stripe_subscription_status=? WHERE stripe_customer_id=?", (status, customer_id))
        conn.commit()
    elif event_type == "invoice.payment_failed":
        customer_id = data_obj.get("customer")
        conn.execute("UPDATE users SET stripe_subscription_status='past_due' WHERE stripe_customer_id=?", (customer_id,))
        conn.commit()

    return jsonify({"ok": True})


@app.route("/robots.txt")
def robots_txt():
    return Response(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    pages = ["/", "/pricing", "/mentions-legales", "/confidentialite", "/login", "/register"]
    urls = "".join(f"<url><loc>{SITE_URL}{p}</loc></url>" for p in pages)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(xml, mimetype="application/xml")


@app.route("/admin")
@login_required
def admin_dashboard():
    if not ADMIN_USERNAME or session["user"] != ADMIN_USERNAME:
        return render_template_string(ERROR_HTML, code=404, msg="Cette page n'existe pas."), 404

    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_premium = conn.execute(
        "SELECT COUNT(*) FROM users WHERE stripe_subscription_status='active'"
    ).fetchone()[0]
    total_lists = conn.execute("SELECT COUNT(*) FROM lists").fetchone()[0]
    total_items = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    total_referrals = conn.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL").fetchone()[0]
    signups_7j = conn.execute(
        "SELECT COUNT(*) FROM analytics_events WHERE event_type='signup' AND created_at >= datetime('now', '-7 days')"
    ).fetchone()[0]
    recent_events = conn.execute(
        "SELECT event_type, user_id, created_at FROM analytics_events ORDER BY id DESC LIMIT 25"
    ).fetchall()

    return render_template_string(
        ADMIN_HTML,
        total_users=total_users, total_premium=total_premium, total_lists=total_lists,
        total_items=total_items, total_referrals=total_referrals, signups_7j=signups_7j,
        recent_events=recent_events, price_label=PREMIUM_PRICE_LABEL,
        cloud_backup_enabled=CLOUD_BACKUP_ENABLED,
    )


@app.route("/admin/backup", methods=["POST"])
@login_required
def admin_backup():
    if not ADMIN_USERNAME or session["user"] != ADMIN_USERNAME:
        return render_template_string(ERROR_HTML, code=404, msg="Cette page n'existe pas."), 404
    ok, info = backup_to_cloud()
    if ok:
        flash(f"Sauvegarde cloud effectuée : {info}")
    else:
        flash(f"Échec de la sauvegarde : {info}")
    return redirect(url_for("admin_dashboard"))


@app.route("/export_data")
@login_required
def export_data():
    """Export RGPD : toutes les données de l'utilisateur en un fichier JSON téléchargeable."""
    uid = session["uid"]
    conn = get_db()

    user_row = conn.execute(
        "SELECT username, email, created_at, referral_code FROM users WHERE id=?", (uid,)
    ).fetchone()
    lists_rows = conn.execute("SELECT id, nom, created_at FROM lists WHERE user_id=?", (uid,)).fetchall()
    list_ids = [r[0] for r in lists_rows]
    courses_rows = []
    if list_ids:
        placeholders = ",".join("?" * len(list_ids))
        courses_rows = conn.execute(
            f"SELECT nom, prix, qte, fait, cat, date_ajout, list_id FROM courses WHERE list_id IN ({placeholders})",
            list_ids
        ).fetchall()
    histo_rows = conn.execute(
        "SELECT total, nb_articles, date_achat, list_nom FROM historique WHERE user_id=?", (uid,)
    ).fetchall()
    templates_rows = conn.execute("SELECT title, items_json FROM templates WHERE user_id=?", (uid,)).fetchall()
    price_memory_rows = conn.execute(
        "SELECT nom, dernier_prix, cat, frequency FROM price_memory WHERE user_id=?", (uid,)
    ).fetchall()
    shared_by_me = conn.execute("""
        SELECT u.username, l.nom FROM shares s
        JOIN users u ON u.id = s.shared_with_id
        JOIN lists l ON l.id = s.list_id
        WHERE s.owner_id=?
    """, (uid,)).fetchall()

    export = {
        "compte": {
            "nom_utilisateur": user_row[0],
            "email": user_row[1],
            "cree_le": user_row[2],
            "code_parrainage": user_row[3],
        },
        "listes": [{"id": r[0], "nom": r[1], "creee_le": r[2]} for r in lists_rows],
        "articles": [
            {"nom": r[0], "prix": r[1], "quantite": r[2], "achete": bool(r[3]), "categorie": r[4], "ajoute_le": r[5], "liste_id": r[6]}
            for r in courses_rows
        ],
        "historique_achats": [
            {"total": r[0], "nb_articles": r[1], "date": r[2], "liste": r[3]} for r in histo_rows
        ],
        "modeles_sauvegardes": [{"titre": r[0], "articles": json.loads(r[1])} for r in templates_rows],
        "memoire_prix": [
            {"nom": r[0], "dernier_prix": r[1], "categorie": r[2], "frequence_achat": r[3]} for r in price_memory_rows
        ],
        "listes_partagees_par_moi": [{"avec": r[0], "liste": r[1]} for r in shared_by_me],
        "exporte_le": datetime.now(timezone.utc).isoformat(),
    }

    return Response(
        json.dumps(export, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={"Content-disposition": "attachment; filename=mes_donnees_smartpanier.json"}
    )


@app.route("/leaderboard")
@login_required
def leaderboard():
    conn = get_db()
    top = conn.execute("""
        SELECT u.username, COUNT(r.id) as nb
        FROM users u JOIN users r ON r.referred_by = u.id
        GROUP BY u.id ORDER BY nb DESC LIMIT 10
    """).fetchall()
    my_count = conn.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (session["uid"],)).fetchone()[0]
    return render_template_string(LEADERBOARD_HTML, top=top, my_count=my_count, username=session["user"])


@app.route("/household/create", methods=["POST"])
@login_required
def household_create():
    nom = validate_list_name(request.form.get("nom")) or "Ma famille"
    conn = get_db()
    if get_user_household(session["uid"]):
        flash("Vous appartenez déjà à une famille.")
        return redirect(url_for("profile"))
    cur = conn.execute("INSERT INTO households (owner_id, nom) VALUES (?,?)", (session["uid"], nom))
    conn.execute(
        "INSERT INTO household_members (household_id, user_id, role) VALUES (?,?,'owner')",
        (cur.lastrowid, session["uid"])
    )
    conn.commit()
    flash("Famille créée !")
    return redirect(url_for("profile"))


@app.route("/household/invite", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def household_invite():
    conn = get_db()
    household = get_user_household(session["uid"])
    if not household or household[2] != "owner":
        flash("Seul le créateur de la famille peut inviter.")
        return redirect(url_for("profile"))

    if not get_user_premium_status(session["uid"]):
        count = conn.execute("SELECT COUNT(*) FROM household_members WHERE household_id=?", (household[0],)).fetchone()[0]
        if count >= 2:
            flash("Le plan gratuit limite la famille à 2 membres. Passez Premium pour l'agrandir.")
            return redirect(url_for("pricing"))

    target_username = validate_username(request.form.get("username"))
    target = conn.execute("SELECT id FROM users WHERE username=?", (target_username,)).fetchone() if target_username else None
    if not target:
        flash("Utilisateur introuvable.")
        return redirect(url_for("profile"))
    if get_user_household(target[0]):
        flash("Cette personne appartient déjà à une famille.")
        return redirect(url_for("profile"))

    conn.execute(
        "INSERT INTO household_members (household_id, user_id, role) VALUES (?,?,'membre')",
        (household[0], target[0])
    )
    conn.commit()
    flash(f"{target_username} a rejoint la famille !")
    return redirect(url_for("profile"))


@app.route("/household/leave", methods=["POST"])
@login_required
def household_leave():
    conn = get_db()
    household = get_user_household(session["uid"])
    if not household:
        return redirect(url_for("profile"))
    if household[2] == "owner":
        conn.execute("UPDATE lists SET household_id=NULL WHERE household_id=?", (household[0],))
        conn.execute("DELETE FROM household_members WHERE household_id=?", (household[0],))
        conn.execute("DELETE FROM households WHERE id=?", (household[0],))
        flash("Famille dissoute.")
    else:
        conn.execute("DELETE FROM household_members WHERE household_id=? AND user_id=?", (household[0], session["uid"]))
        flash("Vous avez quitté la famille.")
    conn.commit()
    return redirect(url_for("profile"))


@app.route("/lists/toggle_household/<int:list_id>", methods=["POST"])
@login_required
def toggle_household_list(list_id):
    conn = get_db()
    if not list_is_owned(session["uid"], list_id):
        flash("Cette liste ne vous appartient pas.")
        return redirect(url_for("profile"))
    household = get_user_household(session["uid"])
    if not household:
        flash("Créez ou rejoignez une famille d'abord.")
        return redirect(url_for("profile"))
    current = conn.execute("SELECT household_id FROM lists WHERE id=?", (list_id,)).fetchone()[0]
    new_value = None if current else household[0]
    conn.execute("UPDATE lists SET household_id=? WHERE id=?", (new_value, list_id))
    conn.commit()
    flash("Liste partagée avec la famille." if new_value else "Liste retirée du partage familial.")
    return redirect(url_for("profile"))


# --- IA : ASSISTANT, PHOTO, TICKET, MENU ---
@app.route("/api/ai/assistant", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def api_ai_assistant():
    if not AI_ENABLED:
        return jsonify({"ok": False, "error": "L'assistant IA n'est pas configuré sur ce site."}), 503
    question = (request.form.get("question") or "").strip()[:500]
    if not question:
        return jsonify({"ok": False, "error": "Question vide."}), 400

    list_id = get_current_list_id()
    conn = get_db()
    items = conn.execute("SELECT nom, qte, prix, cat FROM courses WHERE list_id=?", (list_id,)).fetchall()
    liste_txt = "; ".join(f"{i[0]} x{i[1]} ({i[2]} FCFA, {i[3]})" for i in items) or "liste vide"
    budget = session.get("budget", 50000)

    answer = ask_claude_text(
        "Tu es l'assistant intégré à l'app de courses SmartPanier. Réponds en français, de façon "
        "courte et concrète (5 lignes maximum), sur les courses, le budget ou la cuisine. Si la question "
        "n'a aucun rapport, réponds poliment que tu es spécialisé dans les courses et le budget.",
        f"Liste actuelle : {liste_txt}. Budget : {budget} FCFA. Question de l'utilisateur : {question}"
    )
    if answer is None:
        return jsonify({"ok": False, "error": "L'assistant est momentanément indisponible."}), 502
    track_event("ai_assistant_used", session["uid"])
    return jsonify({"ok": True, "answer": answer})


@app.route("/api/ai/recognize_photo", methods=["POST"])
@login_required
@premium_required
@limiter.limit("15 per minute")
def api_ai_recognize_photo():
    if not AI_ENABLED:
        return jsonify({"ok": False, "error": "La reconnaissance photo n'est pas configurée sur ce site."}), 503
    file = request.files.get("photo")
    if not file:
        return jsonify({"ok": False, "error": "Aucune photo reçue."}), 400
    raw = file.read(6_000_000)
    if not raw:
        return jsonify({"ok": False, "error": "Photo vide."}), 400
    b64 = base64.b64encode(raw).decode()
    media_type = file.mimetype or "image/jpeg"

    answer = ask_claude_vision(
        "Tu identifies un produit alimentaire ou ménager sur une photo pour une app de courses. "
        "Réponds UNIQUEMENT avec un objet JSON strict : "
        '{"nom": "...", "cat": "🥦 Fruits & Légumes | 🥩 Protéines | 🥖 Boulangerie | 🥛 Laitiers | 🥤 Boissons | ✨ Autre"}. '
        "Aucun texte avant ou après le JSON.",
        "Identifie ce produit.", b64, media_type
    )
    data = extract_json_from_ai_response(answer)
    if not data or not data.get("nom"):
        return jsonify({"ok": False, "error": "Produit non reconnu, essayez une autre photo."}), 200
    track_event("ai_photo_recognition", session["uid"])
    return jsonify({"ok": True, "nom": str(data.get("nom"))[:100], "cat": data.get("cat") if data.get("cat") in CAT_CONFIG else "✨ Autre"})


@app.route("/api/ai/scan_receipt", methods=["POST"])
@login_required
@premium_required
@limiter.limit("10 per minute")
def api_ai_scan_receipt():
    if not AI_ENABLED:
        return jsonify({"ok": False, "error": "Le scan de tickets n'est pas configuré sur ce site."}), 503
    file = request.files.get("photo")
    if not file:
        return jsonify({"ok": False, "error": "Aucune photo reçue."}), 400
    raw = file.read(8_000_000)
    if not raw:
        return jsonify({"ok": False, "error": "Photo vide."}), 400
    b64 = base64.b64encode(raw).decode()
    media_type = file.mimetype or "image/jpeg"

    answer = ask_claude_vision(
        "Tu lis un ticket de caisse en photo et en extrais la liste des articles achetés. "
        "Réponds UNIQUEMENT avec un tableau JSON strict, sans texte autour : "
        '[{"nom": "...", "prix": 000, "qte": 1, "cat": "🥦 Fruits & Légumes | 🥩 Protéines | 🥖 Boulangerie | 🥛 Laitiers | 🥤 Boissons | ✨ Autre"}]. '
        "Si le prix ou la quantité ne sont pas lisibles, mets une estimation raisonnable. Ignore les lignes qui ne sont pas des articles (total, TVA, etc).",
        "Extrais les articles de ce ticket de caisse.", b64, media_type
    )
    data = extract_json_from_ai_response(answer)
    if not data or not isinstance(data, list):
        return jsonify({"ok": False, "error": "Ticket illisible, réessayez avec une photo plus nette."}), 200

    list_id = get_current_list_id()
    conn = get_db()
    added = []
    for raw_item in data[:40]:
        nom, qte, prix, cat, errors = validate_item_input(
            raw_item.get("nom"), raw_item.get("qte", 1), raw_item.get("prix", 0), raw_item.get("cat")
        )
        if errors:
            continue
        conn.execute(
            "INSERT INTO courses (user_id, nom, prix, qte, fait, cat, added_by, list_id) VALUES (?,?,?,?,0,?,?,?)",
            (session["uid"], nom, prix, qte, cat, session["user"], list_id)
        )
        record_purchase_memory(conn, session["uid"], nom, prix, cat)
        added.append(nom)
    conn.commit()
    track_event("ai_receipt_scan", session["uid"], {"count": len(added)})
    return jsonify({"ok": True, "added": added, "total": _current_total(conn, list_id)})


@app.route("/api/ai/generate_menu", methods=["POST"])
@login_required
@premium_required
@limiter.limit("10 per minute")
def api_ai_generate_menu():
    if not AI_ENABLED:
        return jsonify({"ok": False, "error": "Le générateur de menus n'est pas configuré sur ce site."}), 503
    try:
        budget = float(request.form.get("budget", 0))
        personnes = max(1, min(int(request.form.get("personnes", 4)), 20))
        jours = max(1, min(int(request.form.get("jours", 3)), 14))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Paramètres invalides."}), 400

    devise = session.get("devise", "FCFA")
    answer = ask_claude_text(
        "Tu es un chef cuisinier qui planifie des menus économiques pour des foyers africains "
        "francophones. Réponds UNIQUEMENT avec un objet JSON strict, sans texte autour : "
        '{"menu": [{"jour": "Jour 1", "plats": "..."}], "ingredients": '
        '[{"nom": "...", "prix": 000, "qte": 1, "cat": "🥦 Fruits & Légumes | 🥩 Protéines | 🥖 Boulangerie | 🥛 Laitiers | 🥤 Boissons | ✨ Autre"}]}. '
        "Les prix sont des estimations réalistes dans la devise indiquée.",
        f"Propose un menu pour {jours} jour(s), {personnes} personne(s), avec un budget total d'environ "
        f"{budget} {devise} pour les courses, et la liste d'ingrédients à acheter."
    )
    data = extract_json_from_ai_response(answer)
    if not data or "menu" not in data:
        return jsonify({"ok": False, "error": "Impossible de générer un menu pour le moment."}), 200
    track_event("ai_menu_generated", session["uid"])
    return jsonify({"ok": True, "menu": data.get("menu", []), "ingredients": data.get("ingredients", [])})


@app.route("/stats")
@login_required
def stats_page():
    uid = session["uid"]
    conn = get_db()
    monthly = conn.execute(
        "SELECT strftime('%Y-%m', date_achat) as ym, SUM(total) FROM historique WHERE user_id=? GROUP BY ym ORDER BY ym",
        (uid,)
    ).fetchall()
    top_items = conn.execute(
        "SELECT nom, frequency, dernier_prix FROM price_memory WHERE user_id=? ORDER BY frequency DESC LIMIT 8",
        (uid,)
    ).fetchall()
    tips = generate_savings_tips(uid)
    return render_template_string(
        STATS_HTML,
        top_items=top_items, tips=tips,
        monthly_data={"labels": [m[0] for m in monthly], "totals": [m[1] for m in monthly]},
        has_data=bool(monthly or top_items),
    )


CAT_KEYWORDS = {
    "🥦 Fruits & Légumes": ["fruit", "légume", "legume", "vegetable", "fruits-and-vegetables"],
    "🥩 Protéines": ["meat", "viande", "poisson", "fish", "poultry", "egg", "œuf"],
    "🥖 Boulangerie": ["bread", "pain", "bakery", "pâtisserie", "patisserie"],
    "🥛 Laitiers": ["dairy", "lait", "milk", "cheese", "fromage", "yaourt", "yogurt"],
    "🥤 Boissons": ["beverage", "boisson", "drink", "juice", "jus", "soda", "water", "eau"],
}


def guess_category_from_off(categories_text):
    text = (categories_text or "").lower()
    for cat, keywords in CAT_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return cat
    return "✨ Autre"


@app.route("/api/product/<barcode>")
@login_required
@premium_required
@limiter.limit("30 per minute")
def api_product(barcode):
    barcode = re.sub(r"[^0-9]", "", barcode or "")[:20]
    if not barcode:
        return jsonify({"found": False})
    try:
        resp = requests.get(f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json", timeout=5)
        data = resp.json()
    except (requests.RequestException, ValueError):
        return jsonify({"found": False})

    if data.get("status") != 1:
        return jsonify({"found": False})

    product = data.get("product", {})
    nom = product.get("product_name_fr") or product.get("product_name") or ""
    if not nom:
        return jsonify({"found": False})
    cat = guess_category_from_off(product.get("categories", ""))
    return jsonify({"found": True, "nom": nom[:100], "cat": cat})


@app.route("/profile")
@login_required
def profile():
    conn = get_db()
    my_lists = conn.execute("SELECT id, nom FROM lists WHERE user_id=? ORDER BY id", (session["uid"],)).fetchall()
    shared_users = conn.execute("""
        SELECT u.username, l.nom FROM shares s
        JOIN users u ON u.id = s.shared_with_id
        JOIN lists l ON l.id = s.list_id
        WHERE s.owner_id=?
    """, (session["uid"],)).fetchall()
    user_row = conn.execute(
        "SELECT email, referral_code, stripe_subscription_status, premium_until FROM users WHERE id=?", (session["uid"],)
    ).fetchone()
    referral_count = conn.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (session["uid"],)).fetchone()[0]
    is_premium = user_is_premium((user_row[2], user_row[3])) if user_row else False
    household = get_user_household(session["uid"])
    household_members = []
    if household:
        household_members = conn.execute("""
            SELECT u.username, hm.role FROM household_members hm
            JOIN users u ON u.id = hm.user_id
            WHERE hm.household_id = ?
        """, (household[0],)).fetchall()

    return render_template_string(
        PROFILE_HTML, username=session["user"], shared_users=shared_users, my_lists=my_lists,
        email=user_row[0] if user_row else None,
        referral_code=user_row[1] if user_row else "",
        referral_count=referral_count,
        is_premium=is_premium,
        site_url=SITE_URL,
        household=household,
        household_members=household_members,
    )


@app.route("/share_list", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def share_list():
    target_user = validate_username(request.form.get("share_username"))
    try:
        list_id = int(request.form.get("list_id"))
    except (TypeError, ValueError):
        flash("Liste invalide.")
        return redirect(url_for("profile"))

    conn = get_db()
    if not list_is_owned(session["uid"], list_id):
        flash("Cette liste ne vous appartient pas.")
        return redirect(url_for("profile"))
    if not target_user:
        flash("Nom d'utilisateur invalide.")
        return redirect(url_for("profile"))

    target = conn.execute("SELECT id FROM users WHERE username=?", (target_user,)).fetchone()
    if not target:
        flash("Utilisateur introuvable.")
    elif target[0] == session["uid"]:
        flash("Vous ne pouvez pas partager avec vous-même.")
    else:
        conn.execute("""
            INSERT INTO shares (owner_id, shared_with_id, list_id) VALUES (?,?,?)
            ON CONFLICT(owner_id, shared_with_id, list_id) DO NOTHING
        """, (session["uid"], target[0], list_id))
        conn.commit()
        flash(f"Liste partagée avec {target_user} !")
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
    conn.execute("DELETE FROM courses WHERE list_id IN (SELECT id FROM lists WHERE user_id=?)", (uid,))
    conn.execute("DELETE FROM shares WHERE owner_id=?", (uid,))
    conn.execute("DELETE FROM lists WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM historique WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM templates WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM price_memory WHERE user_id=?", (uid,))
    conn.commit()
    session.pop("current_list_id", None)
    flash("Toutes vos données ont été réinitialisées.")
    return redirect(url_for("home"))


@app.route("/export_csv")
@login_required
@premium_required
def export_csv():
    devise = session.get("devise", "FCFA")
    list_id = get_current_list_id()
    conn = get_db()
    items = conn.execute("SELECT nom, qte, prix, cat FROM courses WHERE list_id=?", (list_id,)).fetchall()
    csv_data = f"Nom,Quantite,Prix Unitaire ({devise}),Total ({devise}),Categorie\n"
    for item in items:
        nom_safe = item[0].replace('"', '""')
        total = item[1] * item[2]
        csv_data += f'"{nom_safe}",{item[1]},{item[2]},{total},"{item[3]}"\n'
    return Response(csv_data, mimetype="text/csv", headers={"Content-disposition": "attachment; filename=Ma_Liste_SmartPanier.csv"})


# --- GESTION DES LISTES ---
@app.route("/lists/create", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def lists_create():
    nom = validate_list_name(request.form.get("nom"))
    if not nom:
        flash("Nom de liste invalide.")
        return redirect(url_for("home"))
    conn = get_db()
    if not get_user_premium_status(session["uid"]):
        count = conn.execute("SELECT COUNT(*) FROM lists WHERE user_id=?", (session["uid"],)).fetchone()[0]
        if count >= 1:
            flash("Le plan gratuit est limité à 1 liste. Passez Premium pour en créer autant que vous voulez.")
            return redirect(url_for("pricing"))
    cur = conn.execute("INSERT INTO lists (user_id, nom) VALUES (?,?)", (session["uid"], nom))
    new_list_id = cur.lastrowid
    conn.commit()
    session["current_list_id"] = new_list_id
    return redirect(request.referrer or url_for("home"))


@app.route("/lists/switch", methods=["POST"])
@login_required
def lists_switch():
    try:
        list_id = int(request.form.get("list_id"))
    except (TypeError, ValueError):
        return redirect(url_for("home"))
    if list_is_visible(session["uid"], list_id):
        session["current_list_id"] = list_id
    else:
        flash("Cette liste ne vous est pas accessible.")
    return redirect(url_for("home"))


@app.route("/lists/rename/<int:list_id>", methods=["POST"])
@login_required
def lists_rename(list_id):
    nom = validate_list_name(request.form.get("nom"))
    if nom and list_is_owned(session["uid"], list_id):
        conn = get_db()
        conn.execute("UPDATE lists SET nom=? WHERE id=?", (nom, list_id))
        conn.commit()
    else:
        flash("Nom invalide ou liste introuvable.")
    return redirect(url_for("profile"))


@app.route("/lists/delete/<int:list_id>", methods=["POST"])
@login_required
def lists_delete(list_id):
    uid = session["uid"]
    conn = get_db()
    if not list_is_owned(uid, list_id):
        flash("Cette liste ne vous appartient pas.")
        return redirect(url_for("profile"))
    count = conn.execute("SELECT COUNT(*) FROM lists WHERE user_id=?", (uid,)).fetchone()[0]
    if count <= 1:
        flash("Vous devez garder au moins une liste.")
        return redirect(url_for("profile"))
    conn.execute("DELETE FROM courses WHERE list_id=?", (list_id,))
    conn.execute("DELETE FROM shares WHERE list_id=?", (list_id,))
    conn.execute("DELETE FROM lists WHERE id=?", (list_id,))
    conn.commit()
    if session.get("current_list_id") == list_id:
        session.pop("current_list_id", None)
    flash("Liste supprimée.")
    return redirect(url_for("profile"))


if socketio:
    @socketio.on("join")
    def handle_join(data):
        if "uid" not in session:
            return
        try:
            list_id = int(data.get("list_id"))
        except (TypeError, ValueError, AttributeError):
            return
        if list_is_visible(session["uid"], list_id):
            join_room(str(list_id))


# --- PAGE PRINCIPALE ---
@app.route("/")
def home():
    if "uid" not in session:
        return render_template_string(LANDING_HTML, site_url=SITE_URL, plausible_domain=PLAUSIBLE_DOMAIN)

    uid = session["uid"]
    budget_user = session.get("budget", 50000.0)
    devise_user = session.get("devise", "FCFA")
    list_id = get_current_list_id()

    conn = get_db()
    liste = conn.execute("SELECT * FROM courses WHERE list_id=? ORDER BY fait ASC, id DESC", (list_id,)).fetchall()
    total = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE list_id=? AND fait=0", (list_id,)).fetchone()[0] or 0
    histo = conn.execute(
        "SELECT id, total, nb_articles, date_achat, list_nom FROM historique WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,)
    ).fetchall()
    templates = conn.execute("SELECT id, user_id, title FROM templates WHERE user_id=?", (uid,)).fetchall()
    my_lists, shared_lists = get_visible_lists(uid)
    is_premium = get_user_premium_status(uid)

    chart_labels, chart_data, chart_colors = [], [], []
    for c, color in CAT_CONFIG.items():
        s = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE list_id=? AND cat=?", (list_id, c)).fetchone()[0] or 0
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
        histo=histo,
        devises=DEVISES,
        devise=devise_user,
        preset_recipes=PRESET_RECIPES,
        templates=templates,
        chart_data={"labels": chart_labels, "data": chart_data, "colors": chart_colors},
        histo_data={"labels": histo_labels, "totals": histo_totals},
        url=SITE_URL,
        my_lists=my_lists,
        shared_lists=shared_lists,
        current_list_id=list_id,
        onesignal_app_id=ONESIGNAL_APP_ID,
        realtime_enabled=bool(socketio),
        is_premium=is_premium,
        plausible_domain=PLAUSIBLE_DOMAIN,
        ai_enabled=AI_ENABLED,
    )


# --- API JSON (AJAX) ---
def _current_total(conn, list_id):
    return conn.execute("SELECT SUM(prix*qte) FROM courses WHERE list_id=? AND fait=0", (list_id,)).fetchone()[0] or 0


@app.route("/api/add", methods=["POST"])
@login_required
@limiter.limit("120 per minute")
def api_add():
    list_id = get_current_list_id()
    nom, qte, prix, cat, errors = validate_item_input(
        request.form.get("nom"), request.form.get("qte", 1), request.form.get("prix"), request.form.get("cat")
    )
    if errors:
        return jsonify({"ok": False, "error": " ".join(errors)}), 400
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO courses (user_id, nom, prix, qte, fait, cat, added_by, list_id) VALUES (?,?,?,?,0,?,?,?)",
        (session["uid"], nom, prix, qte, cat, session["user"], list_id)
    )
    new_id = cur.lastrowid
    record_purchase_memory(conn, session["uid"], nom, prix, cat)
    conn.commit()
    total = _current_total(conn, list_id)
    maybe_trigger_budget_alert(total)
    item_payload = {"id": new_id, "nom": nom, "prix": prix, "qte": qte, "cat": cat, "color": CAT_CONFIG.get(cat, "#64748b")}
    rt_emit("item_added", {
        "item": item_payload, "total": total,
        "client_id": request.form.get("client_id", ""), "by": session["user"]
    }, list_id)
    return jsonify({"ok": True, "total": total, "item": item_payload})


@app.route("/api/edit/<int:item_id>", methods=["POST"])
@login_required
@limiter.limit("120 per minute")
def api_edit(item_id):
    list_id = get_current_list_id()
    nom, qte, prix, cat, errors = validate_item_input(
        request.form.get("nom"), request.form.get("qte", 1), request.form.get("prix"), request.form.get("cat")
    )
    if errors:
        return jsonify({"ok": False, "error": " ".join(errors)}), 400
    conn = get_db()
    cur = conn.execute(
        "UPDATE courses SET nom=?, qte=?, prix=?, cat=? WHERE id=? AND list_id=?",
        (nom, qte, prix, cat, item_id, list_id)
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
    total = _current_total(conn, list_id)
    item_payload = {"id": item_id, "nom": nom, "prix": prix, "qte": qte, "cat": cat, "color": CAT_CONFIG.get(cat, "#64748b")}
    rt_emit("item_updated", {
        "item": item_payload, "total": total,
        "client_id": request.form.get("client_id", ""), "by": session["user"]
    }, list_id)
    return jsonify({"ok": True, "total": total, "item": item_payload})


@app.route("/api/check/<int:item_id>", methods=["POST"])
@login_required
@limiter.limit("120 per minute")
def api_check(item_id):
    list_id = get_current_list_id()
    conn = get_db()
    cur = conn.execute(
        "UPDATE courses SET fait = NOT fait, checked_by=? WHERE id=? AND list_id=?",
        (session["user"], item_id, list_id)
    )
    if cur.rowcount == 0:
        return jsonify({"ok": False, "error": "Article introuvable."}), 404
    conn.commit()
    total = _current_total(conn, list_id)
    maybe_trigger_budget_alert(total)
    rt_emit("item_checked", {
        "item_id": item_id, "total": total,
        "client_id": request.form.get("client_id", ""), "by": session["user"]
    }, list_id)
    return jsonify({"ok": True, "total": total})


@app.route("/api/del/<int:item_id>", methods=["POST"])
@login_required
@limiter.limit("120 per minute")
def api_del(item_id):
    list_id = get_current_list_id()
    conn = get_db()
    cur = conn.execute("DELETE FROM courses WHERE id=? AND list_id=?", (item_id, list_id))
    if cur.rowcount == 0:
        return jsonify({"ok": False, "error": "Article introuvable."}), 404
    conn.commit()
    total = _current_total(conn, list_id)
    rt_emit("item_deleted", {
        "item_id": item_id, "total": total,
        "client_id": request.form.get("client_id", ""), "by": session["user"]
    }, list_id)
    return jsonify({"ok": True, "total": total})


@app.route("/api/save_push_id", methods=["POST"])
@login_required
def api_save_push_id():
    player_id = (request.form.get("player_id") or "").strip()[:200]
    if player_id:
        conn = get_db()
        conn.execute("UPDATE users SET onesignal_player_id=? WHERE id=?", (player_id, session["uid"]))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/schedule_reminder", methods=["POST"])
@login_required
def api_schedule_reminder():
    try:
        minutes = max(1, min(int(request.form.get("minutes", 30)), 1440))
    except (TypeError, ValueError):
        minutes = 30
    count = request.form.get("count", "0")
    conn = get_db()
    r = conn.execute("SELECT onesignal_player_id FROM users WHERE id=?", (session["uid"],)).fetchone()
    player_id = r[0] if r else None
    if player_id:
        schedule_push_in_background(
            minutes * 60, player_id, "🛒 C'est l'heure des courses !", f"Tu as {count} article(s) dans ta liste."
        )
    return jsonify({"ok": True})


# --- LISTES : RECETTES / MODÈLES / BUDGET / DEVISE / CLÔTURE ---
@app.route("/load_recipe", methods=["POST"])
@login_required
def load_recipe():
    list_id = get_current_list_id()
    r_name = request.form.get("recipe_name")
    if r_name in PRESET_RECIPES:
        conn = get_db()
        for item in PRESET_RECIPES[r_name]:
            conn.execute(
                "INSERT INTO courses (user_id, nom, prix, qte, fait, cat, added_by, list_id) VALUES (?,?,?,?,0,?,?,?)",
                (session["uid"], item["nom"], item["prix"], item["qte"], item["cat"], session["user"], list_id)
            )
        conn.commit()
    return redirect(url_for("home"))


@app.route("/save_template", methods=["POST"])
@login_required
def save_template():
    list_id = get_current_list_id()
    title = (request.form.get("title") or "Ma liste").strip()[:80]
    conn = get_db()
    items = conn.execute("SELECT nom, prix, qte, cat FROM courses WHERE list_id=?", (list_id,)).fetchall()
    items_json = json.dumps([{"nom": x[0], "prix": x[1], "qte": x[2], "cat": x[3]} for x in items])
    conn.execute("INSERT INTO templates (user_id, title, items_json) VALUES (?,?,?)", (session["uid"], title, items_json))
    conn.commit()
    return redirect(url_for("home"))


@app.route("/load_template/<int:template_id>", methods=["POST"])
@login_required
def load_template(template_id):
    list_id = get_current_list_id()
    conn = get_db()
    r = conn.execute("SELECT items_json FROM templates WHERE id=? AND user_id=?", (template_id, session["uid"])).fetchone()
    if r:
        try:
            items = json.loads(r[0])
        except (json.JSONDecodeError, TypeError):
            items = []
        for item in items:
            conn.execute(
                "INSERT INTO courses (user_id, nom, prix, qte, fait, cat, added_by, list_id) VALUES (?,?,?,?,0,?,?,?)",
                (session["uid"], item.get("nom", "Article"), item.get("prix", 0), item.get("qte", 1),
                 item.get("cat", "✨ Autre"), session["user"], list_id)
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
        session["_budget_alert_sent"] = False
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
    list_id = get_current_list_id()
    conn = get_db()
    list_row = conn.execute("SELECT nom FROM lists WHERE id=?", (list_id,)).fetchone()
    list_nom = list_row[0] if list_row else "Liste"
    total = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE list_id=? AND fait=0", (list_id,)).fetchone()[0] or 0
    count = conn.execute("SELECT COUNT(*) FROM courses WHERE list_id=? AND fait=0", (list_id,)).fetchone()[0] or 0
    if total > 0 or count > 0:
        conn.execute(
            "INSERT INTO historique (user_id, total, nb_articles, list_id, list_nom) VALUES (?,?,?,?,?)",
            (uid, total, count, list_id, list_nom)
        )
        conn.execute("DELETE FROM courses WHERE list_id=?", (list_id,))
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
    if socketio:
        socketio.run(app, host="0.0.0.0", port=port, debug=debug_mode, allow_unsafe_werkzeug=True)
    else:
        app.run(host="0.0.0.0", port=port, debug=debug_mode)
