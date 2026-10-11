"""The API and the UI dev server listen on the loopback interface only (local single-user tool)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LocalBindingTests(unittest.TestCase):
    def test_vite_dev_server_binds_to_loopback(self):
        cfg = (ROOT / "webui" / "vite.config.js").read_text(encoding="utf-8")
        hosts = re.findall(r"\bhost\s*:\s*([^,\n}]+)", cfg)
        self.assertEqual([h.strip() for h in hosts], ['"127.0.0.1"'])  # not true / "0.0.0.0" (all interfaces)

    def test_launcher_binds_api_and_ui_to_loopback(self):
        bat = (ROOT / "start_ats_stack.bat").read_text(encoding="utf-8")
        self.assertIn("uvicorn ats.server:app --host 127.0.0.1", bat)
        self.assertIn("npm run dev -- --host 127.0.0.1", bat)
        self.assertNotIn("0.0.0.0", bat)


if __name__ == "__main__":
    unittest.main()
