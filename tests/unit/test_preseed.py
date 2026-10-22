"""Regression tests for OVN cluster initialization with preseed."""

from types import SimpleNamespace
from typing import Any, Optional
from unittest import mock

import pytest
import yaml
from charm import LxdCharm


class _FakeCharm(LxdCharm):
    """Run charm methods without initializing the ops framework."""

    config: Any = None
    model: Any = None
    _stored: Any = None

    def __init__(self, **attrs):
        """Set the attributes required by each test."""
        for name, value in attrs.items():
            setattr(self, name, value)


@pytest.mark.parametrize(
    ("driver", "source", "attached", "expected"),
    [
        pytest.param("zfs", None, True, "/dev/juju-volume", id="attached-zfs"),
        pytest.param("zfs", "/dev/explicit", True, "/dev/explicit", id="explicit-source"),
        pytest.param("dir", None, True, None, id="directory-pool"),
        pytest.param("zfs", None, False, None, id="no-attached-storage"),
    ],
)
def test_preseed_uses_attached_storage(
    driver: str, source: Optional[str], attached: bool, expected: Optional[str]
) -> None:
    """Use Juju's disk for a local ZFS pool without overriding an explicit source."""
    config = {} if source is None else {"source": source}
    preseed = {"storage_pools": [{"name": "local", "driver": driver, "config": config}]}
    storages = {"local": [SimpleNamespace(location="/dev/juju-volume")]} if attached else {}
    charm = _FakeCharm(model=SimpleNamespace(storages=storages))

    with mock.patch("charm.subprocess.run") as run:
        charm.lxd_apply_preseed(yaml.safe_dump(preseed).encode())

    applied = yaml.safe_load(run.call_args.kwargs["input"])
    assert applied["storage_pools"][0]["config"].get("source") == expected


def test_ovsdb_relation_waits_for_certificates() -> None:
    """Replay database configuration after certificates arrive."""
    charm = _FakeCharm(
        _stored=SimpleNamespace(
            ovn_certificates_present=False, config={"snap-config-ovn-builtin": True}
        ),
        unit_blocked=mock.Mock(),
    )
    event = SimpleNamespace(defer=mock.Mock())

    with mock.patch("charm.pylxd.Client") as client:
        client.return_value.has_api_extension.return_value = True
        charm._on_ovsdb_cms_relation_changed(event)

    event.defer.assert_called_once_with()
    charm.unit_blocked.assert_called_once_with("'certificates' missing")


@pytest.mark.parametrize("preseed_enabled", [True, False])
def test_cluster_join_uses_preseed_network_parent(preseed_enabled: bool) -> None:
    """Carry the configured physical parent to joining cluster members."""
    preseed = {"networks": [{"name": "UPLINK", "type": "physical", "config": {"parent": "br-ex"}}]}
    member_config = [
        {"entity": "network", "name": "UPLINK", "key": "parent", "value": ""},
        {"entity": "network", "name": "other", "key": "parent", "value": "eth1"},
        {"entity": "storage-pool", "name": "local", "key": "source", "value": ""},
    ]
    apply = mock.Mock()
    charm = _FakeCharm(
        config={"lxd-preseed": yaml.safe_dump(preseed) if preseed_enabled else ""},
        model=SimpleNamespace(storages={"local": [SimpleNamespace(location="/dev/juju-volume")]}),
        _stored=SimpleNamespace(lxd_clustered=False),
        juju_space_get_address=lambda name: "192.0.2.10",
        unit_maintenance=lambda message: None,
        unit_active=lambda: None,
        lxd_apply_preseed=apply,
    )

    charm.lxd_cluster_join("test-token", member_config)

    applied = yaml.safe_load(apply.call_args.args[0])
    values = applied["cluster"]["member_config"]
    assert values[0]["value"] == ("br-ex" if preseed_enabled else "")
    assert values[1]["value"] == "eth1"
    assert values[2]["value"] == "/dev/juju-volume"
    assert charm._stored.lxd_clustered
