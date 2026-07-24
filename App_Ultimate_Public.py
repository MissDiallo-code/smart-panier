import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for, jsonify

app = Flask(__name__)

# --- INITIALIZATION DATABASE ---
def init_db():
    conn = sqlite3.connect('smartpanier.db')
    c = conn.cursor()
    # Table des articles actuels
    c.execute('''CREATE TABLE IF NOT EXISTS panier (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nom TEXT NOT NULL,
                    prix REAL NOT NULL,
                    quantite INTEGER NOT NULL DEFAULT 1,
                    valide INTEGER NOT NULL DEFAULT 0
                )''')
    # Table du budget
    c.execute('''CREATE TABLE IF NOT EXISTS budget (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    montant REAL NOT NULL
                )''')
    c.execute('''INSERT OR IGNORE INTO budget (id, montant) VALUES (1, 0.0)''')
    
    # Table de l'historique des clôtures
    c.execute('''CREATE TABLE IF NOT EXISTS historique (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    total_depense REAL NOT NULL,
                    budget_initial REAL NOT NULL,
                    nbr_articles INTEGER NOT NULL,
                    date_cloture TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )''')
    conn.commit()
    conn.close()

init_db()

# --- TEMPLATE HTML / CSS / JS ---
MAIN_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SmartPanier</title>
    <!-- Bootstrap 5 CSS -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <!-- FontAwesome Icons -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <!-- Chart.js -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    
    <style>
        :root {
            --bg-body: #0f172a;
            --bg-card: #1e293b;
            --text: #f8fafc;
            --border: #334155;
            --primary: #3b82f6;
            --success: #22c55e;
            --danger: #ef4444;
        }

        body {
            background-color: var(--bg-body);
            color: var(--text);
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            padding-bottom: 30px;
        }

        .card {
            background-color: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            color: var(--text);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
        }

        /* Corrections Lisibilité Texte */
        .card h5, .card h6, .card span, .card small, .card label {
            color: var(--text) !important;
        }
        
        .text-muted-custom {
            color: #94a3b8 !important; /* Gris clair parfaitement lisible sur fond sombre */
        }

        .form-control {
            background-color: #0f172a;
            border: 1px solid var(--border);
            color: #ffffff;
        }

        .form-control:focus {
            background-color: #0f172a;
            color: #ffffff;
            border-color: var(--primary);
            box-shadow: none;
        }

        .table-custom {
            color: var(--text);
        }

        .table-custom td, .table-custom th {
            border-color: var(--border);
            vertical-align: middle;
        }

        .item-checked {
            text-decoration: line-through;
            opacity: 0.5;
        }

        .badge-budget {
            font-size: 1.1rem;
            padding: 8px 12px;
        }
    </style>
</head>
<body>

<div class="container py-4" style="max-width: 800px;">
    
    <!-- En-tête -->
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h2><i class="fa-solid fa-cart-shopping text-primary me-2"></i>SmartPanier</h2>
        <div>
            <form action="/cloturer" method="POST" onsubmit="return confirm('Voulez-vous vraiment enregistrer et clôturer cette liste ?');">
                <button type="submit" class="btn btn-warning btn-sm fw-bold">
                    <i class="fa-solid fa-flag-checkered me-1"></i> 🏁 Finir
                </button>
            </form>
        </div>
    </div>

    <!-- Section Budget & Bilan -->
    <div class="row g-3 mb-4">
        <div class="col-md-6">
            <div class="card p-3 text-center">
                <span class="text-muted-custom small">Budget Défini</span>
                <form action="/set_budget" method="POST" class="d-flex align-items-center justify-content-center mt-2">
                    <input type="number" step="0.01" name="budget" class="form-control form-control-sm text-center me-2" style="width: 120px;" value="{{ '%.2f'|format(budget) }}" required>
                    <button type="submit" class="btn btn-sm btn-outline-primary"><i class="fa-solid fa-check"></i></button>
                </form>
            </div>
        </div>

        <div class="col-md-6">
            <div class="card p-3 text-center">
                <span class="text-muted-custom small">Total Dépensé</span>
                <h3 class="mt-1 mb-0 {{ 'text-danger' if total_depense > budget and budget > 0 else 'text-success' }}">
                    {{ '%.2f'|format(total_depense) }} FCFA
                </h3>
                {% if budget > 0 %}
                    <small class="mt-1 {{ 'text-danger' if (budget - total_depense) < 0 else 'text-muted-custom' }}">
                        Reste : {{ '%.2f'|format(budget - total_depense) }} FCFA
                    </small>
                {% endif %}
            </div>
        </div>
    </div>

    <!-- Graphique Temporel (si historique existe) -->
    {% if histo %}
    <div class="card p-3 mb-4">
        <h6 class="mb-3"><i class="fa-solid fa-chart-line text-info me-2"></i>Évolution des Dépenses (Historique)</h6>
        <!-- Conteneur avec hauteur fixe pour Chart.js -->
        <div style="position: relative; height: 180px; width: 100%;">
            <canvas id="monthlyChart"></canvas>
        </div>
    </div>
    {% endif %}

    <!-- Formulaire d'ajout -->
    <div class="card p-3 mb-4">
        <h6 class="mb-3"><i class="fa-solid fa-plus text-primary me-2"></i>Ajouter un Article</h6>
        <form action="/ajouter" method="POST" class="row g-2">
            <div class="col-6 col-md-5">
                <input type="text" name="nom" class="form-control" placeholder="Article (ex: Lait)" required>
            </div>
            <div class="col-3 col-md-3">
                <input type="number" step="0.01" name="prix" class="form-control" placeholder="Prix" required>
            </div>
            <div class="col-3 col-md-2">
                <input type="number" name="quantite" class="form-control" value="1" min="1" required>
            </div>
            <div class="col-12 col-md-2">
                <button type="submit" class="btn btn-primary w-100"><i class="fa-solid fa-add"></i></button>
            </div>
        </form>
    </div>

    <!-- Liste des Courses -->
    <div class="card p-3 mb-4">
        <h6 class="mb-3"><i class="fa-solid fa-list-check me-2"></i>Liste Actuelle ({{ articles|length }})</h6>
        {% if articles %}
        <div class="table-responsive">
            <table class="table table-custom align-middle">
                <thead>
                    <tr>
                        <th style="width: 40px;"></th>
                        <th>Article</th>
                        <th class="text-center">Qté</th>
                        <th class="text-end">Prix U.</th>
                        <th class="text-end">Total</th>
                        <th class="text-center" style="width: 50px;"></th>
                    </tr>
                </thead>
                <tbody>
                    {% for item in articles %}
                    <tr class="{{ 'item-checked' if item[4] else '' }}">
                        <td class="text-center">
                            <a href="/toggle/{{ item[0] }}" class="text-decoration-none">
                                <i class="fa-regular {{ 'fa-square-check text-success fs-5' if item[4] else 'fa-square text-secondary fs-5' }}"></i>
                            </a>
                        </td>
                        <td class="fw-bold">{{ item[1] }}</td>
                        <td class="text-center">{{ item[3] }}</td>
                        <td class="text-end">{{ '%.2f'|format(item[2]) }}</td>
                        <td class="text-end fw-bold">{{ '%.2f'|format(item[2] * item[3]) }}</td>
                        <td class="text-center">
                            <a href="/supprimer/{{ item[0] }}" class="text-danger"><i class="fa-solid fa-trash-can"></i></a>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% else %}
            <p class="text-center text-muted-custom my-3">Votre panier est vide pour le moment.</p>
        {% endif %}
    </div>

    <!-- Dernières Clôtures -->
    {% if histo %}
    <div class="card p-3">
        <h6 class="mb-3"><i class="fa-solid fa-history me-2"></i>Dernières Listes Clôturées</h6>
        <div class="list-group list-group-flush">
            {% for h in histo[:5] %}
            <div class="list-group-item bg-transparent text-white border-bottom border-secondary d-flex justify-content-between align-items-center px-0">
                <div>
                    <strong>{{ h[1] }} FCFA</strong> 
                    <small class="text-muted-custom ms-2">({{ h[3] }} articles)</small>
                </div>
                <small class="text-muted-custom">{{ str(h[4])[:10] }}</small>
            </div>
            {% endfor %}
        </div>
    </div>
    {% endif %}

</div>

<!-- JS Chart.js -->
{% if histo %}
<script>
const histoLabels = {{ histo_labels | tojson }};
const histoTotals = {{ histo_totals | tojson }};

const ctxLine = document.getElementById('monthlyChart').getContext('2d');
new Chart(ctxLine, {
    type: 'line',
    data: {
        labels: histoLabels,
        datasets: [{
            label: 'Dépenses (FCFA)',
            data: histoTotals,
            borderColor: '#60a5fa',
            backgroundColor: 'rgba(96, 165, 250, 0.2)',
            fill: true,
            tension: 0.3,
            pointBackgroundColor: '#ffffff',
            pointRadius: 4
        }]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false }
        },
        scales: {
            x: {
                ticks: { color: '#cbd5e1' },
                grid: { color: '#334155' }
            },
            y: {
                ticks: { color: '#cbd5e1' },
                grid: { color: '#334155' }
            }
        }
    }
});
</script>
{% endif %}

</body>
</html>
"""

# --- ROUTES FLASK ---

@app.route('/')
def index():
    conn = sqlite3.connect('smartpanier.db')
    c = conn.cursor()
    
    # Budget
    c.execute('SELECT montant FROM budget WHERE id = 1')
    budget = c.fetchone()[0]
    
    # Articles
    c.execute('SELECT id, nom, prix, quantite, valide FROM panier')
    articles = c.fetchall()
    
    # Total Dépensé
    total_depense = sum(item[2] * item[3] for item in articles)
    
    # Historique
    c.execute('SELECT id, total_depense, budget_initial, nbr_articles, date_cloture FROM historique ORDER BY date_cloture DESC')
    histo = c.fetchall()
    
    # Données pour la courbe (du plus ancien au plus récent)
    histo_labels = [str(h[4])[:10] for h in reversed(histo)]
    histo_totals = [h[1] for h in reversed(histo)]
    
    conn.close()
    
    return render_template_string(
        MAIN_HTML,
        budget=budget,
        articles=articles,
        total_depense=total_depense,
        histo=histo,
        histo_labels=histo_labels,
        histo_totals=histo_totals,
        str=str
    )

@app.route('/set_budget', methods=['POST'])
def set_budget():
    budget = request.form.get('budget', type=float)
    conn = sqlite3.connect('smartpanier.db')
    c = conn.cursor()
    c.execute('UPDATE budget SET montant = ? WHERE id = 1', (budget,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/ajouter', methods=['POST'])
def ajouter():
    nom = request.form.get('nom')
    prix = request.form.get('prix', type=float)
    quantite = request.form.get('quantite', type=int)
    
    if nom and prix is not None and quantite:
        conn = sqlite3.connect('smartpanier.db')
        c = conn.cursor()
        c.execute('INSERT INTO panier (nom, prix, quantite) VALUES (?, ?, ?)', (nom, prix, quantite))
        conn.commit()
        conn.close()
    return redirect(url_for('index'))

@app.route('/toggle/<int:item_id>')
def toggle(item_id):
    conn = sqlite3.connect('smartpanier.db')
    c = conn.cursor()
    c.execute('UPDATE panier SET valide = CASE WHEN valide = 1 THEN 0 ELSE 1 END WHERE id = ?', (item_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/supprimer/<int:item_id>')
def supprimer(item_id):
    conn = sqlite3.connect('smartpanier.db')
    c = conn.cursor()
    c.execute('DELETE FROM panier WHERE id = ?', (item_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/cloturer', methods=['POST'])
def cloturer():
    conn = sqlite3.connect('smartpanier.db')
    c = conn.cursor()
    
    # Calculer le bilan actuel
    c.execute('SELECT prix, quantite FROM panier')
    items = c.fetchall()
    
    if items:
        total_depense = sum(item[0] * item[1] for item in items)
        nbr_articles = len(items)
        
        c.execute('SELECT montant FROM budget WHERE id = 1')
        budget_actuel = c.fetchone()[0]
        
        # Enregistrer dans l'historique
        c.execute('INSERT INTO historique (total_depense, budget_initial, nbr_articles) VALUES (?, ?, ?)',
                  (total_depense, budget_actuel, nbr_articles))
        
        # Vider la liste actuelle
        c.execute('DELETE FROM panier')
        conn.commit()
        
    conn.close()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
