#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.


import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cmk.base.check_legacy_includes.cpu_util import check_cpu_util_unix, CPUInfo

from cmk.agent_based.legacy.v0_unstable import (
    LegacyCheckDefinition,
    LegacyCheckResult,
    LegacyDiscoveryResult,
)
from cmk.agent_based.v2 import get_rate, get_value_store, SNMPTree, StringTable
from cmk.plugins.lib import ucd_hr_detection

check_info = {}

#    UCD-SNMP-MIB::ssCpuRawUser.0 = Counter32: 219998591
#    UCD-SNMP-MIB::ssCpuRawNice.0 = Counter32: 0
#    UCD-SNMP-MIB::ssCpuRawSystem.0 = Counter32: 98206536
#    UCD-SNMP-MIB::ssCpuRawIdle.0 = Counter32: 3896034232
#    UCD-SNMP-MIB::ssCpuRawWait.0 = Counter32: 325152257
#    UCD-SNMP-MIB::ssCpuRawInterrupt.0 = Counter32: 1940759
#    UCD-SNMP-MIB::ssIORawSent.0 = Counter32: 898622850
#    UCD-SNMP-MIB::ssIORawReceived.0 = Counter32: 445747508
#    UCD-SNMP-MIB::ssCpuRawSoftIRQ.0 = Counter32: 277402


@dataclass(frozen=True)
class _IO:
    received: int
    send: int


@dataclass(frozen=True)
class Section:
    error: str | None
    cpu_ticks: CPUInfo
    io: _IO | None


def parse_ucd_cpu_util(string_table: StringTable) -> Section | None:
    if not string_table:
        return None

    (
        error,
        raw_cpu_user,
        raw_cpu_nice,
        raw_cpu_system,
        raw_cpu_idle,
        raw_cpu_wait,
        raw_cpu_interrupt,
        raw_io_send,
        raw_io_received,
        raw_cpu_softirq,
    ) = string_table[0]

    try:
        cpu_ticks = CPUInfo(
            "cpu",
            raw_cpu_user or None,
            raw_cpu_nice or None,
            raw_cpu_system or None,
            raw_cpu_idle or None,
            raw_cpu_wait or None,
            raw_cpu_interrupt or None,
            raw_cpu_softirq or None,
        )
    except ValueError:
        return None

    try:
        io = _IO(int(raw_io_received), int(raw_io_send))
    except ValueError:
        io = None

    return Section(error=error or None, cpu_ticks=cpu_ticks, io=io)


def inventory_ucd_cpu_util(section: Section) -> LegacyDiscoveryResult:
    yield None, {}


def check_ucd_cpu_util(
    _no_item: None, params: Mapping[str, Any], section: Section
) -> LegacyCheckResult:
    now = time.time()
    value_store = get_value_store()

    if section.error and section.error != "systemStats":
        yield 1, f"Error: {section.error}"

    yield from check_cpu_util_unix(section.cpu_ticks, params)

    if section.io is None:
        return

    yield (
        0,
        "",
        [
            ("read_blocks", get_rate(value_store, "io_received", now, section.io.received)),
            ("write_blocks", get_rate(value_store, "io_send", now, section.io.send)),
        ],
    )


check_info["ucd_cpu_util"] = LegacyCheckDefinition(
    name="ucd_cpu_util",
    detect=ucd_hr_detection.PREFER_HR_ELSE_UCD,
    fetch=SNMPTree(
        base=".1.3.6.1.4.1.2021.11",
        oids=["2", "50", "51", "52", "53", "54", "56", "57", "58", "61"],
    ),
    parse_function=parse_ucd_cpu_util,
    service_name="CPU utilization",
    discovery_function=inventory_ucd_cpu_util,
    check_function=check_ucd_cpu_util,
    check_ruleset_name="cpu_iowait",
)
