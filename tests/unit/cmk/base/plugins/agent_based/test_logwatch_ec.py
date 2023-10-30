#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

import cmk.utils.paths
from cmk.utils.hostaddress import HostName

from cmk.base.plugins.agent_based import logwatch_ec
from cmk.base.plugins.agent_based.agent_based_api.v1 import Metric, Result, Service, State
from cmk.base.plugins.agent_based.agent_based_api.v1.type_defs import (
    CheckResult,
    DiscoveryResult,
    StringTable,
)
from cmk.base.plugins.agent_based.logwatch_section import parse_logwatch
from cmk.base.plugins.agent_based.utils import logwatch as logwatch_

from cmk.ec.export import SyslogMessage

_STRING_TABLE_NO_MESSAGES = [
    ["[[[log1]]]"],
    ["[[[log2]]]"],
    ["[[[log3:missing]]]"],
    ["[[[log4:cannotopen]]]"],
    ["[[[log5]]]"],
    ["[[[log1:missing]]]"],
]

_STRING_TABLE_MESSAGES_LOG1 = [
    ["[[[log1]]]"],
    ["BATCH: 1680617834-122172169179246007103019047128114004006211120121"],
    ["C ERROR: issue 1"],
    ["C ERROR: issue 2"],
    ["[[[log2]]]"],
    ["[[[log3:missing]]]"],
    ["[[[log4:cannotopen]]]"],
    ["[[[log5]]]"],
    ["[[[log1:missing]]]"],
]

_STRING_TABLE_MESSAGES_LOG1_2 = [
    ["[[[log1]]]"],
    ["BATCH: 1680617840-135239174175144102013221144181058125008119107236"],
    ["C ERROR: issue 1"],
    ["C ERROR: issue 2"],
    ["C ERROR: issue 3"],
]

_STRING_TABLE_MESSAGES_LOG5 = [
    ["[[[log2]]]"],
    ["[[[log3:missing]]]"],
    ["[[[log4:cannotopen]]]"],
    ["[[[log5]]]"],
    ["BATCH: 1680617711-122172169179246007103019047128114004006211120555"],
    ["C ERROR: issue 1"],
    ["C ERROR: issue 2"],
]


SECTION1 = logwatch_.Section(
    errors=[],
    logfiles={
        "log1": {
            "attr": "ok",
            "lines": {
                "test": [
                    "W This long message should be written to one spool file",
                    "C And this long message should be written to another spool file",
                    "W This last long message should be written to a third spool file",
                ]
            },
        },
    },
)


@pytest.mark.parametrize(
    "info, fwd_rule, expected_result",
    [
        (_STRING_TABLE_NO_MESSAGES, [], []),
        (
            _STRING_TABLE_NO_MESSAGES,
            [{"separate_checks": True}],
            [
                Service(item="log1", parameters={"expected_logfiles": ["log1"]}),
                Service(item="log2", parameters={"expected_logfiles": ["log2"]}),
                Service(item="log4", parameters={"expected_logfiles": ["log4"]}),
                Service(item="log5", parameters={"expected_logfiles": ["log5"]}),
            ],
        ),
        (_STRING_TABLE_NO_MESSAGES, [{"restrict_logfiles": [".*"]}], []),
        (
            _STRING_TABLE_NO_MESSAGES,
            [
                {
                    "restrict_logfiles": [".*"],
                    "separate_checks": True,
                }
            ],
            [
                Service(item="log1", parameters={"expected_logfiles": ["log1"]}),
                Service(item="log2", parameters={"expected_logfiles": ["log2"]}),
                Service(item="log4", parameters={"expected_logfiles": ["log4"]}),
                Service(item="log5", parameters={"expected_logfiles": ["log5"]}),
            ],
        ),
        (
            _STRING_TABLE_NO_MESSAGES,
            [
                {
                    "restrict_logfiles": [".*"],
                    "separate_checks": False,
                }
            ],
            [],
        ),
        (
            _STRING_TABLE_NO_MESSAGES,
            [
                {
                    "restrict_logfiles": [".*"],
                }
            ],
            [],
        ),
        (
            _STRING_TABLE_NO_MESSAGES,
            [
                {
                    "restrict_logfiles": ["log1"],
                    "separate_checks": True,
                    "method": "pass me on!",
                    "facility": "pass me on!",
                    "monitor_logfilelist": "pass me on!",
                    "monitor_logfile_access_state": "pass me on!",
                    "logwatch_reclassify": "pass me on!",
                    "some_other_key": "I should be discarded!",
                }
            ],
            [
                Service(
                    item="log1",
                    parameters={
                        "expected_logfiles": ["log1"],
                        "method": "pass me on!",
                        "facility": "pass me on!",
                        "monitor_logfilelist": "pass me on!",
                        "monitor_logfile_access_state": "pass me on!",
                        "logwatch_reclassify": "pass me on!",
                    },
                ),
            ],
        ),
    ],
)
def test_logwatch_ec_inventory_single(
    monkeypatch: pytest.MonkeyPatch,
    info: StringTable,
    fwd_rule: Mapping[str, object],
    expected_result: DiscoveryResult,
) -> None:
    parsed = parse_logwatch(info)

    monkeypatch.setattr(logwatch_, "get_ec_rule_params", lambda: fwd_rule)
    actual_result = sorted(logwatch_ec.discover_single(parsed), key=lambda s: s.item or "")
    assert actual_result == expected_result


@pytest.mark.parametrize(
    "info, fwd_rule, expected_result",
    [
        (_STRING_TABLE_NO_MESSAGES, [], []),
        (_STRING_TABLE_NO_MESSAGES, [{"separate_checks": True}], []),
        (
            _STRING_TABLE_NO_MESSAGES,
            [{"separate_checks": False}],
            [
                Service(parameters={"expected_logfiles": ["log1", "log2", "log4", "log5"]}),
            ],
        ),
        (
            _STRING_TABLE_NO_MESSAGES,
            [{"restrict_logfiles": [".*[12]"], "separate_checks": False}],
            [
                Service(parameters={"expected_logfiles": ["log1", "log2"]}),
            ],
        ),
    ],
)
def test_logwatch_ec_inventory_groups(
    monkeypatch: pytest.MonkeyPatch,
    info: StringTable,
    fwd_rule: Mapping[str, object],
    expected_result: DiscoveryResult,
) -> None:
    parsed = parse_logwatch(info)

    monkeypatch.setattr(logwatch_, "get_ec_rule_params", lambda: fwd_rule)
    actual_result = list(logwatch_ec.discover_group(parsed))
    assert actual_result == expected_result


class _FakeForwarder:
    def __call__(
        self,
        method: str | tuple,
        messages: Sequence[SyslogMessage],
    ) -> logwatch_ec.LogwatchForwardedResult:
        return logwatch_ec.LogwatchForwardedResult(num_forwarded=len(messages))


@pytest.mark.parametrize(
    "item, params, parsed, expected_result",
    [
        (
            "log1",
            logwatch_ec.CHECK_DEFAULT_PARAMETERS,
            {"node1": parse_logwatch(_STRING_TABLE_NO_MESSAGES)},
            [
                Result(state=State.OK, summary="Forwarded 0 messages"),
                Metric("messages", 0.0),
            ],
        ),
        (
            "log4",
            {
                "facility": 17,  # default to "local1"
                "method": "",  # local site
                "monitor_logfilelist": False,
                "monitor_logfile_access_state": 2,
                "expected_logfiles": ["log4"],
            },
            {"node1": parse_logwatch(_STRING_TABLE_NO_MESSAGES)},
            [
                Result(state=State.CRIT, summary="[node1] Could not read log file 'log4'"),
                Result(state=State.OK, summary="Forwarded 0 messages"),
                Metric("messages", 0.0),
            ],
        ),
    ],
)
def test_check_logwatch_ec_common_single_node(
    item: str | None,
    params: Mapping[str, Any],
    parsed: logwatch_ec.ClusterSection,
    expected_result: CheckResult,
) -> None:
    assert (
        list(
            logwatch_ec.check_logwatch_ec_common(
                item,
                params,
                parsed,
                service_level=10,
                value_store={},
                hostname=HostName("test-host"),
                message_forwarder=_FakeForwarder(),
            )
        )
        == expected_result
    )


def test_check_logwatch_ec_common_single_node_item_missing() -> None:
    assert not list(
        logwatch_ec.check_logwatch_ec_common(
            "log1",
            logwatch_ec.CHECK_DEFAULT_PARAMETERS,
            {
                "node1": parse_logwatch(_STRING_TABLE_MESSAGES_LOG5),
            },
            service_level=10,
            value_store={},
            hostname=HostName("test-host"),
            message_forwarder=_FakeForwarder(),
        )
    )


def test_check_logwatch_ec_common_single_node_log_missing() -> None:
    actual_result = list(
        logwatch_ec.check_logwatch_ec_common(
            "log3",
            {
                "facility": 17,  # default to "local1"
                "method": "",  # local site
                "monitor_logfilelist": True,
                "monitor_logfile_access_state": 2,
                "expected_logfiles": ["log3"],
            },
            {
                "node1": parse_logwatch(_STRING_TABLE_MESSAGES_LOG5),
            },
            service_level=10,
            value_store={},
            hostname=HostName("test-host"),
            message_forwarder=_FakeForwarder(),
        )
    )

    assert actual_result == [
        Result(state=State.WARN, summary="Missing logfiles: log3 (on node1)"),
        Result(state=State.OK, summary="Forwarded 0 messages"),
        Metric("messages", 0.0),
    ]


@pytest.mark.parametrize(
    ["cluster_section", "expected_result"],
    [
        pytest.param(
            {
                "node1": parse_logwatch(_STRING_TABLE_NO_MESSAGES),
                "node2": parse_logwatch(_STRING_TABLE_NO_MESSAGES),
            },
            [
                Result(state=State.OK, summary="Forwarded 0 messages"),
                Metric("messages", 0.0),
            ],
            id="no messages",
        ),
        pytest.param(
            {
                "node1": parse_logwatch(_STRING_TABLE_NO_MESSAGES),
                "node2": parse_logwatch(_STRING_TABLE_MESSAGES_LOG1),
            },
            [
                Result(state=State.OK, summary="Forwarded 2 messages from log1"),
                Metric("messages", 2.0),
            ],
            id="messages on one node",
        ),
        pytest.param(
            {
                "node1": parse_logwatch(_STRING_TABLE_MESSAGES_LOG1),
                "node2": parse_logwatch(_STRING_TABLE_MESSAGES_LOG1_2),
            },
            [
                Result(state=State.OK, summary="Forwarded 5 messages from log1"),
                Metric("messages", 5.0),
            ],
            id="messages on both nodes",
        ),
    ],
)
def test_check_logwatch_ec_common_multiple_nodes_grouped(
    cluster_section: logwatch_ec.ClusterSection,
    expected_result: CheckResult,
) -> None:
    assert (
        list(
            logwatch_ec.check_logwatch_ec_common(
                "log1",
                logwatch_ec.CHECK_DEFAULT_PARAMETERS,
                cluster_section,
                service_level=10,
                value_store={},
                hostname=HostName("test-host"),
                message_forwarder=_FakeForwarder(),
            )
        )
        == expected_result
    )


@pytest.mark.parametrize(
    ["params", "cluster_section", "expected_result"],
    [
        pytest.param(
            logwatch_ec.CHECK_DEFAULT_PARAMETERS,
            {
                "node1": parse_logwatch(_STRING_TABLE_NO_MESSAGES),
                "node2": parse_logwatch(_STRING_TABLE_NO_MESSAGES),
            },
            [
                Result(state=State.CRIT, summary="[node1] Could not read log file 'log4'"),
                Result(state=State.CRIT, summary="[node2] Could not read log file 'log4'"),
                Result(state=State.OK, summary="Forwarded 0 messages"),
                Metric("messages", 0.0),
            ],
            id="no messages",
        ),
        pytest.param(
            logwatch_ec.CHECK_DEFAULT_PARAMETERS,
            {
                "node1": parse_logwatch(_STRING_TABLE_NO_MESSAGES),
                "node2": parse_logwatch(_STRING_TABLE_MESSAGES_LOG1),
            },
            [
                Result(state=State.CRIT, summary="[node1] Could not read log file 'log4'"),
                Result(state=State.CRIT, summary="[node2] Could not read log file 'log4'"),
                Result(state=State.OK, summary="Forwarded 2 messages from log1"),
                Metric("messages", 2.0),
            ],
            id="messages on one node",
        ),
        pytest.param(
            {
                "facility": 17,  # default to "local1"
                "method": "",  # local site
                "monitor_logfilelist": False,
                "monitor_logfile_access_state": 2,
                "expected_logfiles": ["log4"],
            },
            {
                "node1": parse_logwatch(_STRING_TABLE_NO_MESSAGES),
                "node2": parse_logwatch(_STRING_TABLE_MESSAGES_LOG1),
            },
            [
                Result(state=State.CRIT, summary="[node1] Could not read log file 'log4'"),
                Result(state=State.CRIT, summary="[node2] Could not read log file 'log4'"),
                Result(state=State.OK, summary="Forwarded 2 messages from log1"),
                Metric("messages", 2.0),
            ],
            id="no access to logfile on both nodes",
        ),
        pytest.param(
            logwatch_ec.CHECK_DEFAULT_PARAMETERS,
            {
                "node1": parse_logwatch(_STRING_TABLE_MESSAGES_LOG1),
                "node2": parse_logwatch(_STRING_TABLE_MESSAGES_LOG1_2),
            },
            [
                Result(state=State.CRIT, summary="[node1] Could not read log file 'log4'"),
                Result(state=State.OK, summary="Forwarded 5 messages from log1"),
                Metric("messages", 5.0),
            ],
            id="messages on both nodes, same logfile",
        ),
        pytest.param(
            logwatch_ec.CHECK_DEFAULT_PARAMETERS,
            {
                "node1": parse_logwatch(_STRING_TABLE_MESSAGES_LOG1),
                "node2": parse_logwatch(_STRING_TABLE_MESSAGES_LOG5),
            },
            [
                Result(state=State.CRIT, summary="[node1] Could not read log file 'log4'"),
                Result(state=State.CRIT, summary="[node2] Could not read log file 'log4'"),
                Result(state=State.OK, summary="Forwarded 4 messages from log1, log5"),
                Metric("messages", 4.0),
            ],
            id="messages on both nodes, different logfiles",
        ),
    ],
)
def test_check_logwatch_ec_common_multiple_nodes_ungrouped(
    params: Mapping[str, Any],
    cluster_section: logwatch_ec.ClusterSection,
    expected_result: CheckResult,
) -> None:
    assert (
        list(
            logwatch_ec.check_logwatch_ec_common(
                None,
                params,
                cluster_section,
                service_level=10,
                value_store={},
                hostname=HostName("test-host"),
                message_forwarder=_FakeForwarder(),
            )
        )
        == expected_result
    )


def test_check_logwatch_ec_common_multiple_nodes_item_completely_missing() -> None:
    assert not list(
        logwatch_ec.check_logwatch_ec_common(
            "log1",
            logwatch_ec.CHECK_DEFAULT_PARAMETERS,
            {
                "node1": parse_logwatch(_STRING_TABLE_MESSAGES_LOG5),
                "node2": parse_logwatch(_STRING_TABLE_MESSAGES_LOG5),
            },
            service_level=10,
            value_store={},
            hostname=HostName("test-host"),
            message_forwarder=_FakeForwarder(),
        )
    )


def test_check_logwatch_ec_common_multiple_nodes_item_partially_missing() -> None:
    assert list(
        logwatch_ec.check_logwatch_ec_common(
            "log1",
            logwatch_ec.CHECK_DEFAULT_PARAMETERS,
            {
                "node1": parse_logwatch(_STRING_TABLE_MESSAGES_LOG1),
                "node2": parse_logwatch(_STRING_TABLE_MESSAGES_LOG5),
            },
            service_level=10,
            value_store={},
            hostname=HostName("test-host"),
            message_forwarder=_FakeForwarder(),
        )
    ) == [
        Result(state=State.OK, summary="Forwarded 2 messages from log1"),
        Metric("messages", 2.0),
    ]


def test_check_logwatch_ec_common_multiple_nodes_logfile_missing() -> None:
    assert list(
        logwatch_ec.check_logwatch_ec_common(
            "log3",
            {
                "facility": 17,  # default to "local1"
                "method": "",  # local site
                "monitor_logfilelist": True,
                "monitor_logfile_access_state": 2,
                "expected_logfiles": ["log3"],
            },
            {
                "node1": parse_logwatch(_STRING_TABLE_MESSAGES_LOG1),
                "node2": parse_logwatch(_STRING_TABLE_MESSAGES_LOG1),
            },
            service_level=10,
            value_store={},
            hostname=HostName("test-host"),
            message_forwarder=_FakeForwarder(),
        )
    ) == [
        Result(state=State.WARN, summary="Missing logfiles: log3 (on node1, node2)"),
        Result(state=State.OK, summary="Forwarded 0 messages"),
        Metric("messages", 0.0),
    ]


def test_check_logwatch_ec_common_spool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logwatch_ec, "_MAX_SPOOL_SIZE", 32)
    assert list(
        logwatch_ec.check_logwatch_ec_common(
            "log1",
            {
                **logwatch_ec.CHECK_DEFAULT_PARAMETERS,
                "method": "spool:",
            },
            {
                "node1": SECTION1,
            },
            service_level=10,
            value_store={},
            hostname=HostName("test-host"),
            message_forwarder=logwatch_ec.MessageForwarder("log1", HostName("test-host")),
        )
    ) == [
        Result(state=State.OK, summary="Forwarded 3 messages from log1"),
        Metric("messages", 3.0),
    ]
    assert len(list(Path(cmk.utils.paths.omd_root, "var/mkeventd/spool").iterdir())) == 3


class FakeTcpError(Exception):
    pass


def _forward_message(
    successful: bool,
) -> tuple[logwatch_ec.LogwatchForwardedResult, list[tuple[object, ...]],]:
    messages_forwarded: list[tuple[object, ...]] = []

    class TestForwardTcpMessageForwarder(logwatch_ec.MessageForwarder):
        @staticmethod
        def _forward_send_tcp(method, message_chunks, result):
            nonlocal messages_forwarded
            if successful:
                for message in message_chunks:
                    messages_forwarded.append(message)
                    result.num_forwarded += 1
            else:
                result.exception = FakeTcpError("could not send messages")

    result = TestForwardTcpMessageForwarder(item="item_name", hostname=HostName("some_host_name"))(
        ("tcp", {"address": "127.0.0.1", "port": 127001}),
        [SyslogMessage(facility=1, severity=1, text="some_text")],
    )

    return result, messages_forwarded


def test_forward_tcp_message_forwarded_ok() -> None:
    result, messages_forwarded = _forward_message(successful=True)
    assert result == logwatch_ec.LogwatchForwardedResult(
        num_forwarded=1,
        num_spooled=0,
        num_dropped=1,  # TODO: this is a bug!
        exception=None,
    )

    assert len(messages_forwarded) == 1
    # first element of message is a timestamp!
    assert messages_forwarded[0][1:] == (
        0,
        ["<9>1 - - - - - [Checkmk@18662] some_text"],
    )


def test_forward_tcp_message_forwarded_nok() -> None:
    result, messages_forwarded = _forward_message(successful=False)

    assert result.num_forwarded == 0
    assert result.num_spooled == 0
    assert result.num_dropped == 1
    assert isinstance(result.exception, FakeTcpError)

    assert len(messages_forwarded) == 0
