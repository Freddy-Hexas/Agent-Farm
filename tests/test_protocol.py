from __future__ import annotations

import unittest

from agent_farm.protocol import (
    MESSAGE_SCHEMAS,
    PROTOCOL_VERSION,
    ProtocolNegotiationError,
    negotiate_protocol,
    protocol_descriptor,
)


class ProtocolTests(unittest.TestCase):
    def test_all_product_messages_have_versioned_json_schemas(self):
        expected = {"thread", "turn", "item", "worker", "tool", "diff", "approval", "usage"}
        self.assertEqual(set(MESSAGE_SCHEMAS), expected)
        for name, schema in MESSAGE_SCHEMAS.items():
            self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
            self.assertIn("schema_version", schema["required"])
            self.assertTrue(schema["$id"].endswith(f"/{name}.schema.json"))

    def test_descriptor_can_publish_schema_metadata_or_full_contracts(self):
        compact = protocol_descriptor()
        complete = protocol_descriptor(include_schemas=True)
        self.assertEqual(compact["protocol_version"], PROTOCOL_VERSION)
        self.assertNotIn("schemas", compact)
        self.assertEqual(set(complete["schemas"]), set(MESSAGE_SCHEMAS))

    def test_negotiation_selects_version_and_capability_intersection(self):
        response = negotiate_protocol(
            {
                "client_name": "test-client",
                "client_version": "1.0",
                "protocol_versions": [99, 1],
                "capabilities": ["approvals.v1", "unknown.v1"],
                "required_capabilities": ["approvals.v1"],
            }
        )
        self.assertEqual(response["protocol_version"], 1)
        self.assertEqual(response["enabled_capabilities"], ["approvals.v1"])
        self.assertTrue(response["session_id"])

    def test_negotiation_rejects_incompatible_version_or_required_capability(self):
        with self.assertRaises(ProtocolNegotiationError):
            negotiate_protocol({"protocol_versions": [99]})
        with self.assertRaises(ProtocolNegotiationError):
            negotiate_protocol(
                {
                    "protocol_versions": [1],
                    "required_capabilities": ["future.v9"],
                }
            )


if __name__ == "__main__":
    unittest.main()
