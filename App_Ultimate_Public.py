import sqlite3
import json
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, Response, jsonify
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "cle_securite_master_789" 
DB_NAME = "courses_multiusers.db"
SITE_URL = "https://smart-panier-1.onrender.com" 

# --- INITIALISATION DE LA BASE DE DONNÉES ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS courses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nom TEXT, prix REAL, qte INTEGER, fait BOOLEAN, cat TEXT, date_ajout DATETIME DEFAULT CURRENT_TIMESTAMP)')
        cursor.execute('CREATE TABLE IF NOT EXISTS historique (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, total REAL, nb_articles INTEGER, date_achat DATETIME DEFAULT CURRENT_TIMESTAMP)')
        cursor.execute('CREATE TABLE IF NOT EXISTS templates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, items_json TEXT)')
        conn.commit()

init_db()

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

# --- PWA WORKER JS & MANIFEST ---
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

SW_JS = """const CACHE_NAME = 'smartpanier-v2';
const urlsToCache = [
  '/', 
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css', 
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css', 
  'https://cdn.jsdelivr.net/npm/chart.js'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache)));
});

self.addEventListener('fetch', event => {
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});"""

# --- TEMPLATES HTML ---

AUTH_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <title>{{ title }} - SmartPanier</title>
    <style>
        body { background: #0f172a; color: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
        .auth-card { background: #1e293b; padding: 30px; border-radius: 20px; width: 100%; max-width: 420px; border: 1px solid #334155; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        .form-control { background: #0f172a; border: 1px solid #334155; color: white; padding: 12px; font-size: 16px; }
        .form-control:focus { background: #0f172a; color: white; border-color: #3b82f6; box-shadow: none; }
        .btn-custom { padding: 12px; font-size: 16px; border-radius: 10px; font-weight: bold; }
    </style>
</head>
<body>
    <div class="auth-card text-center">
        <h3 class="fw-bold mb-4">🛒 SmartPanier</h3>
        {% with m = get_flashed_messages() %}
            {% if m %}<div class="alert alert-warning py-2 small mb-3">{{m[0]}}</div>{% endif %}
        {% endwith %}
        <form method="POST">
            <input type="text" name="user" class="form-control mb-3" placeholder="Nom d'utilisateur" required autocomplete="username">
            <input type="password" name="pass" class="form-control mb-4" placeholder="Mot de passe" required autocomplete="current-password">
            <button type="submit" class="btn btn-warning w-100 btn-custom mb-3">{{ btn }}</button>
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
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
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

PROFILE_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <title>Mon Profil - SmartPanier</title>
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; padding: 30px 15px; }
        .profile-card { background: #1e293b; border: 1px solid #334155; border-radius: 20px; padding: 25px; max-width: 500px; margin: 0 auto; }
        .form-control { background: #0f172a; border: 1px solid #334155; color: white; }
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
            {% if m %}<div class="alert alert-info py-2 small mb-3">{{m[0]}}</div>{% endif %}
        {% endwith %}

        <div class="mb-4">
            <label class="small text-secondary fw-bold">Nom d'utilisateur</label>
            <input type="text" class="form-control" value="{{ username }}" disabled>
        </div>

        <!-- Changer Mot de Passe -->
        <form action="/change_password" method="POST" class="mb-4 border-top border-secondary pt-3">
            <h6 class="fw-bold mb-3 text-warning">🔑 Modifier le mot de passe</h6>
            <input type="password" name="old_pass" class="form-control mb-2" placeholder="Ancien mot de passe" required>
            <input type="password" name="new_pass" class="form-control mb-3" placeholder="Nouveau mot de passe" required>
            <button type="submit" class="btn btn-warning w-100 fw-bold">Mettre à jour</button>
        </form>

        <!-- Actions de Réinitialisation -->
        <div class="border-top border-secondary pt-3">
            <h6 class="fw-bold mb-3 text-danger">⚠️ Zone de Danger</h6>
            <div class="d-flex flex-column gap-2">
                <a href="/clear_history" class="btn btn-outline-warning btn-sm" onclick="return confirm('Effacer tout votre historique d\'achats ?')">Vider l'historique d'achats</a>
                <a href="/reset_all" class="btn btn-outline-danger btn-sm" onclick="return confirm('Attention ! Cela va tout supprimer (listes, modèles, historique). Continuer ?')">Réinitialiser toutes mes données</a>
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
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="manifest" href="/manifest.json">
    <meta name="theme-color" content="#1e293b">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <title>SmartPanier - Dashboard</title>
    <style>
        :root { 
            --bg: #0f172a; 
            --card: #1e293b; 
            --border: #334155; 
            --text: #f8fafc;
            --input-bg: #0f172a;
        }
        
        [data-theme="light"] {
            --bg: #f1f5f9;
            --card: #ffffff;
            --border: #cbd5e1;
            --text: #0f172a;
            --input-bg: #f8fafc;
        }

        body { 
            background: var(--bg); 
            color: var(--text); 
            font-family: system-ui, -apple-system, sans-serif; 
            padding-bottom: 40px; 
            transition: background 0.3s, color 0.3s;
        }

        .card { 
            background: var(--card); 
            border: 1px solid var(--border); 
            border-radius: 16px; 
            margin-bottom: 16px; 
            padding: 18px; 
            box-shadow: 0 4px 12px rgba(0,0,0,0.1); 
            transition: background 0.3s, border-color 0.3s;
        }

        .form-control, .form-select { 
            background: var(--input-bg) !important; 
            border: 1px solid var(--border) !important; 
            color: var(--text) !important; 
            padding: 10px 14px; 
            font-size: 15px; 
        }

        .form-control:focus, .form-select:focus { 
            box-shadow: none; 
            border-color: #3b82f6 !important; 
        }

        .total-display { 
            color: #f59e0b; 
            font-weight: 900; 
            font-size: 2.8rem; 
            line-height: 1.1; 
        }

        .budget-alert-banner {
            background: #ef4444;
            color: white;
            font-weight: bold;
            text-align: center;
            padding: 10px;
            border-radius: 12px;
            margin-bottom: 15px;
            animation: pulse 1.5s infinite;
        }

        .list-group-item { 
            background: var(--card); 
            color: var(--text); 
            border: 1px solid var(--border); 
            margin-bottom: 8px; 
            border-radius: 12px !important; 
            padding: 12px 16px; 
            transition: all 0.3s ease; 
        }

        .done { 
            opacity: 0.4; 
            text-decoration: line-through; 
        }

        .btn-action { 
            padding: 10px; 
            font-weight: bold; 
            border-radius: 10px; 
        }

        .cat-filter-btn {
            font-size: 0.82rem;
            padding: 4px 10px;
            border-radius: 20px;
            cursor: pointer;
            border: 1px solid var(--border);
            background: var(--card);
            color: var(--text);
            transition: all 0.2s;
        }

        .cat-filter-btn.active {
            background: #3b82f6;
            color: white;
            border-color: #3b82f6;
        }

        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.7; } 100% { opacity: 1; } }
        @media print { .no-print { display: none !important; } body { background: white; color: black; } .card { border: none; } }
    </style>
</head>
<body>
    <div class="container" style="max-width: 980px;">
        <!-- Header -->
        <div class="d-flex justify-content-between align-items-center my-3 no-print">
            <div class="d-flex align-items-center gap-2">
                <h5 class="mb-0 fw-bold">👤 {{ username }}</h5>
                <a href="/profile" class="btn btn-sm btn-outline-warning"><i class="fa fa-user-cog"></i> Profil</a>
            </div>

            <div class="d-flex gap-2 align-items-center">
                <!-- Bouton Installer PWA -->
                <button id="pwaInstallBtn" class="btn btn-sm btn-warning fw-bold d-none">
                    <i class="fa fa-download me-1"></i> Installer
                </button>

                <!-- Selecteur Devise -->
                <form action="/set_devise" method="POST" class="m-0">
                    <select name="devise" onchange="this.form.submit()" class="form-select form-select-sm" style="width: auto;">
                        {% for d in devises %}
                            <option value="{{d}}" {% if d == devise %}selected{% endif %}>{{d}}</option>
                        {% endfor %}
                    </select>
                </form>

                <!-- Bouton Thème -->
                <button onclick="toggleTheme()" class="btn btn-sm btn-outline-secondary" id="themeBtn">
                    <i class="fa fa-moon"></i>
                </button>

                <a href="/export_csv" class="btn btn-sm btn-outline-success"><i class="fa fa-file-excel"></i> Excel</a>
                <button onclick="invite()" class="btn btn-sm btn-outline-info"><i class="fa fa-gift"></i> Inviter</button>
                <a href="/logout" class="btn btn-sm btn-outline-danger"><i class="fa fa-sign-out-alt"></i></a>
            </div>
        </div>

        <!-- BANNIÈRE ALERTE BUDGET -->
        {% if total > budget %}
        <div class="budget-alert-banner shadow-lg">
            🚨 ATTTENTION : Budget dépassé de {{ "%.0f"|format(total - budget) }} {{ devise }} ! 🚨
        </div>
        {% endif %}

        <div class="row g-3">
            <!-- Colonne Gauche : Formulaire, Budget, Recettes & Modèles -->
            <div class="col-lg-5 no-print">
                <!-- Formulaire d'ajout -->
                <div class="card">
                    <h6 class="fw-bold mb-3">➕ Ajouter un article</h6>
                    <form action="/add" method="POST">
                        <input type="text" name="nom" class="form-control mb-2" placeholder="Nom du produit (ex: Pain)" required>
                        <div class="row g-2 mb-2">
                            <div class="col-5">
                                <input type="number" name="qte" class="form-control" value="1" min="1" placeholder="Qté">
                            </div>
                            <div class="col-7">
                                <input type="number" step="any" name="prix" class="form-control" placeholder="Prix ({{ devise }})">
                            </div>
                        </div>
                        <select name="cat" class="form-select mb-3">
                            {% for c in categories %}
                                <option value="{{c}}">{{c}}</option>
                            {% endfor %}
                        </select>
                        <button type="submit" class="btn btn-warning w-100 btn-action">AJOUTER AU PANIER</button>
                    </form>
                </div>

                <!-- Recettes & Listes Rapides -->
                <div class="card">
                    <h6 class="fw-bold mb-2">🍲 Recettes & Modèles Rapides</h6>
                    <form action="/load_recipe" method="POST" class="mb-2">
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
                            <div>
                                <a href="/load_template/{{ t[0] }}" class="btn btn-sm btn-outline-info py-0 px-2">Charger</a>
                                <a href="/del_template/{{ t[0] }}" class="btn btn-sm btn-outline-danger py-0 px-1"><i class="fa fa-times"></i></a>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                    {% endif %}

                    <form action="/save_template" method="POST" class="mt-3">
                        <div class="input-group input-group-sm">
                            <input type="text" name="title" class="form-control" placeholder="Nom de la liste courante..." required>
                            <button type="submit" class="btn btn-outline-warning">Sauvegarder Liste</button>
                        </div>
                    </form>
                </div>

                <!-- Budget & Répartition -->
                <div class="card">
                    <h6 class="fw-bold mb-2">📊 Budget Max</h6>
                    <form action="/set_budget" method="POST" class="mb-3">
                        <div class="input-group input-group-sm">
                            <input type="number" step="any" name="val" class="form-control" value="{{ "%.0f"|format(budget) }}" placeholder="Nouveau budget..." required>
                            <span class="input-group-text bg-secondary text-white border-secondary">{{ devise }}</span>
                            <button type="submit" class="btn btn-primary fw-bold">Modifier</button>
                        </div>
                    </form>

                    <hr class="border-secondary my-2">

                    <!-- Graphique Camembert Chart.js -->
                    <h6 class="fw-bold mb-2 small text-uppercase text-secondary">Graphique des Dépenses</h6>
                    <div style="max-width: 250px; margin: 0 auto;">
                        <canvas id="categoryChart"></canvas>
                    </div>
                </div>
            </div>

            <!-- Colonne Droite : Total, Filtres & Liste -->
            <div class="col-lg-7">
                <div class="card text-center">
                    <span class="small text-uppercase text-secondary fw-bold">Total Actuel</span>
                    <div class="total-display my-1">
                        {{ "%.0f"|format(total) }} <span style="font-size: 1.5rem;">{{ devise }}</span>
                    </div>
                    
                    <div class="d-flex gap-2 mt-2 no-print">
                        <button onclick="copyWA()" class="btn btn-success flex-grow-1 btn-action"><i class="fab fa-whatsapp me-1"></i> Partager</button>
                        <button onclick="window.print()" class="btn btn-outline-info btn-action"><i class="fa fa-print"></i></button>
                        <a href="/cloturer" class="btn btn-outline-danger btn-action" onclick="return confirm('Clôturer et enregistrer la liste actuelle ?')">🏁 Finir</a>
                    </div>
                </div>

                <!-- Recherche et Filtres de Catégories -->
                <div class="card no-print mb-3 py-2">
                    <input type="text" id="searchInput" onkeyup="filterItems()" class="form-control mb-2" placeholder="🔍 Rechercher un article...">
                    
                    <div class="d-flex flex-wrap gap-1" id="categoryFilters">
                        <span class="cat-filter-btn active" onclick="setCategoryFilter('ALL', this)">Tous</span>
                        {% for c in categories %}
                            <span class="cat-filter-btn" onclick="setCategoryFilter('{{c}}', this)">{{c}}</span>
                        {% endfor %}
                    </div>
                </div>

                <!-- Liste des articles -->
                <div class="list-group mb-4" id="itemsList">
                    {% for item in liste %}
                    <div class="list-group-item d-flex justify-content-between align-items-center {{ 'done' if item[5] }}" data-cat="{{ item[6] }}">
                        <div class="me-2">
                            <span class="fw-bold item-n d-block">{{ item[2] }} <small class="text-secondary">(x{{ item[4] }})</small></span>
                            <span class="badge rounded-pill mt-1" style="background: {{ config[item[6]] }}; font-weight: 500;">{{ item[6] }}</span>
                        </div>
                        <div class="text-end">
                            <span class="fw-bold d-block text-warning" style="font-size: 1.1rem;">{{ "%.0f"|format(item[3] * item[4]) }} {{ devise }}</span>
                            <div class="no-print mt-1">
                                <a href="/check/{{ item[0] }}" class="text-success me-3 text-decoration-none"><i class="fa fa-check-circle fa-lg"></i></a>
                                <a href="/del/{{ item[0] }}" class="text-danger text-decoration-none"><i class="fa fa-trash fa-lg"></i></a>
                            </div>
                        </div>
                    </div>
                    {% else %}
                    <div class="text-center text-secondary py-4">Votre panier est vide pour l'instant ! 🛒</div>
                    {% endfor %}
                </div>

                <!-- Historique avec Graphique Mensuel -->
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
                            <span class="fw-bold text-info">{{ "%.0f"|format(h[1]) }} {{ devise }}</span>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                {% endif %}
            </div>
        </div>
    </div>

    <script>
        let currentCatFilter = 'ALL';
        const isBudgetOver = {{ 'true' if total > budget else 'false' }};

        // ALERTE SONORE EN CAS DE DÉPASSEMENT DE BUDGET
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
            } catch(e) {}
        }

        if (isBudgetOver) {
            window.addEventListener('load', () => setTimeout(playAlertBeep, 500));
        }

        // SERVICE WORKER & BOUTON D'INSTALLATION PWA
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
                    if (choiceResult.outcome === 'accepted') {
                        installBtn.classList.add('d-none');
                    }
                    deferredPrompt = null;
                });
            }
        });

        // GESTION DU THÈME
        function applyTheme(theme) {
            document.documentElement.setAttribute('data-theme', theme);
            const btn = document.getElementById('themeBtn');
            if (theme === 'light') {
                btn.innerHTML = '<i class="fa fa-sun text-warning"></i>';
            } else {
                btn.innerHTML = '<i class="fa fa-moon"></i>';
            }
            localStorage.setItem('theme', theme);
        }

        function toggleTheme() {
            const current = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
            applyTheme(current);
        }

        const savedTheme = localStorage.getItem('theme') || 'dark';
        applyTheme(savedTheme);

        // FILTRAGE ET RECHERCHE
        function filterItems() {
            let search = document.getElementById('searchInput').value.toLowerCase();
            let items = document.querySelectorAll('#itemsList .list-group-item');
            
            items.forEach(item => {
                let name = item.querySelector('.item-n').innerText.toLowerCase();
                let cat = item.getAttribute('data-cat');
                
                let matchesSearch = name.includes(search);
                let matchesCat = (currentCatFilter === 'ALL' || cat === currentCatFilter);
                
                if (matchesSearch && matchesCat) {
                    item.style.display = "flex";
                } else {
                    item.style.display = "none";
                }
            });
        }

        function setCategoryFilter(cat, btnElement) {
            currentCatFilter = cat;
            document.querySelectorAll('#categoryFilters .cat-filter-btn').forEach(b => b.classList.remove('active'));
            btnElement.classList.add('active');
            filterItems();
        }

        // PARTAGE ET INVITATIONS
        function invite() { 
            window.open("https://wa.me/?text=" + encodeURIComponent("Salut ! Gère ton budget courses simplement ici : {{url}}")); 
        }
        
        function copyWA() {
            let t = "*🛒 MA LISTE SmartPanier*\n\n";
            let items = document.querySelectorAll('.list-group-item:not(.done)');
            if(items.length === 0) { alert("Votre liste est vide !"); return; }
            
            items.forEach(i => {
                t += "🔹 " + i.querySelector('.item-n').innerText + "\n";
            });
            t += "\n*💰 TOTAL : " + document.querySelector('.total-display').innerText.trim() + "*\n\n_Géré avec SmartPanier : {{url}}_";
            navigator.clipboard.writeText(t).then(() => alert("Liste copiée pour WhatsApp !"));
        }

        // CHARTS CHART.JS
        document.addEventListener("DOMContentLoaded", function() {
            // Chart Camembert
            const chartData = {{ chart_data | tojson }};
            const ctxPie = document.getElementById('categoryChart').getContext('2d');
            new Chart(ctxPie, {
                type: 'doughnut',
                data: {
                    labels: chartData.labels,
                    datasets: [{
                        data: chartData.data,
                        backgroundColor: chartData.colors,
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } }
                }
            });

            // Chart Historique Mensuel
            {% if histo %}
            const histoData = {{ histo_data | tojson }};
            const ctxLine = document.getElementById('monthlyChart').getContext('2d');
            new Chart(ctxLine, {
                type: 'line',
                data: {
                    labels: histoData.labels,
                    datasets: [{
                        label: 'Dépenses',
                        data: histoData.totals,
                        borderColor: '#3b82f6',
                        backgroundColor: 'rgba(59, 130, 246, 0.2)',
                        fill: true,
                        tension: 0.3
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { ticks: { color: '#94a3b8' } },
                        y: { ticks: { color: '#94a3b8' } }
                    }
                }
            });
            {% endif %}
        });
    </script>
</body>
</html>
"""

# --- ROUTES FLASK ---

@app.route('/manifest.json')
def manifest():
    return Response(MANIFEST_JSON, mimetype="application/json")

@app.route('/sw.js')
def sw():
    return Response(SW_JS, mimetype="application/javascript")

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u = request.form['user'].strip()
        p = generate_password_hash(request.form['pass'])
        try:
            with sqlite3.connect(DB_NAME) as conn: 
                conn.execute("INSERT INTO users (username, password) VALUES (?,?)", (u,p))
                conn.commit()
            flash("Compte créé avec succès ! Connectez-vous.")
            return redirect(url_for('login'))
        except: 
            flash("Ce nom d'utilisateur est déjà pris.")
    return render_template_string(AUTH_HTML, title="Inscription", btn="CRÉER MON COMPTE")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = request.form['user'].strip()
        p = request.form['pass']
        with sqlite3.connect(DB_NAME) as conn:
            r = conn.execute("SELECT id, password FROM users WHERE username=?", (u,)).fetchone()
            if r and check_password_hash(r[1], p):
                session['uid'], session['user'] = r[0], u
                return redirect(url_for('home'))
        flash("Identifiants incorrects.")
    return render_template_string(AUTH_HTML, title="Login", btn="SE CONNECTER")

@app.route('/logout')
def logout(): 
    session.clear()
    return redirect(url_for('home'))

@app.route('/profile')
def profile():
    if 'uid' not in session: return redirect(url_for('login'))
    return render_template_string(PROFILE_HTML, username=session['user'])

@app.route('/change_password', methods=['POST'])
def change_password():
    if 'uid' not in session: return redirect(url_for('login'))
    
    old_p = request.form.get('old_pass')
    new_p = request.form.get('new_pass')
    
    with sqlite3.connect(DB_NAME) as conn:
        r = conn.execute("SELECT password FROM users WHERE id=?", (session['uid'],)).fetchone()
        if r and check_password_hash(r[0], old_p):
            conn.execute("UPDATE users SET password=? WHERE id=?", (generate_password_hash(new_p), session['uid']))
            conn.commit()
            flash("Mot de passe mis à jour avec succès !")
        else:
            flash("L'ancien mot de passe est incorrect.")
            
    return redirect(url_for('profile'))

@app.route('/clear_history')
def clear_history():
    if 'uid' in session:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("DELETE FROM historique WHERE user_id=?", (session['uid'],))
            conn.commit()
            flash("Historique effacé.")
    return redirect(url_for('profile'))

@app.route('/reset_all')
def reset_all():
    if 'uid' in session:
        uid = session['uid']
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("DELETE FROM courses WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM historique WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM templates WHERE user_id=?", (uid,))
            conn.commit()
            flash("Toutes vos données ont été réinitialisées.")
    return redirect(url_for('home'))

@app.route('/export_csv')
def export_csv():
    if 'uid' not in session: return redirect(url_for('login'))
    
    devise = session.get('devise', 'FCFA')
    with sqlite3.connect(DB_NAME) as conn:
        items = conn.execute("SELECT nom, qte, prix, cat FROM courses WHERE user_id=?", (session['uid'],)).fetchall()
        
    csv_data = f"Nom,Quantite,Prix Unitaire ({devise}),Total ({devise}),Categorie\n"
    for item in items:
        total = item[1] * item[2]
        csv_data += f'"{item[0]}",{item[1]},{item[2]},{total},"{item[3]}"\n'
        
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=Ma_Liste_SmartPanier.csv"}
    )

@app.route('/')
def home():
    if 'uid' not in session: 
        return render_template_string(LANDING_HTML)
    
    uid = session['uid']
    
    budget_user = session.get('budget', 50000.0)
    devise_user = session.get('devise', 'FCFA')

    with sqlite3.connect(DB_NAME) as conn:
        liste = conn.execute("SELECT * FROM courses WHERE user_id=? ORDER BY fait ASC, id DESC", (uid,)).fetchall()
        total = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=? AND fait=0", (uid,)).fetchone()[0] or 0
        histo = conn.execute("SELECT id, total, nb_articles, date_achat FROM historique WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,)).fetchall()
        templates = conn.execute("SELECT id, user_id, title FROM templates WHERE user_id=?", (uid,)).fetchall()

        stats = {}
        chart_labels, chart_data, chart_colors = [], [], []
        glob = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=?", (uid,)).fetchone()[0] or 1
        for c, color in CAT_CONFIG.items():
            s = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=? AND cat=?", (uid, c)).fetchone()[0] or 0
            stats[c] = {"p": int((s/glob)*100), "c": color}
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
        username=session['user'], 
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

@app.route('/load_recipe', methods=['POST'])
def load_recipe():
    if 'uid' in session:
        r_name = request.form.get('recipe_name')
        if r_name in PRESET_RECIPES:
            items = PRESET_RECIPES[r_name]
            with sqlite3.connect(DB_NAME) as conn:
                for item in items:
                    conn.execute("INSERT INTO courses (user_id, nom, prix, qte, fait, cat) VALUES (?,?,?,?,0,?)",
                                 (session['uid'], item['nom'], item['prix'], item['qte'], item['cat']))
                conn.commit()
    return redirect(url_for('home'))

@app.route('/save_template', methods=['POST'])
def save_template():
    if 'uid' in session:
        title = request.form.get('title', 'Ma liste').strip()
        with sqlite3.connect(DB_NAME) as conn:
            items = conn.execute("SELECT nom, prix, qte, cat FROM courses WHERE user_id=?", (session['uid'],)).fetchall()
            items_json = json.dumps([{"nom": x[0], "prix": x[1], "qte": x[2], "cat": x[3]} for x in items])
            conn.execute("INSERT INTO templates (user_id, title, items_json) VALUES (?,?,?)", (session['uid'], title, items_json))
            conn.commit()
    return redirect(url_for('home'))

@app.route('/load_template/<int:template_id>')
def load_template(template_id):
    if 'uid' in session:
        with sqlite3.connect(DB_NAME) as conn:
            r = conn.execute("SELECT items_json FROM templates WHERE id=? AND user_id=?", (template_id, session['uid'])).fetchone()
            if r:
                items = json.loads(r[0])
                for item in items:
                    conn.execute("INSERT INTO courses (user_id, nom, prix, qte, fait, cat) VALUES (?,?,?,?,0,?)",
                                 (session['uid'], item['nom'], item['prix'], item['qte'], item['cat']))
                conn.commit()
    return redirect(url_for('home'))

@app.route('/del_template/<int:template_id>')
def del_template(template_id):
    if 'uid' in session:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("DELETE FROM templates WHERE id=? AND user_id=?", (template_id, session['uid']))
            conn.commit()
    return redirect(url_for('home'))

@app.route('/add', methods=['POST'])
def add():
    if 'uid' in session:
        nom = request.form.get('nom').strip()
        qte = int(request.form.get('qte', 1))
        prix = float(request.form.get('prix') or 0)
        cat = request.form.get('cat', '✨ Autre')
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("INSERT INTO courses (user_id, nom, prix, qte, fait, cat) VALUES (?,?,?,?,0,?)",
                         (session['uid'], nom, prix, qte, cat))
            conn.commit()
    return redirect(url_for('home'))

@app.route('/check/<int:item_id>')
def check(item_id):
    if 'uid' in session:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("UPDATE courses SET fait = NOT fait WHERE id=? AND user_id=?", (item_id, session['uid']))
            conn.commit()
    return redirect(url_for('home'))

@app.route('/del/<int:item_id>')
def delete(item_id):
    if 'uid' in session:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("DELETE FROM courses WHERE id=? AND user_id=?", (item_id, session['uid']))
            conn.commit()
    return redirect(url_for('home'))

@app.route('/set_budget', methods=['POST'])
def set_budget():
    if 'uid' in session:
        try:
            session['budget'] = float(request.form.get('val', 50000))
        except ValueError:
            pass
    return redirect(url_for('home'))

@app.route('/set_devise', methods=['POST'])
def set_devise():
    if 'uid' in session:
        session['devise'] = request.form.get('devise', 'FCFA')
    return redirect(url_for('home'))

@app.route('/cloturer')
def cloturer():
    if 'uid' in session:
        uid = session['uid']
        with sqlite3.connect(DB_NAME) as conn:
            total = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=? AND fait=0", (uid,)).fetchone()[0] or 0
            count = conn.execute("SELECT COUNT(*) FROM courses WHERE user_id=? AND fait=0", (uid,)).fetchone()[0] or 0
            if total > 0 or count > 0:
                conn.execute("INSERT INTO historique (user_id, total, nb_articles) VALUES (?,?,?)", (uid, total, count))
                conn.execute("DELETE FROM courses WHERE user_id=?", (uid,))
                conn.commit()
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)
