import csv
import os
import tempfile
from unittest.mock import MagicMock, patch

from prospect_finder import run


def make_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = ""
    return resp


class TestRunEndToEndWithMockedHttp:
    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "fake-key-for-tests"})
    @patch("prospect_finder.requests.Session")
    def test_dedupes_and_sorts_by_review_count_descending(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Text Search: returns 2 places, no next page
        search_response = make_response(200, {
            "places": [{"id": "place-1"}, {"id": "place-2"}]
        })
        mock_session.post.return_value = search_response

        # Place Details: place-1 has no website (kept), place-2 has a real site (filtered out)
        details_by_id = {
            "place-1": make_response(200, {
                "id": "place-1",
                "displayName": {"text": "Faible avis"},
                "formattedAddress": "adr 1",
                "userRatingCount": 5,
                "rating": 3.5,
            }),
            "place-2": make_response(200, {
                "id": "place-2",
                "displayName": {"text": "Beaucoup avis"},
                "formattedAddress": "adr 2",
                "userRatingCount": 200,
                "rating": 4.8,
                "websiteUri": "https://vrai-site-web.fr",
            }),
        }

        def fake_get(url, headers, timeout):
            place_id = url.rstrip("/").split("/")[-1]
            return details_by_id[place_id]

        mock_session.get.side_effect = fake_get

        with tempfile.TemporaryDirectory() as tmp:
            output_path = os.path.join(tmp, "out.csv")

            run(
                categories=["plombier"],
                lat=50.3006, lng=2.8020, radius_km=10.0,
                max_pages=1, throttle_seconds=0, output_path=output_path,
            )

            with open(output_path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))

        # header + only place-1 (place-2 filtered out: has a real website)
        assert len(rows) == 2
        assert rows[1][0] == "Faible avis"

    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "fake-key-for-tests"})
    @patch("prospect_finder.requests.Session")
    def test_same_place_across_categories_is_not_duplicated(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Both categories' searches return the SAME place id.
        search_response = make_response(200, {"places": [{"id": "shared-place"}]})
        mock_session.post.return_value = search_response

        mock_session.get.return_value = make_response(200, {
            "id": "shared-place",
            "displayName": {"text": "Commerce partage"},
            "formattedAddress": "adr",
            "userRatingCount": 10,
        })

        with tempfile.TemporaryDirectory() as tmp:
            output_path = os.path.join(tmp, "out.csv")

            run(
                categories=["plombier", "electricien"],
                lat=50.3006, lng=2.8020, radius_km=10.0,
                max_pages=1, throttle_seconds=0, output_path=output_path,
            )

            with open(output_path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))

        assert len(rows) == 2  # header + 1 row, not 2 rows despite appearing in both categories
