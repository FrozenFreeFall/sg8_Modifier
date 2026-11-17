"""Show troop recruitment requirements and stage assignments for existing units."""

from collections.abc import Callable, Mapping
import tkinter as tk
from tkinter import ttk

from sg8_campaign import CITIES, Campaign
from sg8_names import SoldierTraining
from sg8_realm import RealmEdits


class SoldierDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, campaign: Campaign, training: Mapping[int, SoldierTraining],
                 officers: Mapping[int, str], city_name: Callable[[int], str], edits: RealmEdits,
                 on_change: Callable[[], None]):
        super().__init__(parent)
        self.title('兵种来源与配兵')
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.campaign = campaign
        self.training = training
        self.officers = officers
        self.city_name = city_name
        self.edits = edits
        self.on_change = on_change
        self.options = {f'{soldier.name or "兵种"} [{soldier_id}]': soldier_id
                        for soldier_id, soldier in sorted(training.items())}
        self.choice = tk.StringVar(self, value=next(iter(self.options), ''))
        self.details = tk.StringVar(self)
        self.status = tk.StringVar(self)

        body = ttk.Frame(self, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='兵种').grid(row=0, column=0, sticky='w', padx=(0, 8))
        ttk.Combobox(body, textvariable=self.choice, values=list(self.options), width=30,
                     state='readonly').grid(row=0, column=1, sticky='w')
        ttk.Label(body, textvariable=self.details, wraplength=420, justify='left').grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(10, 0))
        self.target_list = None
        if officers:
            targets = ttk.LabelFrame(body, text=f'配兵对象（{len(officers)} 名我方武将）', padding=6)
            targets.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(10, 0))
            self.target_list = ttk.Treeview(targets, show='tree', selectmode='none', height=min(len(officers), 6))
            self.target_list.column('#0', width=270)
            self.target_list.pack(side='left', fill='both', expand=True)
            scrollbar = ttk.Scrollbar(targets, orient='vertical', command=self.target_list.yview)
            scrollbar.pack(side='right', fill='y')
            self.target_list.configure(yscrollcommand=scrollbar.set)
            for officer_id, name in officers.items():
                self.target_list.insert('', 'end', iid=str(officer_id), text=f'{name} [{officer_id}]')
            ttk.Label(body, text='配兵保留现有兵力。0 兵力的武将需补兵后出战。', wraplength=420).grid(
                row=3, column=0, columnspan=2, sticky='w', pady=(5, 0))
        else:
            ttk.Label(body, text='在主窗口选中我方武将，即可配置兵种。', wraplength=420).grid(
                row=2, column=0, columnspan=2, sticky='w', pady=(10, 0))
        self.assign_button = ttk.Button(body, text='配给选中的武将', command=self.stage_assignment,
                                        state='normal' if officers else 'disabled')
        self.assign_button.grid(row=4, column=0, columnspan=2, sticky='w', pady=(8, 0))
        ttk.Label(body, textvariable=self.status, wraplength=420).grid(row=5, column=0, columnspan=2,
                                                                       sticky='w', pady=(8, 0))
        ttk.Button(body, text='返回主窗口', command=self.destroy).grid(row=6, column=1, sticky='e', pady=(8, 0))
        self.choice.trace_add('write', self._refresh_details)
        self._refresh_details()
        self.bind('<Escape>', lambda _event: self.destroy())
        self.grab_set()

    def _refresh_details(self, *_):
        soldier_id = self.options.get(self.choice.get())
        if soldier_id is None:
            return
        soldier = self.training[soldier_id]
        candidates = [(city_id, scale) for city_id, scale in soldier.source_cities
                      if city_id in self.campaign.cities]
        owned = {city.id for city in self.campaign.owned_cities}
        city_tokens = self.campaign._save.sections[CITIES]
        met = any(city_id in owned and city_tokens[self.campaign._city_records[city_id].start + 10] >= scale
                  for city_id, scale in candidates)
        lines = [f'最高训练等级 {soldier.max_level} 级']
        if not soldier.source_cities:
            lines.append('基础兵种，可直接征募。')
        elif candidates:
            sources = '、'.join(f'{self.city_name(city_id)} {scale} 级' for city_id, scale in candidates)
            status = '已满足来源条件' if met else '征募前须占有至少一处来源据点，并达到所列规模'
            lines.extend((status, sources))
        else:
            lines.append('当前剧本没有对应的来源据点。')
        self.details.set('\n'.join(lines))

    def stage_assignment(self):
        soldier_id = self.options.get(self.choice.get())
        if soldier_id is None or not self.officers:
            return
        self.edits.soldier_assignments.update(dict.fromkeys(self.officers, soldier_id))
        self.status.set(f'已为 {len(self.officers)} 名武将暂存「{self.training[soldier_id].name}」配兵。')
        self.on_change()
