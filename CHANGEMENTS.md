# SmartPanier — Passage en version "pro"

## 🆕 Round 7 : Fonctions IA, famille, stats — et un audit qui a évité des bugs en prod

### ⚠️ D'abord, ce qui s'est passé
En reprenant le travail sur ce round, j'ai découvert qu'une grande partie des fonctions
demandées (IA, famille, stats, sauvegarde cloud) avait déjà été codée. En voulant les
ajouter à nouveau, j'ai créé des doublons. Je me suis arrêté, j'ai tout audité, et j'ai
trouvé + corrigé plusieurs problèmes réels :
- **Bug critique** : l'enregistrement de la fonction d'affichage des montants (`fmt`) avait
  disparu, ce qui cassait le rendu de presque toutes les pages. Corrigé et re-testé.
- **Sauvegarde cloud jamais déclenchable** : la fonction existait mais aucun bouton/route ne
  l'appelait. Ajout d'un bouton "Sauvegarder maintenant" dans `/admin`.
- **Modèle IA invalide codé en dur** (`claude-sonnet-4-6`, qui n'existe pas) → remplacé par un
  identifiant valide, et rendu configurable via `AI_MODEL`.
- Suppression du code dupliqué/mort (config IA en double, fonction `get_family` orpheline
  référençant des tables inexistantes).

Après nettoyage : 29 tests passent, l'app se charge sans erreur, aucun doublon de route ou de
fonction dans tout le fichier.

### 🤖 Fonctions IA (avantage Premium, sauf l'assistant)
- **Assistant IA** (`/api/ai/assistant`) : disponible à tous les comptes connectés, répond en
  se basant sur le contenu réel de la liste active et le budget.
- **Reconnaissance photo de produit** (Premium) : envoie une photo, l'IA identifie le produit et
  sa catégorie.
- **Scan de ticket de caisse** (Premium) : extrait automatiquement les articles et prix d'une
  photo de ticket et les ajoute à la liste active.
- **Générateur de menu IA** (Premium) : propose un menu sur plusieurs jours avec la liste
  d'ingrédients correspondante, adapté au budget.
- Toutes les fonctions IA sont **désactivées proprement** sans `ANTHROPIC_API_KEY` (message
  clair, pas de plantage), et limitées à 15-20 requêtes/minute par utilisateur pour éviter les
  abus qui coûteraient cher.
- ⚠️ Honnêteté : testées avec un simulateur fidèle de l'API Claude (format des réponses, gestion
  des erreurs, extraction JSON). **La qualité réelle des réponses de l'IA (photo, ticket, menu)
  n'a pas pu être vérifiée** — mon environnement n'a pas accès réseau pour un vrai appel API.
  Teste avec de vraies photos avant d'ouvrir cette fonction au public.
- Variable `AI_MODEL` (défaut `claude-sonnet-5`) si tu veux changer de modèle plus tard.

### 👨‍👩‍👧‍👦 Comptes famille
- Un utilisateur peut créer un foyer, inviter des membres par pseudo, et marquer certaines de
  ses listes comme "partagées avec la famille" — elles apparaissent alors automatiquement chez
  tous les membres, sans partage liste par liste. Testé de bout en bout.

### 📊 Statistiques intelligentes & 💰 conseils d'économie
- Page `/stats` avec l'évolution des dépenses.
- Conseils générés à partir de **données réelles** (variation par rapport au dernier achat,
  articles achetés très régulièrement, proximité du budget) — explicitement **pas un conseil
  financier personnalisé**, juste des observations factuelles sur les habitudes d'achat.

### 🍽️ Générateur de menu gratuit
- Version sans IA : sélectionne parmi les recettes prédéfinies celles qui tiennent dans le
  budget donné (algorithme glouton simple, testé avec plusieurs budgets).

### 🎤 Ajout par la voix
- Bouton micro sur le formulaire d'ajout, utilise la reconnaissance vocale native du navigateur
  (gratuit, aucune API). Fonctionne sur Chrome/Edge ; Firefox et Safari ont un support partiel.

### ☁️ Sauvegarde cloud
- Bouton dans `/admin` pour déclencher une sauvegarde immédiate vers un stockage S3-compatible
  (AWS S3, Backblaze B2, DigitalOcean Spaces...), utilisant l'API de sauvegarde native de SQLite
  pour un instantané cohérent même si la base est activement utilisée.
- Variables : `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_ENDPOINT` (optionnel),
  `S3_REGION` (optionnel).

---

## Rounds 1 à 6 (résumé)
Sécurité (CSRF, rate limiting, validation), fiabilité (SQLite WAL, erreurs propres),
UX AJAX/toasts, multi-listes, push OneSignal, mot de passe oublié, temps réel WebSocket, scanner
Open Food Facts, pages légales, OAuth Google/Facebook, suggestions intelligentes, CI/CD, Sentry,
paiement Stripe + plan Premium, parrainage, SEO, tableau de bord admin, export RGPD, emails HTML,
rate limiting étendu, classement des parrains.

---

## 🟢 Ce qui reste volontairement de côté
- Migration PostgreSQL (cf. `MIGRATION_POSTGRESQL.md`).
- Vérification de la **qualité réelle** des réponses IA (photo/ticket/menu) — nécessite un vrai
  test avec clé API et vraies photos.
- Icônes PWA personnalisées.

## ✅ Suite de tests : 29 tests, tous verts
(+ famille, IA en mode gracieux sans clé, page stats)

## 🚀 Nouvelles variables d'environnement optionnelles
- `ANTHROPIC_API_KEY`, `AI_MODEL` (défaut `claude-sonnet-5`) → fonctions IA
- `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_ENDPOINT`, `S3_REGION` → sauvegarde cloud

## 📋 Recommandation avant d'aller plus loin
Ce round confirme ce que je disais avant : plus on empile de fonctionnalités sans déployer,
plus le risque de bugs invisibles augmente (le bug `fmt` aurait cassé l'app entière en prod).
Je répète mon conseil précédent, avec un exemple concret cette fois pour l'appuyer : teste ce
qui existe déjà avant qu'on continue à coder.
