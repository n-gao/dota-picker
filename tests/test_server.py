import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from conftest import make_scrape

from dota_picker import db, server


@pytest.fixture
def base_url():
    db.save(make_scrape())
    server.refresher = server.Refresher()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def test_index_and_static(base_url):
    status, ctype, body = get(base_url + "/")
    assert status == 200 and "text/html" in ctype and b"Dota Picker" in body
    assert get(base_url + "/app.js")[0] == 200


def test_api_data(base_url):
    status, ctype, body = get(base_url + "/api/data")
    assert ctype == "application/json"
    assert set(json.loads(body)["heroes"]) == {"axe", "lion"}


def test_hero_image(base_url):
    assert get(base_url + "/img/axe.jpg")[2] == b"jpg"


@pytest.mark.parametrize("path", ["/../dota_picker/db.py", "/img/nope.jpg", "/api/build?hero=nope&pos=1"])
def test_not_found(base_url, path):
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base_url + path)
    assert e.value.code == 404
