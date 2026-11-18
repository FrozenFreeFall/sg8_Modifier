from bisect import bisect_right
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from sg8_campaign import CITY_TYPE, Campaign, MAX_OFFICER_LEVEL
from sg8_files import SaveSnapshot, find_save_directory, game_is_running
from sg8_names import GameCatalog, find_game_assets, load_catalog
from sg8_realm import (EQUIPMENT_STOCK, MAX_OFFICER_SOLDIERS, MAX_RESOURCES, RealmEdits,
                       can_assign_soldiers, city_stat_maxima, edit_realm)
from sg8_realm_editor import RealmDialog
from sg8_soldier_editor import SoldierDialog


class Editor(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=16)
        self.root = root
        self.campaign = None
        self.snapshot = None
        self.selected = set()
        self.upgrade_ids = set()
        self.realm_edits = RealmEdits()
        self.catalog = GameCatalog({}, {}, frozenset())
        self.cities = {}
        self.search = tk.StringVar()
        self.destination = tk.StringVar()
        self.eligible_only = tk.BooleanVar(value=True)
        self.own_faction_only = tk.BooleanVar(value=False)
        self.sort_column: str | None = None
        self.sort_descending = False
        self.auto_maximize = tk.BooleanVar(value=True)
        self.source_label = tk.StringVar(value='请选择战役存档')
        self.status = tk.StringVar(value='可按姓名或武将编号搜索。')
        self.selection_label = tk.StringVar()

        self.pack(fill='both', expand=True)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self._build_widgets()
        self.search.trace_add('write', self.refresh)
        self.refresh()

        try:
            assets = find_game_assets()
            if assets:
                self.catalog = load_catalog(assets)
        except (OSError, ValueError) as exc:
            self.status.set(f'读取游戏名称失败。可用武将编号搜索，也可手动选择游戏资源。{exc}')

    def _build_widgets(self):
        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky='ew')
        ttk.Button(toolbar, text='打开存档…', command=self.open_save).pack(side='left')
        ttk.Button(toolbar, text='选择游戏资源…', command=self.choose_assets).pack(side='left', padx=8)
        self.realm_button = ttk.Button(toolbar, text='据点与势力…', command=self.open_realm)
        self.realm_button.pack(side='left')
        self.soldier_button = ttk.Button(toolbar, text='兵种来源与配兵…', command=self.open_soldiers)
        self.soldier_button.pack(side='left', padx=(8, 0))
        ttk.Checkbutton(toolbar, text='转入时拉满', variable=self.auto_maximize,
                        command=self.refresh).pack(side='left', padx=8)
        ttk.Label(toolbar, textvariable=self.selection_label).pack(side='right')
        ttk.Label(self, textvariable=self.source_label).grid(row=1, column=0, sticky='w', pady=(10, 12))

        filters = ttk.Frame(self)
        filters.grid(row=2, column=0, sticky='ew', pady=(0, 8))
        ttk.Label(filters, text='搜索武将').pack(side='left')
        ttk.Entry(filters, textvariable=self.search, width=18).pack(side='left', padx=(6, 12))
        self.transferable_filter = ttk.Checkbutton(
            filters, text='仅显示可转入', variable=self.eligible_only,
            command=lambda: self._change_filter(self.eligible_only, self.own_faction_only))
        self.transferable_filter.pack(side='left')
        self.own_faction_filter = ttk.Checkbutton(
            filters, text='仅显示我方势力', variable=self.own_faction_only,
            command=lambda: self._change_filter(self.own_faction_only, self.eligible_only))
        self.own_faction_filter.pack(side='left', padx=(8, 0))
        ttk.Label(filters, text='目标据点').pack(side='left', padx=(14, 6))
        self.city_box = ttk.Combobox(filters, textvariable=self.destination, state='readonly', width=18)
        self.city_box.pack(side='left')
        self.invert_selection_button = ttk.Button(filters, text='反选', command=self.invert_selection)
        self.invert_selection_button.pack(side='right')
        self.select_all_button = ttk.Button(filters, text='全选', command=self.select_all)
        self.select_all_button.pack(side='right', padx=6)

        table = ttk.Frame(self)
        table.grid(row=3, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        columns = ('marked', 'upgrade', 'name', 'famous', 'level', 'health', 'strength', 'intelligence',
                   'id', 'faction', 'city', 'loyalty', 'state')
        self.tree = ttk.Treeview(table, columns=columns, show='headings', selectmode='extended')
        titles = ('转入', '拉满', '武将', '名将', '等级', '当前体力', '基础武力', '基础智力',
                  '编号', '当前势力', '所在据点', '忠诚', '状态')
        widths = (54, 54, 135, 54, 50, 78, 78, 78, 70, 100, 100, 56, 210)
        for key, title, width in zip(columns, titles, widths):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=48, stretch=key in ('name', 'state'))
        self.sort_headers = {column: self.tree.heading(column, 'text')
                             for column in ('famous', 'health', 'strength', 'intelligence')}
        for column in self.sort_headers:
            self.tree.heading(column, command=lambda column=column: self.sort_officers(column))
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.tree.bind('<Double-1>', self.toggle_row)
        self.tree.bind('<<TreeviewSelect>>', self._refresh_upgrade_button)
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        horizontal_scrollbar = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal_scrollbar.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal_scrollbar.set)

        controls = ttk.Frame(self)
        controls.grid(row=4, column=0, sticky='ew', pady=(10, 0))
        ttk.Button(controls, text='加入所选', command=self.mark_selected).pack(side='left')
        self.famous_button = ttk.Button(controls, text='勾选可转入名将', command=self.mark_famous)
        self.famous_button.pack(side='left', padx=(6, 0))
        self.upgrade_button = ttk.Button(controls, text='武将拉满', command=self.mark_upgrade)
        self.upgrade_button.pack(side='left', padx=(6, 0))
        ttk.Button(controls, text='移除所选', command=self.unmark_selected).pack(side='left', padx=6)
        ttk.Button(controls, text='清空名单', command=self.clear_selection).pack(side='left')
        self.save_button = ttk.Button(controls, text='备份并保存', command=self.save)
        self.save_button.pack(side='right')
        self.export_button = ttk.Button(controls, text='导出修改副本…', command=self.export)
        self.export_button.pack(side='right', padx=6)
        ttk.Label(self, textvariable=self.status, wraplength=850).grid(row=5, column=0, sticky='w', pady=(10, 0))

    def open_save(self):
        filename = filedialog.askopenfilename(
            parent=self.root, title='选择战役存档', initialdir=find_save_directory(),
            filetypes=[('战役存档', 'SG8_*.bytes'), ('所有文件', '*.*')])
        if filename:
            self.load_save(Path(filename))

    def load_save(self, path: Path):
        try:
            snapshot = SaveSnapshot.open(path)
            campaign = Campaign.parse(snapshot.data)
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法打开存档', str(exc), parent=self.root)
            return
        self.snapshot = snapshot
        self.campaign = campaign
        self.selected.clear()
        self.upgrade_ids.clear()
        self.realm_edits = RealmEdits()
        self._refresh_destinations()
        self.refresh()
        ruler = self.officer_name(campaign.player_ruler_id)
        self.source_label.set(f'{snapshot.path.name}　·　我方君主 {ruler}　·　{snapshot.path.parent}')
        self.status.set('选中武将后可点「加入所选」或「武将拉满」。勾选「仅显示我方势力」可查看本方武将。')

    def choose_assets(self):
        filename = filedialog.askopenfilename(parent=self.root, title='选择 SG8_Data 下的 resources.assets',
                                              filetypes=[('游戏资源', 'resources.assets')])
        if not filename:
            return
        try:
            self.catalog = load_catalog(Path(filename))
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法读取游戏名称', str(exc), parent=self.root)
            return
        self._refresh_destinations()
        self.refresh()
        self.status.set('已载入武将、据点、名将、战技和装备数据。')

    def open_realm(self):
        if self.campaign:
            maxima = {city_id: city_stat_maxima(self.campaign, city_id, self.catalog.city_stat_maxima)
                      for city_id in self.cities.values()}
            return RealmDialog(self.root, self.cities, self.realm_edits,
                               len(self.catalog.legendary_equipment), self.refresh,
                               epic_count=len(self.catalog.epic_equipment), maxima_by_city=maxima,
                               soldier_count=len(self._soldier_experience(self.campaign)))

    def open_soldiers(self):
        if not self.campaign:
            return
        training = self._campaign_soldiers(self.campaign)
        if not training:
            self.status.set('请点「选择游戏资源」载入兵种资料。')
            return
        picked = [int(row) for row in self.tree.selection()]
        officers = {identity: self.officer_name(identity) for identity in picked
                    if can_assign_soldiers(self.campaign, identity)}
        return SoldierDialog(self.root, self.campaign, training, officers, self.city_name,
                             self.realm_edits, self.refresh)

    def _campaign_soldiers(self, campaign: Campaign):
        campaign_cities = {city.id for city in campaign.cities.values() if city.kind == CITY_TYPE}
        return {soldier_id: training for soldier_id, training in self.catalog.soldier_training.items()
                if not training.expansion_cities or training.expansion_cities & campaign_cities
                or soldier_id in campaign.player_soldier_experience}

    def _soldier_experience(self, campaign: Campaign) -> dict[int, int]:
        return {soldier_id: training.max_experience
                for soldier_id, training in self._campaign_soldiers(campaign).items()}

    def _pending_upgrades(self) -> set[int]:
        return self.upgrade_ids | self.selected if self.auto_maximize.get() else self.upgrade_ids

    def _refresh_destinations(self):
        previous_id = self.cities.get(self.destination.get())
        self.cities = {}
        if self.campaign:
            cities = sorted(self.campaign.owned_cities, key=lambda city: city.id)
            self.cities = {f'{self.city_name(city.id)} [{city.id}]': city.id for city in cities}
        self.city_box.configure(values=list(self.cities))
        label = next((label for label, city_id in self.cities.items() if city_id == previous_id), None)
        self.destination.set(label or next(iter(self.cities), ''))

    def city_name(self, city_id):
        return self.catalog.city_names.get(city_id, f'据点 {city_id}' if city_id else '—')

    def officer_name(self, officer_id):
        return self.catalog.officer_names.get(officer_id, f'武将 {officer_id}')

    def refresh(self, *_):
        upgrades = self._pending_upgrades()
        focused = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        if self.campaign:
            query = self.search.get().strip().casefold()
            experience_levels = self.catalog.officer_experience_levels
            own_only = self.own_faction_only.get()
            eligible_only = self.eligible_only.get()
            for officer in self.campaign.officers.values():
                is_own = officer.faction_id == self.campaign.player_faction_id
                if (own_only and not is_own) or (eligible_only and officer.block_reason):
                    continue
                name = self.officer_name(officer.id)
                if query and query not in f'{name} {officer.id}'.casefold():
                    continue
                if is_own:
                    faction = '我方'
                else:
                    faction = f'势力 {officer.faction_id}' if officer.faction_id else '在野'
                famous = '是' if officer.id in self.catalog.famous_ids else ''
                level = bisect_right(experience_levels, officer.experience) if experience_levels else '—'
                stats = (self.campaign.custom_officer_base_stats.get(officer.id)
                         or self.catalog.officer_base_stats.get(officer.id))
                self.tree.insert('', 'end', iid=str(officer.id), values=(
                    '已选' if officer.id in self.selected else '',
                    '已选' if officer.id in upgrades else '', name, famous, level, officer.health,
                    stats.strength if stats else '—', stats.intelligence if stats else '—', officer.id, faction,
                    self.city_name(officer.city_id), officer.loyalty, officer.block_reason or officer.status))
        self._apply_officer_sort()
        self.tree.selection_set([row for row in focused if self.tree.exists(row)])
        row_state = 'normal' if self.tree.get_children() else 'disabled'
        self.select_all_button.configure(state=row_state)
        self.invert_selection_button.configure(state=row_state)
        self._refresh_upgrade_button()
        summary = f'待转入 {len(self.selected)} 人　待拉满 {len(upgrades)} 人'
        if self.realm_edits.pending:
            summary += '　据点与势力有待保存修改'
        self.selection_label.set(summary)
        ready = (self.selected or upgrades or self.realm_edits.pending) and (not self.selected or self.cities)
        state = 'normal' if ready else 'disabled'
        self.save_button.configure(state=state)
        self.export_button.configure(state=state)
        self.famous_button.configure(state='normal' if self.campaign and self.catalog.famous_ids else 'disabled')
        self.realm_button.configure(state='normal' if self.campaign else 'disabled')
        self.soldier_button.configure(state='normal' if self.campaign else 'disabled')

    def _change_filter(self, selected_filter: tk.BooleanVar, other_filter: tk.BooleanVar):
        if selected_filter.get():
            other_filter.set(False)
        self.refresh()

    def sort_officers(self, column: str):
        if self.sort_column is not None:
            self.tree.heading(self.sort_column, text=self.sort_headers[self.sort_column])
        self.sort_descending = column != 'famous' and column == self.sort_column and not self.sort_descending
        self.sort_column = column
        arrow = '↓' if self.sort_descending else '↑'
        self.tree.heading(column, text=f'{self.sort_headers[column]} {arrow}')
        self._apply_officer_sort()
        self.tree.yview_moveto(0)

    def _apply_officer_sort(self):
        column = self.sort_column
        if column is None:
            return
        rows = self.tree.get_children()
        if column == 'famous':
            ordered = sorted(rows, key=lambda row: int(row) not in self.catalog.famous_ids)
        else:
            direction = -1 if self.sort_descending else 1

            def numeric_key(row):
                try:
                    return False, direction * int(self.tree.set(row, column))
                except ValueError:
                    return True, 0

            ordered = sorted(rows, key=numeric_key)
        self.tree.set_children('', *ordered)

    def toggle_row(self, event):
        row = self.tree.identify_row(event.y)
        if not row or not self.campaign:
            return
        officer = self.campaign.officers[int(row)]
        if officer.block_reason:
            self.status.set(f'{self.officer_name(officer.id)}　·　{officer.block_reason}')
        else:
            self.selected.symmetric_difference_update({officer.id})
            self.refresh()
        return 'break'

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def invert_selection(self):
        selected_rows = set(self.tree.selection())
        self.tree.selection_set([row for row in self.tree.get_children() if row not in selected_rows])

    def _refresh_upgrade_button(self, _event=None):
        self.upgrade_button.configure(state='normal' if self.tree.selection() else 'disabled')

    def mark_selected(self):
        if not self.campaign:
            return
        officers = [self.campaign.officers[int(row)] for row in self.tree.selection()]
        self.selected.update(officer.id for officer in officers if not officer.block_reason)
        self.refresh()
        blocked = sum(bool(officer.block_reason) for officer in officers)
        if blocked:
            self.status.set(f'所选行中有 {blocked} 名武将暂不可转入，原因见状态列。')

    def unmark_selected(self):
        officer_ids = {int(row) for row in self.tree.selection()}
        self.selected.difference_update(officer_ids)
        self.upgrade_ids.difference_update(officer_ids)
        self.refresh()

    def mark_upgrade(self):
        officer_ids = {int(row) for row in self.tree.selection()}
        if not officer_ids:
            return
        if not self.catalog.battle_skills:
            self.status.set('请先点「选择游戏资源」载入战技数据。')
            return
        self.upgrade_ids.update(officer_ids)
        self.refresh()
        self.status.set(f'已将 {len(officer_ids)} 名所选武将加入等级和战技拉满名单。')

    def mark_famous(self):
        if not self.campaign or not self.catalog.famous_ids:
            return
        eligible = {officer.id for officer in self.campaign.officers.values()
                    if officer.id in self.catalog.famous_ids and not officer.block_reason}
        added = len(eligible - self.selected)
        self.selected.update(eligible)
        self.refresh()
        if added:
            self.status.set(f'已勾选 {added} 名新的可转入名将。')
        elif eligible:
            self.status.set('可转入名将已全部勾选。')
        else:
            self.status.set('当前存档暂时没有可转入的名将。')

    def clear_selection(self):
        self.selected.clear()
        self.upgrade_ids.clear()
        self.refresh()

    def edited_save(self):
        upgrades = self._pending_upgrades()
        if not self.campaign or not (self.selected or upgrades or self.realm_edits.pending):
            raise ValueError('当前没有待保存的修改。')
        campaign = self.campaign
        edited = None
        if self.selected:
            city_id = self.cities.get(self.destination.get())
            if city_id is None:
                raise ValueError('请选择转入武将的目标据点。')
            edited = campaign.transfer(sorted(self.selected), city_id)
        if upgrades:
            if not self.catalog.battle_skills:
                raise ValueError('请先点「选择游戏资源」载入战技数据。也可以取消「转入时拉满」，按转入规则写入满额经验。')
            if edited is not None:
                campaign = Campaign.parse(edited)
            edited = campaign.maximize_officers(sorted(upgrades), self.catalog.battle_skills)
        if self.realm_edits.pending:
            if edited is not None:
                campaign = Campaign.parse(edited)
            edited = edit_realm(campaign, self.realm_edits, self.catalog.legendary_equipment,
                                city_limits=self.catalog.city_stat_maxima,
                                epic_equipment=self.catalog.epic_equipment,
                                soldier_experience=self._soldier_experience(campaign))
        return edited

    def _changes_summary(self):
        changes = []
        if self.selected:
            changes.append(f'{len(self.selected)} 名武将转入 {self.destination.get()} 并升至 {MAX_OFFICER_LEVEL} 级')
        upgrades = self._pending_upgrades()
        if upgrades:
            changes.append(f'{len(upgrades)} 名武将升至 {MAX_OFFICER_LEVEL} 级并拉满已学战技')
        if self.realm_edits.city_stats:
            changes.append(f'调整 {len(self.realm_edits.city_stats)} 座我方据点')
        if self.realm_edits.fill_resources:
            changes.append(f'我方金钱和粮草各 {MAX_RESOURCES:,}')
        if self.realm_edits.fill_legendary:
            changes.append(f'国库中 {len(self.catalog.legendary_equipment)} 种传说装备各 {EQUIPMENT_STOCK} 件')
        if self.realm_edits.fill_epic:
            changes.append(f'国库中 {len(self.catalog.epic_equipment)} 种史诗装备各 {EQUIPMENT_STOCK} 件')
        if self.realm_edits.maximize_soldiers:
            changes.append(f'拉满 {len(self._soldier_experience(self.campaign))} 种我方兵种的训练经验')
        if self.realm_edits.soldier_assignments:
            changes.append(f'为 {len(self.realm_edits.soldier_assignments)} 名我方武将配置兵种，'
                           f'兵力补至 {MAX_OFFICER_SOLDIERS}')
        return '\n'.join(changes)

    def export(self):
        directory = filedialog.askdirectory(parent=self.root, title='选择修改副本的输出位置')
        if not directory:
            return
        try:
            result = self.snapshot.export(Path(directory), self.edited_save())
        except (OSError, ValueError) as exc:
            messagebox.showerror('导出失败', str(exc), parent=self.root)
            return
        self.status.set(f'已导出修改副本\n{self._changes_summary()}\n{result}')

    def save(self):
        try:
            edited = self.edited_save()
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法保存', str(exc), parent=self.root)
            return

        title = '保存并备份存档'
        changes = self._changes_summary()
        question = f'{changes}\n\n保存到 {self.snapshot.path.name}？原存档会备份到同一目录。'
        if self.selected:
            question += '\n原据点如果失去最后一名驻城武将，可能转为无主。'
        if not messagebox.askyesno(title, question, parent=self.root):
            return
        try:
            if game_is_running():
                raise ValueError('请退出游戏后再保存到原槽位。游戏运行时可以导出修改副本。')
            backup = self.snapshot.replace(edited)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            messagebox.showerror('保存失败', str(exc), parent=self.root)
            return
        self.load_save(self.snapshot.path)
        self.status.set(f'已保存修改\n{changes}\n备份文件 {backup}')


def main():
    root = tk.Tk()
    root.title('三国群英传8 · 武将批量转入')
    root.geometry('1010x650')
    root.minsize(875, 450)
    app = Editor(root)
    if len(sys.argv) > 1:
        app.load_save(Path(sys.argv[1]))
    root.mainloop()


if __name__ == '__main__':
    main()
