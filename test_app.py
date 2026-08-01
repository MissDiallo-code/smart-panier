"""
Suite de tests automatisés pour SmartPanier.

Pour l'exécuter :
    pip install -r requirements.txt -r requirements-dev.txt
    pytest test_app.py -v

Ces tests utilisent une base SQLite temporaire (jamais votre base de prod) et
couvrent : authentification, mot de passe oublié, multi-listes, isolation entre
listes, partage, sécurité des permissions, et les actions AJAX (ajout/édition/
coche/suppression d'articles).
"""
import os
import sys
import hashlib
import secrets as pysecrets
import sqlite3
import tempfile

import pytest


@pytest.fixture
def app_module():
    """Charge l'app avec une base de données SQLite temporaire, isolée pour chaque test."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    os.environ["DB_NAME"] = db_path
    os.environ["SITE_URL"] = "http://localhost:5000"
    os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

    import importlib.util
    spec = importlib.util.spec_from_file_location("app_module_test", "App_Ultimate_Public.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    yield mod

    os.remove(db_path)


@pytest.fixture
def client(app_module):
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def register_and_login(client, username, password="secret6", email=None):
    email = email or f"{username}@example.com"
    client.post("/register", data={"user": username, "pass": password, "email": email}, follow_redirects=True)
    return client.post("/login", data={"user": username, "pass": password}, follow_redirects=True)


def has_item(resp, nom):
    return f'data-nom="{nom}"'.encode() in resp.data


# --- AUTHENTIFICATION ---
def test_register_login_logout(client):
    r = register_and_login(client, "alice")
    assert r.status_code == 200
    assert b"Ma liste" in r.data

    r = client.get("/logout", follow_redirects=True)
    assert r.status_code == 200

    r = client.post("/login", data={"user": "alice", "pass": "wrongpass"}, follow_redirects=True)
    assert b"Identifiants incorrects" in r.data


def test_register_duplicate_username_rejected(client):
    register_and_login(client, "bob", email="bob1@example.com")
    r = client.post("/register", data={"user": "bob", "pass": "secret6", "email": "bob2@example.com"}, follow_redirects=True)
    assert b"d\xc3\xa9j\xc3\xa0 pris" in r.data


def test_register_duplicate_email_rejected(client):
    register_and_login(client, "carol", email="carol@example.com")
    r = client.post("/register", data={"user": "carol2", "pass": "secret6", "email": "carol@example.com"}, follow_redirects=True)
    assert b"existe d\xc3\xa9j" in r.data


def test_weak_password_rejected(client):
    r = client.post("/register", data={"user": "dave", "pass": "123", "email": "dave@example.com"}, follow_redirects=True)
    assert b"6 caract" in r.data


# --- MOT DE PASSE OUBLIÉ ---
def test_forgot_password_flow(client, app_module):
    register_and_login(client, "erin", email="erin@example.com")

    r = client.post("/forgot_password", data={"email": "erin@example.com"}, follow_redirects=True)
    assert b"lien de r" in r.data

    with sqlite3.connect(os.environ["DB_NAME"]) as conn:
        row = conn.execute("SELECT reset_token_hash FROM users WHERE username='erin'").fetchone()
    assert row[0], "aucun token généré"

    # Simule la possession du lien envoyé par email (le token en clair n'est jamais stocké)
    fake_token = pysecrets.token_urlsafe(32)
    fake_hash = hashlib.sha256(fake_token.encode()).hexdigest()
    with sqlite3.connect(os.environ["DB_NAME"]) as conn:
        conn.execute("UPDATE users SET reset_token_hash=? WHERE username='erin'", (fake_hash,))
        conn.commit()

    r = client.post(f"/reset_password/{fake_token}", data={"new_pass": "newpass6"}, follow_redirects=True)
    assert b"r\xc3\xa9initialis\xc3\xa9" in r.data

    r = client.post("/login", data={"user": "erin", "pass": "newpass6"}, follow_redirects=True)
    assert b"Ma liste" in r.data


def test_forgot_password_unknown_email_no_enumeration(client):
    r = client.post("/forgot_password", data={"email": "inconnu@example.com"}, follow_redirects=True)
    # Même message générique, qu'un compte existe ou non (anti-énumération).
    assert b"lien de r" in r.data


def test_reset_password_invalid_token_rejected(client):
    r = client.get("/reset_password/token-qui-nexiste-pas", follow_redirects=True)
    assert b"invalide ou expir" in r.data


# --- MULTI-LISTES ---
def test_multi_list_isolation(client):
    register_and_login(client, "frank")

    r = client.post("/api/add", data={"nom": "Pain", "qte": "1", "prix": "500", "cat": "🥖 Boulangerie"})
    assert r.get_json()["ok"]

    # La création d'une 2e liste est une fonctionnalité Premium : on simule un compte Premium
    # pour tester l'isolation elle-même (indépendante du plan tarifaire).
    with sqlite3.connect(os.environ["DB_NAME"]) as conn:
        conn.execute(
            "UPDATE users SET stripe_subscription_status='active' WHERE username='frank'"
        )
        conn.commit()

    client.post("/lists/create", data={"nom": "Ménage"}, follow_redirects=True)
    r = client.post("/api/add", data={"nom": "Savon", "qte": "1", "prix": "1000", "cat": "✨ Autre"})
    assert r.get_json()["ok"]

    r = client.get("/")
    assert has_item(r, "Savon") and not has_item(r, "Pain")


def test_free_plan_limited_to_one_list(client):
    register_and_login(client, "sami")
    r = client.post("/lists/create", data={"nom": "Deuxième liste"}, follow_redirects=True)
    assert b"limit\xc3\xa9" in r.data or b"Premium" in r.data
    with sqlite3.connect(os.environ["DB_NAME"]) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM lists WHERE user_id=(SELECT id FROM users WHERE username='sami')"
        ).fetchone()[0]
    assert count == 1


def test_cannot_delete_last_list(client):
    register_and_login(client, "gina")
    with sqlite3.connect(os.environ["DB_NAME"]) as conn:
        list_id = conn.execute("SELECT id FROM lists WHERE user_id=(SELECT id FROM users WHERE username='gina')").fetchone()[0]
    r = client.post(f"/lists/delete/{list_id}", follow_redirects=True)
    assert b"au moins une liste" in r.data


def test_share_permission_enforced(client, app_module):
    register_and_login(client, "henri")
    with sqlite3.connect(os.environ["DB_NAME"]) as conn:
        henri_list_id = conn.execute(
            "SELECT id FROM lists WHERE user_id=(SELECT id FROM users WHERE username='henri')"
        ).fetchone()[0]

    client2 = app_module.app.test_client()
    register_and_login(client2, "iris")

    # iris ne doit pas pouvoir accéder à la liste d'henri sans partage
    r = client2.post("/lists/switch", data={"list_id": str(henri_list_id)}, follow_redirects=True)
    assert b"pas accessible" in r.data

    # henri partage sa liste avec iris
    client.post("/share_list", data={"share_username": "iris", "list_id": str(henri_list_id)}, follow_redirects=True)

    r = client2.post("/lists/switch", data={"list_id": str(henri_list_id)}, follow_redirects=True)
    assert b"pas accessible" not in r.data


# --- ACTIONS SUR LES ARTICLES (AJAX) ---
def test_add_edit_check_delete_item(client):
    register_and_login(client, "julie")

    r = client.post("/api/add", data={"nom": "Riz", "qte": "2", "prix": "1500", "cat": "✨ Autre"})
    data = r.get_json()
    assert data["ok"] and data["total"] == 3000
    item_id = data["item"]["id"]

    r = client.post(f"/api/edit/{item_id}", data={"nom": "Riz basmati", "qte": "3", "prix": "1600", "cat": "✨ Autre"})
    assert r.get_json()["item"]["nom"] == "Riz basmati"

    r = client.post(f"/api/check/{item_id}", data={})
    assert r.get_json()["ok"]

    r = client.post(f"/api/del/{item_id}", data={})
    assert r.get_json()["ok"]


def test_invalid_item_input_rejected(client):
    register_and_login(client, "karim")
    r = client.post("/api/add", data={"nom": "", "qte": "1", "prix": "100", "cat": "✨ Autre"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_unauthenticated_api_returns_401(client):
    r = client.post("/api/add", data={"nom": "Test", "qte": "1", "prix": "100", "cat": "✨ Autre"})
    assert r.status_code == 401


# --- SUGGESTIONS INTELLIGENTES ---
def test_suggestions_appear_after_repeated_purchase(client):
    register_and_login(client, "louis")

    r = client.post("/api/add", data={"nom": "Lait", "qte": "1", "prix": "800", "cat": "🥛 Laitiers"})
    item_id = r.get_json()["item"]["id"]
    client.post(f"/api/del/{item_id}", data={})
    r = client.post("/api/add", data={"nom": "Lait", "qte": "1", "prix": "850", "cat": "🥛 Laitiers"})
    item_id2 = r.get_json()["item"]["id"]
    client.post(f"/api/del/{item_id2}", data={})

    r = client.get("/api/suggestions")
    noms = [s["nom"] for s in r.get_json()["suggestions"]]
    assert "Lait" in noms


def test_suggestions_exclude_items_already_in_list(client):
    register_and_login(client, "manon")
    for _ in range(2):
        r = client.post("/api/add", data={"nom": "Riz", "qte": "1", "prix": "1000", "cat": "✨ Autre"})
        client.post(f"/api/del/{r.get_json()['item']['id']}", data={})
    client.post("/api/add", data={"nom": "Riz", "qte": "1", "prix": "1000", "cat": "✨ Autre"})

    r = client.get("/api/suggestions")
    noms = [s["nom"] for s in r.get_json()["suggestions"]]
    assert "Riz" not in noms


# --- SÉCURITÉ OAUTH ---
def test_classic_login_blocked_on_oauth_only_account(client):
    with sqlite3.connect(os.environ["DB_NAME"]) as conn:
        conn.execute(
            "INSERT INTO users (username, password, email, oauth_provider, oauth_id) VALUES (?,?,?,?,?)",
            ("oauth_user", None, "oauth@example.com", "google", "abc123")
        )
        conn.commit()
    r = client.post("/login", data={"user": "oauth_user", "pass": "peu importe"}, follow_redirects=True)
    # Ne doit surtout pas planter (mot de passe NULL) et doit rediriger vers un message clair.
    assert r.status_code == 200
    assert b"Google" in r.data or b"Identifiants incorrects" in r.data



def test_legal_pages_accessible(client):
    assert client.get("/mentions-legales").status_code == 200
    assert client.get("/confidentialite").status_code == 200


# --- PARRAINAGE ---
def test_referral_grants_bonus_to_both(client, app_module):
    register_and_login(client, "nora")
    with sqlite3.connect(os.environ["DB_NAME"]) as conn:
        ref_code = conn.execute("SELECT referral_code FROM users WHERE username='nora'").fetchone()[0]
    assert ref_code

    client2 = app_module.app.test_client()
    client2.post(
        "/register",
        data={"user": "oscar", "pass": "secret6", "email": "oscar@example.com", "ref": ref_code},
        follow_redirects=True
    )

    with sqlite3.connect(os.environ["DB_NAME"]) as conn:
        oscar = conn.execute("SELECT referred_by, premium_until FROM users WHERE username='oscar'").fetchone()
        nora = conn.execute("SELECT premium_until FROM users WHERE username='nora'").fetchone()
    assert oscar[0] is not None and oscar[1] is not None
    assert nora[0] is not None


def test_referral_with_invalid_code_still_registers(client):
    r = client.post(
        "/register",
        data={"user": "pia", "pass": "secret6", "email": "pia@example.com", "ref": "CODEBIDON"},
        follow_redirects=True
    )
    assert b"succ\xc3\xa8s" in r.data or r.status_code == 200


# --- MONÉTISATION ---
def test_export_and_scan_gated_behind_premium(client):
    register_and_login(client, "quentin")
    r = client.get("/export_csv", follow_redirects=True)
    assert b"Premium" in r.data
    r = client.get("/api/product/1234567890123")
    assert r.status_code == 402


def test_billing_webhook_disabled_without_stripe_config(client):
    # Par défaut (sans clés Stripe), le webhook doit répondre 404 proprement, jamais planter.
    r = client.post("/billing/webhook", data="{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 404


def test_admin_dashboard_hidden_by_default(client):
    register_and_login(client, "regis")
    r = client.get("/admin")
    assert r.status_code == 404


def test_404_page(client):
    r = client.get("/cette-page-nexiste-pas")
    assert r.status_code == 404


# --- FAMILLE (HOUSEHOLD) ---
def test_household_create_invite_and_shared_visibility(client, app_module):
    register_and_login(client, "papa2")
    r = client.post("/household/create", data={"nom": "Famille Test"}, follow_redirects=True)
    assert b"Famille Test" in r.data

    client2 = app_module.app.test_client()
    register_and_login(client2, "maman2")
    r = client.post("/household/invite", data={"username": "maman2"}, follow_redirects=True)
    assert b"maman2" in r.data

    with sqlite3.connect(os.environ["DB_NAME"]) as conn:
        list_id = conn.execute(
            "SELECT id FROM lists WHERE user_id=(SELECT id FROM users WHERE username='papa2')"
        ).fetchone()[0]
    client.post(f"/lists/toggle_household/{list_id}", follow_redirects=True)

    r = client2.post("/lists/switch", data={"list_id": str(list_id)}, follow_redirects=True)
    assert b"pas accessible" not in r.data


# --- IA (nécessite ANTHROPIC_API_KEY : sans clé, doit rester gracieux) ---
def test_ai_endpoints_disabled_gracefully_without_key(client):
    register_and_login(client, "test_ia")
    r = client.post("/api/ai/assistant", data={"question": "Bonjour"})
    assert r.status_code == 503

    r = client.post("/api/ai/generate_menu", data={"budget": "10000", "personnes": "4", "jours": "3"})
    assert r.status_code in (402, 503)


def test_stats_page_accessible(client):
    register_and_login(client, "stat_user")
    r = client.get("/stats")
    assert r.status_code == 200


# --- EXPORT RGPD ---
def test_export_data_contains_own_data_only(client, app_module):
    register_and_login(client, "walid")
    client.post("/api/add", data={"nom": "Sucre", "qte": "1", "prix": "500", "cat": "✨ Autre"})

    client2 = app_module.app.test_client()
    register_and_login(client2, "yara")

    r = client.get("/export_data")
    data = r.get_json()
    assert data["compte"]["nom_utilisateur"] == "walid"
    assert len(data["articles"]) == 1 and data["articles"][0]["nom"] == "Sucre"

    r2 = client2.get("/export_data")
    data2 = r2.get_json()
    assert data2["compte"]["nom_utilisateur"] == "yara"
    assert data2["articles"] == []


# --- CLASSEMENT DES PARRAINS ---
def test_leaderboard_reflects_referrals(client, app_module):
    register_and_login(client, "zack")
    with sqlite3.connect(os.environ["DB_NAME"]) as conn:
        ref_code = conn.execute("SELECT referral_code FROM users WHERE username='zack'").fetchone()[0]

    client2 = app_module.app.test_client()
    client2.post(
        "/register",
        data={"user": "amira", "pass": "secret6", "email": "amira@example.com", "ref": ref_code},
        follow_redirects=True
    )

    r = client.get("/leaderboard")
    assert b"zack" in r.data
