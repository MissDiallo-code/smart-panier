import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for

app = Flask(__name__)

# --- INITIALISATION BASE DE DONNÉES ---
def init_db():
    conn = sqlite3.connect('smartpanier.db')
    c = conn.cursor()
    # Table des articles actuels
    c.execute('''CREATE TABLE IF NOT EXISTS panier (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nom TEXT NOT NULL,
                    prix REAL NOT NULL,
                    quantite INTEGER NOT NULL DEFAULT 1,
                    categorie TEXT DEFAULT 'Général',
                    valide INTEGER NOT NULL DEFAULT 0
                )''')
    # Migration si la colonne categorie n'existe pas encore
    try:
        c.execute("ALTER TABLE panier ADD COLUMN categorie TEXT DEFAULT 'Général'")
    except sqlite3.OperationalError:
        pass

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
            padding-bottom: 40px;
        }

        .card {
            background-color: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            color: var(--text);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
        }

        /* Forcer la lisibilité de tous les textes */
        .card h5, .card h6, .card span, .card small, .card label, .card strong {
            color: var(--text) !important;
        }
        
        .text-muted-custom {
            color: #94a3b8 !important;
        }

        .form-control, .form-select {
            background-color: #0f172a;
            border: 1px solid var(--border);
            color: #ffffff;
        }

        .form-control:focus, .form-select:focus {
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
            opacity: 0.55;
        }

        .progress {
            background-color: #334155;
            height: 12px;
            border-radius: 6px;
        }
    </style>
</head>
<body>

<div class="container py-4" style="max-width: 850px;">
    
    <!-- En-tête avec actions globales -->
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h2><i class="fa-solid fa-cart-shopping text-primary me-2"></i>SmartPanier</h2>
        <div class="d-flex gap-2">
            {% if articles %}
            <form action="/vider" method="POST" onsubmit="return confirm('Voulez-vous réinitialiser tout le panier ?');">
                <button type="submit" class="btn btn-outline-danger btn-sm">
                    <i class="fa-solid fa-trash me-1"></i> Vider
                </button>
            </form>
            {% endif %}
            <form action="/cloturer" method="POST" onsubmit="return confirm('Enregistrer et archiver cette liste dans l\'historique ?');">
                <button type="submit" class="btn btn-warning btn-sm fw-bold">
                    <i class="fa-solid fa-flag-checkered me-1"></i> 🏁 Finir
                </button>
            </form>
        </div>
    </div>

    <!-- Section Budget, Dépenses et Barre de Progression -->
    <div class="row g-3 mb-4">
        <div class="col-md-6">
            <div class="card p-3 text-center h-100">
                <span class="text-muted-custom small">Budget Défini</span>
                <form action="/set_budget" method="POST" class="d-flex align-items-center justify-content-center mt-2">
                    <input type="number" step="0.01" name="budget" class="form-control form-control-sm text-center me-2" style="width: 130px;" value="{{ '%.2f'|format(budget) }}" required>
                    <button type="submit" class="btn btn-sm btn-outline-primary"><i class="fa-solid fa-check"></i></button>
                </form>
            </div>
        </div>

        <div class="col-md-6">
            <div class="card p-3 text-center h-100">
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

        <!-- Barre de progression du budget -->
        {% if budget > 0 %}
        {% set pct = [(total_depense / budget * 100)|round|int, 100]|min %}
        <div class="col-12">
            <div class="card p-3">
                <div class="d-flex justify-content-between mb-1">
                    <small class="text-muted-custom">Consommation du budget</small>
                    <small class="fw-bold">{{ pct }}%</small>
                </div>
                <div class="progress">
                    <div class="progress-bar {{ 'bg-danger' if pct >= 100 else ('bg-warning' if pct >= 80 else 'bg-success') }}" 
                         role="progressbar" style="width: {{ pct }}%;"></div>
                </div>
            </div>
        </div>
        {% endif %}
    </div>

    <!-- Graphique Temporel (si historique existe) -->
    {% if histo %}
    <div class="card p-3 mb-4">
        <h6 class="mb-3"><i class="fa-solid fa-chart-line text-info me-2"></i>Évolution des Dépenses (Historique)</h6>
        <div style="position: relative; height: 180px; width: 100%;">
            <canvas id="monthlyChart"></canvas>
        </div>
    </div>
    {% endif %}

    <!-- Formulaire d'ajout complet (Nom, Prix, Qté, Catégorie) -->
    <div class="card p-3 mb-4">
        <h6 class="mb-3"><i class="fa-solid fa-plus text-primary me-2"></i>Ajouter un Article</h6>
        <form action="/ajouter" method="POST" class="row g-2">
            <div class="col-12 col-md-4">
                <input type="text" name="nom" class="form-control" placeholder="Nom de l'article (ex: Riz)" required>
            </div>
            <div class="col-6 col-md-3">
                <input type="number" step="0.01" name="prix" class="form-control" placeholder="Prix Unitaire" required>
            </div>
            <div class="col-6 col-md-2">
                <input type="number" name="quantite" class="form-control" value="1" min="1" required>
            </div>
            <div class="col-8 col-md-2">
                <select name="categorie" class="form-select">
                    <option value="Alimentation">Alimentation</option>
                    <option value="Hygiène">Hygiène</option>
                    <option value="Maison">Maison</option>
                    <option value="Divers" selected>Divers</option>
                </select>
            </div>
            <div class="col-4 col-md-1">
                <button type="submit" class="btn btn-primary w-100"><i class="fa-solid fa-plus"></i></button>
            </div>
        </form>
    </div>

    <!-- Liste des Courses -->
    <div class="card p-3 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h6 class="m-0"><i class="fa-solid fa-list-check me-2"></i>Liste Actuelle ({{ articles|length }})</h6>
            {% if articles %}
            <small class="text-muted-custom">Valides : {{ articles|selectattr(5) | list | length }} / {{ articles|length }}</small>
            {% endif %}
        </div>

        {% if articles %}
        <div class="table-responsive">
            <table class="table table-custom align-middle">
                <thead>
                    <tr>
                        <th style="width: 40px;"></th>
                        <th>Article</th>
                        <th>Catégorie</th>
                        <th class="text-center" style="width: 110px;">Qté</th>
                        <th class="text-end">Prix U.</th>
                        <th class="text-end">Total</th>
                        <th class="text-center" style="width: 40px;"></th>
                    </tr>
                </thead>
                <tbody>
                    {% for item in articles %}
                    <tr class="{{ 'item-checked' if item[5] else '' }}">
                        <td class="text-center">
                            <a href="/toggle/{{ item[0] }}" class="text-decoration-none">
                                <i class="fa-regular {{ 'fa-square-check text-success fs-5' if item[5] else 'fa-square text-secondary fs-5' }}"></i>
                            </a>
                        </td>
                        <td class="fw-bold">{{ item[1] }}</td>
                        <td><span class="badge bg-secondary opacity-75">{{ item[4] }}</span></td>
                        <!-- Ajustement de la quantité directly via des boutons +/- -->
                        <td class="text-center">
                            <div class="d-flex justify-content-center align-items-center gap-1">
                                <a href="/qte/{{ item[0] }}/moins" class="btn btn-sm btn-outline-secondary py-0 px-2">-</a>
                                <span>{{ item[3] }}</span>
                                <a href="/qte/{{ item[0] }}/plus" class="btn btn-sm btn-outline-secondary py-0 px-2">+</a>
                            </div>
                        </td>
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

    <!-- Historique des Dernières Clôtures -->
    {% if histo %}
    <div class="card p-3">
        <h6 class="mb-3"><i class="fa-solid fa-history me-2"></i>Dernières Listes Clôturées</h6>
        <div class="list-group list-group-flush">
            {% for h in histo[:5] %}
            <div class="list-group-item bg-transparent border-bottom border-secondary d-flex justify-content-between align-items-center px-0">
                <div>
                    <strong>{{ h[1] }} FCFA</strong> 
                    <small class="text-muted-custom ms-2">({{ h[3] }} articles)</small>
                </div>
                <div class="d-flex align-items-center gap-3">
                    <small class="text-muted-custom">{{ str(h[4])[:10] }}</small>
                    <a href="/supprimer_historique/{{ h[0] }}" class="text-danger small" title="Supprimer de l'historique"><i class="fa-solid fa-xmark"></i></a>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
    {% endif %}

</div>

<!-- Configuration JavaScript Chart.js -->
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
        plugins: { legend: { display: false } },
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
    
    # Articles du panier
    c.execute('SELECT id, nom, prix, quantite, categorie, valide FROM panier')
    articles = c.fetchall()
    
    # Calcul du total dépensé
    total_depense = sum(item[2] * item[3] for item in articles)
    
    # Récupération de l'historique
    c.execute('SELECT id, total_depense, budget_initial, nbr_articles, date_cloture FROM historique ORDER BY date_cloture DESC')
    histo = c.fetchall()
    
    # Préparation des données chronologiques pour la courbe
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
    if budget is not None:
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
    categorie = request.form.get('categorie', default='Divers')
    
    if nom and prix is not None and quantite:
        conn = sqlite3.connect('smartpanier.db')
        c = conn.cursor()
        c.execute('INSERT INTO panier (nom, prix, quantite, categorie) VALUES (?, ?, ?, ?)', (nom, prix, quantite, categorie))
        conn.commit()
        conn.close()
    return redirect(url_for('index'))

@app.route('/qte/<int:item_id>/<action>')
def modifier_qte(item_id, action):
    conn = sqlite3.connect('smartpanier.db')
    c = conn.cursor()
    if action == 'plus':
        c.execute('UPDATE panier SET quantite = quantite + 1 WHERE id = ?', (item_id,))
    elif action == 'moins':
        c.execute('UPDATE panier SET quantite = CASE WHEN quantite > 1 THEN quantite - 1 ELSE 1 END WHERE id = ?', (item_id,))
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

@app.route('/vider', methods=['POST'])
def vider():
    conn = sqlite3.connect('smartpanier.db')
    c = conn.cursor()
    c.execute('DELETE FROM panier')
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/cloturer', methods=['POST'])
def cloturer():
    conn = sqlite3.connect('smartpanier.db')
    c = conn.cursor()
    
    c.execute('SELECT prix, quantite FROM panier')
    items = c.fetchall()
    
    if items:
        total_depense = sum(item[0] * item[1] for item in items)
        nbr_articles = len(items)
        
        c.execute('SELECT montant FROM budget WHERE id = 1')
        budget_actuel = c.fetchone()[0]
        
        c.execute('INSERT INTO historique (total_depense, budget_initial, nbr_articles) VALUES (?, ?, ?)',
                  (total_depense, budget_actuel, nbr_articles))
        
        c.execute('DELETE FROM panier')
        conn.commit()
        
    conn.close()
    return redirect(url_for('index'))

@app.route('/supprimer_historique/<int:histo_id>')
def supprimer_historique(histo_id):
    conn = sqlite3.connect('smartpanier.db')
    c = conn.cursor()
    c.execute('DELETE FROM historique WHERE id = ?', (histo_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
