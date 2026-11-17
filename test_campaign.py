import unittest

from sg8_campaign import Campaign, _officer_records
from sg8_format import Float32, SaveFile, SaveFormatError
from sg8_names import BattleSkill


def campaign_bytes() -> bytes:
    def country(country_id, ruler, officer=0):
        government = [0] * 11
        government[3] = officer
        return [country_id, 0, b"", ruler, country_id, 2, 0, *government, 1, 1, 1121, 60328, 0]

    def city(city_id, country_id, governor, residents, officer=0):
        coordinate = Float32(b"\x00" * 4)
        jobs = [2, officer, 2, 11, 0, 0]
        for kind in (102, 103, 101):
            jobs.extend([kind, 0, 0, 0, 0, 0])
        return [
            city_id, city_id, 0, 12345, 5, 5, coordinate, coordinate, coordinate,
            country_id, 1, *([0] * 10), 1, 2496, 0, 1,
            *([0] * 10), *jobs, *([0] * 6), False, governor, len(residents), *residents, 0, 0,
        ]

    def officer(officer_id, city, position, loyalty=100, experience=0):
        return [
            officer_id, officer_id, loyalty, 1_000_000_000, 6, 9, experience, 3456, 0,
            city, 0, position, 0, 0, 0, 0, 0, 3, 2251, 2511, 2442, 0, 4,
            2251, 0, 2511, 2442, 1121, 0, 0, 0, 0, 0, 0, 0, 0,
        ]

    return SaveFile({
        "PlayerGameData": [b"PlayerGameData", 4, *([0] * 9), 1001],
        "CountryGameData": [b"CountryGameData", 2, 2, *country(1, 1001), *country(2, 1002, 1005)],
        "CityGameData": [
            b"CityGameData", 1, 3, *city(1, 1, 1001, []), *city(2, 2, 1002, [1003]),
            *city(3, 2, 1005, [], 1005),
        ],
        "TroopGameData": [
            b"TroopGameData", 2, 8, *officer(1001, 1, 96, experience=5000), *officer(1002, 2, 96),
            *officer(1003, 2, 999), *officer(1004, 2, 0, 20), *officer(1005, 3, 101),
            *officer(2986, 1_000_001, 0, 40, 1137), *officer(2987, 1_000_001, 0, 40, 1137),
            *officer(2988, 1_000_001, 0, 40, 1137),
        ],
    }).to_bytes()


class CampaignTests(unittest.TestCase):
    def test_historical_ruler_uses_the_saved_player_faction(self):
        save = SaveFile.parse(campaign_bytes())
        save.sections["PlayerGameData"][11] = 0
        save.sections["CountryGameData"].extend([1, 2, 0, 0, 101])

        campaign = Campaign.parse(save.to_bytes())

        self.assertEqual((campaign.player_faction_id, campaign.player_ruler_id), (2, 1002))
        self.assertEqual([city.id for city in campaign.owned_cities], [2, 3])

    def test_rejects_a_missing_or_ambiguous_historical_faction(self):
        save = SaveFile.parse(campaign_bytes())
        save.sections["PlayerGameData"][11] = 0
        countries = save.sections["CountryGameData"]
        start = len(countries)
        for footer in ([], [1, 5, 0, 0, 101], [2, 1, 2, 0, 0, 101], [1, 2]):
            with self.subTest(footer=footer):
                countries[start:] = footer
                with self.assertRaisesRegex(SaveFormatError, "无法确定当前玩家的势力"):
                    Campaign.parse(save.to_bytes())

    def test_reads_officers_with_multiple_passive_skills(self):
        save = SaveFile.parse(campaign_bytes())
        troops = save.sections["TroopGameData"]
        original, _ = _officer_records(troops)
        passive_at = original[0].learned_skills.stop
        troops[passive_at:passive_at + 1] = [2, 415, 425]

        updated, _ = _officer_records(troops)

        self.assertEqual([record.id for record in updated], [record.id for record in original])
        self.assertEqual([troops[index] for index in updated[0].equipped_skills], [2251, 0, 2511, 2442])

    def test_maximize_upgrades_every_battle_skill_and_fills_unlocked_slots(self):
        save = SaveFile.parse(campaign_bytes())
        troops = save.sections["TroopGameData"]
        records = {record.id: record for record in _officer_records(troops)[0]}
        officer = records[1004].start
        troops[officer + 17] = 4
        troops.insert(officer + 21, 1271)
        troops[officer + 26:officer + 28] = [0, 1271]
        skills = {
            2251: BattleSkill(slot=0, max_id=2255),
            2511: BattleSkill(slot=2, max_id=2515),
            2442: BattleSkill(slot=3, max_id=2445),
            1271: BattleSkill(slot=3, max_id=1275),
        }

        edited = Campaign(save).maximize_officers([1004], skills)
        after = SaveFile.parse(edited).sections["TroopGameData"]

        self.assertEqual(after[officer + 6], 96_766_560)
        self.assertEqual(after[officer + 18:officer + 22], [2255, 2515, 2445, 1275])
        self.assertEqual(after[officer + 24:officer + 28], [2255, 0, 2515, 1275])
        self.assertEqual(after[:officer], troops[:officer])
        self.assertEqual(after[officer + 28:], troops[officer + 28:])

    def test_transferred_officers_gain_full_experience(self):
        campaign = Campaign.parse(campaign_bytes())
        edited = campaign.transfer([1003, 1004], 1)
        troops = SaveFile.parse(edited).sections["TroopGameData"]
        records, _ = _officer_records(troops)
        experience = {record.id: troops[record.start + 6] for record in records}
        self.assertEqual(experience[1003], 96_766_560)
        self.assertEqual(experience[1004], 96_766_560)
        self.assertEqual(experience[1001], 5000)

    def test_recruits_only_supported_special_officers_from_hidden_location(self):
        campaign = Campaign.parse(campaign_bytes())
        self.assertIsNotNone(campaign.officers[2988].block_reason)
        updated = Campaign.parse(campaign.transfer([2986, 2987], 1))
        for officer_id in (2986, 2987):
            officer = updated.officers[officer_id]
            self.assertEqual((officer.city_id, officer.faction_id, officer.loyalty), (1, 1, 90))

    def test_batch_transfers_wild_and_stationed_officers(self):
        original = campaign_bytes()
        campaign = Campaign.parse(original)
        updated = Campaign.parse(campaign.transfer([1003, 1004], 1))

        self.assertEqual(campaign.officers[1004].faction_id, 0)
        self.assertEqual(campaign.officers[1003].faction_id, 2)
        self.assertEqual(updated.player_faction_id, 1)
        for officer_id in (1003, 1004):
            officer = updated.officers[officer_id]
            self.assertEqual((officer.city_id, officer.faction_id, officer.loyalty), (1, 1, 90))

        sections = SaveFile.parse(original).sections
        changed = SaveFile.parse(campaign.transfer([1003, 1004], 1)).sections
        self.assertEqual(changed["PlayerGameData"], sections["PlayerGameData"])
        self.assertEqual(changed["CountryGameData"], sections["CountryGameData"])
        self.assertNotIn(1003, changed["CityGameData"])

    def test_last_officer_departure_releases_city_and_offices(self):
        campaign = Campaign.parse(campaign_bytes())
        updated_bytes = campaign.transfer([1005], 1)
        moved = Campaign.parse(updated_bytes)
        changed = SaveFile.parse(updated_bytes).sections

        self.assertEqual(moved.cities[3].faction_id, 0)
        self.assertEqual(moved.cities[2].faction_id, 2)
        self.assertEqual(moved.officers[1005].faction_id, 1)
        self.assertNotIn(1005, changed["CityGameData"])
        self.assertNotIn(1005, changed["CountryGameData"])

    def test_rejects_batch_containing_ruler_before_any_edit(self):
        campaign = Campaign.parse(campaign_bytes())
        with self.assertRaises(SaveFormatError):
            campaign.transfer([1003, 1002], 1)
        self.assertEqual(campaign.officers[1003].faction_id, 2)
        self.assertEqual(campaign.officers[1004].faction_id, 0)


if __name__ == "__main__":
    unittest.main()
