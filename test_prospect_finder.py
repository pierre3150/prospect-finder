import csv
import os
import tempfile

import pytest

from prospect_finder import (
    Prospect,
    build_maps_link,
    details_to_prospect,
    is_missing_or_social_only_website,
    write_csv,
)


class TestIsMissingOrSocialOnlyWebsite:
    def test_none_website_is_missing(self):
        assert is_missing_or_social_only_website(None) is True

    def test_empty_string_is_missing(self):
        assert is_missing_or_social_only_website("") is True

    def test_real_website_is_not_missing(self):
        assert is_missing_or_social_only_website("https://plomberie-dupont.fr") is False

    def test_real_website_with_path_is_not_missing(self):
        assert is_missing_or_social_only_website("https://example.com/contact") is False

    @pytest.mark.parametrize("url", [
        "https://www.facebook.com/monentreprise",
        "https://facebook.com/monentreprise",
        "https://m.facebook.com/monentreprise",
        "https://instagram.com/monentreprise",
        "https://www.instagram.com/monentreprise",
        "https://fb.com/monentreprise",
    ])
    def test_social_only_urls_are_flagged(self, url):
        assert is_missing_or_social_only_website(url) is True

    def test_domain_containing_facebook_as_substring_is_not_falsely_flagged(self):
        # garde-fou anti faux-positif: "facebook-consulting.com" n'est pas facebook.com
        assert is_missing_or_social_only_website("https://facebook-consulting.com") is False

    def test_malformed_url_does_not_crash(self):
        result = is_missing_or_social_only_website("not a url at all :::")
        assert isinstance(result, bool)


class TestBuildMapsLink:
    def test_builds_valid_place_id_link(self):
        link = build_maps_link("ChIJабс123")
        assert "ChIJабс123" in link
        assert link.startswith("https://www.google.com/maps/place/")


class TestDetailsToProspect:
    def test_maps_full_details_to_prospect(self):
        details = {
            "id": "abc123",
            "displayName": {"text": "Plomberie Dupont"},
            "formattedAddress": "1 Rue de la Paix, 62223 Saint-Laurent-Blangy",
            "nationalPhoneNumber": "03 21 00 00 00",
            "rating": 4.5,
            "userRatingCount": 42,
            "googleMapsUri": "https://maps.google.com/?cid=123",
        }

        prospect = details_to_prospect(details, "plombier")

        assert prospect.nom == "Plomberie Dupont"
        assert prospect.nombre_avis == 42
        assert prospect.note_google == 4.5
        assert prospect.categorie == "plombier"
        assert prospect.lien_google_maps == "https://maps.google.com/?cid=123"

    def test_missing_optional_fields_default_gracefully(self):
        details = {"id": "abc123", "displayName": {"text": "Sans avis"}}

        prospect = details_to_prospect(details, "coiffeur")

        assert prospect.nombre_avis == 0
        assert prospect.note_google is None
        assert prospect.telephone == ""

    def test_falls_back_to_built_maps_link_when_missing(self):
        details = {"id": "xyz789", "displayName": {"text": "Test"}}

        prospect = details_to_prospect(details, "restaurant")

        assert "xyz789" in prospect.lien_google_maps


class TestWriteCsv:
    def test_writes_header_and_rows(self):
        prospects = [
            Prospect("A", "Adresse A", "0102030405", 4.0, 10, "plombier", "https://maps/a"),
            Prospect("B", "Adresse B", "0102030406", None, 3, "coiffeur", "https://maps/b"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.csv")
            write_csv(prospects, path)

            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))

        assert rows[0] == ["nom", "adresse", "telephone", "note_google", "nombre_avis", "categorie", "lien_google_maps"]
        assert rows[1][0] == "A"
        assert rows[2][3] == ""  # note_google absente -> chaine vide, pas "None"

    def test_preserves_given_order(self):
        # le tri est fait par l'appelant (run()); write_csv se contente d'ecrire dans l'ordre recu
        prospects = [
            Prospect("HighReviews", "adr", "tel", 4.0, 100, "cat", "link"),
            Prospect("LowReviews", "adr", "tel", 4.0, 5, "cat", "link"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.csv")
            write_csv(prospects, path)

            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))

        assert rows[1][0] == "HighReviews"
        assert rows[2][0] == "LowReviews"
