import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, Response
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import json

app = Flask(__name__)
app.secret_key = "cle_securite_master_789" 
DB_NAME = "courses_multiusers.db"
SITE_URL = "https://smart-panier-1.onrender.com" 

# --- INITIALISATION DE LA BASE DE DONNÉES ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, budget_max REAL DEFAULT 50000)')
        conn.execute('CREATE TABLE IF NOT EXISTS courses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nom TEXT, prix REAL, qte INTEGER, fait BOOLEAN, cat TEXT, date_ajout DATETIME DEFAULT CURRENT_TIMESTAMP)')
        conn.execute('CREATE TABLE IF NOT EXISTS historique (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, total REAL, nb_articles INTEGER, date_achat DATETIME DEFAULT CURRENT_TIMESTAMP)')
        
        try:
            conn.execute('ALTER TABLE users ADD COLUMN budget_max REAL DEFAULT 50000')
        except sqlite3.OperationalError:
            pass
init_db()

CAT_CONFIG = {
    "🥦 Fruits & Légumes": "#10b981", 
    "🥩 Protéines": "#ef4444", 
    "🥖 Boulangerie": "#f59e0b", 
    "🥛 Laitiers": "#3b82f6", 
    "🥤 Boissons": "#8b5cf6", 
    "✨ Autre": "#64748b"
}

# --- TEMPLATE PRINCIPAL AVEC GRAPHIC CHART.JS ET RECHERCHE ---

MAIN_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <!-- Chart.js pour le graphique -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
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
            <!-- Colonne Gauche : Formulaire et Graphique -->
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

                <!-- Répartition du Budget avec Graphique -->
                <div class="card text-center">
                    <h6 class="fw-bold mb-2">📊 Budget (Max: {{ "%.0f"|format(budget) }} f)</h6>
                    <form action="/set_budget" method="POST" class="d-flex gap-2 mb-3">
                        <div class="input-group input-group-sm">
                            <input type="number" step="any" name="val" class="form-control" value="{{ "%.0f"|format(budget) }}" required>
                            <span class="input-group-text bg-secondary text-white border-secondary">FCFA</span>
                            <button type="submit" class="btn btn-primary fw-bold">Modifier</button>
                        </div>
                    </form>

                    <!-- Emplacement Graphique Donut -->
                    <div style="max-width: 220px; margin: 0 auto;">
                        <canvas id="budgetChart"></canvas>
                    </div>
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
                        <a href="/cloturer" class="btn btn-outline-danger btn-action" onclick="return confirm('Clôturer la liste ?')">🏁 Finir</a>
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
        // Graphique Donut Chart.js
        const labels = {{ chart_labels | safe }};
        const dataValues = {{ chart_data | safe }};
        const colors = {{ chart_colors | safe }};

        if (dataValues.length > 0 && dataValues.reduce((a, b) => a + b, 0) > 0) {
            const ctx = document.getElementById('budgetChart').getContext('2d');
            new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: labels,
                    datasets: [{
                        data: dataValues,
                        backgroundColor: colors,
                        borderWidth: 0
                    }]
                },
                options: {
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        }

        // Filtre de recherche dynamique
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

# --- ROUTES FLASK ET EXPORT EXCEL/CSV ---

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
        return redirect(url_for('login'))
    
    uid = session['uid']
    with sqlite3.connect(DB_NAME) as conn:
        user_data = conn.execute("SELECT budget_max FROM users WHERE id=?", (uid,)).fetchone()
        budget_user = user_data[0] if user_data and user_data[0] else 50000.0

        liste = conn.execute("SELECT * FROM courses WHERE user_id=? ORDER BY fait ASC, id DESC", (uid,)).fetchall()
        total = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=? AND fait=0", (uid,)).fetchone()[0] or 0
        histo = conn.execute("SELECT id, total, nb_articles, date_achat FROM historique WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,)).fetchall()

        # Données pour Chart.js
        labels, data_vals, colors = [], [], []
        for c, color in CAT_CONFIG.items():
            s = conn.execute("SELECT SUM(prix*qte) FROM courses WHERE user_id=? AND cat=?", (uid, c)).fetchone()[0] or 0
            if s > 0:
                labels.append(c)
                data_vals.append(s)
                colors.append(color)
            
    return render_template_string(
        MAIN_HTML, 
        liste=liste, 
        total=total, 
        budget=budget_user, 
        username=session['user'], 
        categories=list(CAT_CONFIG.keys()), 
        config=CAT_CONFIG, 
        histo=histo,
        chart_labels=json.dumps(labels),
        chart_data=json.dumps(data_vals),
        chart_colors=json.dumps(colors),
        url=SITE_URL
    )

# (Gardez les autres routes /register, /login, /logout, /add, /check, /del, /cloturer, /set_budget identiques)
