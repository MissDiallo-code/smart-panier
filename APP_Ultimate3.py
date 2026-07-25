import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import os

app = Flask(__name__)
app.secret_key = "votre_cle_secrete_ultra_securisee" # Changez-la pour la mise en ligne
DB_NAME = "courses_multiusers.db"
BUDGET_MAX = 100000.0 

# --- INITIALISATION DE LA BASE DE DONNÉES ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        # Table des utilisateurs
        conn.execute('''CREATE TABLE IF NOT EXISTS users 
            (id INTEGER PRIMARY KEY AUTOINCREMENT, 
             username TEXT UNIQUE NOT NULL, 
             password TEXT NOT NULL)''')
        # Table des courses (avec user_id)
        conn.execute('''CREATE TABLE IF NOT EXISTS courses 
            (id INTEGER PRIMARY KEY AUTOINCREMENT, 
             user_id INTEGER,
             nom TEXT, prix REAL, qte INTEGER, fait BOOLEAN, cat TEXT, 
             date_ajout DATETIME DEFAULT CURRENT_TIMESTAMP,
             FOREIGN KEY(user_id) REFERENCES users(id))''')
        # Table historique (avec user_id)
        conn.execute('''CREATE TABLE IF NOT EXISTS historique 
            (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, total REAL, date_achat DATETIME)''')
init_db()

CAT_CONFIG = {
    "🥦  Fruits & Légumes": "#27ae60", "🥩 Protéines": "#e74c3c", 
    "🥖 Boulangerie": "#f1c40f", "🥛 Produits Laitiers": "#3498db", 
    "🥤 Boissons": "#9b59b6", "✨ Autre": "#34495e"
}

# --- TEMPLATE CONNEXION / INSCRIPTION ---
AUTH_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #0b1120; color: white; display: flex; align-items: center; justify-content: center; height: 100vh; }
        .auth-card { background: #1e293b; padding: 30px; border-radius: 15px; width: 100%; max-width: 400px; border: 1px solid #334155; }
        .form-control { background: #0f172a; border: 1px solid #334155; color: white; }
    </style>
    <title>{{ title }}</title>
</head>
<body>
    <div class="auth-card">
        <h2 class="text-center mb-4">🛒 {{ title }}</h2>
        {% with messages = get_flashed_messages() %}
            {% if messages %}{% for msg in messages %}<div class="alert alert-danger">{{ msg }}</div>{% endfor %}{% endif %}
        {% endwith %}
        <form method="POST">
            <input type="text" name="username" class="form-control mb-3" placeholder="Nom d'utilisateur" required>
            <input type="password" name="password" class="form-control mb-3" placeholder="Mot de passe" required>
            <button type="submit" class="btn btn-warning w-100 fw-bold">{{ btn_text }}</button>
        </form>
        <p class="mt-3 text-center small">
            {% if title == "Connexion" %} Pas de compte ? <a href="/register" class="text-info">S'inscrire</a>
            {% else %} Déjà un compte ? <a href="/login" class="text-info">Se connecter</a>{% endif %}
        </p>
    </div>
</body>
</html>
"""

# --- ROUTES AUTHENTIFICATION ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        user = request.form['username']
        pwd = generate_password_hash(request.form['password'])
        try:
            with sqlite3.connect(DB_NAME) as conn:
                conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (user, pwd))
            flash("Compte créé ! Connectez-vous.")
            return redirect(url_for('login'))
        except:
            flash("Ce nom d'utilisateur existe déjà.")
    return render_template_string(AUTH_HTML, title="Inscription", btn_text="CRÉER MON COMPTE")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = request.form['username']
        pwd = request.form['password']
        with sqlite3.connect(DB_NAME) as conn:
            res = conn.execute("SELECT id, password FROM users WHERE username = ?", (user,)).fetchone()
            if res and check_password_hash(res[1], pwd):
                session['user_id'] = res[0]
                session['username'] = user
                return redirect(url_for('home'))
        flash("Identifiants incorrects.")
    return render_template_string(AUTH_HTML, title="Connexion", btn_text="SE CONNECTER")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- ROUTES APPLICATION (PROTÉGÉES) ---
@app.route('/')
def home():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    uid = session['user_id']
    query = request.args.get('q', '')
    
    with sqlite3.connect(DB_NAME) as conn:
        favs = conn.execute("SELECT nom, COUNT(nom) as freq FROM courses WHERE user_id=? GROUP BY nom ORDER BY freq DESC LIMIT 5", (uid,)).fetchall()
        liste = conn.execute("SELECT * FROM courses WHERE user_id=? AND nom LIKE ? ORDER BY fait ASC, cat ASC", (uid, '%'+query+'%')).fetchall()
        
        total_art = conn.execute("SELECT COUNT(*) FROM courses WHERE user_id=?", (uid,)).fetchone()[0] or 0
        coches = conn.execute("SELECT COUNT(*) FROM courses WHERE user_id=? AND fait = 1", (uid,)).fetchone()[0] or 0
        prog = int((coches / total_art * 100)) if total_art > 0 else 0
        total_actuel = conn.execute("SELECT SUM(prix * qte) FROM courses WHERE user_id=? AND fait = 0", (uid,)).fetchone()[0] or 0
        
        stats = {}
        global_total = conn.execute("SELECT SUM(prix * qte) FROM courses WHERE user_id=?", (uid,)).fetchone()[0] or 1
        for cat, color in CAT_CONFIG.items():
            cat_sum = conn.execute("SELECT SUM(prix * qte) FROM courses WHERE user_id=? AND cat = ?", (uid, cat)).fetchone()[0] or 0
            percent = int((cat_sum / global_total) * 100)
            stats[cat] = {"percent": percent, "color": color}

    # Note: On réutilise ton HTML_PAGE précédent ici (pense à ajouter le bouton Logout dans le HTML)
    from App_Ultimate2 import HTML_PAGE # Ou copier le contenu ici
    return render_template_string(HTML_PAGE, liste=liste, total=total_actuel, progression=prog, favoris=favs, stats=stats, query=query, categories=list(CAT_CONFIG.keys()), config=CAT_CONFIG, budget_max=BUDGET_MAX, username=session['username'])

# Les autres routes (ajouter, cocher, supprimer) doivent maintenant inclure "user_id = session['user_id']" dans chaque requête SQL.

if __name__ == '__main__':
    app.run(debug=True)