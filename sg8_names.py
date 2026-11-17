"""Read officer and city data from the installed game's Unity assets."""

from collections import defaultdict
from dataclasses import dataclass, field
import json
import mmap
import os
import re
import struct
from pathlib import Path


def _read_vdf(path: Path, key: str) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    pattern = rf'^\s*"{re.escape(key)}"\s+"((?:\\.|[^"\\])*)"'
    return [
        re.sub(r'\\(["\\])', r"\1", value)
        for value in re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
    ]


def _steam_directories() -> set[Path]:
    directories = {
        Path(os.environ[name]) / "Steam"
        for name in ("ProgramFiles(x86)", "ProgramFiles")
        if name in os.environ
    }
    if os.name == "nt":
        import winreg

        for hive, key, value in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key) as entry:
                    path, _ = winreg.QueryValueEx(entry, value)
                directories.add(Path(path))
            except OSError:
                continue
    return directories


def find_game_assets() -> Path | None:
    """Find the resources.assets file in a Steam library."""
    libraries = _steam_directories()
    for steam in libraries.copy():
        libraries.update(
            Path(value)
            for value in _read_vdf(steam / "steamapps" / "libraryfolders.vdf", "path")
        )

    for library in sorted(libraries):
        for steamapps in (library / "steamapps", library):
            for name in _read_vdf(steamapps / "appmanifest_875210.acf", "installdir"):
                folder = Path(name)
                if folder.name != name or name in {".", ".."}:
                    continue
                assets = steamapps / "common" / folder / "SG8_Data" / "resources.assets"
                if assets.is_file():
                    return assets
    return None


def _text_asset(data: mmap.mmap, name: str) -> list | None:
    encoded = name.encode("ascii")
    marker = struct.pack("<I", len(encoded)) + encoded
    padded_length = (len(encoded) + 3) & ~3
    offset = data.find(marker)
    while offset != -1:
        size_offset = offset + 4 + padded_length
        payload_start = size_offset + 4
        if offset % 4 == 0 and payload_start < len(data):
            size, = struct.unpack_from("<I", data, size_offset)
            payload_end = payload_start + size
            if payload_end <= len(data) and data[payload_start] == ord("["):
                try:
                    table = json.loads(data[payload_start:payload_end])
                except ValueError:
                    pass
                else:
                    if isinstance(table, list) and len(table) >= 4:
                        return table
        offset = data.find(marker, offset + len(marker))
    return None


def _rows(data: mmap.mmap, name: str, *columns: str, optional: bool = False):
    table = _text_asset(data, name)
    if table is None and optional:
        return
    if table is None or not isinstance(table[3], list):
        raise ValueError(f"游戏资源缺少 {name} 数据表。")
    try:
        indexes = [table[3].index(column) for column in columns]
        for row in table[4:]:
            yield tuple(row[index] for index in indexes)
    except (ValueError, IndexError, TypeError) as error:
        raise ValueError(f"游戏资源中的 {name} 数据表格式不受支持。") from error


@dataclass(frozen=True)
class BattleSkill:
    slot: int
    max_id: int


def _battle_skills(data: mmap.mmap) -> dict[int, BattleSkill]:
    skills = {
        identity: (site, group, level, upgrade)
        for identity, site, group, level, upgrade in _rows(
            data, "Skill", "id", "skill_site", "skill_group", "skill_level", "skill_level_up", optional=True
        )
        if identity > 0 and 1 <= site <= 4
    }
    result = {}
    for identity, (site, group, level, upgrade) in skills.items():
        maximum = identity
        visited = {identity}
        while upgrade in skills:
            next_site, next_group, next_level, next_upgrade = skills[upgrade]
            if next_site != site or next_group != group or next_level < level:
                break
            if upgrade in visited:
                maximum = identity
                break
            # Summon skills can have equal numeric levels even across named ranks.
            maximum = upgrade
            visited.add(maximum)
            level, upgrade = next_level, next_upgrade
        result[identity] = BattleSkill(site - 1, maximum)
    return result


def _city_metadata(data: mmap.mmap, strings: dict[int, str]) -> tuple[
    dict[int, str], dict[tuple[int, int], dict[str, int]]
]:
    levels = list(_rows(
        data, "CityLevel", "id", "city_maxagriculture", "city_maxbusiness",
        "city_maxtechnology", "city_maxdef", "city_maxsoldier", optional=True
    ))
    columns = ("id", "name_id", "name_for_edit")
    if levels:
        columns += ("agriculture_scale", "business_scale", "technology_scale", "max_def")

    names = {}
    maxima = {}
    for city_id, name_id, fallback, *scales in _rows(data, "City", *columns):
        names[city_id] = strings.get(name_id) or fallback
        if not scales:
            continue
        agriculture_scale, commerce_scale, technology_scale, defense_scale = scales
        for level, agriculture, commerce, technology, defense, reserve in levels:
            maxima[(city_id, level)] = {
                "agriculture": agriculture * agriculture_scale // 100,
                "commerce": commerce * commerce_scale // 100,
                "technology": technology * technology_scale // 100,
                "wall_defense": defense * defense_scale // 100,
                "reserve_soldiers": reserve,
            }
    return names, maxima


@dataclass(frozen=True)
class SoldierTraining:
    max_level: int
    max_experience: int
    expansion_cities: frozenset[int] = frozenset()
    name: str = ""
    source_cities: tuple[tuple[int, int], ...] = ()


def _soldier_training(data: mmap.mmap, strings: dict[int, str]) -> dict[int, SoldierTraining]:
    members = {
        soldier_id for group_id, soldier_id in _rows(
            data, "Soldier_Group", "group_id", "soldier_id", optional=True
        ) if group_id == 7001
    }
    experience = dict(_rows(data, "Exp", "id", "soldier_levelup_exp", optional=True))
    soldiers = list(_rows(
        data, "Soldier", "id", "type", "max_lv", "version_tag", "register_soldier",
        "nameId", "editor_label", optional=True
    ))
    if not (members and experience and soldiers):
        return {}

    recruiting_cities = defaultdict(dict)
    for city_id, *soldier_ids in _rows(
        data, "City", "id", "lv1_soldier", "lv2_soldier", "lv3_soldier", "lv4_soldier", "lv5_soldier"
    ):
        for level, soldier_id in enumerate(soldier_ids, start=1):
            if soldier_id in members:
                recruiting_cities[soldier_id].setdefault(city_id, level)

    training = {}
    for soldier_id, kind, max_level, version, registered, name_id, fallback in soldiers:
        if soldier_id not in members or kind != 1 or version < 1 or max_level <= 0:
            continue
        if max_level not in experience:
            continue
        cities = recruiting_cities.get(soldier_id, {})
        if (registered != 0 or version != 1) and not cities:
            continue
        training[soldier_id] = SoldierTraining(
            max_level, experience[max_level], frozenset(cities) if version != 1 else frozenset(),
            name=strings.get(name_id) or fallback,
            source_cities=tuple(sorted(cities.items())),
        )
    return training


@dataclass(frozen=True)
class OfficerBaseStats:
    strength: int
    intelligence: int


@dataclass(frozen=True)
class GameCatalog:
    officer_names: dict[int, str]
    city_names: dict[int, str]
    famous_ids: frozenset[int]
    battle_skills: dict[int, BattleSkill] = field(default_factory=dict)
    legendary_equipment: frozenset[int] = frozenset()
    epic_equipment: frozenset[int] = frozenset()
    city_stat_maxima: dict[tuple[int, int], dict[str, int]] = field(default_factory=dict)
    soldier_training: dict[int, SoldierTraining] = field(default_factory=dict)
    officer_base_stats: dict[int, OfficerBaseStats] = field(default_factory=dict)
    officer_experience_levels: tuple[int, ...] = ()


def load_catalog(assets: Path) -> GameCatalog:
    with assets.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
        strings = dict(_rows(data, "Strings_Full.zh-cn", "id", "text"))
        strings.update(_rows(data, "Strings_Override.zh-cn", "id", "text", optional=True))

        officers = {}
        officer_base_stats = {}
        famous_ids = set()
        for name, optional in (("Leader", False), ("Leader.AB_3", True)):
            for identity, surname, given_name, famous, strength, intelligence in _rows(
                data, name, "id", "surnameId", "first_nameId", "famous",
                "strength", "intelligence", optional=optional
            ):
                full_name = (strings.get(surname, "") + strings.get(given_name, "")).strip()
                if full_name:
                    officers.setdefault(identity, full_name)
                if famous == 1:
                    famous_ids.add(identity)
                officer_base_stats.setdefault(identity, OfficerBaseStats(strength, intelligence))

        level_experience = dict(_rows(data, "Exp", "id", "leader_levelup_exp", optional=True))
        level_ids = sorted(level_experience)
        officer_experience_levels = ()
        if level_ids == list(range(1, len(level_ids) + 1)):
            thresholds = tuple(level_experience[level] for level in level_ids)
            if all(previous < current for previous, current in zip(thresholds, thresholds[1:])):
                officer_experience_levels = thresholds

        cities, city_stat_maxima = _city_metadata(data, strings)
        battle_skills = _battle_skills(data)
        soldier_training = _soldier_training(data, strings)
        # Internal beast loadouts have no inventory stack. Test rows use version zero.
        equipment_by_rarity = {4: set(), 5: set()}
        for identity, kind, rarity, stack, version in _rows(
            data, "Item", "id", "item_type", "rarity", "stack", "version_tag", optional=True
        ):
            if kind in (3, 4, 5) and rarity in equipment_by_rarity and stack > 0 and version > 0:
                equipment_by_rarity[rarity].add(identity)

    return GameCatalog(
        officers, cities, frozenset(famous_ids), battle_skills,
        legendary_equipment=frozenset(equipment_by_rarity[5]),
        epic_equipment=frozenset(equipment_by_rarity[4]),
        city_stat_maxima=city_stat_maxima,
        soldier_training=soldier_training,
        officer_base_stats=officer_base_stats,
        officer_experience_levels=officer_experience_levels,
    )


def load_names(assets: Path) -> tuple[dict[int, str], dict[int, str]]:
    """Return officer and city names by their configuration IDs."""
    catalog = load_catalog(assets)
    return catalog.officer_names, catalog.city_names
