#!/usr/bin/env python3
"""
prospect_finder.py

Identifie des prospects commerciaux sans site web (ou avec seulement une page
Facebook/Instagram) autour d'un point donne, via l'API Google Places (New).

Utilisation :
    export GOOGLE_PLACES_API_KEY="votre-cle"
    python prospect_finder.py --categories "plombier,electricien,coiffeur" --dry-run
    python prospect_finder.py --categories "plombier,electricien,coiffeur" --output prospects.csv

La cle API n'est JAMAIS lue depuis le code ou un argument en ligne de commande :
uniquement depuis la variable d'environnement GOOGLE_PLACES_API_KEY.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import requests

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL_TEMPLATE = "https://places.googleapis.com/v1/places/{place_id}"

# Champs demandes a l'etape de recherche (juste de quoi identifier et paginer :
# on garde le field mask minimal ici pour limiter le cout, le detail complet
# est recupere ensuite via Place Details comme demande).
SEARCH_FIELD_MASK = "places.id,places.displayName,nextPageToken"

# Champs demandes a l'etape Place Details.
DETAILS_FIELD_MASK = (
    "id,displayName,formattedAddress,nationalPhoneNumber,"
    "internationalPhoneNumber,rating,userRatingCount,websiteUri,"
    "googleMapsUri,types"
)

RESULTS_PER_PAGE = 20  # taille de page fixe de l'API Places (New) Text Search
SOCIAL_ONLY_DOMAINS = ("facebook.com", "instagram.com", "fb.com", "fb.watch")


@dataclass
class Prospect:
    nom: str
    adresse: str
    telephone: str
    note_google: Optional[float]
    nombre_avis: int
    categorie: str
    lien_google_maps: str


@dataclass
class RunStats:
    search_requests: int = 0
    details_requests: int = 0
    errors: list[str] = field(default_factory=list)


def get_api_key() -> str:
    """Lit la cle API depuis l'environnement uniquement. Ne jamais coder la
    cle en dur ni l'accepter en argument CLI (ca finirait dans l'historique
    shell / les logs)."""
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key:
        print(
            "Erreur : la variable d'environnement GOOGLE_PLACES_API_KEY n'est pas definie.\n"
            "  export GOOGLE_PLACES_API_KEY=\"votre-cle\"",
            file=sys.stderr,
        )
        sys.exit(1)
    return api_key


def is_missing_or_social_only_website(website_uri: Optional[str]) -> bool:
    """True si le prospect n'a pas de vrai site web : soit pas de websiteUri
    du tout, soit un websiteUri qui pointe vers une page Facebook/Instagram
    (ce qui, pour la prospection, revient a ne pas avoir de site)."""
    if not website_uri:
        return True

    try:
        netloc = urllib.parse.urlparse(website_uri).netloc.lower()
    except ValueError:
        return False

    netloc = netloc.removeprefix("www.").removeprefix("m.").removeprefix("l.")
    return any(netloc == d or netloc.endswith("." + d) for d in SOCIAL_ONLY_DOMAINS)


def build_maps_link(place_id: str) -> str:
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"


def throttled_post(
    session: requests.Session,
    url: str,
    headers: dict,
    json_body: dict,
    throttle_seconds: float,
    max_retries: int = 4,
) -> requests.Response:
    """POST avec pause fixe entre requetes + backoff exponentiel sur 429/erreurs
    de quota, pour respecter les limites de l'API et eviter de la marteler."""
    for attempt in range(max_retries):
        response = session.post(url, headers=headers, json=json_body, timeout=15)
        if response.status_code == 429:
            wait = throttle_seconds * (2 ** (attempt + 1))
            print(f"  (quota atteint, pause de {wait:.1f}s avant retry...)", file=sys.stderr)
            time.sleep(wait)
            continue
        time.sleep(throttle_seconds)
        return response

    return response  # dernier essai, meme s'il a echoue


def throttled_get(
    session: requests.Session,
    url: str,
    headers: dict,
    throttle_seconds: float,
    max_retries: int = 4,
) -> requests.Response:
    for attempt in range(max_retries):
        response = session.get(url, headers=headers, timeout=15)
        if response.status_code == 429:
            wait = throttle_seconds * (2 ** (attempt + 1))
            print(f"  (quota atteint, pause de {wait:.1f}s avant retry...)", file=sys.stderr)
            time.sleep(wait)
            continue
        time.sleep(throttle_seconds)
        return response

    return response


def search_category(
    session: requests.Session,
    api_key: str,
    category: str,
    lat: float,
    lng: float,
    radius_m: float,
    max_pages: int,
    throttle_seconds: float,
    stats: RunStats,
) -> list[dict]:
    """Text Search (New) pour une categorie, avec pagination via pageToken."""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": SEARCH_FIELD_MASK,
    }

    results: list[dict] = []
    page_token: Optional[str] = None

    for page_num in range(max_pages):
        body = {
            "textQuery": category,
            "locationBias": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": radius_m,
                }
            },
        }
        if page_token:
            body["pageToken"] = page_token

        print(f"  [{category}] recherche page {page_num + 1}...")
        response = throttled_post(session, TEXT_SEARCH_URL, headers, body, throttle_seconds)
        stats.search_requests += 1

        if response.status_code != 200:
            msg = (
                f"Text Search a echoue pour '{category}' (page {page_num + 1}): "
                f"{response.status_code} {response.text[:200]}"
            )
            print(f"  ! {msg}", file=sys.stderr)
            stats.errors.append(msg)
            break

        data = response.json()
        page_places = data.get("places", [])
        results.extend(page_places)

        page_token = data.get("nextPageToken")
        if not page_token:
            break

        # L'API a besoin d'un court delai avant qu'un nouveau pageToken soit
        # valide - on attend un peu plus que le throttle normal par securite.
        time.sleep(max(throttle_seconds, 2.0))

    return results


def get_place_details(
    session: requests.Session,
    api_key: str,
    place_id: str,
    throttle_seconds: float,
    stats: RunStats,
) -> Optional[dict]:
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": DETAILS_FIELD_MASK,
    }
    url = DETAILS_URL_TEMPLATE.format(place_id=place_id)

    response = throttled_get(session, url, headers, throttle_seconds)
    stats.details_requests += 1

    if response.status_code != 200:
        msg = f"Place Details a echoue pour {place_id}: {response.status_code} {response.text[:200]}"
        print(f"  ! {msg}", file=sys.stderr)
        stats.errors.append(msg)
        return None

    return response.json()


def details_to_prospect(details: dict, category: str) -> Prospect:
    display_name = details.get("displayName", {}).get("text", "Nom inconnu")
    address = details.get("formattedAddress", "")
    phone = details.get("nationalPhoneNumber") or details.get("internationalPhoneNumber") or ""
    rating = details.get("rating")
    review_count = details.get("userRatingCount", 0) or 0
    place_id = details.get("id", "")
    maps_link = details.get("googleMapsUri") or build_maps_link(place_id)

    return Prospect(
        nom=display_name,
        adresse=address,
        telephone=phone,
        note_google=rating,
        nombre_avis=review_count,
        categorie=category,
        lien_google_maps=maps_link,
    )


def estimate_dry_run(categories: list[str], max_pages: int) -> None:
    """Estimation SANS AUCUN appel API : borne haute basee sur la configuration
    (max_pages, taille de page fixe de 20). Le nombre reel de resultats depend
    des donnees en direct et sera probablement plus bas."""
    max_search_requests = len(categories) * max_pages
    max_places_found = len(categories) * max_pages * RESULTS_PER_PAGE
    max_details_requests = max_places_found  # 1 appel Details par fiche trouvee

    print("=== DRY RUN : estimation avant lancement (aucun appel API effectue) ===")
    print(f"Categories ({len(categories)}) : {', '.join(categories)}")
    print(f"Pages max par categorie : {max_pages} (20 resultats/page)")
    print()
    print(f"Requetes Text Search  : jusqu'a {max_search_requests}")
    print(f"Fiches potentielles   : jusqu'a {max_places_found}")
    print(f"Requetes Place Details: jusqu'a {max_details_requests} (1 par fiche, avant filtrage)")
    print(f"TOTAL requetes API    : jusqu'a {max_search_requests + max_details_requests}")
    print()
    print("Ceci est une borne haute. Le nombre reel de resultats depend des donnees")
    print("Google en direct et sera generalement inferieur (moins de 20 resultats/page,")
    print("moins de pages disponibles que max_pages, etc.)")


def run(
    categories: list[str],
    lat: float,
    lng: float,
    radius_km: float,
    max_pages: int,
    throttle_seconds: float,
    output_path: str,
) -> None:
    api_key = get_api_key()
    radius_m = radius_km * 1000
    stats = RunStats()
    prospects: list[Prospect] = []
    seen_place_ids: set[str] = set()

    with requests.Session() as session:
        for category in categories:
            print(f"\n--- Categorie : {category} ---")
            search_results = search_category(
                session, api_key, category, lat, lng, radius_m, max_pages, throttle_seconds, stats
            )
            print(f"  {len(search_results)} fiche(s) trouvee(s) pour '{category}'.")

            for place in search_results:
                place_id = place.get("id")
                if not place_id or place_id in seen_place_ids:
                    continue  # deduplication (un meme etablissement peut ressortir sur plusieurs categories)
                seen_place_ids.add(place_id)

                details = get_place_details(session, api_key, place_id, throttle_seconds, stats)
                if details is None:
                    continue

                website = details.get("websiteUri")
                if is_missing_or_social_only_website(website):
                    prospects.append(details_to_prospect(details, category))

    prospects.sort(key=lambda p: p.nombre_avis, reverse=True)
    write_csv(prospects, output_path)

    print("\n=== Termine ===")
    print(f"Requetes Text Search  : {stats.search_requests}")
    print(f"Requetes Place Details: {stats.details_requests}")
    print(f"TOTAL requetes API    : {stats.search_requests + stats.details_requests}")
    if stats.errors:
        print(f"Erreurs rencontrees   : {len(stats.errors)} (voir stderr ci-dessus)")
    print(f"Prospects retenus     : {len(prospects)}")
    print(f"CSV ecrit dans        : {output_path}")


def write_csv(prospects: list[Prospect], output_path: str) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "nom", "adresse", "telephone", "note_google", "nombre_avis",
            "categorie", "lien_google_maps",
        ])
        for p in prospects:
            writer.writerow([
                p.nom, p.adresse, p.telephone,
                p.note_google if p.note_google is not None else "",
                p.nombre_avis, p.categorie, p.lien_google_maps,
            ])


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trouve des prospects commerciaux sans site web via Google Places API (New)."
    )
    parser.add_argument(
        "--categories",
        type=str,
        default="plombier,electricien,coiffeur,menuisier,restaurant,boulangerie",
        help="Liste de categories separees par des virgules (defaut: metiers courants).",
    )
    parser.add_argument("--lat", type=float, default=50.3006, help="Latitude du centre de recherche.")
    parser.add_argument("--lng", type=float, default=2.8020, help="Longitude du centre de recherche.")
    parser.add_argument("--radius-km", type=float, default=10.0, help="Rayon de recherche en km.")
    parser.add_argument(
        "--max-pages", type=int, default=3,
        help="Nombre max de pages (20 resultats/page) par categorie. Limite le cout.",
    )
    parser.add_argument(
        "--throttle-seconds", type=float, default=0.3,
        help="Pause entre chaque requete API pour respecter les quotas.",
    )
    parser.add_argument("--output", type=str, default="prospects.csv", help="Chemin du CSV de sortie.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="N'effectue AUCUN appel API : affiche seulement le nombre de requetes prevues.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    categories = [c.strip() for c in args.categories.split(",") if c.strip()]

    if not categories:
        print("Erreur : aucune categorie fournie.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        estimate_dry_run(categories, args.max_pages)
        return

    run(
        categories=categories,
        lat=args.lat,
        lng=args.lng,
        radius_km=args.radius_km,
        max_pages=args.max_pages,
        throttle_seconds=args.throttle_seconds,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
