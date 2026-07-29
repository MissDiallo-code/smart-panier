# SmartPanier — Passage en version "pro"

## 🆕 Round 5 : Monétisation, parrainage, référencement, admin

Avant tout, une clarification importante : je ne peux pas garantir qu'une app devienne
"virale" ou "admirée par le monde entier" — ça dépend de facteurs hors du code (marketing,
timing, bouche-à-oreille, chance). Ce round construit les **outils concrets** pour maximiser
les chances de croissance et pour que l'app rapporte vraiment de l'argent si elle prend.

### 💳 Paiement et plan Premium (Stripe)
- Page `/pricing` avec un plan **Gratuit** (1 liste, fonctionnalités de base) et un plan
  **Premium** (listes illimitées, export CSV/Excel, scanner code-barres).
- Paiement par carte via Stripe Checkout (abonnement mensuel récurrent).
- Un utilisateur peut gérer/annuler son abonnement lui-même via le portail client Stripe
  (`/billing/portal`), sans avoir à te contacter.
- **Sécurité prise au sérieux** : le webhook Stripe vérifie la signature cryptographique de
  chaque requête (`stripe.Webhook.construct_event`) — une requête falsifiée est rejetée (400),
  jamais traitée comme un vrai paiement.
- Sans configuration, la page `/pricing` s'affiche normalement mais le bouton d'abonnement
  indique "Bientôt disponible" plutôt que de planter.
- Pour activer, sur Render :
  - `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_ID` (créés depuis ton
    tableau de bord Stripe, en mode test d'abord)
  - `STRIPE_WEBHOOK_SECRET` (à récupérer en configurant un endpoint webhook Stripe pointant
    vers `https://TON-SITE/billing/webhook`, écoutant au minimum : `checkout.session.completed`,
    `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`)
- ⚠️ **Honnêteté sur les tests** : j'ai testé toute la logique (création de session de paiement,
  vérification de signature, activation/annulation du Premium selon les évènements du webhook,
  déblocage des fonctionnalités payantes) via une simulation fidèle de l'API Stripe — mon
  environnement n'a pas d'accès réseau pour un vrai paiement de bout en bout. **Teste
  impérativement un vrai paiement en mode test Stripe avant d'ouvrir au public** (Stripe fournit
  des numéros de carte de test dédiés à ça, aucun risque).

### 🎁 Parrainage
- Chaque utilisateur a un lien de parrainage unique, visible et copiable depuis son profil.
- Quand quelqu'un s'inscrit via ce lien : le filleul reçoit **14 jours Premium offerts**, le
  parrain **30 jours Premium offerts**. Testé de bout en bout (y compris avec un code de
  parrainage invalide, qui n'empêche pas l'inscription).
- C'est le levier de croissance organique le plus fiable qui existe — bien plus efficace que
  n'importe quelle astuce de "growth hacking".

### 🔍 Référencement (SEO) et page d'accueil
- Meta description, Open Graph (partage Facebook/LinkedIn/WhatsApp avec aperçu propre), Twitter
  Card, données structurées Schema.org, URL canonique.
- `/robots.txt` et `/sitemap.xml` générés automatiquement pour que Google explore le site
  correctement.
- Page d'accueil retravaillée avec une présentation claire des fonctionnalités et un lien vers
  les tarifs. **Je n'ai volontairement inclus aucun faux témoignage** — des avis fabriqués
  attribués à des personnes qui n'existent pas seraient trompeurs pour tes visiteurs (et risqué
  légalement dans plusieurs pays). Si tu veux de vrais témoignages plus tard, il faudra les
  recueillir auprès de vrais utilisateurs.

### 📊 Tableau de bord admin + analytics
- `/admin` : accessible uniquement au compte dont le nom d'utilisateur correspond à
  `ADMIN_USERNAME` (à définir sur Render) — testé qu'un utilisateur normal reçoit une page 404
  (pas de fuite d'information sur l'existence de la page).
- Affiche : nombre d'utilisateurs, membres Premium actifs, inscriptions des 7 derniers jours,
  parrainages réussis, listes créées, articles ajoutés, revenu mensuel estimé, journal
  d'évènements récents.
- Intégration optionnelle de **Plausible Analytics** (respectueux de la vie privée, sans
  cookies) via `PLAUSIBLE_DOMAIN` — nécessite un compte Plausible (payant, ou auto-hébergé).

---

## Round 4 : Connexion sociale, suggestions intelligentes, CI/CD, monitoring
- Connexion Google/Facebook (OAuth), optionnelle, avec liaison automatique des comptes par email.
- Suggestions intelligentes basées sur la fréquence d'achat.
- Pipeline CI/CD GitHub Actions (`.github/workflows/tests.yml`) qui lance les tests à chaque push.
- Monitoring d'erreurs Sentry (optionnel).
- PostgreSQL : non fait, guide de migration fourni (`MIGRATION_POSTGRESQL.md`).

## Round 3 : Mot de passe oublié, temps réel, scanner intelligent, pages légales
- Mot de passe oublié par email, collaboration en temps réel (WebSocket) sur les listes
  partagées, scanner code-barres branché sur Open Food Facts, pages légales, suite de tests.

## Round 2 : Multi-listes + notifications push réelles
- Listes multiples isolées, partage par liste, notifications push réelles (OneSignal).

## Round 1 : Sécurité, fiabilité, UX
- CSRF, rate limiting, validation stricte, SQLite en WAL, actions en AJAX, toasts.

---

## 🟢 Ce qui reste volontairement de côté
- Icônes PWA personnalisées (pas d'outil de génération d'image disponible ici).
- Migration PostgreSQL (cf. `MIGRATION_POSTGRESQL.md`).
- Vrais témoignages/avis clients (à recueillir auprès de vrais utilisateurs, pas fabriqués).
- Growth hacking agressif (pop-ups intrusifs, dark patterns) — délibérément évité, ça nuit à la
  confiance sur le long terme plus que ça n'aide.

## ✅ Suite de tests : 24 tests, tous verts
Authentification, mot de passe oublié, multi-listes, permissions de partage, actions AJAX,
suggestions intelligentes, sécurité OAuth, parrainage, gates Premium, sécurité webhook Stripe,
accès admin, pages légales, 404.

## 🚀 Checklist de déploiement Render (à jour)
1. `SECRET_KEY` + `FLASK_ENV=production`.
2. `Procfile` : `gunicorn -w 1 --threads 8 --timeout 120 App_Ultimate_Public:app`.
3. `requirements.txt` à jour (inclut maintenant Stripe).
4. Disque persistant Render pour la base SQLite.
5. `.github/workflows/tests.yml` dans ton dépôt GitHub.
6. Variables optionnelles :
   - `STRIPE_SECRET_KEY` / `STRIPE_PUBLISHABLE_KEY` / `STRIPE_PRICE_ID` / `STRIPE_WEBHOOK_SECRET` → paiement
   - `ADMIN_USERNAME` → accès au tableau de bord `/admin`
   - `PLAUSIBLE_DOMAIN` → analytics
   - `ONESIGNAL_APP_ID/KEY`, `SMTP_*`, `GOOGLE_CLIENT_ID/SECRET`, `FACEBOOK_CLIENT_ID/SECRET`, `SENTRY_DSN`, `ENABLE_REALTIME=false` (cf. rounds précédents)
7. **Avant d'ouvrir au public : teste un vrai paiement Stripe en mode test.** C'est la seule
   brique de ce round que je n'ai pas pu vérifier avec un vrai aller-retour réseau.
8. `pytest test_app.py -v` (24 tests).
