import os
from pathlib import Path


def test_consistency_api_with_fixture():
    # Point UI to fixture data before importing the app
    base = Path('frontend/fixtures/demo_1').resolve()
    os.environ['UI_DATA_DIR'] = str(base)

    # Import after setting env so ui_server picks it up
    from frontend.ui_server import app  # noqa: WPS433 (import inside function for env isolation)

    client = app.test_client()

    # Sanity: suggestion endpoint
    r_sug = client.get('/api/suggestion')
    assert r_sug.status_code == 200
    sug = r_sug.get_json()
    assert sug.get('suggested_card') == 'Example Mapper'

    # Sanity: offers endpoint
    r_off = client.get('/api/offers')
    assert r_off.status_code == 200
    rows = r_off.get_json().get('rows', [])
    assert len(rows) == 3
    picked = [r for r in rows if str(r.get('is_picked')) == '1']
    assert len(picked) == 1
    assert picked[0].get('card_name') == 'Example Mapper'

    # Consistency API should be OK given fixture alignment
    r = client.get('/api/consistency')
    assert r.status_code == 200
    data = r.get_json()
    assert data.get('ok') is True

    c1 = data['checks']['suggestion_vs_offers']
    assert c1['ok'] is True
    assert c1['expected'] == 'Example Mapper'
    assert c1['actual'] == 'Example Mapper'

    # Fixture has no recent_picks.csv, so this check is skipped (ok=None)
    c2 = data['checks']['recent_picks_vs_offers']
    assert c2['ok'] is None

