import sqlite3
import json
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, Response
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
    ]
}

MANIFEST_JSON = """{
  "short_name": "SmartPanier",
  "name": "SmartPanier - Gestion de Courses & Budget",
  "icons": [{"src": "https://cdn-icons-png.flaticon.com/512/3081/3081986.png", "type": "image/png", "sizes": "512x512"}],
  "start_url": "/",
  "background_color": "#0f172a",
  "theme_color": "#1e293b",
  "display": "standalone"
}"""

SW_JS = """const CACHE_NAME = 'smartpanier-v3';
const urlsToCache = ['/', 'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css', 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css', 'https://cdn.jsdelivr.net/npm/chart.js'];
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(urlsToCache))));
self.addEventListener('fetch', e => e.respondWith(fetch(e.request).catch(() => caches.match(e.request))));"""

MAIN_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="manifest" href="/manifest.json">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <title>SmartPanier</title>
    <style>
        :root { --bg: #0f172a; --card: #1e293b; --border: #334155; --text: #f8fafc; }
        body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; padding-bottom: 40px; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; margin-bottom: 16px; padding: 18px; }
        .form-control, .form-select { background: #0f172a !important; border: 1px solid var(--border) !important; color: white !important; }
        .total-display { color: #f59e0b; font-weight: 900; font-size: 2.5rem; }
        .list-group-item { background: var(--card); color: var(--text); border: 1px solid var(--border); margin-bottom: 8px; border-radius: 12px !important; }
        .done { opacity: 0.4; text-decoration: line-through; }
    </style>
</head>
<body>
    <div class="container" style="max-width: 980px;">
        <div class="d-flex justify-content-between align-items-center my-3">
            <h5 class="mb-0 fw-bold">👤 {{ username }}</h5>
            <div class="d-flex gap-2">
                <form action="/set_devise" method="POST" class="m-0">
                    <select name="devise" onchange="this.form.submit()" class="form-select form-select-sm">
                        {% for d in devises %}<option value="{{d}}" {% if d == devise %}selected{% endif %}>{{d}}</option>{% endfor %}
                    </select>
                </form>
                <a href="/logout" class="btn btn-sm btn-outline-danger"><i class="fa fa-sign-out-alt"></i></a>
            </div>
        </div>

        {% with messages = get_flashed_messages() %}
            {% if messages %}
                {% for msg in messages %}
                    <div class="alert alert-info py-2 small mb-3">{{ msg }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        <div class="row g-3">
            <div class="col-lg-5">
                <!-- Formulaire d'ajout d'article -->
                <div class="card">
                    <h6 class="fw-bold mb-3">➕ Ajouter un article</h6>
                    <form action="/add" method="POST">
                        <input type="text" name="nom" class="form-control mb-2" placeholder="Nom du produit (ex: Pain)" required>
                        <div class="row g-2 mb-2">
                            <div class="col-5"><input type="number" name="qte" class="form-control" value="1" min="1"></div>
                            <div class="col-7"><input type="number" step="any" name="prix" class="form-control" placeholder="Prix ({{ devise }})"></div>
                        </div>
                        <select name="cat" class="form-select mb-3">
                            {% for c in categories %}<option value="{{c}}">{{c}}</option>{% endfor %}
                        </select>
                        <button type="submit" class="btn btn-warning w-100 fw-bold">AJOUTER AU PANIER</button>
                    </form>
                </div>

                <!-- Section Modèles et Recettes -->
                <div class="card">
                    <h6 class="fw-bold mb-2">🍲 Recettes & Modèles Rapides</h6>
                    <form action="/load_recipe" method="POST" class="mb-3">
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
                    <h6 class="fw-bold mb-2 small text-uppercase text-secondary">MES MODÈLES SAUVEGARDÉS</h6>
                    <div class="d-flex flex-column gap-2 mb-3">
                        {% for t in templates %}
                        <div class="d-flex justify-content-between align-items-center bg-dark p-2 rounded border border-secondary">
                            <span class="small fw-bold text-truncate" style="max-width: 180px;">{{ t[2] }}</span>
                            <div>
                                <a href="/load_template/{{ t[0] }}" class="btn btn-sm btn-info py-0 px-2 fw-bold">Charger</a>
                                <a href="/del_template/{{ t[0] }}" class="btn btn-sm btn-outline-danger py-0 px-1"><i class="fa fa-times"></i></a>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                    {% endif %}

                    <form action="/save_template" method="POST">
                        <div class="input-group input-group-sm">
                            <input type="text" name="title" class="form-control" placeholder="Nom de la liste..." required>
                            <button type="submit" class="btn btn-outline-warning fw-bold">Sauvegarder Liste</button>
                        </div>
                    </form>
                </div>
            </div>

            <div class="col-lg-7">
                <div class="card text-center">
                    <span class="small text-uppercase text-secondary fw-bold">TOTAL ACTUEL</span>
                    <div class="total-display my-1">{{ "%.0f"|format(total) }} {{ devise }}</div>
                    <div class="d-flex gap-2 mt-2">
                        <a href="/cloturer" class="btn btn-danger flex-grow-1 fw-bold" onclick="return confirm('Enregistrer la liste et vider le panier ?')">🏁 Finir</a>
                    </div>
                </div>

                <!-- Contenu du Panier -->
                <div class="list-group mb-4">
                    {% for item in liste %}
                    <div class="list-group-item d-flex justify-content-between align-items-center {{ 'done' if item[5] }}">
                        <div>
                            <span class="fw-bold d-block">{{ item[2] }} <small class="text-secondary">(x{{ item[4] }})</small></span>
                            <span class="badge rounded-pill mt-1" style="background: {{ config[item[6]] }}">{{ item[6] }}</span>
                        </div>
                        <div class="text-end">
                            <span class="fw-bold d-block text-warning">{{ "%.0f"|format(item[3] * item[4]) }} {{ devise }}</span>
                            <a href="/check/{{ item[0] }}" class="text-success me-2"><i class="fa fa-check-circle"></i></a>
                            <a href="/del/{{ item[0] }}" class="text-danger"><i class="fa fa-trash"></i></a>
                        </div>
                    </div>
                    {% else %}
                    <div class="text-center text-secondary py-4">Votre panier est vide pour l'instant ! 🛒</div>
                    {% endfor %}
                </div>

                {% if histo %}
                <div class="card">
                    <h6 class="fw-bold mb-3"><i class="fa fa-history text-info me-2"></i> Historique d'achats</h6>
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
</body>
</html>
"""

# --- ROUTES ---

@app.route('/')
def home():
    if 'uid' not in session: 
        session['uid'] = 1
        session['user'] = 'Utilisateur'
    
    uid = session['uid']
    devise_user = session.get('devise', 'FCFA')

    with sqlite3.connect(DB_NAME) as conn:
        liste = conn.execute("SELECT * FROM courses WHERE user_id=? ORDER BY fait ASC, id DESC", (uid,)).fetchall()
        total = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=? AND fait=0", (uid,)).fetchone()[0] or 0
        histo = conn.execute("SELECT id, total, nb_articles, date_achat FROM historique WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,)).fetchall()
        templates = conn.execute("SELECT id, user_id, title FROM templates WHERE user_id=?", (uid,)).fetchall()

    return render_template_string(
        MAIN_HTML, 
        liste=liste, 
        total=total, 
        username=session['user'], 
        categories=list(CAT_CONFIG.keys()), 
        config=CAT_CONFIG, 
        histo=histo,
        devises=DEVISES,
        devise=devise_user,
        preset_recipes=PRESET_RECIPES,
        templates=templates
    )

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

@app.route('/save_template', methods=['POST'])
def save_template():
    if 'uid' in session:
        title = request.form.get('title', 'Ma liste').strip()
        with sqlite3.connect(DB_NAME) as conn:
            items = conn.execute("SELECT nom, prix, qte, cat FROM courses WHERE user_id=?", (session['uid'],)).fetchall()
            if not items:
                flash("Impossible de sauvegarder : votre panier actuel est vide !")
            else:
                items_json = json.dumps([{"nom": x[0], "prix": x[1], "qte": x[2], "cat": x[3]} for x in items])
                conn.execute("INSERT INTO templates (user_id, title, items_json) VALUES (?,?,?)", (session['uid'], title, items_json))
                conn.commit()
                flash(f"Liste '{title}' sauvegardée avec succès !")
    return redirect(url_for('home'))

@app.route('/load_template/<int:template_id>')
def load_template(template_id):
    if 'uid' in session:
        with sqlite3.connect(DB_NAME) as conn:
            r = conn.execute("SELECT items_json, title FROM templates WHERE id=? AND user_id=?", (template_id, session['uid'])).fetchone()
            if r:
                items = json.loads(r[0])
                for item in items:
                    conn.execute("INSERT INTO courses (user_id, nom, prix, qte, fait, cat) VALUES (?,?,?,?,0,?)",
                                 (session['uid'], item['nom'], item['prix'], item['qte'], item['cat']))
                conn.commit()
                flash(f"Modèle '{r[1]}' chargé dans votre panier !")
    return redirect(url_for('home'))

@app.route('/del_template/<int:template_id>')
def del_template(template_id):
    if 'uid' in session:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("DELETE FROM templates WHERE id=? AND user_id=?", (template_id, session['uid']))
            conn.commit()
            flash("Modèle supprimé.")
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
                flash("Panier clôturé et enregistré dans l'historique !")
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)
