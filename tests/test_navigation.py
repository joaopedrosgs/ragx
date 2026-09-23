import unittest

from ragx.commands import navigation_cmd as navi


class NavigationTests(unittest.TestCase):
    MAPS = [["prontera", "Prontera", 5001, 312, 392]]
    NPCS = [
        ["prontera", 12001, 102, 83, "Tool Dealer", "", 134, 221],
        ["prontera", 12000, 101, 117, "Kafra Employee", "", 146, 89],
    ]
    MOBS = [["prt_fild08", 17000, 300, 70 << 16 | 1002, "Poring", "PORING", 1, 0]]

    def test_rows_keep_the_generator_layout(self):
        data = navi.convert(self.MAPS, self.NPCS, self.MOBS)
        self.assertEqual(data["maps"]["prontera"], {"name": "Prontera", "w": 312, "h": 392})
        self.assertEqual(data["npcs"][1], {"map": "prontera", "name": "Tool Dealer",
                                           "x": 134, "y": 221, "shop": True})
        self.assertEqual(data["mobs"][0], {"map": "prt_fild08", "name": "Poring",
                                           "sprite": "PORING", "level": 1,
                                           "amount": 70, "mvp": False})

    def test_npcs_are_sorted_by_map_then_name(self):
        data = navi.convert([], self.NPCS, [])
        self.assertEqual([n["name"] for n in data["npcs"]], ["Kafra Employee", "Tool Dealer"])

    def test_the_clients_obfuscated_names_are_dropped(self):
        data = navi.convert([], [["abbey01", 1, 101, 481, "\x1c7QYYDA\x1c", "QN8", 51, 45]], [])
        self.assertEqual(data["npcs"][0]["name"], "")
        self.assertEqual((data["npcs"][0]["x"], data["npcs"][0]["y"]), (51, 45))

    def test_mvp_spawns_are_marked(self):
        data = navi.convert([], [], [["gef_dun02", 1, 301, 1 << 16 | 1039, "Baphomet",
                                      "BAPHOMET", 81, 0]])
        self.assertTrue(data["mobs"][0]["mvp"])


if __name__ == "__main__":
    unittest.main()
