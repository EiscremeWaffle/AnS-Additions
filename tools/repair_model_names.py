#!/usr/bin/env python3
"""Update renamed geometry identifiers by comparing two Bedrock resource packs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def geometry_records(pack: Path) -> tuple[set[str], dict[str, set[str]]]:
    """Return all geometry identifiers and their identifier-independent fingerprints."""
    models_dir = pack / "models"
    if not models_dir.is_dir():
        raise ValueError(f"Resource pack has no models directory: {models_dir}")

    identifiers: set[str] = set()
    fingerprints_by_identifier: dict[str, set[str]] = defaultdict(set)
    for path in sorted(models_dir.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Could not read model JSON {path}: {error}") from error

        geometries = data.get("minecraft:geometry", [])
        if isinstance(geometries, dict):
            geometries = [geometries]
        if not isinstance(geometries, list):
            raise ValueError(f"Expected a geometry list in {path}")

        for geometry in geometries:
            if not isinstance(geometry, dict):
                raise ValueError(f"Expected a geometry object in {path}")
            description = geometry.get("description", {})
            if not isinstance(description, dict):
                raise ValueError(f"Expected a geometry description in {path}")
            identifier = description.get("identifier")
            if not isinstance(identifier, str):
                continue

            comparable = dict(geometry)
            comparable_description = dict(description)
            comparable_description.pop("identifier", None)
            comparable["description"] = comparable_description
            canonical = json.dumps(comparable, sort_keys=True, separators=(",", ":"))
            fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            identifiers.add(identifier)
            fingerprints_by_identifier[identifier].add(fingerprint)

    return identifiers, fingerprints_by_identifier


def json_files(pack: Path) -> list[Path]:
    return sorted(path for path in pack.rglob("*.json") if path.is_file())


def referenced_identifiers(pack: Path, identifiers: set[str]) -> set[str]:
    references: set[str] = set()
    pattern = re.compile(r"(?<![A-Za-z0-9_.])geometry\.[A-Za-z0-9_.-]+(?![A-Za-z0-9_.])")
    for path in json_files(pack):
        try:
            text = path.read_bytes().decode("utf-8-sig")
        except (OSError, UnicodeError) as error:
            raise ValueError(f"Could not read {path}: {error}") from error
        references.update(match for match in pattern.findall(text) if match in identifiers)
    return references


def entity_variant_migrations(
    old_pack: Path, new_pack: Path, old_identifiers: set[str], new_identifiers: set[str]
) -> dict[str, set[str]]:
    old_entities_dir = old_pack / "entity"
    new_entities_dir = new_pack / "entity"
    if not old_entities_dir.is_dir() or not new_entities_dir.is_dir():
        return {}

    def entity_variants(directory: Path) -> dict[str, list[dict[str, str]]]:
        variants: dict[str, list[dict[str, str]]] = defaultdict(list)
        for path in sorted(directory.rglob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ValueError(f"Could not read entity JSON {path}: {error}") from error

            description = data.get("minecraft:client_entity", {}).get("description", {})
            identifier = description.get("identifier")
            geometry = description.get("geometry", {})
            if isinstance(identifier, str) and isinstance(geometry, dict):
                variants[identifier].append(
                    {
                        key: value
                        for key, value in geometry.items()
                        if isinstance(key, str) and isinstance(value, str)
                    }
                )
        return variants

    old_entities = entity_variants(old_entities_dir)
    new_entities = entity_variants(new_entities_dir)
    candidates: dict[str, set[str]] = defaultdict(set)

    for entity_identifier, old_variants in old_entities.items():
        for old_variant in old_variants:
            for variant_key, old_model in old_variant.items():
                if old_model not in old_identifiers:
                    continue
                for new_variant in new_entities.get(entity_identifier, []):
                    new_model = new_variant.get(variant_key)
                    if new_model in new_identifiers:
                        candidates[old_model].add(new_model)

    return candidates


def build_migration(
    old_identifiers: set[str],
    old_fingerprints: dict[str, set[str]],
    new_identifiers: set[str],
    new_fingerprints: dict[str, set[str]],
    referenced: set[str],
    entity_candidates: dict[str, set[str]],
) -> tuple[dict[str, str], dict[str, str]]:
    identifiers_by_fingerprint: dict[str, set[str]] = defaultdict(set)
    for identifier, fingerprints in new_fingerprints.items():
        for fingerprint in fingerprints:
            identifiers_by_fingerprint[fingerprint].add(identifier)

    migrations: dict[str, str] = {}
    unresolved: dict[str, str] = {}
    to_migrate = (old_identifiers & referenced) - new_identifiers
    for identifier in sorted(to_migrate):
        candidates: set[str] = set()
        for fingerprint in old_fingerprints[identifier]:
            candidates.update(identifiers_by_fingerprint.get(fingerprint, set()))

        if len(candidates) != 1:
            variant_candidates = entity_candidates.get(identifier, set())
            if len(variant_candidates) == 1:
                candidates = variant_candidates

        if len(candidates) == 1:
            migrations[identifier] = next(iter(candidates))
        elif candidates:
            unresolved[identifier] = "ambiguous match"
        else:
            unresolved[identifier] = "no exact match in the newer pack"

    return migrations, unresolved


def replace_identifiers(pack: Path, migrations: dict[str, str], apply: bool) -> tuple[int, int]:
    if not migrations:
        return 0, 0

    patterns = [
        (re.compile(rf"(?<![A-Za-z0-9_.]){re.escape(old)}(?![A-Za-z0-9_.])"), new)
        for old, new in sorted(migrations.items(), key=lambda item: -len(item[0]))
    ]
    changed_files = 0
    replacements = 0
    for path in json_files(pack):
        try:
            raw = path.read_bytes()
            has_bom = raw.startswith(b"\xef\xbb\xbf")
            text = raw.decode("utf-8-sig")
        except (OSError, UnicodeError) as error:
            raise ValueError(f"Could not read {path}: {error}") from error

        updated = text
        file_replacements = 0
        for pattern, replacement in patterns:
            updated, count = pattern.subn(lambda _match, value=replacement: value, updated)
            file_replacements += count

        if file_replacements:
            changed_files += 1
            replacements += file_replacements
            if apply:
                encoded = updated.encode("utf-8")
                path.write_bytes((b"\xef\xbb\xbf" if has_bom else b"") + encoded)

    return changed_files, replacements


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview or update renamed geometry IDs using two resource-pack versions."
    )
    parser.add_argument("--target", type=Path, required=True, help="Pack to update")
    parser.add_argument("--old-pack", type=Path, required=True, help="Older source pack")
    parser.add_argument("--new-pack", type=Path, required=True, help="Newer source pack")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (without this flag the script only previews changes)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = args.target.resolve()
    old_pack = args.old_pack.resolve()
    new_pack = args.new_pack.resolve()

    for pack in (target, old_pack, new_pack):
        if not pack.is_dir():
            print(f"Error: directory does not exist: {pack}", file=sys.stderr)
            return 2
    if target == old_pack or target == new_pack:
        print("Error: target pack must be different from both source packs.", file=sys.stderr)
        return 2
    if any(pack in target.parents or target in pack.parents for pack in (old_pack, new_pack)):
        print("Error: target and source pack directories must not overlap.", file=sys.stderr)
        return 2

    try:
        old_ids, old_fingerprints = geometry_records(old_pack)
        new_ids, new_fingerprints = geometry_records(new_pack)
        referenced = referenced_identifiers(target, old_ids)
        entity_candidates = entity_variant_migrations(old_pack, new_pack, old_ids, new_ids)
        migrations, unresolved = build_migration(
            old_ids,
            old_fingerprints,
            new_ids,
            new_fingerprints,
            referenced,
            entity_candidates,
        )
        for old, new in migrations.items():
            print(f"{old} -> {new}")
        changed_files, replacements = replace_identifiers(target, migrations, args.apply)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    mode = "Updated" if args.apply else "Would update"
    print(f"{mode} {replacements} reference(s) in {changed_files} file(s).")
    if unresolved:
        print(f"Left {len(unresolved)} old model ID(s) unchanged (no safe exact match):")
        for identifier, reason in unresolved.items():
            print(f"  {identifier}: {reason}")
    if not args.apply:
        print("Preview only; pass --apply to write these changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
