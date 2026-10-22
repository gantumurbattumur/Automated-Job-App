from datetime import datetime, timedelta, timezone

import pytest

from backend import feed, geo
from backend.models import Job
from backend.settings import Settings
from tests.test_sources_feed import db  # noqa: F401  (fixture)

# tiny stand-in for the GeoNames list: name -> [lat, lon, country, admin1, population]
FAKE = {"los angeles": [[34.0522, -118.2437, "US", "CA", 3900000]], "pasadena": [[34.1478, -118.1445, "US", "CA", 140000]],
        "irvine": [[33.6846, -117.8265, "US", "CA", 300000]], "san francisco": [[37.7749, -122.4194, "US", "CA", 870000]],
        "seattle": [[47.6062, -122.3321, "US", "WA", 750000]], "new york city": [[40.7128, -74.006, "US", "NY", 8800000]],
        "cambridge": [[52.2053, 0.1218, "GB", "ENG", 145000], [42.3736, -71.1097, "US", "MA", 118000]],
        "london": [[51.5074, -0.1278, "GB", "ENG", 8900000], [37.129, -84.0833, "US", "KY", 8000]],
        "san jose": [[37.3382, -121.8863, "US", "CA", 1000000]], "boise": [[43.615, -116.2023, "US", "ID", 230000]]}


@pytest.fixture(autouse=True)
def fake_index(monkeypatch):
    monkeypatch.setattr(geo, "index", lambda: FAKE)
    geo.points.cache_clear()
    yield
    geo.points.cache_clear()


@pytest.mark.parametrize("loc,labels", [
    ("Pasadena, CA", ["Pasadena, CA"]),
    ("Hybrid - Irvine, California", ["Irvine, CA"]),
    ("Boise, ID - Main Site", ["Boise, ID"]),
    ("USA - California - San Jose", ["San Jose, CA"]),
    ("Seattle, San Francisco, New York City", ["Seattle, WA", "San Francisco, CA", "New York City, NY"]),
    ("New York, New York, USA; Seattle, WA", ["New York City, NY", "Seattle, WA"]),
    ("Remote - US", []), ("2 Locations", []), ("", []),
    ("Cambridge", ["Cambridge, MA"]),  # US one is big enough to win a bare name
    ("London", ["London, GB"]),  # tiny London, KY doesn't
    ("Santa Monica, CA", []),  # not in the list -> unknown, not a wrong guess
])
def test_points(loc, labels):
    assert [p[2] for p in geo.points(loc)] == labels


def test_center_aliases_and_distance():
    la = geo.center("LA")
    assert la[2] == "Los Angeles, CA" and geo.center("Los Angeles CA")[2] == "Los Angeles, CA"
    assert 8 < geo.nearest("Pasadena, CA", la) < 11
    assert 340 < geo.nearest("San Francisco, CA; Seattle, WA", la) < 355  # nearest of several
    assert geo.center("Atlantis") is None


def _job(i, loc):
    return Job(id=f"j{i}", source="t", company="c", title=f"ML Intern {i}", location=loc, url="u",
               posted_at=datetime.now(timezone.utc) - timedelta(hours=1), description="python")


def test_radius_filter_in_feed(db, monkeypatch):  # noqa: F811
    from backend import models, refresh
    refresh._upsert("t", [_job(1, "Pasadena, CA"), _job(2, "San Francisco, CA"), _job(3, "Remote - US"),
                          _job(4, "Multiple Locations"), _job(5, "Irvine, CA")], None, datetime.now(timezone.utc))
    st = Settings(near_location="Los Angeles, CA", near_radius_miles=50, categories=[])
    rows = {r["id"]: r for r in feed.build(st)}
    assert rows["j1"]["hidden_reasons"] == [] and rows["j1"]["distance_mi"] == 9
    assert rows["j5"]["hidden_reasons"] == [] and rows["j5"]["distance_mi"] < 50
    assert rows["j2"]["hidden_reasons"] == ["347 mi from Los Angeles, CA"]
    assert rows["j3"]["hidden_reasons"] == []  # remote passes by default
    assert rows["j4"]["hidden_reasons"] == ["location unknown"]
    rows = {r["id"]: r for r in feed.build(Settings(near_location="LA", near_radius_miles=400, near_include_remote=False,
                                                   near_keep_unknown=True, categories=[]))}
    assert rows["j2"]["hidden_reasons"] == [] and rows["j4"]["hidden_reasons"] == []
    assert rows["j3"]["hidden_reasons"] == []  # unknown point kept by near_keep_unknown
