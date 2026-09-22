import pytest

from agent_upgrade_api import get_upgrade_target_asset_ids, parse_upgrade_target_asset_ids


def test_parse_upgrade_target_asset_ids_normalizes_unique_positive_integers():
    assert parse_upgrade_target_asset_ids('[2213, "42", 2213]') == [42, 2213]
    assert get_upgrade_target_asset_ids({"target_asset_ids": [2213]}) == [2213]


@pytest.mark.parametrize("value", ["not-json", "{}", "[0]", "[-1]", "[true]", "[1.5]"])
def test_parse_upgrade_target_asset_ids_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_upgrade_target_asset_ids(value)


def test_malformed_legacy_manifest_falls_back_to_global_rollout():
    assert get_upgrade_target_asset_ids({"target_asset_ids": "invalid"}) == []
