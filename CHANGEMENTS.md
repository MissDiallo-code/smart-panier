# SmartPanier — Passage en version "pro"

## 🔴 Sécurité
- `secret_key` n'est plus codée en dur : elle vient de la variable d'environnement `SECRET_KEY` (à définir sur Render → Settings → Environment). Sans elle, l'app démarre quand même mais tout le monde est déconnecté à chaque redéploiement.
- Protection **CSRF** sur tous les formulaires et sur les appels AJAX (Flask-WTF).
- Toutes les routes qui modifient des données (`check`, `del`, `del_template`, `load_template`, `cloturer`, `clear_history`, `reset_all`) sont passées de **GET à POST** — un lien malveillant ne peut plus déclencher une action à ta place.
- Rate limiting sur `/login`, `/register`, `/change_password` (10 tentatives/minute) contre le bruteforce.
- Validation stricte des entrées côté serveur (nom, quantité, prix, nom d'utilisateur, mot de passe ≥ 6 caractères) — plus aucune confiance aveugle dans ce qui vient du formulaire.
- Cookies de session sécurisés (`HttpOnly`, `SameSite=Lax`, `Secure` en production).
- En-têtes de sécurité HTTP ajoutés (`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`).
- Plus de `except:` nus — les erreurs sont désormais loguées (`logging`) au lieu d'être avalées silencieusement.
- `debug=True` supprimé en production (contrôlé par la variable `FLASK_DEBUG`).

## 🟠 Fiabilité (pour supporter du public)
- Connexion SQLite en mode **WAL** + timeout, pour encaisser plusieurs utilisateurs simultanés sans erreur "database is locked".
- Connexion réutilisée par requête (`flask.g`) au lieu d'en ouvrir une nouvelle à chaque ligne de code.
- Pages d'erreur 404 / 429 / 500 propres, dans le thème de l'app, au lieu de la page blanche Flask par défaut.
- L'app écoute maintenant sur `0.0.0.0` et le port `$PORT` fourni par Render — **important**, sans ça le déploiement peut ne pas fonctionner correctement.
- `Procfile` + `requirements.txt` fournis pour déployer avec `gunicorn` (serveur de production) au lieu du serveur de dev Flask.

## 🟡 Expérience "pro"
- **Cocher / décocher / supprimer un article ne recharge plus la page** — tout se fait en AJAX, instantané.
- **Ajouter et modifier un article** aussi : la liste se met à jour en direct.
- Suppression d'un article → petit toast "Article supprimé" avec bouton **Annuler** (undo) pendant 5 secondes.
- Système de notifications discrètes (toasts) qui remplace les `alert()` du navigateur, plus propres.
- Boutons avec petit spinner de chargement pendant les actions (évite les double-clics et donne un vrai feedback).
- Les montants s'affichent maintenant avec des espaces comme séparateurs de milliers (`125 000 FCFA` au lieu de `125000`).
- Un seul modal de modification partagé par tous les articles (au lieu d'un par article, ce qui alourdissait la page).
- Favicon ajoutée (icône panier dans l'onglet du navigateur).
- Sur les listes partagées ("mode coloc"), chaque article affiche désormais **qui l'a ajouté** si ce n'est pas toi.

## 🟢 Ce qui a été volontairement laissé de côté pour cette passe
Tu avais demandé "tout faire" — voici ce que j'ai fait à dessein sans y toucher, pour ne pas risquer de casser ta base en prod avec des changements trop lourds d'un coup :
- **Multi-listes** (courses / ménage / etc.) : demande une refonte du schéma de base de données (nouvelle table `lists`, migration des données existantes). Je peux m'y attaquer dans un prochain message si tu veux, proprement et testé.
- **Notifications push serveur** (au lieu des notifications navigateur locales) : nécessite un service tiers (ex. OneSignal, Web Push + VAPID keys) et donc des clés d'API à configurer.
- Génération de vraies icônes PWA (512x512 personnalisées) : je n'ai pas d'outil de génération d'images ici, l'icône Flaticon générique du manifeste est restée.

## 🚀 Pour déployer sur Render
1. Sur Render → ton service → **Environment**, ajoute une variable `SECRET_KEY` avec une valeur aléatoire longue (par ex. générée avec `python3 -c "import secrets; print(secrets.token_hex(32))"`).
2. Ajoute aussi `FLASK_ENV=production`.
3. Remplace la commande de démarrage par : `gunicorn App_Ultimate_Public:app` (le `Procfile` fourni s'en charge automatiquement si Render le détecte).
4. Vérifie que `requirements.txt` est bien pris en compte (Render l'installe automatiquement).
5. Le disque de Render étant éphémère par défaut, pense à un **disque persistant** (Render Disks) pointé sur le dossier où vit `courses_multiusers.db`, sinon ta base est effacée à chaque redéploiement — c'était déjà vrai avant, mais ça vaut le coup de le vérifier maintenant que tu vises du public.

## ✅ Testé
J'ai fait tourner un test fonctionnel complet (inscription, connexion, ajout/édition/suppression/coche d'article en AJAX, recette, modèle, partage, changement de mot de passe, clôture, export CSV, page 404) — tout passe sans erreur.
