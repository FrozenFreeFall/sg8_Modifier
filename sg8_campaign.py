"""Read officer affiliation and apply recruitments to Kingdom Heroes 8 saves."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sg8_format import Float32, SaveFile, SaveFormatError, Token
from sg8_names import BattleSkill, OfficerBaseStats


PLAYER = "PlayerGameData"
COUNTRIES = "CountryGameData"
CITIES = "CityGameData"
OFFICERS = "TroopGameData"

CITY_TYPE = 5
OFFICER_LIMIT = 1_000_000_000
MAX_OFFICER_LEVEL = 99
# The installed game's Exp table gives the cumulative threshold for level 99.
MAX_OFFICER_EXP = 96_766_560
OFFICER_EXP_FIELD = 6
OFFICER_HEALTH_FIELD = 7
SPECIAL_WILD_CITY = 1_000_001
SPECIAL_WILD_OFFICERS = {2986, 2987}  # 魔吕布、神赵云
NEW_OFFICER_POST = 999
NEW_OFFICER_LOYALTY = 90
NEW_OFFICER_SOLDIER = 1121


@dataclass(frozen=True)
class City:
    id: int
    faction_id: int
    kind: int


@dataclass(frozen=True)
class Officer:
    id: int
    city_id: int
    faction_id: int
    loyalty: int
    status: str
    block_reason: str | None
    experience: int = 0
    health: int = 0


@dataclass(frozen=True)
class _CityRecord:
    city: City
    start: int
    end: int
    resident_count_at: int | None
    building_flags: tuple[int, ...] = ()
    job_starts: tuple[int, ...] = ()


@dataclass(frozen=True)
class _OfficerRecord:
    id: int
    start: int
    soldier_state_at: int
    learned_skills: range
    equipped_skills: range


@dataclass(frozen=True)
class _CountryRecord:
    ruler_id: int
    offices: tuple[int, ...]
    soldier_states: dict[int, int]
    start: int
    government_at: int


def _require_int(section: list[Token], index: int, label: str) -> int:
    if not 0 <= index < len(section) or type(section[index]) is not int:
        raise SaveFormatError(f"{label}字段缺失或格式不符")
    return section[index]


def _count(section: list[Token], index: int, label: str) -> int:
    value = _require_int(section, index, label)
    if not 0 <= value <= len(section) - index - 1:
        raise SaveFormatError(f"{label}数量无效")
    return value


def _section(save: SaveFile, name: str, versions: set[int]) -> list[Token]:
    values = save.sections.get(name, [])
    version = _require_int(values, 1, name)
    if version not in versions:
        raise SaveFormatError(f"暂未支持 {name} 版本 {version}")
    return values


def _country_records(values: list[Token]) -> dict[int, _CountryRecord]:
    countries = {}
    position = 3
    for _ in range(_count(values, 2, "势力")):
        country = _require_int(values, position, "势力编号")
        ruler = _require_int(values, position + 3, "君主编号")
        if country in countries or country <= 0 or type(values[position + 2]) is not bytes:
            raise SaveFormatError("势力记录格式不符")
        stock_entries = _count(values, position + 6, "势力国库")
        government_at = position + 7 + 2 * stock_entries
        army_at = government_at + 12
        army_entries = _count(values, army_at, "势力兵种")
        soldiers = {}
        for index in range(army_at + 1, army_at + 1 + 2 * army_entries, 2):
            soldier = _require_int(values, index, "兵种编号")
            soldiers[soldier] = _require_int(values, index + 1, "兵种状态")
        offices = tuple(government_at + offset for offset in (3, 5, 7))
        countries[country] = _CountryRecord(ruler, offices, soldiers, position, government_at)
        position = army_at + 2 + 2 * army_entries
        _require_int(values, position - 1, "势力记录末尾")
    return countries


def _player_faction(player: list[Token], values: list[Token], countries: dict[int, _CountryRecord]) -> int:
    ruler = _require_int(player, 11, "自建君主编号")
    if ruler > 0:
        matches = [country for country, record in countries.items() if record.ruler_id == ruler]
        if len(matches) == 1:
            return matches[0]
    elif ruler == 0 and countries:
        # Historical campaigns leave the custom ruler unset. The country section
        # lists the player factions after the country records and before diplomacy.
        last_country = next(reversed(countries.values()))
        army_at = last_country.government_at + 12
        footer_at = army_at + 2 + 2 * _count(values, army_at, "势力兵种")
        footer = values[footer_at:footer_at + 3]
        if len(footer) == 3 and all(type(value) is int for value in footer):
            count, country, relations = footer
            if (count == 1 and country in countries and countries[country].ruler_id > 0
                    and 0 <= relations <= (len(values) - footer_at - 3) // 4):
                return country
    raise SaveFormatError("无法确定当前玩家的势力")


def _city_records(values: list[Token]) -> dict[int, _CityRecord]:
    starts = []
    # All city types serialize six integer fields immediately before their XYZ coordinates.
    for index in range(3, len(values) - 9):
        if (
            isinstance(values[index + 6], Float32)
            and isinstance(values[index + 7], Float32)
            and isinstance(values[index + 8], Float32)
            and all(type(value) is int for value in values[index:index + 6])
            and 0 < values[index] < 1_000_000
            and type(values[index + 9]) is int
        ):
            starts.append(index)
    expected = _count(values, 2, "据点")
    if not starts or starts[0] != 3 or len(starts) != expected:
        raise SaveFormatError("无法完整识别据点记录")

    records = {}
    for start, end in zip(starts, starts[1:] + [len(values)]):
        city_id, template_id = values[start:start + 2]
        kind = values[start + 4]
        faction_id = values[start + 9]
        if city_id <= 0 or city_id in records:
            raise SaveFormatError("据点编号无效或重复")
        city = City(city_id, faction_id, kind)
        if kind != CITY_TYPE:
            records[city_id] = _CityRecord(city, start, end, None)
        else:
            if city_id != template_id:
                raise SaveFormatError("城池编号与模板编号不符")
            state_at = next(
                (index for index in range(end - 1, start + 9, -1) if type(values[index]) is bool),
                None,
            )
            if state_at is None or state_at + 2 >= end:
                raise SaveFormatError(f"城池 {city_id} 的驻城数据缺失")
            count_at = state_at + 2
            count = _count(values, count_at, "驻城武将")
            roster_end = count_at + 1 + count
            if roster_end + 2 > end or not all(
                type(value) is int for value in values[state_at + 1:roster_end]
            ):
                raise SaveFormatError(f"城池 {city_id} 的驻城武将格式不符")
            buildings = _count(values, start + 21, "城池建筑")
            first_job = start + 32 + 3 * buildings
            jobs_end = first_job + 6 * (3 + buildings)
            if jobs_end > state_at or any(
                values[index] not in (2, 101, 102, 103)
                or not all(type(value) is int for value in values[index:index + 6])
                for index in range(first_job, jobs_end, 6)
            ):
                raise SaveFormatError(f"城池 {city_id} 的任职资料格式不符")
            if not all(
                type(value) is int for value in values[start + 22:start + 22 + 3 * buildings]
            ):
                raise SaveFormatError(f"城池 {city_id} 的建筑资料格式不符")
            records[city_id] = _CityRecord(
                city, start, end, count_at,
                tuple(range(start + 24, start + 22 + 3 * buildings, 3)),
                tuple(range(first_job, jobs_end, 6)),
            )
    return records


def _officer_records(values: list[Token]) -> tuple[list[_OfficerRecord], int]:
    records = []
    seen = set()
    position = 3
    count = _count(values, 2, "部队")
    for _ in range(count):
        troop_id = _require_int(values, position, "部队编号")
        template_id = _require_int(values, position + 1, "部队模板编号")
        limit = _require_int(values, position + 3, "部队上限")
        if troop_id <= 0 or troop_id in seen:
            raise SaveFormatError("部队编号无效或重复")
        seen.add(troop_id)

        title_count = _count(values, position + 12, "武将称号")
        learned_at = position + 17 + title_count
        learned_count = _count(values, learned_at, "已学战技")
        passive_at = learned_at + 1 + learned_count
        passive_count = _count(values, passive_at, "武将特技")
        equipped_at = passive_at + 1 + passive_count
        equipped_count = _count(values, equipped_at, "已装备战技")
        soldiers_at = equipped_at + 1 + equipped_count
        relations = _count(values, soldiers_at + 5, "部队相关势力")
        end = soldiers_at + 9 + 2 * relations
        _require_int(values, end - 1, "部队记录末尾")

        if troop_id == template_id and limit == OFFICER_LIMIT:
            loyalty = _require_int(values, position + 2, "忠诚")
            if not 0 <= loyalty <= 100:
                raise SaveFormatError("武将忠诚度超出范围")
            records.append(_OfficerRecord(
                troop_id, position, soldiers_at + 1,
                range(learned_at + 1, passive_at), range(equipped_at + 1, soldiers_at),
            ))
        position = end
    if not records:
        raise SaveFormatError("存档中未找到可识别的武将记录")
    return records, position


def _live_references(save: SaveFile, troop_tail: list[Token]) -> set[int]:
    values = troop_tail.copy()
    for name in ("LegionGameData", "LeaderActionGameData"):
        values.extend(save.sections.get(name, []))
    player = save.sections[PLAYER]
    for index, token in enumerate(player):
        if type(token) is bytes and token.endswith(b"Job"):
            values.extend(player[index + 1:index + 7])
    return {value for value in values if type(value) is int}


def _custom_base_stats(save: SaveFile) -> dict[int, OfficerBaseStats]:
    values = save.sections.get("CustomLeaderGameData", [])
    # Version 1 records with four battle slots and two passive slots occupy 41 tokens.
    record_size = 41
    if len(values) < 3 or values[1] != 1 or type(values[2]) is not int:
        return {}
    if len(values) != 3 + record_size * values[2]:
        return {}

    result = {}
    for start in range(3, len(values), record_size):
        officer_id, template_id = values[start:start + 2]
        stats = values[start + 21:start + 23]
        if (type(officer_id) is int and officer_id == template_id and values[start + 23] == 4
                and values[start + 30] == 2 and all(type(value) is int for value in stats)):
            result[officer_id] = OfficerBaseStats(*stats)
    return result


class Campaign:
    def __init__(self, save: SaveFile):
        self._save = save
        self.custom_officer_base_stats = _custom_base_stats(save)
        player = _section(save, PLAYER, {4})
        country_tokens = _section(save, COUNTRIES, {2})
        countries = _country_records(country_tokens)
        self.player_faction_id = _player_faction(player, country_tokens, countries)
        ruler = countries[self.player_faction_id].ruler_id
        self.player_ruler_id = ruler
        self.player_soldier_experience = countries[self.player_faction_id].soldier_states
        self._recruit_soldier_state = self.player_soldier_experience.get(NEW_OFFICER_SOLDIER)

        city_tokens = _section(save, CITIES, {1})
        self._city_records = _city_records(city_tokens)
        self.cities = {city_id: record.city for city_id, record in self._city_records.items()}
        self.owned_cities = [
            city for city in self.cities.values()
            if city.faction_id == self.player_faction_id and city.kind == CITY_TYPE
        ]

        troop_tokens = _section(save, OFFICERS, {2})
        records, troop_end = _officer_records(troop_tokens)
        self._records_by_id = {record.id: record for record in records}
        if ruler not in self._records_by_id:
            raise SaveFormatError("玩家君主记录缺失")
        live = _live_references(save, troop_tokens[troop_end:])
        event_references = SPECIAL_WILD_OFFICERS.intersection(save.sections.get("EventGameData", []))

        mentions = defaultdict(list)
        known_city_links = {}
        for city_id, record in self._city_records.items():
            at = record.resident_count_at
            if at is not None:
                known_city_links[at - 1] = (city_id, "governor", at - 1)
                for index in range(at + 1, at + 1 + city_tokens[at]):
                    known_city_links[index] = (city_id, "resident", at)
                for index in record.job_starts:
                    known_city_links[index + 1] = (city_id, "job", index)
            for index in range(record.start + 10, record.end):
                value = city_tokens[index]
                if type(value) is int and value in self._records_by_id:
                    mentions[value].append(index)

        country_links = defaultdict(list)
        for country_id, record in countries.items():
            for index in record.offices:
                officer_id = _require_int(country_tokens, index, "势力任职")
                if officer_id:
                    country_links[officer_id].append((country_id, index))
        self._city_links = {}
        self._country_links = {}
        rulers = {country.ruler_id for country in countries.values()}
        self.officers = {}
        for record in records:
            start = record.start
            officer_id = record.id
            loyalty = _require_int(troop_tokens, start + 2, "忠诚")
            city_id = _require_int(troop_tokens, start + 9, "武将所在地")
            post = _require_int(troop_tokens, start + 11, "武将官职")
            city = self.cities.get(city_id)
            special_wild = officer_id in SPECIAL_WILD_OFFICERS and city_id == SPECIAL_WILD_CITY and post == 0
            faction_id = city.faction_id if city is not None and post != 0 else 0
            status, blocked = "在野", None
            if officer_id in rulers:
                status, blocked = "君主", "君主无法转入"
            elif faction_id == self.player_faction_id:
                status, blocked = "己方", "该武将已在己方"
            elif any(
                _require_int(troop_tokens, start + offset, "武将状态") != 0
                for offset in (8, 10)
            ):
                status, blocked = "特殊状态", "武将状态需先在游戏内处理"
            elif (city is None or city.kind != CITY_TYPE) and not special_wild:
                status, blocked = "出城或未登场", "武将当前不在常规城池"
            elif post and (faction_id not in countries or countries[faction_id].ruler_id == 0):
                status, blocked = "特殊势力", "无法核实原势力"
            elif not 0 <= post <= 1000:
                status, blocked = "特殊官职", "无法核实原官职"
            elif officer_id in live:
                status, blocked = "行动中", "武将仍被军团或任务引用"
            elif special_wild and officer_id in event_references:
                status, blocked = "事件中", "武将仍被剧本事件引用"
            else:
                links = [
                    (index, known_city_links.get(index)) for index in mentions[officer_id]
                ]
                government_offices = country_links[officer_id]
                status = "特殊在野" if special_wild else "驻城" if post else "在野"
                if any(link is None or link[0] != city_id for _, link in links):
                    blocked = "武将仍有其他据点安排"
                elif any(country_id != faction_id for country_id, _ in government_offices):
                    blocked = "武将仍有其他势力任职"
                elif not post and links:
                    blocked = "在野武将仍有据点安排"
                else:
                    city_links = [(index, link[1], link[2]) for index, link in links]
                    if any(kind == "governor" for _, kind, _ in city_links):
                        status = "太守"
                    self._city_links[officer_id] = city_links
                    self._country_links[officer_id] = [index for _, index in government_offices]
            experience = _require_int(troop_tokens, start + OFFICER_EXP_FIELD, "武将经验")
            health = _require_int(troop_tokens, start + OFFICER_HEALTH_FIELD, "武将当前体力")
            self.officers[officer_id] = Officer(officer_id, city_id, faction_id, loyalty, status, blocked,
                                                experience, health)

    @classmethod
    def parse(cls, data: bytes) -> Campaign:
        return cls(SaveFile.parse(data))

    def maximize_officers(self, officer_ids: Iterable[int], battle_skills: Mapping[int, BattleSkill]) -> bytes:
        chosen = sorted(set(officer_ids))
        if not chosen:
            raise SaveFormatError("请至少选择一名武将")
        if not battle_skills:
            raise SaveFormatError("请选择含有战技资料的游戏资源文件")

        sections = self._save.sections.copy()
        troops = sections[OFFICERS].copy()
        for officer_id in chosen:
            record = self._records_by_id.get(officer_id)
            if record is None:
                raise SaveFormatError(f"找不到武将 {officer_id}")

            learned_by_slot = {}
            try:
                for index in record.learned_skills:
                    skill_id = _require_int(troops, index, "已学战技")
                    if skill_id > 0:
                        skill = battle_skills[skill_id]
                        troops[index] = skill.max_id
                        learned_by_slot.setdefault(skill.slot, skill.max_id)

                # The officer is now eligible to equip skills in the higher-level slots.
                for slot, index in enumerate(record.equipped_skills):
                    skill_id = _require_int(troops, index, "已装备战技")
                    if skill_id > 0:
                        troops[index] = battle_skills[skill_id].max_id
                    elif skill_id == 0 and slot in learned_by_slot:
                        troops[index] = learned_by_slot[slot]
            except KeyError as error:
                raise SaveFormatError(f"武将 {officer_id} 的战技 {error.args[0]} 未在游戏资源中找到") from error

            troops[record.start + OFFICER_EXP_FIELD] = MAX_OFFICER_EXP

        sections[OFFICERS] = troops
        return SaveFile(sections).to_bytes()

    def transfer(self, officer_ids: Iterable[int], city_id: int) -> bytes:
        city = self.cities.get(city_id)
        if city not in self.owned_cities:
            raise SaveFormatError("请选择己方城池作为目的地")
        if self._recruit_soldier_state is None:
            raise SaveFormatError("玩家势力的基础兵种资料缺失")

        chosen = set(officer_ids)
        if not chosen:
            raise SaveFormatError("请至少选择一名武将")
        for officer_id in sorted(chosen):
            officer = self.officers.get(officer_id)
            if officer is None:
                raise SaveFormatError(f"找不到武将 {officer_id}")
            if officer.block_reason is not None:
                raise SaveFormatError(f"武将 {officer_id}：{officer.block_reason}")

        source_cities = {
            self.officers[officer_id].city_id for officer_id in chosen if self.officers[officer_id].faction_id
        }
        occupied_cities = {
            officer.city_id for officer in self.officers.values()
            if officer.id not in chosen and officer.faction_id
        }
        vacated_cities = source_cities - occupied_cities
        remaining_factions = {
            city.faction_id for city in self.cities.values()
            if city.kind == CITY_TYPE and city.id not in vacated_cities
        }
        for source_id in vacated_cities:
            if self.cities[source_id].faction_id not in remaining_factions:
                raise SaveFormatError(f"本批次将移除势力最后一座城池 {source_id}")

        sections = self._save.sections.copy()
        troops = sections[OFFICERS].copy()
        cities = sections[CITIES].copy()
        countries = sections[COUNTRIES].copy()
        removals = []
        for officer_id in chosen:
            record = self._records_by_id[officer_id]
            troops[record.start + 2] = NEW_OFFICER_LOYALTY
            troops[record.start + OFFICER_EXP_FIELD] = MAX_OFFICER_EXP
            troops[record.start + 9] = city_id
            troops[record.start + 11] = NEW_OFFICER_POST
            state_at = record.soldier_state_at
            troops[state_at - 1:state_at + 2] = [NEW_OFFICER_SOLDIER, self._recruit_soldier_state, 0]
            for index in self._country_links[officer_id]:
                countries[index] = 0
            for index, kind, slot_at in self._city_links[officer_id]:
                if kind == "governor":
                    cities[index] = 0
                elif kind == "job":
                    cities[slot_at + 1:slot_at + 6] = [0] * 5
                else:
                    cities[slot_at] -= 1
                    removals.append(index)

        for source_id in vacated_cities:
            source = self._city_records[source_id]
            cities[source.start + 9] = 0
            for index in source.building_flags:
                cities[index] = 0
            for index in source.job_starts:
                if cities[index + 1]:
                    cities[index + 1:index + 6] = [0] * 5
        # Later indices must be removed first to retain the earlier offsets.
        for item_at in sorted(removals, reverse=True):
            del cities[item_at]
        sections[OFFICERS] = troops
        sections[CITIES] = cities
        sections[COUNTRIES] = countries
        return SaveFile(sections).to_bytes()
