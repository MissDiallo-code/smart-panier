# Guide de migration vers PostgreSQL

Ce document explique **pourquoi** je n'ai pas migré le code automatiquement, et **comment**
procéder quand tu seras prêt — que ce soit toi, moi dans une prochaine session, ou un autre
développeur.

## Pourquoi je n'ai pas fait ce changement en aveugle

L'app utilise SQLite avec des requêtes SQL directement écrites dans le code (~40 endroits :
`sqlite3.connect`, des `?` comme marqueurs de paramètres, `AUTOINCREMENT`, `ON CONFLICT`,
`PRAGMA table_info`...). Passer à PostgreSQL n'est pas qu'un changement de nom de base :

- Les marqueurs de paramètres changent (`?` → `%s` avec psycopg2)
- `AUTOINCREMENT` devient `SERIAL` / `GENERATED ALWAYS AS IDENTITY`
- `PRAGMA table_info(table)` (utilisé pour les migrations douces) n'existe pas sur Postgres,
  l'équivalent passe par `information_schema.columns`
- Le comportement de `ON CONFLICT ... DO UPDATE` diffère légèrement
- La gestion des connexions (pool, timeouts) est différente

Mon environnement de travail n'a **ni accès réseau, ni serveur PostgreSQL** pour tester quoi que
ce soit de tout ça. Faire cette migration "à l'aveugle" sur une app qui a déjà de vraies données
utilisateurs en production est le genre de changement qui peut corrompre silencieusement des
données — je préfère ne pas le faire sans pouvoir vérifier.

## Est-ce que tu en as vraiment besoin maintenant ?

SQLite en mode WAL (déjà activé dans l'app) encaisse très bien un usage modéré à moyen —
plusieurs dizaines d'utilisateurs actifs simultanément ne posent généralement pas de problème.
Le vrai point de bascule vers Postgres, c'est :
- Un vrai pic de trafic public (centaines d'utilisateurs simultanés)
- Le besoin de plusieurs instances de l'app en parallèle (SQLite ne le supporte pas bien)
- Le risque du disque éphémère de Render si tu n'as pas déjà un disque persistant configuré

Si tu n'es pas encore à ce stade, il n'y a pas d'urgence.

## Comment procéder quand tu seras prêt

1. **Créer une base Postgres de test sur Render** (Render propose un plan gratuit/pas cher pour
   Postgres). Ne touche jamais directement ta base de prod pour tester une migration.
2. **Me redonner la main avec un accès à cette base de test** (ou faire les changements toi-même
   en suivant ce plan), pour que chaque requête puisse être testée avant d'être validée.
3. Étapes techniques :
   - Ajouter `psycopg2-binary` aux dépendances
   - Créer une fine couche d'abstraction (`db.py`) qui adapte automatiquement les requêtes
     selon que `DATABASE_URL` est définie (Postgres) ou non (SQLite reste le défaut)
   - Adapter le script de migration (`migrate_db()`) pour les deux dialectes
   - Exporter les données existantes de SQLite et les réimporter dans Postgres
   - Tester **l'intégralité** de `test_app.py` contre la base Postgres avant de basculer le trafic
   - Garder SQLite comme filet de secours le temps de valider Postgres en conditions réelles
4. Render fournit automatiquement une variable `DATABASE_URL` quand tu attaches une base
   Postgres à ton service — c'est ce qu'on utiliserait comme interrupteur (comme pour OneSignal,
   SMTP, etc. dans le reste de l'app).

Dis-moi quand tu veux t'y attaquer, idéalement avec une base de test disponible, et on le fait
proprement.
