# Prospect Finder - Google Places API (New)

[![CI](https://github.com/pierre3150/prospect-finder/actions/workflows/ci.yml/badge.svg)](https://github.com/pierre3150/prospect-finder/actions/workflows/ci.yml)

Script Python qui identifie des commerces **sans site web** (ou avec seulement une page Facebook/Instagram) autour d'un point donne, via l'API **Google Places (New)**. Pense pour de la prospection commerciale locale.

## Securite de la cle API - lis ceci avant de commiter quoi que ce soit

- La cle est lue **uniquement** depuis la variable d'environnement `GOOGLE_PLACES_API_KEY`. Elle n'existe nulle part dans le code, et il n'y a **pas d'argument `--api-key`** possible (volontairement, pour eviter qu'elle finisse dans ton historique shell ou dans les logs CI).
- `.env` est dans `.gitignore`. Seul `.env.example` (un gabarit sans vraie cle) est versionne.
- Le CI fait tourner **Gitleaks** sur chaque push/PR pour detecter toute cle qui se serait glissee dans un commit avant meme que tu la pousses vraiment en remote.
- Les CSV de sortie (`*.csv`) sont aussi ignores par git par defaut (ils peuvent contenir des donnees de prospection que tu ne veux pas forcement publier).

**Recommandation supplementaire** : restreins ta cle Google Cloud a l'API Places (New) uniquement, et si possible a une plage d'IP ou de referrers, depuis la Google Cloud Console. Une cle qui fuit mais qui est restreinte a une seule API et un seul usage limite fortement les degats.

## Installation

```bash
python -m venv venv
source venv/bin/activate  # ou venv\Scripts\activate sous Windows
pip install -r requirements.txt

cp .env.example .env
# edite .env et colle ta cle, PUIS charge-la dans ton shell :
export GOOGLE_PLACES_API_KEY=$(grep GOOGLE_PLACES_API_KEY .env | cut -d= -f2)
# ou plus simplement, exporte-la directement sans passer par le fichier :
export GOOGLE_PLACES_API_KEY="ta-cle"
```

## Utilisation

**Toujours commencer par un dry-run** pour voir le volume de requetes prevu avant que ca coute quoi que ce soit :

```bash
python prospect_finder.py --dry-run
```

Puis lancer pour de vrai :

```bash
python prospect_finder.py \
  --categories "plombier,electricien,coiffeur,menuisier,restaurant,boulangerie" \
  --lat 50.3006 --lng 2.8020 --radius-km 10 \
  --max-pages 3 \
  --output prospects.csv
```

### Options

| Option | Defaut | Description |
|---|---|---|
| `--categories` | metiers courants | Liste separee par des virgules |
| `--lat` / `--lng` | 50.3006 / 2.8020 | Centre de recherche (Saint-Laurent-Blangy) |
| `--radius-km` | 10 | Rayon de recherche |
| `--max-pages` | 3 | Pages max par categorie (20 resultats/page) - controle le cout |
| `--throttle-seconds` | 0.3 | Pause entre requetes API |
| `--output` | prospects.csv | Chemin du CSV de sortie |
| `--dry-run` | - | Aucun appel API, affiche juste l'estimation |

## Comment ca marche

1. **Text Search (New)** par categorie, avec `locationBias` (cercle autour du point donne), pagination via `pageToken`
2. **Place Details** pour chaque fiche trouvee (dedupliquee si elle ressort sur plusieurs categories), recuperation de `websiteUri`, note, avis, telephone, adresse
3. **Filtrage** : on garde uniquement les fiches sans `websiteUri`, ou dont le `websiteUri` pointe vers Facebook/Instagram
4. **Tri** par nombre d'avis decroissant (priorite aux commerces etablis)
5. **Export CSV** : nom, adresse, telephone, note_google, nombre_avis, categorie, lien_google_maps

## Gestion des quotas

- Pause fixe configurable entre chaque requete (`--throttle-seconds`)
- Backoff exponentiel automatique sur les reponses `429`
- `--max-pages` plafonne le nombre de requetes par categorie
- Le mode `--dry-run` calcule une borne haute du nombre de requetes **sans faire un seul appel API**

## Tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

Les tests couvrent la detection site manquant/Facebook-Instagram, le mapping des reponses API vers le CSV, la deduplication multi-categories, et le tri par nombre d'avis - avec les appels HTTP mockes (aucun appel reel, aucune cle necessaire pour lancer les tests).
