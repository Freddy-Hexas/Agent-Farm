import tempfile
import unittest
from pathlib import Path

from agent_farm.attachments import AttachmentStore, MAX_FILE_BYTES


class AttachmentStoreTests(unittest.TestCase):
    def test_context_exposes_stable_attachment_id_and_per_id_maps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "notes.txt"
            source.write_text("private scoped context", encoding="utf-8")
            store = AttachmentStore(root / "store")
            item = store.add(str(source))
            context = store.context_for([item.attachment_id])
            contexts = store.contexts_by_id([item.attachment_id])
            self.assertIn(f"Attachment ID: {item.attachment_id}", context)
            self.assertEqual(contexts[item.attachment_id], context)
            self.assertEqual(store.model_inputs_by_id([item.attachment_id]), {})

    def test_text_attachment_is_staged_and_exposed_as_untrusted_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "notes.md"
            source.write_text("Important storage-chip research", encoding="utf-8")
            store = AttachmentStore(root / "staged")
            try:
                item = store.add(str(source))
                public = item.public_json()
                context = store.context_for([item.attachment_id])

                self.assertEqual(public["name"], "notes.md")
                self.assertEqual(public["kind"], "text")
                self.assertNotIn(str(item.path), public.values())
                self.assertIn("untrusted reference data", context)
                self.assertIn("Important storage-chip research", context)
                self.assertTrue(item.path.is_file())
            finally:
                session_root = store.session_root
                store.close()
                self.assertFalse(session_root.exists())

    def test_image_attachment_becomes_a_data_url_for_multimodal_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "chart.png"
            source.write_bytes(b"\x89PNG\r\n\x1a\nimage-test")
            store = AttachmentStore(root / "staged")
            try:
                item = store.add(str(source))
                model_inputs = store.model_inputs_for([item.attachment_id])
                self.assertEqual(model_inputs[0]["name"], "chart.png")
                self.assertTrue(model_inputs[0]["data_url"].startswith("data:image/png;base64,"))
            finally:
                store.close()

    def test_credentials_unsupported_types_and_oversized_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AttachmentStore(root / "staged")
            try:
                credential = root / ".env"
                credential.write_text("API_KEY=secret", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Credential"):
                    store.add(str(credential))

                unsupported = root / "archive.bin"
                unsupported.write_bytes(b"not supported")
                with self.assertRaisesRegex(ValueError, "Unsupported attachment type"):
                    store.add(str(unsupported))
                self.assertEqual(list(store.session_root.iterdir()), [])

                oversized = root / "large.txt"
                with oversized.open("wb") as handle:
                    handle.truncate(MAX_FILE_BYTES + 1)
                with self.assertRaisesRegex(ValueError, "10 MB"):
                    store.add(str(oversized))
            finally:
                store.close()

    def test_attachment_ids_are_validated_and_removable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.txt"
            source.write_text("hello", encoding="utf-8")
            store = AttachmentStore(root / "staged")
            try:
                item = store.add(str(source))
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    store.resolve([item.attachment_id, item.attachment_id])
                with self.assertRaises(FileNotFoundError):
                    store.resolve(["att-expired"])
                self.assertTrue(store.remove(item.attachment_id))
                self.assertFalse(store.remove(item.attachment_id))
                self.assertFalse(item.path.exists())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
