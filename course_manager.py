import json
import os

class Article:
    """Représente un article de la liste de courses."""
    def __init__(self, nom, quantite, categorie, achete=False):
        self.nom = nom
        self.quantite = quantite
        self.categorie = categorie
        self.achete = achete

    def marquer_comme_achete(self):
        self.achete = True

    def modifier_info(self, nouveau_nom=None, nouvelle_qte=None):
        """Méthode dédiée à la modification des attributs de l'article."""
        if nouveau_nom:
            self.nom = nouveau_nom
        if nouvelle_qte is not None:
            self.quantite = nouvelle_qte

    def __str__(self):
        """Formatage de l'affichage de l'article."""
        statut = "[X]" if self.achete else "[ ]"
        return f"{statut} {self.nom} (x{self.quantite}) - {self.categorie}"

    def to_dict(self):
        """Convertit l'objet en dictionnaire pour la sauvegarde JSON."""
        return {
            "nom": self.nom,
            "quantite": self.quantite,
            "categorie": self.categorie,
            "achete": self.achete
        }

class CourseManager:
    """Gère la collection d'objets Article."""
    def __init__(self, filename="liste_courses.json"):
        self.filename = filename
        self.articles = self.charger_donnees()

    def charger_donnees(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    donnees = json.load(f)
                    # On transforme les dictionnaires JSON en vrais objets 'Article'
                    return [Article(**d) for d in donnees]
            except:
                return []
        return []

    def sauvegarder(self):
        with open(self.filename, 'w', encoding='utf-8') as f:
            # On convertit chaque objet Article en dictionnaire avant d'écrire
            json.dump([a.to_dict() for a in self.articles], f, indent=4)
        print("✅ Données sauvegardées.")

    def ajouter_article(self, nom, qte, cat):
        nouvel_article = Article(nom, qte, cat)
        self.articles.append(nouvel_article)
        print(f"✔️ {nom} ajouté.")

    def afficher_liste(self):
        if not self.articles:
            print("\nLa liste est vide.")
            return
        print("\n--- MA LISTE DE COURSES ---")
        for i, art in enumerate(self.articles, 1):
            print(f"{i}. {art}") # Appelle automatiquement Article.__str__

    def supprimer_article(self, nom):
        self.articles = [a for a in self.articles if a.nom.lower() != nom.lower()]
        print(f"🗑️ Tentative de suppression de {nom} terminée.")

    def marquer_achete(self, nom):
        for art in self.articles:
            if art.nom.lower() == nom.lower():
                art.marquer_comme_achete()
                print(f"🛒 {art.nom} est coché.")
                return
        print("⚠️ Article non trouvé.")

    def modifier_article(self, nom):
        """Utilise la méthode interne de l'objet Article."""
        for art in self.articles:
            if art.nom.lower() == nom.lower():
                print(f"Modification de : {art.nom}")
                n_nom = input("Nouveau nom (laisser vide pour garder l'ancien) : ")
                n_qte = input("Nouvelle quantité (laisser vide pour garder l'ancienne) : ")
                
                # Conversion sécurisée de la quantité
                val_qte = int(n_qte) if n_qte.isdigit() else None
                
                # On délègue la modification à l'objet lui-même !
                art.modifier_info(nouveau_nom=n_nom if n_nom else None, nouvelle_qte=val_qte)
                print("📝 Article mis à jour.")
                return
        print("⚠️ Article introuvable.")

    def rechercher_article(self, mot):
        resultats = [a for a in self.articles if mot.lower() in a.nom.lower()]
        for res in resultats:
            print(res)

    def afficher_par_categorie(self):
        cats = {}
        for a in self.articles:
            cats.setdefault(a.categorie, []).append(a.nom)
        for c, items in cats.items():
            print(f"{c}: {', '.join(items)}")

    def vider_liste(self):
        self.articles = []