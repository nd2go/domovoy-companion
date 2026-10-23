import copy
import json
import unittest
from pathlib import Path

from homebox_companion.core.inventory_schema import (
    SchemaError,
    check_evolution,
    effective_fields,
    encode_value,
    normalize_values,
    validate_schema,
)


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(
            (Path(__file__).resolve().parents[1] / "src/homebox_companion/inventory_schema_seed.json").read_text()
        )

    def test_library_and_new_category(self):
        validate_schema(self.schema)
        self.assertEqual(sum(v["parent"] is None for v in self.schema["categories"].values()), 22)
        self.schema["categories"]["new_kind"] = dict(
            label="Новая категория", parent="tool", blocks=["dimensions"], fields=[]
        )
        validate_schema(self.schema)
        self.assertIn("power_w", effective_fields(self.schema, "new_kind"))
        self.assertEqual(
            len(effective_fields(self.schema, "new_kind")), len(set(effective_fields(self.schema, "new_kind")))
        )

    def test_exact_values(self):
        for raw, expected in [("2,5", "2.5"), ("3.2", "3.2"), ("0", "0"), ("", "")]:
            self.assertEqual(encode_value(self.schema, "diameter_mm", raw), expected)
        self.assertEqual(encode_value(self.schema, "diameter_mm", "0.6", "cm"), "6")
        self.assertEqual(encode_value(self.schema, "diameter_mm", "0.5", "inch"), "12.7")
        self.assertEqual(encode_value(self.schema, "thread", "G½"), "G½")
        self.assertEqual(encode_value(self.schema, "waterproof", "false"), "false")
        self.assertEqual(encode_value(self.schema, "expiry_date", "2028-02-29"), "2028-02-29")
        self.assertEqual(encode_value(self.schema, "applications", '["wood", "metal"]'), '["wood","metal"]')
        self.assertEqual(
            normalize_values(self.schema, {"owner_note": "Keep me", "diameter_mm": "2,5"}),
            {"owner_note": "Keep me", "diameter_mm": "2.5"},
        )

    def test_invalid_values(self):
        for key, value, unit in [
            ("diameter_mm", "NaN", None),
            ("diameter_mm", "1e99", None),
            ("diameter_mm", "2", "kg"),
            ("diameter_mm", "-1", None),
            ("core_count", "2.5", None),
            ("waterproof", "yes", None),
            ("expiry_date", "2027-02-29", None),
            ("condition", "invented", None),
            ("applications", '["wood","wood"]', None),
            ("thread", "x" * 501, None),
        ]:
            with self.subTest(key=key, value=value), self.assertRaises(SchemaError):
                encode_value(self.schema, key, value, unit)

    def test_cycles_and_unknown_references(self):
        for mutate in [
            lambda s: s["categories"]["tool"].update(parent="drill"),
            lambda s: s["blocks"]["common"]["fields"].append("missing"),
            lambda s: s["fields"]["diameter_mm"].update(unit="missing"),
        ]:
            bad = copy.deepcopy(self.schema)
            mutate(bad)
            with self.assertRaises(SchemaError):
                validate_schema(bad)

    def test_evolution(self):
        after = copy.deepcopy(self.schema)
        after["fields"]["diameter_mm"]["label"] = "Renamed"
        after["categories"]["tool"]["archived"] = True
        check_evolution(self.schema, after)
        after["fields"]["diameter_mm"]["unit"] = "cm"
        with self.assertRaises(SchemaError):
            check_evolution(self.schema, after)
        after = copy.deepcopy(self.schema)
        del after["fields"]["size"]
        with self.assertRaises(SchemaError):
            check_evolution(self.schema, after)

    def test_malformed_types(self):
        for section, key, prop in [
            ("fields", "diameter_mm", "kind"),
            ("fields", "diameter_mm", "unit"),
            ("categories", "tool", "parent"),
        ]:
            bad = copy.deepcopy(self.schema)
            bad[section][key][prop] = []
            with self.assertRaises(SchemaError):
                validate_schema(bad)

    def test_conversion_does_not_round(self):
        self.schema["fields"]["length_in"] = dict(label="Length", kind="decimal", unit="inch")
        with self.assertRaises(SchemaError):
            encode_value(self.schema, "length_in", "1", "mm")

    def test_bounds(self):
        self.schema["blocks"]["common"]["fields"] = ["color"] * 101
        with self.assertRaises(SchemaError):
            validate_schema(self.schema)


if __name__ == "__main__":
    unittest.main()
