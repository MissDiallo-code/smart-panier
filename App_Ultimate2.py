import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for
from datetime import datetime

app = Flask(__name__)
DB_NAME = "courses_master.db"
BUDGET_MAX = 100000.0 

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS courses 
            (id INTEGER PRIMARY KEY AUTOINCREMENT, 
             nom TEXT, prix REAL, qte INTEGER, fait BOOLEAN, cat TEXT, date_ajout DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS historique 
            (id INTEGER PRIMARY KEY AUTOINCREMENT, total REAL, date_achat DATETIME)''')
init_db()

CAT_CONFIG = {
    "🥦  Fruits & Légumes": "#27ae60", "🥩 Protéines": "#e74c3c", 
    "🥖 Boulangerie": "#f1c40f", "🥛 Produits Laitiers": "#3498db", 
    "🥤 Boissons": "#9b59b6", "✨ Autre": "#34495e"
}

HTML_PAGE = """
<!DOCTYPE html>
<html lang="fr" id="html-tag">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <title>Mon Panier Ultimate Pro</title>
    <style>
        :root { --bg: #0b1120; --card: #1e293b; --text: #ffffff; --border: #334155; --input-bg: #0f172a; }
        .light-theme { --bg: #f8fafc; --card: #ffffff; --text: #1e293b; --border: #cbd5e1; --input-bg: #ffffff; }
        body { background: var(--bg) !important; color: var(--text) !important; transition: 0.3s; font-family: sans-serif; }
        .card { background: var(--card) !important; border: 2px solid var(--border) !important; border-radius: 12px; padding: 15px; margin-bottom: 15px; }
        .form-control, .form-select { background: var(--input-bg) !important; border: 1px solid var(--border) !important; color: var(--text) !important; }
         h1, h2, h4, h5, h6, label, .text-muted, .form-label, span { color: #ffffff !important; opacity: 1 !important; }
        .form-control, .form-select { background: var(--input-bg) !important; border: 1px solid var(--border) !important; color: var(--text) !important; }
        .form-control::placeholder { color: #94a3b8 !important; }
        .form-control { background: #0f172a !important; border: 1px solid #64748b !important; color: #ffffff !important; font-weight: bold; }
        .form-control, .form-select { background: var(--input-bg) !important; border: 1px solid var(--border) !important; color: var(--text) !important; }

        /* STYLE DU TOTAL ET ALERTE */
        .total-display { color: #fbbf24; font-weight: 900; font-size: 2.5rem; transition: 0.3s; }
        .budget-over { color: #ff4757 !important; text-shadow: 0 0 10px rgba(255, 71, 87, 0.5); animation: shake 0.5s; }
        @keyframes shake { 0% { transform: translateX(0); } 25% { transform: translateX(-5px); } 50% { transform: translateX(5px); } 100% { transform: translateX(0); } }

        .list-group-item { background: var(--card) !important; border: 1px solid var(--border) !important; margin-bottom: 8px; border-radius: 10px !important; color: var(--text) !important; }
        .done-style { opacity: 0.4; filter: grayscale(0.8); text-decoration: line-through; }
        .btn-toggle { position: fixed; top: 15px; right: 15px; z-index: 1000; border-radius: 50%; width: 45px; height: 45px; border: 2px solid #38bdf8; background: var(--card); color: #38bdf8; }
        .btn-cocher { width: 35px; height: 35px; border-radius: 8px; border: 2px solid var(--text); display: flex; align-items: center; justify-content: center; text-decoration: none; font-weight: bold; color: var(--text); margin-right: 12px; }
        .btn-cocher.checked { background: #22c55e !important; border-color: #22c55e !important; color: white !important; }
        .fav-badge { cursor: pointer; border: 1px solid #38bdf8; margin: 3px; display: inline-block; padding: 5px 12px; border-radius: 20px; background: rgba(56, 189, 248, 0.1); color: #38bdf8; text-decoration: none; font-weight: bold; font-size: 0.8rem; }
        
        @media print { 
            .no-print { display: none !important; } 
            body { background: white !important; color: black !important; }
            .card { border: 1px solid #ccc !important; box-shadow: none !important; }
            .total-display { color: black !important; }
        }
    </style>
</head>
<body class="dark-theme">
    <button class="btn-toggle no-print" onclick="toggleTheme()" id="theme-btn">🌙</button>

    <div class="container py-3">
        <h2 class="text-center mb-4 no-print">🛒 PANIER PRO ULTIMATE</h2>
        <a href="/logout" class="btn btn-sm btn-outline-danger no-print">Déconnexion</a>

        <div class="row">
            <div class="col-md-5 no-print">
                <div class="card">
                    <h5 id="form-title">AJOUTER / MODIFIER</h5>
                    <form action="/ajouter" method="POST">
                        <input type="hidden" name="id" id="edit-id">
                        <input type="text" name="nom" id="edit-nom" class="form-control mb-2" placeholder="Nom du produit..." required>
                        <div class="row g-2 mb-2">
                            <div class="col-5"><input type="number" name="qte" id="edit-qte" class="form-control" value="1"></div>
                            <div class="col-7"><input type="text" name="prix" id="edit-prix" class="form-control" placeholder="Prix (f)"></div>
                        </div>
                        <select name="cat" id="edit-cat" class="form-select mb-3">
                            {% for c in categories %}<option value="{{c}}">{{c}}</option>{% endfor %}
                        </select>
                        <button type="submit" id="btn-submit" class="btn btn-warning w-100 fw-bold">➕ AJOUTER AU PANIER</button>
                    </form>
                </div>

                <div class="card">
                    <h6 class="small mb-2 opacity-75">Favoris :</h6>
                    <div class="d-flex flex-wrap">
                        {% for fav in favoris %}<a href="/ajouter_fav/{{ fav[0] }}" class="fav-badge">{{ fav[0] }}</a>{% endfor %}
                    </div>
                </div>

                <div class="card">
                    <h6 class="small mb-3 text-uppercase">Répartition :</h6>
                    {% for cat, val in stats.items() %}
                    <div class="mb-2">
                        <div class="d-flex justify-content-between small"><span>{{ cat }}</span><span>{{ val.percent }}%</span></div>
                        <div style="background: #334155; height: 6px; border-radius: 3px; overflow: hidden;">
                            <div style="width: {{ val.percent }}%; background: {{ val.color }}; height: 100%;"></div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <div class="col-md-7">
                <div class="card text-center">
                    <h5 class="mb-0">TOTAL ESTIMÉ</h5>
                    <div class="total-display {{ 'budget-over' if total > budget_max }}">
                        {{ "%.0f"|format(total) }} f
                    </div>
                    {% if total > budget_max %}
                    <div class="text-danger small fw-bold no-print pulse">⚠️ Budget dépassé de {{ "%.0f"|format(total - budget_max) }} f</div>
                    {% endif %}
                    
                    <div class="progress mt-2 no-print" style="height: 8px; background: #334155;">
                        <div class="progress-bar {{ 'bg-danger' if total > budget_max else 'bg-success' }}" 
                             style="width: {{ progression }}%"></div>
                    </div>

                    <div class="mt-3 d-flex flex-wrap gap-2 no-print">
                        <button onclick="copyToWhatsapp()" class="btn btn-success flex-grow-1 fw-bold">
                            <i class="fa-brands fa-whatsapp"></i> WhatsApp
                        </button>
                        <button onclick="window.print()" class="btn btn-outline-info fw-bold">
                            <i class="fa-solid fa-print"></i> Imprimer
                        </button>
                        <a href="/cloturer" class="btn btn-primary fw-bold" onclick="return confirm('Enregistrer ce ticket et vider la liste ?')">
                            <i class="fa-solid fa-flag-checkered"></i> FINIR
                        </a>
                    </div>
                </div>

                <div class="mb-3 no-print">
                    <form action="/" method="GET" class="d-flex gap-2">
                        <input type="text" name="q" class="form-control" placeholder="Rechercher..." value="{{ query }}">
                        <button type="submit" class="btn btn-secondary">
                            <i class="fa-solid fa-magnifying-glass"></i>
                        </button>
                    </form>
                </div>

                <div class="list-group">
                    {% for item in liste %}
                    <div class="list-group-item d-flex justify-content-between align-items-center {{ 'done-style' if item[4] }}">
                        <div class="d-flex align-items-center">
                            <a href="/cocher/{{ item[0] }}" class="btn-cocher {{ 'checked' if item[4] }} no-print">
                                {% if item[4] %} ✓ {% else %} ○ {% endif %}
                            </a>
                            <div>
                                <div class="item-name fw-bold">{{ item[1] }} (x{{ item[3] }})</div>
                                <span class="badge small" style="background: {{ config[item[5]] }}">{{ item[5] }}</span>
                            </div>
                        </div>
                        <div class="text-end">
                            <div class="fw-bold price-tag" style="color:#fbbf24">{{ "%.0f"|format(item[2] * item[3]) }} f</div>
                            <div class="no-print">
                                <span style="cursor:pointer" class="text-info small me-2" onclick="editItem({{ item[0] }}, '{{ item[1] }}', {{ item[2] }}, {{ item[3] }}, '{{ item[5] }}')">Modifier</span>
                                <a href="/supprimer/{{ item[0] }}" class="text-danger small">X</a>
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
    </div>

    <script>
        function toggleTheme() {
            document.getElementById('html-tag').classList.toggle('light-theme');
        }

        function editItem(id, nom, prix, qte, cat) {
            document.getElementById('edit-id').value = id;
            document.getElementById('edit-nom').value = nom;
            document.getElementById('edit-prix').value = prix;
            document.getElementById('edit-qte').value = qte;
            document.getElementById('edit-cat').value = cat;
            document.getElementById('btn-submit').innerText = "💾 ENREGISTRER MODIF.";
            document.getElementById('btn-submit').className = "btn btn-info w-100 fw-bold";
            window.scrollTo(0,0);
        }

        function copyToWhatsapp() {
            let texte = "*🛒 MA LISTE DE COURSES*\\n\\n";
            document.querySelectorAll('.list-group-item:not(.done-style)').forEach(item => {
                texte += "🔹 " + item.querySelector('.item-name').innerText + " : " + item.querySelector('.price-tag').innerText + "\\n";
            });
            texte += "\\n*💰 TOTAL : " + document.querySelector('.total-display').innerText.trim() + "*";
            navigator.clipboard.writeText(texte).then(() => alert("Copié !"));
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    query = request.args.get('q', '')
    with sqlite3.connect(DB_NAME) as conn:
        favs = conn.execute("SELECT nom, COUNT(nom) as freq FROM courses GROUP BY nom ORDER BY freq DESC LIMIT 5").fetchall()
        liste = conn.execute("SELECT * FROM courses WHERE nom LIKE ? ORDER BY fait ASC, cat ASC, nom ASC", ('%'+query+'%',)).fetchall()
        
        total_art = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0] or 0
        coches = conn.execute("SELECT COUNT(*) FROM courses WHERE fait = 1").fetchone()[0] or 0
        prog = int((coches / total_art * 100)) if total_art > 0 else 0
        total_actuel = conn.execute("SELECT SUM(prix * qte) FROM courses WHERE fait = 0").fetchone()[0] or 0
        
        stats = {}
        global_total = conn.execute("SELECT SUM(prix * qte) FROM courses").fetchone()[0] or 1
        for cat, color in CAT_CONFIG.items():
            cat_sum = conn.execute("SELECT SUM(prix * qte) FROM courses WHERE cat = ?", (cat,)).fetchone()[0] or 0
            percent = int((cat_sum / global_total) * 100)
            stats[cat] = {"percent": percent, "color": color}

    return render_template_string(HTML_PAGE, liste=liste, total=total_actuel, progression=prog, favoris=favs, stats=stats, query=query, categories=list(CAT_CONFIG.keys()), config=CAT_CONFIG, budget_max=BUDGET_MAX)

@app.route('/ajouter', methods=['POST'])
def ajouter():
    idx = request.form.get('id')
    nom = request.form.get('nom').strip()
    prix = float(request.form.get('prix', '0').replace(',', '.') or 0)
    qte = int(request.form.get('qte') or 1)
    cat = request.form.get('cat')
    with sqlite3.connect(DB_NAME) as conn:
        if idx:
            conn.execute("UPDATE courses SET nom=?, prix=?, qte=?, cat=? WHERE id=?", (nom, prix, qte, cat, idx))
        else:
            conn.execute("INSERT INTO courses (nom, prix, qte, fait, cat) VALUES (?, ?, ?, 0, ?)", (nom, prix, qte, cat))
    return redirect(url_for('home'))

@app.route('/cloturer')
def cloturer():
    with sqlite3.connect(DB_NAME) as conn:
        total = conn.execute("SELECT SUM(prix * qte) FROM courses WHERE fait = 1").fetchone()[0] or 0
        if total > 0:
            conn.execute("INSERT INTO historique (total, date_achat) VALUES (?, ?)", (total, datetime.now()))
        conn.execute("DELETE FROM courses")
    return redirect(url_for('home'))

@app.route('/cocher/<int:id>')
def cocher(id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE courses SET fait = NOT fait WHERE id = ?", (id,))
    return redirect(url_for('home'))

@app.route('/supprimer/<int:id>')
def supprimer(id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM courses WHERE id = ?", (id,))
    return redirect(url_for('home'))

@app.route('/ajouter_fav/<string:nom>')
def ajouter_fav(nom):
    with sqlite3.connect(DB_NAME) as conn:
        last = conn.execute("SELECT prix, cat FROM courses WHERE nom = ? ORDER BY id DESC LIMIT 1", (nom,)).fetchone()
        conn.execute("INSERT INTO courses (nom, prix, qte, fait, cat) VALUES (?, ?, 1, 0, ?)", (nom, last[0], last[1]))
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)