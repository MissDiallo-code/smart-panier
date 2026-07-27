# SmartPanier — Passage en version "pro"

## 🆕 Round 2 : Multi-listes + vraies notifications push

### 📋 Multi-listes
- Tu peux maintenant créer autant de listes que tu veux (Courses, Ménage, Anniversaire...) via le bouton **+** à côté du sélecteur en haut du tableau de bord, ou depuis ton **Profil**.
- Un sélecteur en haut de la page permet de **switcher instantanément** entre tes listes.
- Chaque liste est **totalement isolée** : articles, budget, catégories affichées, tout est propre à la liste active (testé).
- Depuis ton Profil, tu peux **renommer** ou **supprimer** une liste (impossible de supprimer la dernière — il en faut toujours au moins une).
- Le **partage "mode coloc"** se fait maintenant liste par liste : tu choisis laquelle partager avec quel utilisateur. La personne avec qui tu partages voit cette liste apparaître dans son propre sélecteur ("👥 Partagées avec moi"), et peut y ajouter/cocher/supprimer des articles comme toi.
- **Sécurité vérifiée** : j'ai testé qu'un utilisateur ne peut absolument pas accéder à une liste qui ne lui appartient pas et qui n'a pas été partagée avec lui, même en devinant l'ID.
- L'historique d'achats garde en mémoire le **nom de la liste** clôturée, pour que ton historique reste lisible même si tu as plusieurs listes actives.

### 🔔 Vraies notifications push (OneSignal)
- L'app garde son ancien système de rappel local (fonctionne tant que l'onglet/l'appli reste ouvert) **et** peut maintenant envoyer de **vraies notifications push**, qui arrivent même si l'appli est fermée — comme une vraie app pro.
- C'est **optionnel et automatique** : sans configuration, l'app fonctionne exactement comme avant (repli local). Dès que tu ajoutes tes clés OneSignal sur Render, le vrai push s'active tout seul, sans rien changer côté utilisateurs.
- Deux notifications sont câblées :
  - **"Rappel dans 30 min"** → programme un vrai push.
  - **Alerte budget dépassé** → un push est envoyé automatiquement dès que le total dépasse le budget fixé (une seule fois par dépassement, pour ne pas spammer).

### 🚀 Pour activer le vrai push (facultatif mais recommandé)
1. Crée un compte gratuit sur onesignal.com (le plan gratuit couvre largement le lancement d'une app publique).
2. Crée une app "Web Push", configure l'URL de ton site.
3. Récupère ton **App ID** et une **REST API Key**.
4. Sur Render → Environment, ajoute :
   - `ONESIGNAL_APP_ID` = ton App ID (publique, sans risque si elle fuite)
   - `ONESIGNAL_API_KEY` = ta REST API Key (⚠️ secrète, ne jamais la mettre dans le code)
5. Redéploie. Un badge "Push réel activé" apparaît alors dans le tableau de bord.

### ⚠️ Limite honnête sur les rappels programmés
Le rappel "dans 30 min" utilise un minuteur en mémoire côté serveur (`threading.Timer`). Ça fonctionne très bien pour une petite app, mais si Render redémarre ton service entre-temps (mise en veille sur le plan gratuit, redéploiement...), le rappel programmé est perdu. Pour une fiabilité à 100% à grande échelle, il faudrait une vraie file de tâches (Celery/RQ + Redis) — je peux m'y atteler si l'app grossit et que ça devient un vrai besoin.

---

## Round 1 : Sécurité, fiabilité, UX (rappel)

### 🔴 Sécurité
- Plus de `secret_key` codée en dur (variable d'environnement `SECRET_KEY`).
- Protection CSRF partout (Flask-WTF).
- Toutes les actions destructrices sont passées de GET à POST.
- Rate limiting sur login/register/change_password.
- Validation stricte de toutes les entrées serveur.
- Cookies sécurisés, en-têtes HTTP de sécurité, plus de `except:` nus, `debug=False` en prod.

### 🟠 Fiabilité
- SQLite en mode WAL + timeout pour encaisser plusieurs utilisateurs simultanés.
- Connexion réutilisée par requête.
- Pages d'erreur 404/429/500 propres.
- L'app écoute sur `0.0.0.0` et `$PORT` (compatible Render), `Procfile` + `requirements.txt` fournis pour `gunicorn`.

### 🟡 UX pro
- Cocher/supprimer/ajouter/modifier un article en AJAX (plus de rechargement).
- Toasts au lieu des `alert()`, undo après suppression.
- Montants formatés avec espaces, favicon, badge "Ajouté par X" sur les listes partagées.

## 🟢 Ce qui reste volontairement de côté
- Génération de vraies icônes PWA personnalisées (512x512) : pas d'outil de génération d'image disponible ici, l'icône générique Flaticon est restée dans le manifeste.
- File de tâches robuste pour les rappels (cf. limite ci-dessus) — à envisager si l'app prend de l'ampleur.

## ✅ Testé
- Test fonctionnel complet (inscription, login, ajout/édition/suppression/coche AJAX, recettes, modèles, changement mot de passe, clôture, export CSV, 404).
- Test multi-listes : création, isolation stricte entre listes, switch, partage, **vérification qu'un utilisateur ne peut pas accéder à une liste non-partagée**, suppression avec protection de la dernière liste.
- Test de migration : simulation d'une base "comme en prod" avec l'ancien schéma (avant multi-listes) → migration automatique testée, **aucune perte de données**, une liste par défaut créée automatiquement pour l'utilisateur existant.

## 🚀 Pour déployer sur Render (rappel)
1. Variable d'environnement `SECRET_KEY` (aléatoire, longue).
2. `FLASK_ENV=production`.
3. Commande de démarrage : `gunicorn App_Ultimate_Public:app` (via le `Procfile`).
4. `requirements.txt` pris en compte automatiquement.
5. Pense à un disque persistant Render pour ta base SQLite si ce n'est pas déjà fait.
6. (Facultatif) `ONESIGNAL_APP_ID` + `ONESIGNAL_API_KEY` pour le vrai push.
