import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from ragx.commands.ui_cmd import UI_GRF_PREFIX, export_folders, interface_files


def bmp(color=(255, 0, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 1), color).save(buf, "BMP")
    return buf.getvalue()


class FakeArchive:
    def __init__(self, files: dict):
        self.files = files

    def namelist(self):
        return list(self.files)

    def read(self, key):
        return self.files[key]

    def __contains__(self, key):
        return key in self.files


ARCHIVE = FakeArchive({
    UI_GRF_PREFIX + "swap_equipment\\ico_change.bmp": bmp((10, 20, 30)),
    UI_GRF_PREFIX + "swap_equipment\\notes.txt": b"not art",
    UI_GRF_PREFIX + "basic_interface\\rodexsystem\\renewal\\bg.bmp": bmp(),
    UI_GRF_PREFIX + "basic_interface\\btn_equip_off.bmp": bmp(),
    UI_GRF_PREFIX + "inventory\\bg.bmp": bmp(),
    "data\\texture\\effect\\x.bmp": bmp(),
})


class InterfaceListTests(unittest.TestCase):
    def test_lists_relative_paths_filtered_case_insensitively(self):
        self.assertEqual(interface_files(ARCHIVE.namelist(), "EQUIP"),
                         ["basic_interface/btn_equip_off.bmp",
                          "swap_equipment/ico_change.bmp",
                          "swap_equipment/notes.txt"])

    def test_only_the_interface_tree(self):
        self.assertNotIn("effect", " ".join(interface_files(ARCHIVE.namelist())))


class FolderExportTests(unittest.TestCase):
    def test_every_bitmap_at_any_depth_is_mirrored_and_keyed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            count = export_folders(ARCHIVE, out, ["swap_equipment", "basic_interface"])
            self.assertEqual(count, 3)       # the .txt is not art; inventory not asked
            skin = out / "ui" / "skin"
            self.assertTrue((skin / "swap_equipment" / "ico_change.png").is_file())
            nested = skin / "basic_interface" / "rodexsystem" / "renewal" / "bg.png"
            self.assertTrue(nested.is_file())
            self.assertFalse((skin / "inventory").exists())
            # Magenta is the client's transparency key.
            self.assertEqual(Image.open(nested).getpixel((0, 0))[3], 0)
            self.assertEqual(Image.open(skin / "swap_equipment" / "ico_change.png")
                             .getpixel((0, 0)), (10, 20, 30, 255))

    def test_unknown_folder_exports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(export_folders(ARCHIVE, Path(tmp), ["nope"]), 0)


if __name__ == "__main__":
    unittest.main()
