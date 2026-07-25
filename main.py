import sqlite3
import os
from flask import Flask, render_template_string, request, redirect, url_for

app = Flask(__name__)
DB_NAME = "courses_master.db"
BUDGET_MAX = 150.0

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS courses 
            (id INTEGER PRIMARY KEY AUTOINCREMENT, 
             nom TEXT, prix REAL, qte INTEGER, fait BOOLEAN, cat TEXT, date_ajout DATETIME DEFAULT CURRENT_TIMESTAMP)''')
init_db()

CAT_CONFIG = {
    "🥦 Légumes": "#27ae60", "🥩 Protéines": "#e74c3c", 
    "🥖 Boulangerie": "#f1c40f", "🥛 Produits Laitiers": "#3498db", 
    "🥤 Boissons": "#9b59b6", "✨ Autre": "#34495e"
}

HTML_PAGE = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <title>Courses Master Ultimate</title>
    <style>
        body { background: #0f172a !important; color: #ffffff !important; font-family: 'Inter', sans-serif; }
        .card { background: #1e293b !important; border: 1px solid #334155 !important; border-radius: 15px; padding: 20px; margin-bottom: 20px; }
        h2, h3, h4, h5, h6, span, small { color: #ffffff !important; }
        .text-muted { color: #94a3b8 !important; }
        .budget-ok { color: #4ade80 !important; font-weight: bold; }
        .budget-alerte { color: #f87171 !important; font-weight: bold; animation: shake 0.5s; }
        @keyframes shake { 0%, 100% { transform: translateX(0); } 25% { transform: translateX(5px); } 75% { transform: translateX(-5px); } }
        .form-control, .form-select { background: #334155 !important; border: 1px solid #475569 !important; color: #ffffff !important; }
        .list-group-item { background: transparent !important; border-color: #334155 !important; color: white !important; }
        .done-style { opacity: 0.3; text-decoration: line-through; }
        .badge-cat { font-size: 0.7rem; padding: 5px 10px; border-radius: 20px; color: white !important; }
    </style>
</head>
<body>
    <div class="container py-4" style="max-width: 900px;">
        <div class="row">
            <div class="col-md-5">
                <div class="card shadow">
                    <h4 class="mb-3" style="color: #38bdf8 !important;">Nouvel Article</h4>
                    <form action="/ajouter" method="POST">
                        <input type="text" name="nom" class="form-control mb-2" placeholder="Nom..." required>
                        <div class="row g-2 mb-2">
                            <div class="col-6"><input type="number" name="qte" class="form-control" value="1" min="1"></div>
                            <div class="col-6"><input type="text" name="prix" class="form-control" placeholder="Prix f"></div>
                        </div>
                        <select name="cat" class="form-select mb-3">
                            {% for c in categories %}<option value="{{c}}">{{c}}</option>{% endfor %}
                        </select>
                        <button class="btn btn-primary w-100 fw-bold">AJOUTER</button>
                    </form>
                </div>
                
                <div class="card shadow">
                    <h5 class="text-muted">Résumé Budget</h5>
                    <h2 class="{{ 'budget-alerte' if total > budget_max else 'budget-ok' }}">
                        {{ "%.2f"|format(total) }} f
                    </h2>
                    <small class="text-muted">Limite fixée à {{ budget_max }} f</small>
                </div>
            </div>

            <div class="col-md-7">
                <div class="card shadow">
                    <form action="/" method="GET" class="mb-3">
                        <div class="input-group">
                            <input type="text" name="q" class="form-control" placeholder="Chercher un article..." value="{{ query }}">
                            <button class="btn btn-outline-info" type="submit">🔍</button>
                        </div>
                    </form>

                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h4 class="mb-0">Ma Liste ({{ nb_restant }})</h4>
                        <a href="/vider" class="btn btn-sm btn-outline-danger" onclick="return confirm('Vider la liste ?')">Tout Vider</a>
                    </div>

                    <div class="mb-4">
                        <div class="d-flex justify-content-between mb-1">
                            <small class="text-muted">Progression</small>
                            <small class="fw-bold text-info">{{ progression }}%</small>
                        </div>
                        <div class="progress" style="height: 12px; background-color: #334155; border-radius: 10px;">
                            <div class="progress-bar progress-bar-striped progress-bar-animated bg-success" 
                                 style="width: {{ progression }}%"></div>
                        </div>
                    </div>

                    <div class="list-group list-group-flush">
                        {% for item in liste %}
                        <div class="list-group-item d-flex justify-content-between align-items-center px-0">
                            <div class="d-flex align-items-center {{ 'done-style' if item[4] }}">
                                <a href="/cocher/{{ item[0] }}" class="btn btn-sm {{ 'btn-success' if item[4] else 'btn-outline-secondary' }} rounded-circle me-3">✓</a>
                                <div>
                                    <span class="fw-bold d-block">{{ item[1] }} (x{{ item[3] }})</span>
                                    <span class="badge badge-cat" style="background: {{ config[item[5]] }}">{{ item[5] }}</span>
                                </div>
                            </div>
                            <div class="text-end">
                                <div class="fw-bold">{{ "%.2f"|format(item[2] * item[3]) }} f</div>
                                <a href="/supprimer/{{ item[0] }}" class="text-danger small text-decoration-none">Supprimer</a>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    query = request.args.get('q', '')
    with sqlite3.connect(DB_NAME) as conn:
        if query:
            cursor = conn.execute("SELECT * FROM courses WHERE nom LIKE ? ORDER BY fait ASC, date_ajout DESC", ('%'+query+'%',))
        else:
            cursor = conn.execute("SELECT * FROM courses ORDER BY fait ASC, date_ajout DESC")
        liste = cursor.fetchall()
        
        total_articles = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0] or 0
        coches = conn.execute("SELECT COUNT(*) FROM courses WHERE fait = 1").fetchone()[0] or 0
        progression = int((coches / total_articles * 100)) if total_articles > 0 else 0
        total_prix = conn.execute("SELECT SUM(prix * qte) FROM courses WHERE fait = 0").fetchone()[0] or 0
        
    return render_template_string(HTML_PAGE, liste=liste, total=total_prix, progression=progression, 
                                 nb_restant=(total_articles - coches), query=query,
                                 budget_max=BUDGET_MAX, categories=list(CAT_CONFIG.keys()), config=CAT_CONFIG)

@app.route('/ajouter', methods=['POST'])
def ajouter():
    try:
        nom = request.form.get('nom').strip()
        prix = float(request.form.get('prix', '0').replace(',', '.') or 0)
        qte = int(request.form.get('qte') or 1)
        cat = request.form.get('cat')
        if nom:
            with sqlite3.connect(DB_NAME) as conn:
                conn.execute("INSERT INTO courses (nom, prix, qte, fait, cat) VALUES (?, ?, ?, 0, ?)", (nom, prix, qte, cat))
    except: pass
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

@app.route('/vider')
def vider():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM courses")
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)