import unittest

from ragx.commands import hat_effects_cmd as hat


class HatEffectTests(unittest.TestCase):
    IDS = {
        "HAT_EF_STRANGELIGHTS": 33,
        "HAT_EF_FIREWORK": 35,
        "FOOTPRINT_EF_STR_BASE": 35,  # another table's id space: never a hat name
    }

    def test_str_names_match_the_effects_export(self):
        self.assertEqual(hat.str_effect_name("efst_STRANGELIGHTS\\strangelights.str"),
                         "efst_strangelights/strangelights")

    def test_str_rows_keep_their_offsets_and_flags(self):
        rows = hat.convert({33: {
            "resourceFileName": "efst_STRANGELIGHTS\\strangelights.str",
            "hatEffectPos": -4.0, "hatEffectPosX": 0.5,
            "isRenderBeforeCharacter": True, "isAdjustSizeWhenShrinkState": True,
        }}, self.IDS)
        self.assertEqual(rows["33"], {
            "name": "HAT_EF_STRANGELIGHTS", "str": "efst_strangelights/strangelights",
            "pos": -4, "pos_x": 0.5, "before": True, "shrink_size": True,
        })

    def test_effect_table_rows_and_hat_names_only(self):
        rows = hat.convert({35: {"hatEffectID": 1057.0}}, self.IDS)
        self.assertEqual(rows["35"], {"name": "HAT_EF_FIREWORK", "effect_id": 1057})

    def test_rows_are_keyed_by_number_in_order(self):
        rows = hat.convert({40: {"hatEffectID": 1}, 7: {"hatEffectID": 2}}, {})
        self.assertEqual(list(rows), ["7", "40"])
        self.assertNotIn("name", rows["7"])


if __name__ == "__main__":
    unittest.main()
