"""Edit city fields, resources and a faction's stockpile."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from sg8_campaign import CITIES, CITY_TYPE, COUNTRIES, OFFICERS, Campaign, _country_records, _require_int
from sg8_format import SaveFile, SaveFormatError, Token


MAX_RESOURCES = 999_999_999
EQUIPMENT_STOCK = 99

# Use the largest configured bounds when the local game's city tables are unavailable.
_CITY_STATS = {
    'agriculture': ('农业值', 11, 1800),
    'commerce': ('商业值', 12, 2160),
    'technology': ('技术值', 13, 1800),
    'resentment': ('民怨', 14, 100),
    'wall_defense': ('城防', 19, 12500),
    'reserve_soldiers': ('预备兵力', 18, 45000),
}
CITY_STAT_LABELS = {name: label for name, (label, _, _) in _CITY_STATS.items()}
CityLimits = Mapping[tuple[int, int], Mapping[str, int]]


@dataclass
class RealmEdits:
    city_stats: dict[int, dict[str, int]] = field(default_factory=dict)
    fill_resources: bool = False
    fill_legendary: bool = False
    fill_epic: bool = False
    maximize_soldiers: bool = False
    soldier_assignments: dict[int, int] = field(default_factory=dict)

    @property
    def pending(self) -> bool:
        return (self.fill_resources or self.fill_legendary or self.fill_epic
                or self.maximize_soldiers or bool(self.soldier_assignments) or any(self.city_stats.values()))


def city_stat_maxima(campaign: Campaign, city_id: int, city_limits: CityLimits | None) -> dict[str, int]:
    record = campaign._city_records.get(city_id)
    if record is None or record.city.faction_id != campaign.player_faction_id or record.city.kind != CITY_TYPE:
        raise SaveFormatError(f'据点 {city_id} 不是可修改的我方据点')
    level = _require_int(campaign._save.sections[CITIES], record.start + 10, '据点规模')
    configured = city_limits.get((city_id, level), {}) if city_limits else {}
    return {name: configured.get(name, default) if name != 'resentment' else default
            for name, (_, _, default) in _CITY_STATS.items()}


def validate_city_stat(name: str, value: int, maxima: Mapping[str, int]) -> None:
    if name not in _CITY_STATS:
        raise SaveFormatError(f'未知的据点属性 {name}')
    maximum = maxima[name]
    if type(value) is not int or not 0 <= value <= maximum:
        raise SaveFormatError(f'{CITY_STAT_LABELS[name]}须为 0 至 {maximum:,} 的整数')


def _edit_cities(campaign: Campaign, values: list[Token], city_stats: dict[int, dict[str, int]],
                 city_limits: CityLimits | None) -> None:
    for city_id, stats in city_stats.items():
        maxima = city_stat_maxima(campaign, city_id, city_limits)
        record = campaign._city_records[city_id]
        for name, value in stats.items():
            validate_city_stat(name, value, maxima)
            label, offset, _ = _CITY_STATS[name]
            index = record.start + offset
            _require_int(values, index, label)
            # The save encodes public order rather than the displayed resentment.
            values[index] = 100 - value if name == 'resentment' else value


def _edit_soldier_experience(values: list[Token], count_at: int, experience: Mapping[int, int]) -> None:
    count = _require_int(values, count_at, '势力兵种数量')
    end = count_at + 1 + 2 * count
    missing = set(experience)
    for index in range(count_at + 1, end, 2):
        soldier_id = _require_int(values, index, '兵种编号')
        if soldier_id in experience:
            values[index + 1] = experience[soldier_id]
            missing.discard(soldier_id)
    values[count_at] = count + len(missing)
    values[end:end] = [token for soldier_id in sorted(missing) for token in (soldier_id, experience[soldier_id])]


def can_assign_soldiers(campaign: Campaign, officer_id: int) -> bool:
    officer = campaign.officers.get(officer_id)
    return officer is not None and officer.faction_id == campaign.player_faction_id


def _edit_officer_soldiers(campaign: Campaign, values: list[Token], experience: Mapping[int, int],
                           assignments: Mapping[int, int]) -> None:
    for officer in campaign.officers.values():
        if officer.faction_id == campaign.player_faction_id:
            state_at = campaign._records_by_id[officer.id].soldier_state_at
            soldier_id = _require_int(values, state_at - 1, '武将兵种编号')
            if officer.id in assignments:
                soldier_id = assignments[officer.id]
                values[state_at - 1] = soldier_id
            if soldier_id in experience:
                _require_int(values, state_at, '武将兵种经验')
                values[state_at] = experience[soldier_id]


def _edit_country(campaign: Campaign, values: list[Token], edits: RealmEdits,
                  legendary_equipment: Iterable[int], epic_equipment: Iterable[int],
                  experience: Mapping[int, int] | None) -> None:
    record = _country_records(values)[campaign.player_faction_id]
    if experience:
        _edit_soldier_experience(values, record.government_at + 12, experience)
    if edits.fill_resources:
        for offset, label in ((1, '金钱'), (2, '粮草')):
            index = record.government_at + offset
            _require_int(values, index, label)
            values[index] = MAX_RESOURCES

    if edits.fill_legendary or edits.fill_epic:
        missing = set()
        for enabled, equipment, rarity in ((edits.fill_legendary, legendary_equipment, '传说'),
                                           (edits.fill_epic, epic_equipment, '史诗')):
            if enabled:
                items = set(equipment)
                if not items:
                    raise SaveFormatError(f'请选择含{rarity}装备数据的游戏资源')
                missing.update(items)
        if any(type(item_id) is not int or item_id <= 0 for item_id in missing):
            raise SaveFormatError('装备编号无效')

        for index in range(record.start + 7, record.government_at, 2):
            item_id = _require_int(values, index, '国库装备编号')
            if item_id in missing:
                _require_int(values, index + 1, '国库装备数量')
                values[index + 1] = EQUIPMENT_STOCK
                missing.remove(item_id)

        additions = [token for item_id in sorted(missing) for token in (item_id, EQUIPMENT_STOCK)]
        count_at = record.start + 6
        values[count_at] = _require_int(values, count_at, '国库物品种数') + len(missing)
        values[record.government_at:record.government_at] = additions


def edit_realm(campaign: Campaign, edits: RealmEdits, legendary_equipment: Iterable[int], *,
               city_limits: CityLimits | None = None, epic_equipment: Iterable[int] = (),
               soldier_experience: Mapping[int, int] | None = None) -> bytes:
    experience = None
    if edits.maximize_soldiers or edits.soldier_assignments:
        if not soldier_experience:
            raise SaveFormatError('请选择含兵种资料的游戏资源')
        for officer_id, soldier_id in edits.soldier_assignments.items():
            if not can_assign_soldiers(campaign, officer_id):
                raise SaveFormatError(f'武将 {officer_id} 必须属于我方才能配置兵种')
            if type(soldier_id) is not int or soldier_id not in soldier_experience:
                raise SaveFormatError(f'兵种 {soldier_id} 不在当前剧本的兵种资料中')
        experience = (soldier_experience if edits.maximize_soldiers else
                      {soldier_id: soldier_experience[soldier_id] for soldier_id in edits.soldier_assignments.values()})
        if any(type(soldier_id) is not int or type(value) is not int
               or not 0 < soldier_id <= 2_147_483_647 or not 0 <= value <= 2_147_483_647
               for soldier_id, value in experience.items()):
            raise SaveFormatError('兵种编号或经验值无效')
        experience = {soldier_id: max(value, campaign.player_soldier_experience.get(soldier_id, 0))
                      for soldier_id, value in experience.items()}
    sections = campaign._save.sections.copy()
    if edits.city_stats:
        cities = sections[CITIES].copy()
        _edit_cities(campaign, cities, edits.city_stats, city_limits)
        sections[CITIES] = cities
    if edits.fill_resources or edits.fill_legendary or edits.fill_epic or experience:
        country = sections[COUNTRIES].copy()
        _edit_country(campaign, country, edits, legendary_equipment, epic_equipment, experience)
        sections[COUNTRIES] = country
    if experience:
        troops = sections[OFFICERS].copy()
        _edit_officer_soldiers(campaign, troops, experience, edits.soldier_assignments)
        sections[OFFICERS] = troops
    return SaveFile(sections).to_bytes()
