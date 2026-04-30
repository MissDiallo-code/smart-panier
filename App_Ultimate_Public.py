import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "cle_securite_master_789" 
DB_NAME = "courses_multiusers.db"
BUDGET_MAX = 100000.0 
SITE_URL = "https://mon-panier.onrender.com" 

# --- INITIALISATION ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS courses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nom TEXT, prix REAL, qte INTEGER, fait BOOLEAN, cat TEXT, date_ajout DATETIME DEFAULT CURRENT_TIMESTAMP)')
        conn.execute('CREATE TABLE IF NOT EXISTS historique (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, total REAL, date_achat DATETIME)')
init_db()

CAT_CONFIG = {
    "🥦 Fruits & Légumes": "#27ae60", "🥩 Protéines": "#e74c3c", 
    "🥖 Boulangerie": "#f1c40f", "🥛 Laitiers": "#3498db", 
    "🥤 Boissons": "#9b59b6", "✨ Autre": "#34495e"
}

# --- TEMPLATES HTML ---
AUTH_HTML = """
<!DOCTYPE html>
<html lang="fr"><head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #0b1120; color: white; display: flex; align-items: center; justify-content: center; height: 100vh; }
        .auth-card { background: #1e293b; padding: 30px; border-radius: 15px; width: 100%; max-width: 400px; border: 1px solid #334155; }
        .form-control { background: #0f172a; border: 1px solid #334155; color: white; }
    </style><title>{{ title }}</title></head>
<body>
    <div class="auth-card text-center">
        <h3 class="mb-4">🛒 SmartPanier</h3>
        {% with m = get_flashed_messages() %}{% if m %}<div class="alert alert-warning py-1 small">{{m[0]}}</div>{% endif %}{% endwith %}
        <form method="POST">
            <input type="text" name="user" class="form-control mb-3" placeholder="Utilisateur" required>
            <input type="password" name="pass" class="form-control mb-3" placeholder="Mot de passe" required>
            <button type="submit" class="btn btn-warning w-100 fw-bold">{{ btn }}</button>
        </form>
        <div class="mt-3 small">{% if title=="Login" %}<a href="/register" class="text-info">Créer un compte</a>{% else %}<a href="/login" class="text-info">Se connecter</a>{% endif %}</div>
    </div>
</body></html>
"""

LANDING_HTML = """
<!DOCTYPE html>
<html lang="fr"><head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #0b1120; color: white; text-align: center; }
        .hero { padding: 80px 20px; background: linear-gradient(180deg, #1e293b 0%, #0b1120 100%); }
        .btn-start { background: #fbbf24; color: #0b1120; font-weight: 900; padding: 15px 40px; border-radius: 50px; text-decoration: none; display: inline-block; }
    </style><title>Bienvenue sur SmartPanier</title></head>
<body>
    <div class="hero">
        <h1 class="display-4 fw-bold">🛒 SmartPanier</h1>
        <p class="lead text-secondary mb-5">Gérez votre budget courses et partagez vos listes facilement.</p>
        <a href="/register" class="btn btn-start">COMMENCER GRATUITEMENT</a>
        <p class="mt-3 small text-secondary">Déjà membre ? <a href="/login" class="text-info">Connexion</a></p>
    </div>
</body></html>
"""

MAIN_HTML = """
<!DOCTYPE html>
<html lang="fr"><head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <style>
        :root { --bg: #0b1120; --card: #1e293b; --text: #ffffff; }
        body { background: var(--bg); color: var(--text); font-family: sans-serif; padding: 15px; }
        .card { background: var(--card); border: 1px solid #334155; border-radius: 12px; margin-bottom: 15px; padding: 15px; }
        h6, label, .text-muted, .form-label, span { color: #ffffff !important; opacity: 1 !important; }
        .total-display { color: #fbbf24; font-weight: 900; font-size: 2.5rem; }
        .budget-over { color: #ff4757 !important; animation: shake 0.5s; }
        .list-group-item { background: var(--card); color: white; border: 1px solid #334155; margin-bottom: 5px; border-radius: 8px !important; }
        .done { opacity: 0.4; text-decoration: line-through; }
        @keyframes shake { 0%, 100% { transform: translateX(0); } 25% { transform: translateX(-5px); } 75% { transform: translateX(5px); } }
        @media print { .no-print { display: none !important; } body { background: white; color: black; } }
    </style></head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-3 no-print">
            <h5 class="mb-0">👤 {{ username }}</h5>
            <div>
                <button onclick="invite()" class="btn btn-sm btn-outline-info me-1">🎁 Inviter</button>
                <a href="/logout" class="btn btn-sm btn-outline-danger">Quitter</a>
            </div>
        </div>

        <div class="row">
            <div class="col-md-5 no-print">
                <div class="card">
                    <form action="/add" method="POST">
                        <input type="text" name="nom" class="form-control mb-2" placeholder="Nom du produit..." required>
                        <div class="row g-2 mb-2">
                            <div class="col-6"><input type="number" name="qte" class="form-control" value="1"></div>
                            <div class="col-6"><input type="text" name="prix" class="form-control" placeholder="Prix (f)"></div>
                        </div>
                        <select name="cat" class="form-select mb-3">
                            {% for c in categories %}<option value="{{c}}">{{c}}</option>{% endfor %}
                        </select>
                        <button type="submit" class="btn btn-warning w-100 fw-bold">➕ AJOUTER AU PANIER</button>
                    </form>
                </div>
                
                <div class="card">
                    <h6 class="small text-uppercase mb-3">Répartition Budget :</h6>
                    {% for c, v in stats.items() %}
                    <div class="mb-2">
                        <div class="d-flex justify-content-between small"><span>{{c}}</span><span>{{v.p}}%</span></div>
                        <div style="background: #334155; height: 5px; border-radius: 3px; overflow: hidden;">
                            <div style="width: {{v.p}}%; background: {{v.c}}; height: 100%;"></div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <div class="col-md-7">
                <div class="card text-center">
                    <div class="total-display {{ 'budget-over' if total > budget }}">{{ "%.0f"|format(total) }} f</div>
                    {% if total > budget %}<div class="text-danger small mb-2">⚠️ Budget dépassé !</div>{% endif %}
                    <div class="d-flex gap-2 no-print">
                        <button onclick="copyWA()" class="btn btn-success flex-grow-1"><i class="fab fa-whatsapp"></i> Partager</button>
                        <button onclick="window.print()" class="btn btn-info"><i class="fa fa-print"></i></button>
                        <a href="/cloturer" class="btn btn-primary" onclick="return confirm('Vider la liste ?')">🏁 Finir</a>
                    </div>
                </div>

                <div class="list-group mt-3">
                    {% for item in liste %}
                    <div class="list-group-item d-flex justify-content-between align-items-center {{ 'done' if item[5] }}">
                        <div>
                            <b class="item-n">{{ item[2] }} (x{{ item[4] }})</b><br>
                            <span class="badge" style="background: {{ config[item[6]] }}">{{ item[6] }}</span>
                        </div>
                        <div class="text-end">
                            <span class="fw-bold d-block">{{ "%.0f"|format(item[3] * item[4]) }} f</span>
                            <div class="no-print">
                                <a href="/check/{{ item[0] }}" class="text-success me-2"><i class="fa fa-check-circle"></i></a>
                                <a href="/del/{{ item[0] }}" class="text-danger"><i class="fa fa-trash"></i></a>
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
    </div>
    <script>
        function invite() { window.open("https://wa.me/?text=" + encodeURIComponent("Salut ! Gère ton budget courses avec moi ici : {{url}}")); }
        function copyWA() {
            let t = "*🛒 MA LISTE SmartPanier*\\n\\n";
            document.querySelectorAll('.list-group-item:not(.done)').forEach(i => {
                t += "🔹 " + i.querySelector('.item-n').innerText + "\\n";
            });
            t += "\\n*💰 TOTAL : " + document.querySelector('.total-display').innerText.trim() + "*\\n\\n_Géré avec SmartPanier : {{url}}_";
            navigator.clipboard.writeText(t).then(()=>alert("Copié !"));
        }
    </script>
</body></html>
"""

# --- ROUTES ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u, p = request.form['user'], generate_password_hash(request.form['pass'])
        try:
            with sqlite3.connect(DB_NAME) as conn: conn.execute("INSERT INTO users (username, password) VALUES (?,?)", (u,p))
            flash("Compte créé !")
            return redirect(url_for('login'))
        except: flash("Erreur (nom déjà pris ?)")
    return render_template_string(AUTH_HTML, title="Inscription", btn="CRÉER UN COMPTE")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u, p = request.form['user'], request.form['pass']
        with sqlite3.connect(DB_NAME) as conn:
            r = conn.execute("SELECT id, password FROM users WHERE username=?", (u,)).fetchone()
            if r and check_password_hash(r[1], p):
                session['uid'], session['user'] = r[0], u
                return redirect(url_for('home'))
        flash("Identifiants incorrects.")
    return render_template_string(AUTH_HTML, title="Login", btn="SE CONNECTER")

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('home'))

@app.route('/')
def home():
    if 'uid' not in session: return render_template_string(LANDING_HTML)
    uid = session['uid']
    with sqlite3.connect(DB_NAME) as conn:
        liste = conn.execute("SELECT * FROM courses WHERE user_id=? ORDER BY fait ASC, cat ASC", (uid,)).fetchall()
        total = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=? AND fait=0", (uid,)).fetchone()[0] or 0
        stats = {}
        glob = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=?", (uid,)).fetchone()[0] or 1
        for c, color in CAT_CONFIG.items():
            s = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=? AND cat=?", (uid, c)).fetchone()[0] or 0
            stats[c] = {"p": int((s/glob)*100), "c": color}
    return render_template_string(MAIN_HTML, liste=liste, total=total, budget=BUDGET_MAX, username=session['user'], categories=list(CAT_CONFIG.keys()), config=CAT_CONFIG, stats=stats, url=SITE_URL)

@app.route('/add', methods=['POST'])
def add():
    n, p = request.form.get('nom'), float(request.form.get('prix', 0) or 0)
    q, c = int(request.form.get('qte', 1)), request.form.get('cat')
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT INTO courses (user_id, nom, prix, qte, fait, cat) VALUES (?,?,?,?,0,?)", (session['uid'],n,p,q,c))
    return redirect(url_for('home'))

@app.route('/check/<int:id>')
def check(id):
    with sqlite3.connect(DB_NAME) as conn: conn.execute("UPDATE courses SET fait = NOT fait WHERE id=? AND user_id=?", (id, session['uid']))
    return redirect(url_for('home'))

@app.route('/del/<int:id>')
def delete(id):
    with sqlite3.connect(DB_NAME) as conn: conn.execute("DELETE FROM courses WHERE id=? AND user_id=?", (id, session['uid']))
    return redirect(url_for('home'))

@app.route('/cloturer')
def cloturer():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM courses WHERE user_id=?", (session['uid'],))
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)