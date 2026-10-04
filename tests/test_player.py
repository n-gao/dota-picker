import pytest

from dota_picker.player import PlayerError, parse_account_id


@pytest.mark.parametrize(
    "text",
    [
        "105248644",
        "  105248644 ",
        "https://www.dotabuff.com/players/105248644",
        "https://www.dotabuff.com/players/105248644/heroes",
        "https://www.opendota.com/players/105248644",
        "https://stratz.com/player/105248644",
        "76561198065514372",  # Steam64
        "https://steamcommunity.com/profiles/76561198065514372/",
    ],
)
def test_parse_account_id(text):
    assert parse_account_id(text) == 105248644


@pytest.mark.parametrize("text", ["", "abc", "https://www.dotabuff.com/heroes/axe", "0"])
def test_parse_account_id_rejects_garbage(text):
    with pytest.raises(PlayerError):
        parse_account_id(text)
