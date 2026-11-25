import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from quackguard import console
from quackguard.artifacts import load_policy
from quackguard.annotation import build_annotation_output


class ConsoleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="quackguard-console-")
        cls.workspace = cls.temp.name
        cls.pack_config = console.CONFIG if "最小对接样例" in str(console.CONFIG) else (
            console.PACK / "04_最小对接样例" / "config.json"
        )
        cls.original_config = console.CONFIG
        console.CONFIG = cls.pack_config
        cls.imported = console._action(
            cls.workspace, "import", {"use_pack": True}
        )
        cls.built = console._action(cls.workspace, "build", {})
        cls.inferred = console._action(cls.workspace, "infer", {})
        console.Handler.workspace = cls.workspace
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), console.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        console.CONFIG = cls.original_config
        cls.temp.cleanup()

    def get(self, path):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        try:
            with urllib.request.urlopen(url) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_import_build_and_infer_are_connected(self):
        self.assertEqual(self.imported["records"], 62)
        self.assertEqual(self.imported["unique"], 62)
        self.assertEqual(self.imported["failures"], [])
        self.assertEqual(self.built["code"], 0)
        policy = load_policy(console.paths(self.workspace)["artifacts"])
        self.assertEqual(len(policy["rules"]), 62)
        self.assertEqual(self.inferred["code"], 0)
        self.assertEqual(self.inferred["payload"]["records"], 64)

    def test_bad_and_duplicate_import_are_localized(self):
        temp = tempfile.TemporaryDirectory(prefix="quackguard-import-")
        self.addCleanup(temp.cleanup)
        result = console._action(
            temp.name,
            "import",
            {"text": '{"rule_id":"R001","version":"3.0","rule_text":"a"}\n'
                     'not json\n'
                     '{"rule_id":"R001","version":"3.0","rule_text":"a"}\n'},
        )
        self.assertEqual(result["records"], 2)
        self.assertEqual(result["unique"], 1)
        self.assertEqual(result["failures"][0]["line"], 2)
        self.assertEqual(result["duplicates"][0]["line"], 3)

    def test_unit_detail_exposes_rule_evidence_and_versions(self):
        units = console.units(self.workspace)["units"]
        detail = console.unit_detail(self.workspace, units[0]["audit_unit_id"])
        self.assertTrue(detail["rule"]["rule_text"])
        self.assertEqual(
            detail["artifact_version"], detail["output"]["artifact_version"]
        )
        self.assertTrue(detail["trace_id"])

    def test_annotation_without_bbox_is_not_fabricated(self):
        output = build_annotation_output(
            {
                "annotation_id": "a1",
                "audit_unit_id": "u1",
                "material_id": "m1",
                "page_index": 1,
                "target": "提取型号",
            },
            {"fields": {"model": {"status": "unreadable", "value": None}}},
            {"label": "ambiguous"},
        )
        self.assertIsNone(output["bbox"])

    def test_console_page_and_api(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("QuackGuard".encode(), body)
        self.assertNotIn(b"reviewer", body.lower())
        status, body = self.get("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["records"], 64)
        status, body = self.get("/api/units")
        self.assertEqual(len(json.loads(body)["units"]), 64)

    def test_report_and_media_paths_are_restricted(self):
        status, _ = self.get("/report?name=../state.sqlite3")
        self.assertEqual(status, 400)
        status, _ = self.get("/media?unit=missing&material=x&page=1")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
