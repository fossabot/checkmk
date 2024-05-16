#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import datetime
import errno
import logging
import os
import re
import tempfile
import time
from collections.abc import Container, Iterable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NamedTuple

import cmk.utils
import cmk.utils.paths
import cmk.utils.store as store
from cmk.utils.agentdatatype import AgentRawData
from cmk.utils.hostaddress import HostAddress, HostName

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PiggybackFileInfo:
    source: HostName
    file_path: Path
    valid: bool
    message: str
    status: int

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError(self.message)


class PiggybackRawDataInfo(NamedTuple):
    info: PiggybackFileInfo
    raw_data: AgentRawData


_PiggybackTimeSetting = tuple[str | None, str, int]

PiggybackTimeSettings = Sequence[_PiggybackTimeSetting]

_PiggybackTimeSettingsMap = Mapping[tuple[str | None, str], int]

# ***** Terminology *****
# "piggybacked_host_folder":
# - tmp/check_mk/piggyback/HOST
#
# "piggybacked_hostname":
# - Path(tmp/check_mk/piggyback/HOST).name
#
# "piggybacked_host_source":
# - tmp/check_mk/piggyback/HOST/SOURCE
#
# "source_state_file":
# - tmp/check_mk/piggyback_sources/SOURCE
#
# "source_hostname":
# - Path(tmp/check_mk/piggyback/HOST/SOURCE).name
# - Path(tmp/check_mk/piggyback_sources/SOURCE).name


def get_piggyback_raw_data(
    piggybacked_hostname: HostName | HostAddress | None,
    time_settings: PiggybackTimeSettings,
) -> Sequence[PiggybackRawDataInfo]:
    """Returns the usable piggyback data for the given host

    A list of two element tuples where the first element is
    the source host name and the second element is the raw
    piggyback data (byte string)
    """
    if not piggybacked_hostname:
        return []

    piggyback_file_infos = _get_piggyback_processed_file_infos(piggybacked_hostname, time_settings)
    if not piggyback_file_infos:
        logger.debug(
            "No piggyback files for '%s'. Skip processing.",
            piggybacked_hostname,
        )
        return []

    piggyback_data = []
    for file_info in piggyback_file_infos:
        try:
            # Raw data is always stored as bytes. Later the content is
            # converted to unicode in abstact.py:_parse_info which respects
            # 'encoding' in section options.
            piggyback_raw_data = PiggybackRawDataInfo(
                info=file_info,
                raw_data=AgentRawData(store.load_bytes_from_file(file_info.file_path)),
            )

        except OSError as exc:
            piggyback_raw_data = PiggybackRawDataInfo(
                PiggybackFileInfo(
                    source=file_info.source,
                    file_path=file_info.file_path,
                    valid=False,
                    message=f"Cannot read piggyback raw data from source '{file_info.source}': {exc}",
                    status=0,
                ),
                raw_data=AgentRawData(b""),
            )

        logger.debug(
            "Piggyback file '%s': %s", file_info.file_path, piggyback_raw_data.info.message
        )
        piggyback_data.append(piggyback_raw_data)
    return piggyback_data


def get_source_and_piggyback_hosts(
    time_settings: PiggybackTimeSettings,
) -> Iterator[tuple[HostName, HostName]]:
    """Generates all piggyback pig/piggybacked host pairs that have up-to-date data"""

    for piggybacked_host_folder in _get_piggybacked_host_folders():
        for file_info in _get_piggyback_processed_file_infos(
            HostName(piggybacked_host_folder.name),
            time_settings,
        ):
            if not file_info.valid:
                continue
            yield HostName(file_info.source), HostName(piggybacked_host_folder.name)


def has_piggyback_raw_data(
    piggybacked_hostname: HostName,
    time_settings: PiggybackTimeSettings,
) -> bool:
    return any(
        fi.valid for fi in _get_piggyback_processed_file_infos(piggybacked_hostname, time_settings)
    )


# TODO: first shot, improve this!
class _TimeSettingsMap:
    def __init__(
        self,
        source_hostnames: Container[HostName],
        piggybacked_hostname: HostName | HostAddress,
        time_settings: PiggybackTimeSettings,
    ) -> None:
        matching_time_settings: dict[tuple[str | None, str], int] = {}
        for expr, key, value in time_settings:
            # expr may be
            #   - None (global settings) or
            #   - 'source-hostname' or
            #   - 'piggybacked-hostname' or
            #   - '~piggybacked-[hH]ostname'
            # the first entry ('piggybacked-hostname' vs '~piggybacked-[hH]ostname') wins
            if expr is None or expr in source_hostnames or expr == piggybacked_hostname:
                matching_time_settings.setdefault((expr, key), value)
            elif expr.startswith("~") and re.match(expr[1:], piggybacked_hostname):
                matching_time_settings.setdefault((piggybacked_hostname, key), value)

        self._expanded_settings: Final = matching_time_settings

    def _match(
        self, key: str, source_hostname: HostName, piggybacked_hostname: HostName | HostAddress
    ) -> int:
        with suppress(KeyError):
            return self._expanded_settings[(piggybacked_hostname, key)]
        with suppress(KeyError):
            return self._expanded_settings[(source_hostname, key)]
        return self._expanded_settings[(None, key)]

    def max_cache_age(
        self,
        source_hostname: HostName,
        piggybacked_hostname: HostName | HostAddress,
    ) -> int:
        return self._match("max_cache_age", source_hostname, piggybacked_hostname)

    def validity_period(
        self,
        source_hostname: HostName,
        piggybacked_hostname: HostName | HostAddress,
    ) -> int | None:
        try:
            return self._match("validity_period", source_hostname, piggybacked_hostname)
        except KeyError:
            return None

    def validity_state(
        self,
        source_hostname: HostName,
        piggybacked_hostname: HostName | HostAddress,
    ) -> int:
        try:
            return self._match("validity_state", source_hostname, piggybacked_hostname)
        except KeyError:
            return 0


def _get_piggyback_processed_file_infos(
    piggybacked_hostname: HostName | HostAddress,
    time_settings: PiggybackTimeSettings,
) -> Sequence[PiggybackFileInfo]:
    """Gather a list of piggyback files to read for further processing.

    Please note that there may be multiple parallel calls executing the
    _get_piggyback_processed_file_infos(), store_piggyback_raw_data() or cleanup_piggyback_files()
    functions. Therefor all these functions needs to deal with suddenly vanishing or
    updated files/directories.
    """
    source_hostnames = get_source_hostnames(piggybacked_hostname)
    expanded_time_settings = _TimeSettingsMap(source_hostnames, piggybacked_hostname, time_settings)
    return [
        _get_piggyback_processed_file_info(
            source_hostname,
            piggybacked_hostname=piggybacked_hostname,
            piggyback_file_path=_get_piggybacked_file_path(source_hostname, piggybacked_hostname),
            settings=expanded_time_settings,
        )
        for source_hostname in source_hostnames
    ]


def _get_piggyback_processed_file_info(
    source_hostname: HostName,
    *,
    piggybacked_hostname: HostName | HostAddress,
    piggyback_file_path: Path,
    settings: _TimeSettingsMap,
) -> PiggybackFileInfo:
    try:
        file_age = _time_since_last_modification(piggyback_file_path)
    except FileNotFoundError:
        return PiggybackFileInfo(
            source=source_hostname,
            file_path=piggyback_file_path,
            valid=False,
            message="Piggyback file is missing",
            status=0,
        )

    if file_age > (allowed := settings.max_cache_age(source_hostname, piggybacked_hostname)):
        return PiggybackFileInfo(
            source=source_hostname,
            file_path=piggyback_file_path,
            valid=False,
            message=f"Piggyback file too old (age: {_render_time(file_age)}, allowed: {_render_time(allowed)})",
            status=0,
        )

    validity_period = settings.validity_period(source_hostname, piggybacked_hostname)
    validity_state = settings.validity_state(source_hostname, piggybacked_hostname)

    status_file_path = _get_source_status_file_path(source_hostname)
    if _is_piggybacked_host_abandoned(status_file_path, piggyback_file_path):
        valid_msg = _validity_period_message(file_age, validity_period)
        return PiggybackFileInfo(
            source=source_hostname,
            file_path=piggyback_file_path,
            valid=bool(valid_msg),
            message=(f"Piggyback data not updated by source '{source_hostname}'{valid_msg}"),
            status=validity_state if valid_msg else 0,
        )

    return PiggybackFileInfo(
        source=source_hostname,
        file_path=piggyback_file_path,
        valid=True,
        message=f"Successfully processed from source '{source_hostname}'",
        status=0,
    )


def _validity_period_message(
    file_age: float,
    validity_period: int | None,
) -> str:
    if validity_period is None or (time_left := validity_period - file_age) <= 0:
        return ""
    return f" (still valid, {_render_time(time_left)} left)"


def _is_piggybacked_host_abandoned(
    status_file_path: Path,
    piggyback_file_path: Path,
) -> bool:
    """Return True if the status file is missing or it is newer than the payload file

    It will return True if the payload file is "abandoned", i.e. the source host is
    still sending data, but no longer has data for this piggybacked ( = target) host.
    """
    try:
        # TODO use Path.stat() but be aware of:
        # On POSIX platforms Python reads atime and mtime at nanosecond resolution
        # but only writes them at microsecond resolution.
        # (We're using os.utime() in _store_status_file_of())
        return os.stat(str(status_file_path))[8] > os.stat(str(piggyback_file_path))[8]
    except FileNotFoundError:
        return True


def _remove_piggyback_file(piggyback_file_path: Path) -> bool:
    try:
        piggyback_file_path.unlink()
        return True
    except FileNotFoundError:
        return False


def remove_source_status_file(source_hostname: HostName) -> bool:
    """Remove the source_status_file of this piggyback host which will
    mark the piggyback data from this source as outdated."""
    source_status_path = _get_source_status_file_path(source_hostname)
    return _remove_piggyback_file(source_status_path)


def store_piggyback_raw_data(
    source_hostname: HostName,
    piggybacked_raw_data: Mapping[HostName, Sequence[bytes]],
) -> None:
    if not piggybacked_raw_data:
        # Cleanup the status file when no piggyback data was sent this turn.
        logger.debug("Received no piggyback data")
        remove_source_status_file(source_hostname)
        return

    piggyback_file_paths = []
    for piggybacked_hostname, lines in piggybacked_raw_data.items():
        piggyback_file_path = _get_piggybacked_file_path(source_hostname, piggybacked_hostname)
        logger.debug("Storing piggyback data for: %r", piggybacked_hostname)
        # Raw data is always stored as bytes. Later the content is
        # converted to unicode in abstact.py:_parse_info which respects
        # 'encoding' in section options.
        store.save_bytes_to_file(piggyback_file_path, b"%s\n" % b"\n".join(lines))
        piggyback_file_paths.append(piggyback_file_path)

    # Store the last contact with this piggyback source to be able to filter outdated data later
    # We use the mtime of this file later for comparison.
    # Only do this for hosts that sent piggyback data this turn.
    logger.debug("Received piggyback data for %d hosts", len(piggybacked_raw_data))
    status_file_path = _get_source_status_file_path(source_hostname)
    _store_status_file_of(status_file_path, piggyback_file_paths)


def _store_status_file_of(
    status_file_path: Path,
    piggyback_file_paths: Iterable[Path],
) -> None:
    store.makedirs(status_file_path.parent)

    # Cannot use store.save_bytes_to_file like:
    # 1. store.save_bytes_to_file(status_file_path, b"")
    # 2. set utime of piggybacked host files
    # Between 1. and 2.:
    # - the piggybacked host may check its files
    # - status file is newer (before utime of piggybacked host files is set)
    # => piggybacked host file is outdated
    with tempfile.NamedTemporaryFile(
        "wb", dir=str(status_file_path.parent), prefix=f".{status_file_path.name}.new", delete=False
    ) as tmp:
        tmp_path = tmp.name
        tmp.write(b"")

        tmp_stats = os.stat(tmp_path)
        status_file_times = (tmp_stats.st_atime, tmp_stats.st_mtime)
        for piggyback_file_path in piggyback_file_paths:
            try:
                # TODO use Path.stat() but be aware of:
                # On POSIX platforms Python reads atime and mtime at nanosecond resolution
                # but only writes them at microsecond resolution.
                # (We're using os.utime() in _store_status_file_of())
                os.utime(str(piggyback_file_path), status_file_times)
            except FileNotFoundError:
                continue
    os.rename(tmp_path, str(status_file_path))


#   .--folders/files-------------------------------------------------------.
#   |         __       _     _                  ____ _ _                   |
#   |        / _| ___ | | __| | ___ _ __ ___   / / _(_) | ___  ___         |
#   |       | |_ / _ \| |/ _` |/ _ \ '__/ __| / / |_| | |/ _ \/ __|        |
#   |       |  _| (_) | | (_| |  __/ |  \__ \/ /|  _| | |  __/\__ \        |
#   |       |_|  \___/|_|\__,_|\___|_|  |___/_/ |_| |_|_|\___||___/        |
#   |                                                                      |
#   '----------------------------------------------------------------------'


def get_source_hostnames(
    piggybacked_hostname: HostName | HostAddress | None = None,
) -> Sequence[HostName]:
    if piggybacked_hostname is None:
        return [
            HostName(source_host.name)
            for piggybacked_host_folder in _get_piggybacked_host_folders()
            for source_host in _files_in(piggybacked_host_folder)
        ]

    piggybacked_host_folder = cmk.utils.paths.piggyback_dir / Path(piggybacked_hostname)
    return [HostName(source_host.name) for source_host in _files_in(piggybacked_host_folder)]


def _get_piggybacked_host_folders() -> Sequence[Path]:
    return _files_in(cmk.utils.paths.piggyback_dir)


def _get_source_state_files() -> Sequence[Path]:
    return _files_in(cmk.utils.paths.piggyback_source_dir)


def _files_in(path: Path) -> Sequence[Path]:
    try:
        return [f for f in path.iterdir() if not f.name.startswith(".")]
    except FileNotFoundError:
        return []


def _get_source_status_file_path(source_hostname: HostName) -> Path:
    return cmk.utils.paths.piggyback_source_dir / str(source_hostname)


def _get_piggybacked_file_path(
    source_hostname: HostName,
    piggybacked_hostname: HostName | HostAddress,
) -> Path:
    return cmk.utils.paths.piggyback_dir / piggybacked_hostname / source_hostname


# .
#   .--clean up------------------------------------------------------------.
#   |                     _                                                |
#   |                 ___| | ___  __ _ _ __    _   _ _ __                  |
#   |                / __| |/ _ \/ _` | '_ \  | | | | '_ \                 |
#   |               | (__| |  __/ (_| | | | | | |_| | |_) |                |
#   |                \___|_|\___|\__,_|_| |_|  \__,_| .__/                 |
#   |                                               |_|                    |
#   '----------------------------------------------------------------------'


def cleanup_piggyback_files(time_settings: PiggybackTimeSettings) -> None:
    """This is a housekeeping job to clean up different old files from the
    piggyback directories.

    # Source status files and/or piggybacked data files are cleaned up/deleted
    # if and only if they have exceeded the maximum cache age configured in the
    # global settings or in the rule 'Piggybacked Host Files'."""
    logger.debug("Cleanup piggyback files.")
    logger.debug("Time settings: %r.", time_settings)

    piggybacked_hosts_settings = _get_piggybacked_hosts_settings(time_settings)

    _cleanup_old_source_status_files(piggybacked_hosts_settings)
    _cleanup_old_piggybacked_files(piggybacked_hosts_settings)


def _get_piggybacked_hosts_settings(
    time_settings: PiggybackTimeSettings,
) -> Sequence[tuple[Path, Sequence[Path], _TimeSettingsMap]]:
    piggybacked_hosts_settings = []
    for piggybacked_host_folder in _get_piggybacked_host_folders():
        source_hosts = _files_in(piggybacked_host_folder)
        time_settings_map = _TimeSettingsMap(
            [HostName(source_host.name) for source_host in source_hosts],
            HostName(piggybacked_host_folder.name),
            time_settings,
        )
        piggybacked_hosts_settings.append(
            (piggybacked_host_folder, source_hosts, time_settings_map)
        )
    return piggybacked_hosts_settings


def _cleanup_old_source_status_files(
    piggybacked_hosts_settings: Iterable[tuple[Path, Iterable[Path], _TimeSettingsMap]]
) -> None:
    """Remove source status files which exceed configured maximum cache age.
    There may be several 'Piggybacked Host Files' rules where the max age is configured.
    We simply use the greatest one per source."""

    max_cache_age_by_sources: dict[str, int] = {}
    for piggybacked_host_folder, source_hosts, time_settings in piggybacked_hosts_settings:
        for source_host in source_hosts:
            max_cache_age = time_settings.max_cache_age(
                HostName(source_host.name),
                HostName(piggybacked_host_folder.name),
            )

            max_cache_age_of_source = max_cache_age_by_sources.get(source_host.name)
            if max_cache_age_of_source is None or max_cache_age_of_source <= max_cache_age:
                max_cache_age_by_sources[source_host.name] = max_cache_age

    for source_state_file in _get_source_state_files():
        try:
            file_age = _time_since_last_modification(source_state_file)
        except FileNotFoundError:
            continue  # File has been removed, that's OK.

        # No entry -> no file
        max_cache_age_of_source = max_cache_age_by_sources.get(source_state_file.name)
        if max_cache_age_of_source is None:
            logger.debug("No piggyback data from source '%s'", source_state_file.name)
            continue

        if file_age > max_cache_age_of_source:
            logger.debug(
                "Piggyback source status file '%s' too old (age: %s, allowed: %s). Remove it.",
                source_state_file,
                _render_time(file_age),
                _render_time(max_cache_age_of_source),
            )
            _remove_piggyback_file(source_state_file)


def _cleanup_old_piggybacked_files(
    piggybacked_hosts_settings: Iterable[tuple[Path, Iterable[Path], _TimeSettingsMap]]
) -> None:
    """Remove piggybacked data files which exceed configured maximum cache age."""

    for piggybacked_host_folder, source_hosts, time_settings in piggybacked_hosts_settings:
        for piggybacked_host_source in source_hosts:
            src = HostName(piggybacked_host_source.name)
            dst = HostName(piggybacked_host_folder.name)

            try:
                file_age = _time_since_last_modification(piggybacked_host_source)
            except FileNotFoundError:
                continue

            max_cache_age = time_settings.max_cache_age(src, dst)
            validity_period = time_settings.validity_period(src, dst) or 0
            if file_age <= max_cache_age or file_age <= validity_period:
                # Do not remove files just because they're abandoned.
                # We don't use them anymore, but the DCD still needs to know about them for a while.
                continue

            logger.debug("Piggyback file '%s' is outdated. Remove it.", piggybacked_host_source)
            _remove_piggyback_file(piggybacked_host_source)

        # Remove empty backed host directory
        try:
            piggybacked_host_folder.rmdir()
        except OSError as e:
            if e.errno == errno.ENOTEMPTY:
                continue
            raise
        logger.debug(
            "Piggyback folder '%s' is empty. Removed it.",
            piggybacked_host_folder,
        )


def _time_since_last_modification(path: Path) -> float:
    """Return the time difference between the last modification and now.

    Raises:
        FileNotFoundError if `path` does not exist.

    """
    return time.time() - path.stat().st_mtime


def _render_time(value: float | int) -> str:
    """Format time difference seconds into human readable text

    >>> _render_time(184)
    '0:03:04'

    Unlikely in this context, but still acceptable:
    >>> _render_time(92635.3)
    '1 day, 1:43:55'
    """
    return str(datetime.timedelta(seconds=round(value)))
