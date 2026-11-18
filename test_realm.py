import unittest

from sg8_campaign import Campaign, _city_records, _country_records
from sg8_format import SaveFile, SaveFormatError
from sg8_realm import RealmEdits, edit_realm
from test_campaign import campaign_bytes


class RealmEditsTests(unittest.TestCase):
    def test_unlocks_and_maxes_own_soldiers_alongside_treasury_edits(self):
        original = SaveFile.parse(campaign_bytes())
        country = original.sections['CountryGameData']
        army_at = _country_records(country)[1].government_at + 12
        country[army_at:army_at + 3] = [2, 1121, 123, 1999, 13]
        campaign = Campaign(original)
        before = original.to_bytes()
        updates = RealmEdits(fill_resources=True, fill_epic=True, maximize_soldiers=True)
        experience = {1121: 60_328, 1125: 119_466, 1141: 328_368}

        edited = edit_realm(campaign, updates, (), epic_equipment=[2001],
                            soldier_experience=experience)
        after = Campaign.parse(edited)
        countries = after._save.sections['CountryGameData']
        factions = _country_records(countries)
        self.assertEqual(factions[1].soldier_states, {1121: 60_328, 1999: 13, 1125: 119_466, 1141: 328_368})
        self.assertEqual(countries[9:12], [1, 2001, 99])
        gov = factions[1].government_at
        self.assertEqual(countries[gov + 1:gov + 3], [999_999_999, 999_999_999])
        self.assertEqual(countries[gov + 12:gov + 22], [4, 1121, 60_328, 1999, 13, 1125, 119_466, 1141, 328_368, 0])
        old_rival = _country_records(country)[2].start
        self.assertEqual(countries[factions[2].start:], country[old_rival:])

        troops = after._save.sections['TroopGameData']
        state_at = after._records_by_id[1001].soldier_state_at
        self.assertEqual(troops[state_at:state_at + 2], [60_328, 0])
        self.assertEqual(troops[after._records_by_id[1002].soldier_state_at], 0)
        self.assertEqual(original.to_bytes(), before)

    def test_rejects_missing_or_out_of_range_soldier_data(self):
        campaign = Campaign.parse(campaign_bytes())
        for experience in (None, {0: 1}, {1121: -1}, {1121: 2_147_483_648}):
            with self.subTest(experience=experience):
                with self.assertRaises(SaveFormatError):
                    edit_realm(campaign, RealmEdits(maximize_soldiers=True), (),
                               soldier_experience=experience)

    def test_assigns_a_trained_soldier_type_and_fills_existing_troops(self):
        save = SaveFile.parse(campaign_bytes())
        campaign = Campaign(save)
        state_at = campaign._records_by_id[1001].soldier_state_at
        save.sections['TroopGameData'][state_at + 1] = 60
        edits = RealmEdits(soldier_assignments={1001: 1171})

        after = Campaign.parse(edit_realm(campaign, edits, (), soldier_experience={1171: 60_328}))

        self.assertEqual(after._save.sections['TroopGameData'][state_at - 1:state_at + 2],
                         [1171, 60_328, 500])
        self.assertEqual(after.player_soldier_experience[1171], 60_328)
        self.assertEqual(save.sections['TroopGameData'][state_at - 1:state_at + 2], [1121, 0, 60])

    def test_fills_troops_when_assigning_an_officer_with_zero_troops(self):
        campaign = Campaign.parse(campaign_bytes())
        state_at = campaign._records_by_id[1001].soldier_state_at
        edits = RealmEdits(soldier_assignments={1001: 1171})

        after = Campaign.parse(edit_realm(campaign, edits, (), soldier_experience={1171: 60_328}))

        self.assertEqual(after._save.sections['TroopGameData'][state_at - 1:state_at + 2],
                         [1171, 60_328, 500])

    def test_updates_requested_stats_on_selected_owned_city_only(self):
        original = campaign_bytes()
        updates = RealmEdits(city_stats={1: {
            'agriculture': 120, 'commerce': 230, 'technology': 240,
            'resentment': 17, 'wall_defense': 56, 'reserve_soldiers': 678,
        }})
        edited = edit_realm(Campaign.parse(original), updates, ())

        old = SaveFile.parse(original).sections
        new = SaveFile.parse(edited).sections
        start = _city_records(old['CityGameData'])[1].start
        expected = old['CityGameData'].copy()
        expected[start + 11:start + 15] = [120, 230, 240, 83]
        expected[start + 18:start + 20] = [678, 56]
        self.assertEqual(new['CityGameData'], expected)
        self.assertEqual(new['CountryGameData'], old['CountryGameData'])

    def test_rejects_resentment_greater_than_one_hundred(self):
        campaign = Campaign.parse(campaign_bytes())
        with self.assertRaisesRegex(SaveFormatError, '0 至 100'):
            edit_realm(campaign, RealmEdits(city_stats={1: {'resentment': 101}}), ())

    def test_city_input_accepts_both_bounds_and_rejects_values_outside_them(self):
        campaign = Campaign.parse(campaign_bytes())
        caps = {(1, 1): dict(agriculture=200, commerce=225, technology=250,
                             wall_defense=1200, reserve_soldiers=5000)}
        maxima = caps[1, 1] | {'resentment': 100}
        for stat, maximum in maxima.items():
            with self.subTest(stat=stat):
                for value in (0, maximum):
                    edit_realm(campaign, RealmEdits(city_stats={1: {stat: value}}), (), city_limits=caps)
                for value in (-1, maximum + 1):
                    with self.assertRaisesRegex(SaveFormatError, f'0 至 {maximum:,}'):
                        edit_realm(campaign, RealmEdits(city_stats={1: {stat: value}}), (), city_limits=caps)

    def test_rejects_city_edits_targeting_a_rival_city(self):
        original = campaign_bytes()
        campaign = Campaign.parse(original)
        updates = RealmEdits(city_stats={1: {'agriculture': 123}, 2: {'agriculture': 456}})
        with self.assertRaises(SaveFormatError):
            edit_realm(campaign, updates, ())
        self.assertEqual(campaign._save.to_bytes(), original)

    def test_epic_stock_refill_leaves_unselected_legendary_stock_untouched(self):
        original = SaveFile.parse(campaign_bytes())
        values = original.sections['CountryGameData']
        values[9:10] = [2, 1200, 4, 2001, 2]
        edited = edit_realm(Campaign(original), RealmEdits(fill_epic=True), [1200],
                            epic_equipment=[2001, 2002])
        after = SaveFile.parse(edited).sections['CountryGameData']
        self.assertEqual(after[9:16], [3, 1200, 4, 2001, 99, 2002, 99])

    def test_expands_player_stock_and_fills_resources_without_shifting_fields(self):
        original = SaveFile.parse(campaign_bytes())
        values = original.sections['CountryGameData']
        values[9:10] = [2, 1200, 146, 2001, 2]
        campaign = Campaign(original)
        updates = RealmEdits(fill_resources=True, fill_legendary=True)
        edited = edit_realm(campaign, updates, [2002, 1200, 1998, 1200])
        after = SaveFile.parse(edited)

        stock_and_resources = after.sections['CountryGameData'][9:21]
        self.assertEqual(stock_and_resources, [
            4, 1200, 99, 2001, 2, 1998, 99, 2002, 99, 0, 999_999_999, 999_999_999,
        ])
        old_rival = _country_records(values)[2].start
        new_rival = _country_records(after.sections['CountryGameData'])[2].start
        self.assertEqual(after.sections['CountryGameData'][new_rival:], values[old_rival:])
        self.assertEqual(values[9:14], [2, 1200, 146, 2001, 2])
        self.assertEqual(Campaign.parse(edited).player_faction_id, 1)
        self.assertEqual(edit_realm(Campaign.parse(edited), updates, [1200, 1998, 2002]), edited)


if __name__ == '__main__':
    unittest.main()
