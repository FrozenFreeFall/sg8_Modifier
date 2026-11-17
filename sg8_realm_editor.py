"""Stage city and faction edits in a Tk dialog."""

from collections.abc import Callable, Mapping
import tkinter as tk
from tkinter import ttk

from sg8_realm import CITY_STAT_LABELS, EQUIPMENT_STOCK, MAX_RESOURCES, RealmEdits, validate_city_stat


ALL_CITIES = '全部我方据点'


class RealmDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, cities: Mapping[str, int], edits: RealmEdits,
                 legendary_count: int, on_change: Callable[[], None], *, epic_count: int, soldier_count: int,
                 maxima_by_city: Mapping[int, Mapping[str, int]]):
        super().__init__(parent)
        self.title('据点与势力')
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.cities = dict(cities)
        self.edits = edits
        self.legendary_count = legendary_count
        self.epic_count = epic_count
        self.soldier_count = soldier_count
        self.maxima_by_city = maxima_by_city
        self.on_change = on_change
        self.scope = tk.StringVar(self, value=next(iter(self.cities), ''))
        self.status = tk.StringVar(self)
        self.summary = tk.StringVar(self)
        self.stat_inputs = {name: tk.StringVar(self) for name in CITY_STAT_LABELS}
        self.stat_ranges = {name: tk.StringVar(self) for name in CITY_STAT_LABELS}

        self._build_widgets()
        self.scope.trace_add('write', self._refresh_ranges)
        self._refresh_ranges()
        self._refresh_summary()
        self.bind('<Escape>', lambda _event: self.destroy())
        self.grab_set()

    def _build_widgets(self):
        body = ttk.Frame(self, padding=16)
        body.pack(fill='both', expand=True)

        city_group = ttk.LabelFrame(body, text='我方据点', padding=10)
        city_group.pack(fill='x')
        ttk.Label(city_group, text='适用据点').grid(row=0, column=0, sticky='w')
        options = [*self.cities, ALL_CITIES] if self.cities else []
        ttk.Combobox(city_group, textvariable=self.scope, values=options, width=25,
                     state='readonly' if options else 'disabled').grid(row=0, column=1, columnspan=3, sticky='w')
        for index, (name, label) in enumerate(CITY_STAT_LABELS.items()):
            row, column = divmod(index, 2)
            input_row = 2 * row + 1
            ttk.Label(city_group, text=label).grid(row=input_row, column=column * 2,
                                                   padx=(0, 8), pady=(7, 0), sticky='w')
            ttk.Entry(city_group, textvariable=self.stat_inputs[name], width=15).grid(
                row=input_row, column=column * 2 + 1, padx=(0, 12 if column == 0 else 0), pady=(7, 0), sticky='w')
            ttk.Label(city_group, textvariable=self.stat_ranges[name]).grid(
                row=input_row + 1, column=column * 2 + 1, sticky='w')
        ttk.Label(city_group, text='空白项沿用原值。全部据点采用共同范围。').grid(
            row=7, column=0, columnspan=4, pady=(10, 0), sticky='w')
        self.stage_stats_button = ttk.Button(city_group, text='加入据点修改', command=self.stage_stats,
                                             state='normal' if options else 'disabled')
        self.stage_stats_button.grid(row=8, column=0, columnspan=4, pady=(8, 0), sticky='w')

        faction_group = ttk.LabelFrame(body, text='我方势力', padding=10)
        faction_group.pack(fill='x', pady=(10, 0))
        self.resources_button = ttk.Button(faction_group, text='钱粮拉满', command=self.stage_resources)
        self.resources_button.grid(row=0, column=0, sticky='w')
        self.legendary_button = ttk.Button(faction_group, text=f'传说装备各{EQUIPMENT_STOCK}件',
                                           command=lambda: self.stage_equipment(epic=False))
        self.legendary_button.grid(row=0, column=1, padx=(8, 0), sticky='w')
        self.epic_button = ttk.Button(faction_group, text=f'史诗装备各{EQUIPMENT_STOCK}件',
                                      command=lambda: self.stage_equipment(epic=True))
        self.epic_button.grid(row=0, column=2, padx=(8, 0), sticky='w')
        self.soldiers_button = ttk.Button(faction_group, text='兵种训练拉满', command=self.stage_soldiers)
        self.soldiers_button.grid(row=1, column=0, columnspan=3, pady=(8, 0), sticky='w')

        ttk.Label(body, textvariable=self.summary, wraplength=450).pack(anchor='w', pady=(12, 0))
        ttk.Label(body, textvariable=self.status, wraplength=450).pack(anchor='w', pady=(5, 0))
        controls = ttk.Frame(body)
        controls.pack(fill='x', pady=(12, 0))
        self.clear_realm_button = ttk.Button(controls, text='清空据点与势力修改', command=self.clear_realm)
        self.clear_realm_button.pack(side='left')
        ttk.Button(controls, text='返回主窗口', command=self.destroy).pack(side='right')

    def _target_cities(self) -> list[int]:
        scope = self.scope.get()
        if scope == ALL_CITIES:
            return list(self.cities.values())
        return [self.cities[scope]] if scope in self.cities else []

    def _common_maxima(self, city_ids: list[int]) -> dict[str, int]:
        return {name: min(self.maxima_by_city[city_id][name] for city_id in city_ids)
                for name in CITY_STAT_LABELS} if city_ids else {}

    def _refresh_ranges(self, *_):
        maxima = self._common_maxima(self._target_cities())
        for name, display in self.stat_ranges.items():
            display.set(f'0–{maxima[name]:,}' if name in maxima else '')

    def stage_stats(self):
        city_ids = self._target_cities()
        if not city_ids:
            self.status.set('请选择要修改的我方据点。')
            return

        maxima = self._common_maxima(city_ids)
        stats = {}
        for name, entry in self.stat_inputs.items():
            raw = entry.get().strip()
            if not raw:
                continue
            try:
                value = int(raw)
            except ValueError:
                value = -1
            try:
                validate_city_stat(name, value, maxima)
            except ValueError as error:
                self.status.set(f'{error}。')
                return
            stats[name] = value
        if not stats:
            self.status.set('请至少填写一项据点数值。')
            return

        for city_id in city_ids:
            self.edits.city_stats.setdefault(city_id, {}).update(stats)
        self._staged(f'已加入 {len(city_ids)} 座据点的 {len(stats)} 项属性修改。')

    def stage_resources(self):
        self.edits.fill_resources = True
        self._staged(f'已加入钱粮修改，金钱和粮草各 {MAX_RESOURCES:,}。')

    def stage_equipment(self, *, epic: bool):
        rarity, count = ('史诗', self.epic_count) if epic else ('传说', self.legendary_count)
        if not count:
            self.status.set(f'请返回主窗口，点「选择游戏资源」读取{rarity}装备目录。')
            return
        if epic:
            self.edits.fill_epic = True
        else:
            self.edits.fill_legendary = True
        self._staged(f'已加入 {count} 种{rarity}装备，每种 {EQUIPMENT_STOCK} 件。')

    def stage_soldiers(self):
        if not self.soldier_count:
            self.status.set('请返回主窗口，点「选择游戏资源」读取兵种训练数据。')
            return
        self.edits.maximize_soldiers = True
        self._staged(f'已暂存 {self.soldier_count} 种兵种的满级经验。征募条件可在主窗口查看。')

    def clear_realm(self):
        self.edits.city_stats.clear()
        self.edits.fill_resources = False
        self.edits.fill_legendary = False
        self.edits.fill_epic = False
        self.edits.maximize_soldiers = False
        self.edits.soldier_assignments.clear()
        for entry in self.stat_inputs.values():
            entry.set('')
        self._staged('已清空待保存的据点与势力修改。')

    def _staged(self, message: str):
        self._refresh_summary()
        self.status.set(message)
        self.on_change()

    def _refresh_summary(self):
        pending = []
        if self.edits.city_stats:
            pending.append(f'{len(self.edits.city_stats)} 座据点属性')
        if self.edits.fill_resources:
            pending.append('钱粮拉满')
        if self.edits.fill_legendary:
            pending.append(f'传说装备各 {EQUIPMENT_STOCK} 件')
        if self.edits.fill_epic:
            pending.append(f'史诗装备各 {EQUIPMENT_STOCK} 件')
        if self.edits.maximize_soldiers:
            pending.append('兵种训练拉满')
        if self.edits.soldier_assignments:
            pending.append(f'{len(self.edits.soldier_assignments)} 名武将配兵')
        self.summary.set('待保存　' + '、'.join(pending) if pending else '暂无待保存的据点与势力修改')
