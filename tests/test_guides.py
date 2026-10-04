import threading
import time

import pytest

from dota_picker import guides

# Synthetic markup mirroring the structure of a Dotabuff guide block.
GUIDE = """
<section>
  <a class="link-type-player" href="/players/1">{player}</a><time>2026-10-01</time>
  <span>{player} won a Close Match</span>
  <i class="fa fa-lane-{lane} lane-icon"></i><i class="fa fa-role-{role} role-icon"></i>
  <div class="kv">~ 5,500 MMR</div>
  <div class="kv"><a href="/items/tango"><img alt="Tango" src="/assets/items/tango.jpg"></a>
    <small>Starting Items</small></div>
  <div class="kv"><a href="/items/phase-boots"><img alt="Phase Boots" src="/assets/items/phase-boots.jpg"></a>
    <small>{boots}</small></div>
  <div class="kv"><a href="/items/blink-dagger"><img alt="Blink Dagger" src="/assets/items/blink-dagger.jpg"></a>
    <small>14:00</small></div>
  <a><img alt="Counter Helix" src="/assets/skills/axe-counter-helix-5009.jpg"><div>1</div></a>
  <a><img alt="Battle Hunger" src="/assets/skills/axe-battle-hunger-5008.jpg"><div>2</div></a>
  <a><img alt="Talent: +40 Counter Helix Damage" src="/assets/skills/talent.jpg"><div>10</div></a>
  <span>Opponents</span>
  <img alt="Phase Boots" src="/assets/items/phase-boots.jpg">
  <img alt="Blink Dagger" src="/assets/items/blink-dagger.jpg">
  <img alt="Prophet's Pendulum" src="/assets/items/prophets-pendulum.jpg">
  <a href="/matches/123">View Match</a>
</section>
"""


def page(*guides_):
    return "<main>" + "".join(guides_) + "</main>"


def guide(player="Dan", lane="offlane", role="core", boots="06:00"):
    return GUIDE.format(player=player, lane=lane, role=role, boots=boots)


def test_parse_guide():
    [g] = guides.parse_guides(page(guide()))
    assert g["player"] == "Dan"
    assert g["won"] is True
    assert (g["lane"], g["role"]) == ("offlane", "core")
    assert g["mmr"] == 5500
    assert g["matchId"] == 123
    assert [i["slug"] for i in g["start"]] == ["tango"]
    assert [(t["slug"], t["t"]) for t in g["timeline"]] == [("phase-boots", 360), ("blink-dagger", 840)]
    assert [s["name"] for s in g["skills"]] == ["Counter Helix", "Battle Hunger", "+40 Counter Helix Damage"]
    assert [s["talent"] for s in g["skills"]] == [False, False, True]
    assert [i["slug"] for i in g["final"]] == ["phase-boots", "blink-dagger", "prophets-pendulum"]


def test_build_aggregates_position_games(monkeypatch):
    parsed = guides.parse_guides(
        page(
            guide("a", boots="05:00"),
            guide("b", boots="06:00"),
            guide("c", boots="07:00"),
            guide("support", lane="safelane", role="support"),
        )
    )
    monkeypatch.setattr(guides, "get_guides", lambda slug: parsed)

    b = guides.build("axe", 3)
    assert b["matchedPosition"] is True
    assert b["guides"] == 3  # the support game is left out
    assert [i["slug"] for i in b["items"]] == ["phase-boots", "blink-dagger"]
    assert b["items"][0]["time"] == 360  # median of 5:00, 6:00, 7:00
    assert all(i["core"] for i in b["items"])
    assert [i["slug"] for i in b["neutrals"]] == ["prophets-pendulum"]  # never bought -> neutral
    assert [s["name"] for s in b["skillOrder"][:2]] == ["Counter Helix", "Battle Hunger"]
    assert [a["name"] for a in b["abilities"]] == ["Battle Hunger", "Counter Helix"]  # slot order


def test_talent_share_counts_games_not_picks(monkeypatch):
    repeated = "<a><img alt='Talent: Attribute Bonus' src='/assets/skills/talent.jpg'><div>{}</div></a>"
    g = guide().replace("<span>Opponents</span>", repeated.format(17) + repeated.format(19) + "<span>Opponents</span>")
    monkeypatch.setattr(guides, "get_guides", lambda slug: guides.parse_guides(page(g, guide())))
    shares = {t["name"]: t["share"] for t in guides.build("axe", 3)["talents"]}
    assert shares["Attribute Bonus"] == 0.5
    assert shares["+40 Counter Helix Damage"] == 1.0


def test_build_falls_back_to_all_roles(monkeypatch):
    monkeypatch.setattr(guides, "get_guides", lambda slug: guides.parse_guides(page(guide(), guide())))
    b = guides.build("axe", 5)
    assert b["matchedPosition"] is False
    assert b["guides"] == 2


def test_asset_paths_are_whitelisted():
    assert guides.get_asset("../db.py") is None
    assert guides.get_asset("heroes/axe.jpg") is None


def test_concurrent_requests_share_one_fetch(monkeypatch):

    calls = []

    def fetch(path):
        calls.append(path)
        time.sleep(0.05)
        return page(guide())

    monkeypatch.setattr(guides.scraper, "fetch", fetch)
    threads = [threading.Thread(target=guides.get_guides, args=("axe",)) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == guides.PAGES  # one scrape, not five
    assert len(guides.get_guides("axe")) == guides.PAGES  # now served from the cache
    assert len(calls) == guides.PAGES


def test_failed_fetch_backs_off(monkeypatch):
    calls = []

    def fetch(path):
        calls.append(path)
        raise ConnectionError("down")

    monkeypatch.setattr(guides.scraper, "fetch", fetch)
    monkeypatch.setattr(guides, "_failed", {})
    for _ in range(3):
        with pytest.raises((ConnectionError, RuntimeError)):
            guides.get_guides("lion")
    assert len(calls) == 1  # later requests don't hit Dotabuff again
