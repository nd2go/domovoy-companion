"""Validate inventory definitions and encode Homebox text values."""

import copy
import json
import re
from datetime import date
from decimal import Decimal, DecimalException, Inexact, localcontext

MAX_BYTES = 2 * 1024 * 1024
MAX_FIELDS = 100
KINDS = {"text", "integer", "decimal", "boolean", "date", "choice", "choices"}
ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
NUMBER = re.compile(r"-?[0-9]{1,30}(?:[.,][0-9]{1,12})?\Z")


class SchemaError(ValueError):
    """The definition or value is invalid."""


def check(condition, message):
    if not condition:
        raise SchemaError(message)


def text(value, maximum=255):
    check(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum, "Invalid text")
    return value


def decimal(value):
    check(isinstance(value, str) and NUMBER.fullmatch(value), "Use a finite decimal value")
    return Decimal(value.replace(",", "."))


def effective_fields(schema, category_id):
    """Return inherited field IDs without duplicate definitions."""
    result = []
    visited = set()
    while category_id is not None:
        check(category_id not in visited and len(visited) < 16, "Category cycle or excessive depth")
        visited.add(category_id)
        category = schema["categories"].get(category_id)
        check(category is not None, "Unknown category")
        result.extend(category["fields"])
        for block_id in category["blocks"]:
            if not schema["blocks"][block_id].get("archived"):
                result.extend(schema["blocks"][block_id]["fields"])
        category_id = category.get("parent")
    result = list(dict.fromkeys(result))
    check(len(result) <= MAX_FIELDS, "Too many fields in a template")
    return result


def validate_schema(schema):
    """Validate bounded definitions before a file write or API operation."""
    check(isinstance(schema, dict), "Expected an object")
    check(set(schema) == {"version", "fields", "categories", "blocks", "units"}, "Unknown schema members")
    check(type(schema["version"]) is int and schema["version"] == 1, "Unsupported schema version")
    for section, limit in (("fields", 500), ("categories", 500), ("blocks", 100), ("units", 100)):
        values = schema[section]
        check(isinstance(values, dict) and len(values) <= limit, "Excessive or invalid definitions")
        for key, value in values.items():
            check(isinstance(key, str) and ID.fullmatch(key), "Invalid definition ID")
            check(isinstance(value, dict), "Expected a definition")
            text(value.get("label"))
    for unit in schema["units"].values():
        check(set(unit) <= {"label", "dimension", "factor", "offset"}, "Unknown unit members")
        text(unit.get("dimension"), 64)
        check(decimal(unit.get("factor")) > 0, "Invalid unit scale")
        decimal(unit.get("offset", "0"))
    for field in schema["fields"].values():
        check(
            set(field) <= {"label", "kind", "unit", "options", "minimum", "maximum", "archived", "help"},
            "Unknown field members",
        )
        check(isinstance(field.get("kind"), str) and field["kind"] in KINDS, "Invalid field type")
        check(type(field.get("archived", False)) is bool, "Invalid archive flag")
        if "help" in field:
            text(field["help"], 500)
        unit = field.get("unit")
        check(unit is None or (isinstance(unit, str) and unit in schema["units"]), "Unknown unit")
        check(unit is None or field["kind"] in ("integer", "decimal"), "Only numbers have units")
        options = field.get("options", [])
        check(isinstance(options, list) and len(options) <= 100, "Invalid choices")
        check(all(isinstance(x, str) and ID.fullmatch(x) for x in options), "Invalid choice ID")
        check(len(options) == len(set(options)), "Duplicate choices")
        check(not options or field["kind"] in ("choice", "choices"), "Unexpected choices")
        for bound in ("minimum", "maximum"):
            if bound in field:
                check(field["kind"] in ("integer", "decimal"), "Only numbers have bounds")
                decimal(field[bound])
        if "minimum" in field and "maximum" in field:
            check(decimal(field["minimum"]) <= decimal(field["maximum"]), "Invalid numeric range")
    for block in schema["blocks"].values():
        check(set(block) <= {"label", "fields", "archived"}, "Unknown block members")
        references(block.get("fields"), schema["fields"])
        check(type(block.get("archived", False)) is bool, "Invalid archive flag")
    for category in schema["categories"].values():
        check(set(category) <= {"label", "parent", "fields", "blocks", "archived"}, "Unknown category members")
        check(
            category.get("parent") is None
            or (isinstance(category["parent"], str) and category["parent"] in schema["categories"]),
            "Unknown parent",
        )
        references(category.get("fields"), schema["fields"])
        references(category.get("blocks"), schema["blocks"])
        check(type(category.get("archived", False)) is bool, "Invalid archive flag")
    for key in schema["categories"]:
        effective_fields(schema, key)
    return schema


def references(values, target):
    check(isinstance(values, list) and len(values) <= MAX_FIELDS, "Invalid references")
    check(all(isinstance(x, str) and x in target for x in values), "Unknown reference")
    check(len(values) == len(set(values)), "Duplicate references")


def check_evolution(before, after):
    """Keep IDs and stored meanings during definition changes."""
    validate_schema(after)
    for section in ("fields", "categories", "blocks", "units"):
        check(set(before[section]) <= set(after[section]), "Archive definitions instead of deleting them")
    for key, old in before["fields"].items():
        new = after["fields"][key]
        check(all(old.get(x) == new.get(x) for x in ("kind", "unit")), "Create a new ID for a different type or unit")
        check(set(old.get("options", [])) <= set(new.get("options", [])), "Keep existing choice IDs")
    for key, old in before["units"].items():
        check(
            all(old.get(x, "0") == after["units"][key].get(x, "0") for x in ("dimension", "factor", "offset")),
            "Create a new ID for a different unit",
        )


def encode_value(schema, field_id, value, input_unit=None):
    """Encode a known value as Homebox text. Empty text means unknown."""
    field = schema["fields"][field_id]
    check(isinstance(value, str) and len(value) <= 500, "Value exceeds the Homebox limit")
    if value == "":
        return ""
    kind = field["kind"]
    if kind in ("integer", "decimal"):
        number = decimal(value)
        target = field.get("unit")
        if input_unit is not None:
            check(target is not None and input_unit in schema["units"], "Unknown input unit")
            a, b = schema["units"][input_unit], schema["units"][target]
            check(a["dimension"] == b["dimension"], "Incompatible units")
            try:
                with localcontext() as context:
                    context.prec = 200
                    context.traps[Inexact] = True
                    number = (
                        number * decimal(a["factor"]) + decimal(a.get("offset", "0")) - decimal(b.get("offset", "0"))
                    ) / decimal(b["factor"])
            except DecimalException:
                raise SchemaError("Conversion cannot be stored exactly") from None
        if kind == "integer":
            check(number == number.to_integral_value(), "Expected an integer")
        for bound, compare in (("minimum", lambda a, b: a >= b), ("maximum", lambda a, b: a <= b)):
            if bound in field:
                check(compare(number, decimal(field[bound])), "Value is outside the allowed range")
        result = format(number, "f")
        if "." in result:
            result = result.rstrip("0").rstrip(".")
        return "0" if number == 0 else result
    check(input_unit is None, "Only numeric values have units")
    if kind == "boolean":
        check(value in ("true", "false"), "Expected true, false, or unknown")
    elif kind == "date":
        try:
            check(date.fromisoformat(value).isoformat() == value, "Use YYYY-MM-DD")
        except ValueError:
            raise SchemaError("Use YYYY-MM-DD") from None
    elif kind == "choice":
        check(value in field.get("options", []), "Unknown choice")
    elif kind == "choices":
        try:
            values = json.loads(value)
        except ValueError, RecursionError:
            raise SchemaError("Expected a list of choices") from None
        check(isinstance(values, list) and len(values) <= 100, "Invalid choice list")
        check(all(isinstance(x, str) and x in field.get("options", []) for x in values), "Unknown choice")
        check(len(values) == len(set(values)), "Duplicate choices")
        return json.dumps(values, separators=(",", ":"), ensure_ascii=False)
    return value


def normalize_values(schema, values):
    """Check known fields and retain unrelated Homebox fields."""
    check(isinstance(values, dict) and len(values) <= MAX_FIELDS, "Too many custom fields")
    result = {}
    for key, value in values.items():
        text(key)
        check(isinstance(value, str) and len(value) <= 500, "Invalid custom field value")
        result[key] = encode_value(schema, key, value) if key in schema["fields"] else value
    return result


def clone_schema(schema):
    return copy.deepcopy(validate_schema(schema))
