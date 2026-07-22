import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, Response
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

MAIN_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <title>SmartPanier - Dashboard</title>
    <style>
        :root { --bg: #0f172a; --card: #1e293b; --border: #334155; }
        body { background: var(--bg); color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; padding-bottom: 40px; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; margin-bottom: 16px; padding: 18px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
        .form-control, .form-select { background: #0f172a !important; border: 1px solid var(--border) !important; color: white !important; padding: 10px 14px; font-size: 15px; }
        .form-control:focus, .form-select:focus { box-shadow: none; border-color: #3b82f6 !important; }
        .total-display { color: #f59e0b; font-weight: 900; font-size: 2.8rem; line-height: 1.1; }
        .budget-over { color: #ef4444 !important; animation: shake 0.5s; }
        .list-group-item { background: var(--card); color: white; border: 1px solid var(--border); margin-bottom: 8px; border-radius: 12px !important; padding: 12px 16px; transition: all 0.2s; }
        .done { opacity: 0.4; text-decoration: line-through; }
        .btn-action { padding: 10px; font-weight: bold; border-radius: 10px; }
        @keyframes shake { 0%, 100% { transform: translateX(0); } 25% { transform: translateX(-4px); } 75% { transform: translateX(4px); } }
        @media print { .no-print { display: none !important; } body { background: white; color: black; } .card { border: none; } }
    </style>
</head>
<body>
    <div class="container" style="max-width: 980px;">
        <!-- Header -->
        <div class="d-flex justify-content-between align-items-center my-3 no-print">
            <h5 class="mb-0 fw-bold">👤 {{ username }}</h5>
            <div>
                <a href="/export_csv" class="btn btn-sm btn-outline-success me-1"><i class="fa fa-file-excel"></i> Excel</a>
                <button onclick="invite()" class="btn btn-sm btn-outline-info me-1"><i class="fa fa-gift"></i> Inviter</button>
                <a href="/logout" class="btn btn-sm btn-outline-danger"><i class="fa fa-sign-out-alt"></i></a>
            </div>
        </div>

        <div class="row g-3">
            <!-- Colonne Gauche : Formulaire et Budget -->
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
                                <input type="number" step="any" name="prix" class="form-control" placeholder="Prix unitaire (FCFA)">
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

                <!-- Répartition et Modification du Budget -->
                <div class="card">
                    <h6 class="fw-bold mb-2">📊 Budget Max</h6>
                    <form action="/set_budget" method="POST" class="mb-3">
                        <div class="input-group input-group-sm">
                            <input type="number" step="any" name="val" class="form-control" value="{{ "%.0f"|format(budget) }}" placeholder="Nouveau budget..." required>
                            <span class="input-group-text bg-secondary text-white border-secondary">FCFA</span>
                            <button type="submit" class="btn btn-primary fw-bold">Modifier</button>
                        </div>
                    </form>

                    <hr class="border-secondary my-2">

                    {% for c, v in stats.items() %}
                    <div class="mb-2">
                        <div class="d-flex justify-content-between small mb-1">
                            <span>{{c}}</span>
                            <span class="fw-bold">{{v.p}}%</span>
                        </div>
                        <div style="background: #0f172a; height: 6px; border-radius: 4px; overflow: hidden;">
                            <div style="width: {{v.p}}%; background: {{v.c}}; height: 100%;"></div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <!-- Colonne Droite : Total, Recherche et Liste -->
            <div class="col-lg-7">
                <div class="card text-center">
                    <span class="small text-uppercase text-secondary fw-bold">Total Actuel</span>
                    <div class="total-display my-1 {{ 'budget-over' if total > budget }}">
                        {{ "%.0f"|format(total) }} <span style="font-size: 1.5rem;">FCFA</span>
                    </div>
                    {% if total > budget %}
                        <div class="text-danger small fw-bold mb-2">⚠️ Budget max dépassé de {{ "%.0f"|format(total - budget) }} FCFA !</div>
                    {% endif %}
                    
                    <div class="d-flex gap-2 mt-2 no-print">
                        <button onclick="copyWA()" class="btn btn-success flex-grow-1 btn-action"><i class="fab fa-whatsapp me-1"></i> Partager</button>
                        <button onclick="window.print()" class="btn btn-outline-info btn-action"><i class="fa fa-print"></i></button>
                        <a href="/cloturer" class="btn btn-outline-danger btn-action" onclick="return confirm('Clôturer et enregistrer la liste actuelle ?')">🏁 Finir</a>
                    </div>
                </div>

                <!-- Barre de recherche rapide -->
                <div class="mb-3 no-print">
                    <input type="text" id="searchInput" onkeyup="filterList()" class="form-control" placeholder="🔍 Rechercher un article dans la liste...">
                </div>

                <!-- Items Liste -->
                <div class="list-group mb-4" id="itemsList">
                    {% for item in liste %}
                    <div class="list-group-item d-flex justify-content-between align-items-center {{ 'done' if item[5] }}">
                        <div class="me-2">
                            <span class="fw-bold item-n d-block">{{ item[2] }} <small class="text-secondary">(x{{ item[4] }})</small></span>
                            <span class="badge rounded-pill mt-1" style="background: {{ config[item[6]] }}; font-weight: 500;">{{ item[6] }}</span>
                        </div>
                        <div class="text-end">
                            <span class="fw-bold d-block text-warning" style="font-size: 1.1rem;">{{ "%.0f"|format(item[3] * item[4]) }} f</span>
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

                <!-- Historique -->
                {% if histo %}
                <div class="card no-print">
                    <h6 class="fw-bold mb-3"><i class="fa fa-history text-info me-2"></i> Derniers Achats Clôturés</h6>
                    <div class="list-group list-group-flush">
                        {% for h in histo %}
                        <div class="d-flex justify-content-between align-items-center py-2 border-bottom border-secondary">
                            <div>
                                <small class="text-secondary d-block">{{ h[3] }}</small>
                                <span class="small">{{ h[2] }} article(s)</span>
                            </div>
                            <span class="fw-bold text-info">{{ "%.0f"|format(h[1]) }} FCFA</span>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                {% endif %}
            </div>
        </div>
    </div>

    <script>
        function filterList() {
            let input = document.getElementById('searchInput').value.toLowerCase();
            let items = document.querySelectorAll('#itemsList .list-group-item');
            
            items.forEach(item => {
                let text = item.querySelector('.item-n').innerText.toLowerCase();
                if (text.includes(input)) {
                    item.style.display = "flex";
                } else {
                    item.style.display = "none";
                }
            });
        }

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
    </script>
</body>
</html>
"""

# --- ROUTES FLASK ---

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

@app.route('/export_csv')
def export_csv():
    if 'uid' not in session: return redirect(url_for('login'))
    
    with sqlite3.connect(DB_NAME) as conn:
        items = conn.execute("SELECT nom, qte, prix, cat FROM courses WHERE user_id=?", (session['uid'],)).fetchall()
        
    csv_data = "Nom,Quantite,Prix Unitaire (FCFA),Total (FCFA),Categorie\n"
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
    
    # Récupérer le budget depuis la session (par défaut 50000)
    budget_user = session.get('budget', 50000.0)

    with sqlite3.connect(DB_NAME) as conn:
        liste = conn.execute("SELECT * FROM courses WHERE user_id=? ORDER BY fait ASC, id DESC", (uid,)).fetchall()
        total = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=? AND fait=0", (uid,)).fetchone()[0] or 0
        histo = conn.execute("SELECT id, total, nb_articles, date_achat FROM historique WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,)).fetchall()

        stats = {}
        glob = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=?", (uid,)).fetchone()[0] or 1
        for c, color in CAT_CONFIG.items():
            s = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=? AND cat=?", (uid, c)).fetchone()[0] or 0
            stats[c] = {"p": int((s/glob)*100), "c": color}
            
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
        url=SITE_URL
    )

@app.route('/set_budget', methods=['POST'])
def set_budget():
    if 'uid' in session:
        try:
            val = float(request.form.get('val', 50000))
            if val >= 0:
                session['budget'] = val  # Sauvegardé directement en session
        except (ValueError, TypeError):
            pass
    return redirect(url_for('home'))

@app.route('/add', methods=['POST'])
def add():
    if 'uid' in session:
        n = request.form.get('nom').strip()
        p = float(request.form.get('prix', 0) or 0)
        q = int(request.form.get('qte', 1) or 1)
        c = request.form.get('cat')
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("INSERT INTO courses (user_id, nom, prix, qte, fait, cat) VALUES (?,?,?,?,0,?)", (session['uid'], n, p, q, c))
            conn.commit()
    return redirect(url_for('home'))

@app.route('/check/<int:id>')
def check(id):
    if 'uid' in session:
        with sqlite3.connect(DB_NAME) as conn: 
            conn.execute("UPDATE courses SET fait = NOT fait WHERE id=? AND user_id=?", (id, session['uid']))
            conn.commit()
    return redirect(url_for('home'))

@app.route('/del/<int:id>')
def delete(id):
    if 'uid' in session:
        with sqlite3.connect(DB_NAME) as conn: 
            conn.execute("DELETE FROM courses WHERE id=? AND user_id=?", (id, session['uid']))
            conn.commit()
    return redirect(url_for('home'))

@app.route('/cloturer')
def cloturer():
    if 'uid' in session:
        uid = session['uid']
        with sqlite3.connect(DB_NAME) as conn:
            res = conn.execute("SELECT SUM(prix*qte), COUNT(*) FROM courses WHERE user_id=?", (uid,)).fetchone()
            total = res[0] or 0
            nb = res[1] or 0
            
            if total > 0:
                conn.execute("INSERT INTO historique (user_id, total, nb_articles) VALUES (?,?,?)", (uid, total, nb))
                conn.execute("DELETE FROM courses WHERE user_id=?", (uid,))
                conn.commit()
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True))
