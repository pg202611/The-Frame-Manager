# The Frame Manager 3.0

**Version stable de référence — Windows 11 · Python 3.12 · Samsung The Frame (mode Art)**

Application **non officielle**, réalisée avec l'aide de ChatGPT. Développée et essayée principalement sur un Samsung The Frame 32 pouces de 2022. Le fonctionnement sur d'autres générations n'est **pas garanti**.

## Structure

```text
The_Frame_Manager_3.0/
├── the_frame_manager.py       # Interface graphique FR/EN et opérations
├── frame_core.py              # API Samsung, stockage SQLite, JPEG, préférences
├── lancer_manager.bat         # Démarrage Windows
├── requirements.txt           # Dépendances Python
├── README.md                  # Ce guide
└── .gitignore                 # Protection lors d'une publication Git
```

Le dossier `the_frame_manager_data/` est **créé à l'exécution** et n'est fourni dans **aucune** archive. Il contient des informations personnelles; **ne le publiez pas**.

## Installation initiale

1. Installer **Python 3.12** sur Windows (cocher l'installation du lanceur `py` si proposé).
2. Extraire tous les fichiers de cette archive dans un nouveau dossier.
3. Ouvrir un terminal Windows dans ce dossier et exécuter :

   ```cmd
   py -3.12 -m pip install -r requirements.txt
   ```

4. Lancer `lancer_manager.bat`.
5. Ouvrir **Paramètres / Settings**, saisir l'**adresse IPv4 de votre propre téléviseur**, puis enregistrer. Aucune adresse IP n'est préconfigurée dans la distribution.
6. Autoriser l'appairage sur la TV si Samsung le demande. L'ordinateur et la TV doivent communiquer sur le réseau local.
7. Sélectionner le dossier de vos JPEG sources; vérifier les correspondances avant toute écriture sur le téléviseur.

## Mise à niveau depuis The Frame Manager 2.1.x

1. **Fermer complètement** l'ancienne application.
2. **Sauvegarder** son dossier `the_frame_manager_data` en entier; si la base SQLite possède des fichiers `-wal` ou `-shm`, les préserver aussi.
3. Créer un **nouveau dossier** pour la v3.0 et y extraire les fichiers de cette archive. Ne pas recopier les anciens scripts `.py`.
4. Copier votre ancien dossier `the_frame_manager_data` dans ce nouveau dossier.
5. Installer les dépendances et lancer `lancer_manager.bat`.
6. Confirmer les 2 listes, vos identifiants source, la qualité JPEG, l'adresse IP et les préférences.

> **Compatibilité des données :** le fichier de base continue intentionnellement de s'appeler `manager_v2.sqlite`. C'est pour préserver vos données existantes, pas une version de logiciel obsolète. Le logiciel ne remplace jamais vos JPEG sources.

## Capacités

- Bibliothèque des photos The Frame; lecture des miniatures via l'API Art.
- Indexation des JPEG locaux, empreintes SHA-256/pHash et correspondances avec les photos TV.
- Identifiants de source stables, tri, redimensionnement et mémorisation des colonnes.
- Transfert et suppression sécurisés, individuels et groupés, avec arrêt après la photo courante.
- Passe-partout et couleurs individuels/groupés; effets artistiques envoyés par API et consignés dans SQLite.
- Comparaison des qualités JPEG (90, 85, 80) et remplacements groupés/réversibles, y compris retour au JPEG original.
- Suivi dans la barre d'état, journal d'opérations, export CSV et sauvegarde SQLite.
- Interface bilingue français/anglais et réglages par défaut.

## Limites importantes

- Les **filtres individuels** sont enregistrés lorsqu'ils sont appliqués dans Manager, mais leur valeur **n'est pas relisible** de façon fiable depuis le téléviseur. Les filtres inconnus restent « inconnus ».
- Le remplacement JPEG nécessite temporairement de la place pour une **nouvelle photo**. Samsung peut retourner `-11` (espace insuffisant) même si son stockage général semble libre.
- Une nouvelle photo reçoit généralement un **nouvel identifiant Samsung**. Manager ne supprime l'ancienne qu'après confirmation du transfert et des paramètres connus.
- L'API Art et ses comportements varient selon le modèle/micrologiciel. **Faire un essai sur 1 à 3 photos avant un grand lot.**
- L'utilisation d'une API tierce et des dépendances Python comporte les risques habituels de tout logiciel; procéder à ses propres vérifications.

## Confidentialité et sécurité

La distribution ne contient **aucun** jeton Samsung, historique, base SQLite, chemin personnel Windows, photo, IP d'un particulier ou identifiant réseau personnel. Elle ne comporte pas de télémétrie ajoutée par ce projet. Les communications prévues sont celles nécessaires pour rejoindre le téléviseur configuré localement via `samsungtvws`.

**NE JAMAIS PARTAGER** votre `the_frame_manager_data/`, notamment :

- `token.txt` (jeton d'accès), `preferences.json` (adresse IP et préférences);
- `manager_v2.sqlite`, ses fichiers `-wal` et `-shm` (historique, métadonnées et chemins sources);
- `journal.log`, `operations.jsonl`, `Exports/`, `Sauvegardes/`;
- `Optimisations/`, vos JPEG ou des captures montrant des photos privées et des chemins d'accès.

Publier uniquement les fichiers du programme ou **l'archive publique propre**, pas le dossier d'installation après l'avoir utilisé. Un audit statique réduit les risques, mais ne peut garantir l'absence absolue de tout risque informatique.

## Attribution

Projet communautaire non affilié à Samsung ni à SmartThings. Conserver les mentions de provenance des bibliothèques et choisir une licence adaptée avant une publication en dépôt Git.
