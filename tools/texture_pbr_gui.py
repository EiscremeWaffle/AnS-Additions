#!/usr/bin/env python3
"""Generate Bedrock MER, MERS, and/or normal maps from paired source textures."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import numpy as np
    from PIL import Image
except ImportError as error:
    raise SystemExit("Pillow and NumPy are required. Install with: py -m pip install -r requirements.txt") from error

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]
LAB = tuple[float, float, float]
NORMAL_METHODS = ("Copy source normal", "Luminance patches", "Normalized pattern", "Edge gradients")
MERS_METHODS = (
    "Albedo pattern matching",
    "Copy source (matching layout)",
    "Model UV remap (merge target MER)",
)


@dataclass
class MERPalette:
    mer_by_rgb: dict[RGB, RGBA]
    lab_by_rgb: dict[RGB, LAB]
    ambiguous_colors: int


@dataclass
class NormalSource:
    albedo: np.ndarray
    normal: np.ndarray


@dataclass
class MERSSource:
    albedo: np.ndarray
    opaque: np.ndarray
    mers: np.ndarray


@dataclass
class UVFace:
    uv: tuple[float, float]
    uv_size: tuple[float, float]
    rotation: int = 0


def rgb_to_lab(rgb: RGB) -> LAB:
    linear = []
    for channel in rgb:
        value = channel / 255.0
        linear.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)

    x = (0.4124564 * linear[0] + 0.3575761 * linear[1] + 0.1804375 * linear[2]) / 0.95047
    y = 0.2126729 * linear[0] + 0.7151522 * linear[1] + 0.0721750 * linear[2]
    z = (0.0193339 * linear[0] + 0.1191920 * linear[1] + 0.9503041 * linear[2]) / 1.08883
    delta = 6 / 29

    def pivot(value: float) -> float:
        return value ** (1 / 3) if value > delta**3 else value / (3 * delta**2) + 4 / 29

    fx, fy, fz = pivot(x), pivot(y), pivot(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def color_distance(left: LAB, right: LAB) -> float:
    return sum((left[index] - right[index]) ** 2 for index in range(3)) ** 0.5


def build_mer_palette(source_path: Path, mer_path: Path) -> MERPalette:
    with Image.open(source_path) as source_image, Image.open(mer_path) as mer_image:
        if source_image.size != mer_image.size:
            raise ValueError(
                "Source albedo and MER dimensions must match "
                f"({source_image.width}x{source_image.height} vs {mer_image.width}x{mer_image.height})."
            )
        source_pixels = list(source_image.convert("RGBA").get_flattened_data())
        mer_pixels = list(mer_image.convert("RGBA").get_flattened_data())

    samples: dict[RGB, Counter[RGBA]] = defaultdict(Counter)
    for source_pixel, mer_color in zip(source_pixels, mer_pixels):
        if source_pixel[3] == 0:
            continue
        source_color = (source_pixel[0], source_pixel[1], source_pixel[2])
        samples[source_color][mer_color] += 1
    mer_by_rgb = {color: values.most_common(1)[0][0] for color, values in samples.items()}
    lab_by_rgb = {color: rgb_to_lab(color) for color in mer_by_rgb}
    ambiguous = sum(1 for values in samples.values() if len(values) > 1)
    return MERPalette(mer_by_rgb, lab_by_rgb, ambiguous)


def resolve_mer(color: RGB, palette: MERPalette, tolerance: float) -> RGBA | None:
    exact = palette.mer_by_rgb.get(color)
    if exact is not None:
        return exact
    if tolerance <= 0:
        return None
    target_lab = rgb_to_lab(color)
    nearest_color = None
    nearest_distance = float("inf")
    for source_color, source_lab in palette.lab_by_rgb.items():
        distance = color_distance(target_lab, source_lab)
        if distance < nearest_distance:
            nearest_color, nearest_distance = source_color, distance
    if nearest_color is None or nearest_distance > tolerance:
        return None
    return palette.mer_by_rgb[nearest_color]


def transfer_mer(target_path: Path, palette: MERPalette, tolerance: float):
    with Image.open(target_path) as image:
        target = image.convert("RGBA")
        target_pixels = list(target.get_flattened_data())
        target_size = target.size

    mapped: dict[RGB, RGBA | None] = {}
    for red, green, blue, alpha in target_pixels:
        if alpha == 0:
            continue
        color = (red, green, blue)
        if color not in mapped:
            mapped[color] = resolve_mer(color, palette, tolerance)
    unmatched = sum(
        1
        for pixel in target_pixels
        if pixel[3] > 0 and mapped[(pixel[0], pixel[1], pixel[2])] is None
    )
    if unmatched:
        return None, unmatched

    output = Image.new("RGBA", target_size)
    output.putdata(
        [
            (0, 0, 0, 0) if pixel[3] == 0 else mapped[(pixel[0], pixel[1], pixel[2])]
            for pixel in target_pixels
        ]
    )
    return output, 0


def build_mers_source(source_path: Path, mers_path: Path) -> MERSSource:
    with Image.open(source_path) as source_image, Image.open(mers_path) as mers_image:
        if source_image.size != mers_image.size:
            raise ValueError(
                "Source albedo and MERS dimensions must match "
                f"({source_image.width}x{source_image.height} vs {mers_image.width}x{mers_image.height})."
            )
        source_pixels = np.asarray(source_image.convert("RGBA"), dtype=np.uint8).copy()
        mers_pixels = np.asarray(mers_image.convert("RGBA"), dtype=np.uint8).copy()

    albedo = source_pixels[:, :, :3].astype(np.float32) / 255.0
    opaque = np.ones(source_pixels.shape[:2], dtype=bool)
    return MERSSource(albedo, opaque, mers_pixels)


def transfer_mers(target_path: Path, source: MERSSource, method: str, tolerance: float, radius: int):
    with Image.open(target_path) as image:
        target_pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    target_height, target_width, _ = target_pixels.shape
    target_albedo = target_pixels[:, :, :3].astype(np.float32) / 255.0

    source_descriptors = texture_descriptors(source.albedo, radius, method)
    target_descriptors = texture_descriptors(target_albedo, radius, method)
    source_valid = source.opaque.reshape(-1)
    source_descriptors = source_descriptors[source_valid]
    source_mers = source.mers.reshape(-1, 4)[source_valid]
    source_norm_squared = np.sum(source_descriptors * source_descriptors, axis=1)
    target_norm_squared = np.sum(target_descriptors * target_descriptors, axis=1)
    target_indices = np.arange(len(target_descriptors))
    output = np.zeros((target_height * target_width, 4), dtype=np.uint8)
    matched = np.zeros(target_height * target_width, dtype=bool)
    errors = []
    batch_size = max(1, min(32, 2_000_000 // max(1, len(source_descriptors))))

    for start in range(0, len(target_indices), batch_size):
        indices = target_indices[start : start + batch_size]
        block = target_descriptors[indices]
        distances_squared = (
            target_norm_squared[indices, None]
            + source_norm_squared[None, :]
            - 2 * (block @ source_descriptors.T)
        )
        np.maximum(distances_squared, 0, out=distances_squared)
        distances = np.sqrt(distances_squared / source_descriptors.shape[1])
        best_indices = np.argmin(distances, axis=1)
        best_errors = distances[np.arange(len(indices)), best_indices]
        valid = best_errors <= tolerance
        output[indices[valid]] = source_mers[best_indices[valid]]
        matched[indices[valid]] = True
        errors.extend(best_errors[valid].tolist())

    unmatched = int(np.count_nonzero(~matched))
    average_error = float(np.mean(errors)) if errors else 0.0
    if unmatched:
        return None, unmatched, average_error
    result = Image.fromarray(output.reshape(target_height, target_width, 4), "RGBA")
    return result, 0, average_error


def copy_mers_layout(target_path: Path, source: MERSSource):
    with Image.open(target_path) as image:
        target_pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    if source.mers.shape[:2] != target_pixels.shape[:2]:
        raise ValueError(
            "Copy source MERS requires matching source and target dimensions "
            f"({source.mers.shape[1]}x{source.mers.shape[0]} vs "
            f"{target_pixels.shape[1]}x{target_pixels.shape[0]})."
        )
    output = source.mers.copy()
    return Image.fromarray(output, "RGBA")


def _texture_geometry(model_path: Path):
    with model_path.open("r", encoding="utf-8") as file:
        document = json.load(file)
    geometries = document.get("minecraft:geometry")
    if not isinstance(geometries, list) or len(geometries) != 1:
        raise ValueError(f"{model_path.name} must contain exactly one minecraft:geometry entry.")
    geometry = geometries[0]
    description = geometry.get("description", {})
    texture_width = description.get("texture_width")
    texture_height = description.get("texture_height")
    if not isinstance(texture_width, (int, float)) or not isinstance(texture_height, (int, float)):
        raise ValueError(f"{model_path.name} must define texture_width and texture_height.")
    if texture_width <= 0 or texture_height <= 0:
        raise ValueError(f"{model_path.name} has invalid texture dimensions.")

    cubes = []
    for bone in geometry.get("bones", []):
        for cube_index, cube in enumerate(bone.get("cubes", [])):
            origin = cube.get("origin")
            size = cube.get("size")
            if not isinstance(origin, list) or not isinstance(size, list) or len(origin) != 3 or len(size) != 3:
                continue
            signature = (
                tuple(round(float(value), 5) for value in origin),
                tuple(round(float(value), 5) for value in size),
                tuple(round(float(value), 5) for value in cube.get("rotation", [0, 0, 0])),
            )
            face_sizes = {
                "north": (size[0], size[1]),
                "south": (size[0], size[1]),
                "east": (size[2], size[1]),
                "west": (size[2], size[1]),
                "up": (size[0], size[2]),
                "down": (size[0], size[2]),
            }
            uv = cube.get("uv")
            faces = {}
            if isinstance(uv, dict):
                for face_name, face_uv in uv.items():
                    if face_name not in face_sizes or not isinstance(face_uv, dict) or "uv" not in face_uv:
                        continue
                    uv_size = tuple(float(value) for value in face_uv.get("uv_size", face_sizes[face_name]))
                    if uv_size[0] == 0 or uv_size[1] == 0:
                        continue
                    faces[face_name] = UVFace(
                        tuple(float(value) for value in face_uv["uv"]),
                        uv_size,
                        int(face_uv.get("uv_rotation", 0)),
                    )
            elif isinstance(uv, list) and len(uv) == 2:
                u, v = (float(value) for value in uv)
                x, y, z = (float(value) for value in size)
                defaults = {
                    "east": (u, v + z, z, y),
                    "north": (u + z, v + z, x, y),
                    "west": (u + z + x, v + z, z, y),
                    "south": (u + z + x + z, v + z, x, y),
                    "up": (u + z, v, x, z),
                    "down": (u + z + x, v, x, z),
                }
                faces = {
                    face_name: UVFace((face_u, face_v), (face_width, face_height))
                    for face_name, (face_u, face_v, face_width, face_height) in defaults.items()
                    if face_width != 0 and face_height != 0
                }
            if faces:
                cubes.append((signature, cube_index, faces))
    if not cubes:
        raise ValueError(f"{model_path.name} contains no cuboid UV faces to transfer.")
    return int(texture_width), int(texture_height), cubes


def _rotation_to_canonical(rotation: int):
    if rotation == 0:
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    if rotation == 90:
        return (0.0, 1.0, 0.0, -1.0, 0.0, 1.0)
    if rotation == 180:
        return (-1.0, 0.0, 1.0, 0.0, -1.0, 1.0)
    if rotation == 270:
        return (0.0, -1.0, 1.0, 1.0, 0.0, 0.0)
    raise ValueError(f"Unsupported face uv_rotation: {rotation}. Use 0, 90, 180, or 270.")


def _inverse_rotation(rotation: int):
    return _rotation_to_canonical((-rotation) % 360)


def _compose_uv_transforms(first, second):
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = second
    return (
        a2 * a1 + b2 * d1,
        a2 * b1 + b2 * e1,
        a2 * c1 + b2 * f1 + c2,
        d2 * a1 + e2 * d1,
        d2 * b1 + e2 * e1,
        d2 * c1 + e2 * f1 + f2,
    )


def _read_uv_rotation(face: UVFace) -> int:
    if face.rotation not in (0, 90, 180, 270):
        raise ValueError(f"Unsupported face uv_rotation: {face.rotation}. Use 0, 90, 180, or 270.")
    return face.rotation


def _scale_uv_face(face: UVFace, scale_x: float, scale_y: float) -> UVFace:
    return UVFace(
        (face.uv[0] * scale_x, face.uv[1] * scale_y),
        (face.uv_size[0] * scale_x, face.uv_size[1] * scale_y),
        face.rotation,
    )


def _uv_alpha_patch(
    source_alpha: Image.Image,
    source_face: UVFace,
    target_face: UVFace,
    texture_width: int,
    texture_height: int,
):
    target_u, target_v = target_face.uv
    target_width, target_height = target_face.uv_size
    source_u, source_v = source_face.uv
    source_width, source_height = source_face.uv_size
    if not all(math.isfinite(value) for value in (target_u, target_v, target_width, target_height, source_u, source_v, source_width, source_height)):
        raise ValueError("Model contains non-finite UV coordinates.")
    if target_width == 0 or target_height == 0 or source_width == 0 or source_height == 0:
        raise ValueError("Model contains a face with zero-sized UV coordinates.")

    target_min_u, target_min_v = min(target_u, target_u + target_width), min(target_v, target_v + target_height)
    target_max_u, target_max_v = max(target_u, target_u + target_width), max(target_v, target_v + target_height)
    if (
        target_min_u < -1
        or target_min_v < -1
        or target_max_u > texture_width + 1
        or target_max_v > texture_height + 1
    ):
        raise ValueError("A model face has UV coordinates outside its texture.")
    left = max(0, math.floor(target_min_u))
    top = max(0, math.floor(target_min_v))
    right = min(texture_width, math.ceil(target_max_u))
    bottom = min(texture_height, math.ceil(target_max_v))
    if right <= left or bottom <= top:
        raise ValueError("A target model face has UV coordinates outside its texture.")

    source_min_u, source_min_v = min(source_u, source_u + source_width), min(source_v, source_v + source_height)
    source_max_u, source_max_v = max(source_u, source_u + source_width), max(source_v, source_v + source_height)
    if (
        source_min_u < -1
        or source_min_v < -1
        or source_max_u > source_alpha.width + 1
        or source_max_v > source_alpha.height + 1
    ):
        raise ValueError("A source model face has UV coordinates outside its texture.")
    source_left = max(0, math.floor(source_min_u))
    source_top = max(0, math.floor(source_min_v))
    source_right = min(source_alpha.width, math.ceil(source_max_u))
    source_bottom = min(source_alpha.height, math.ceil(source_max_v))
    if (
        source_left < 0
        or source_top < 0
        or source_right > source_alpha.width
        or source_bottom > source_alpha.height
        or source_right <= source_left
        or source_bottom <= source_top
    ):
        raise ValueError("A source model face has UV coordinates outside its texture.")
    edge_padding = 2
    source_patch = np.asarray(
        source_alpha.crop((source_left, source_top, source_right, source_bottom)),
        dtype=np.uint8,
    )
    source_patch = np.pad(source_patch, edge_padding, mode="edge")
    padded_source = Image.fromarray(source_patch, "L")

    target_rotation = _rotation_to_canonical(_read_uv_rotation(target_face))
    source_rotation = _inverse_rotation(_read_uv_rotation(source_face))
    canonical_to_source = _compose_uv_transforms(target_rotation, source_rotation)
    a, b, c, d, e, f = canonical_to_source

    normalized_x = (left - target_u) / target_width
    normalized_y = (top - target_v) / target_height
    # PIL affine coordinates address texel edges at half-integer pixel centers.
    coefficients = (
        source_width * a / target_width,
        source_width * b / target_height,
        source_u + source_width * (a * normalized_x + b * normalized_y + c) + edge_padding - source_left,
        source_height * d / target_width,
        source_height * e / target_height,
        source_v + source_height * (d * normalized_x + e * normalized_y + f) + edge_padding - source_top,
    )
    patch = padded_source.transform(
        (right - left, bottom - top),
        Image.Transform.AFFINE,
        coefficients,
        resample=Image.Resampling.BICUBIC,
    )
    mask = np.zeros((texture_height, texture_width), dtype=bool)
    xs = np.arange(left, right, dtype=np.float64) + 0.5
    ys = np.arange(top, bottom, dtype=np.float64) + 0.5
    s = (xs - target_u) / target_width
    t = (ys - target_v) / target_height
    mask[top:bottom, left:right] = (t[:, None] >= 0) & (t[:, None] <= 1) & (s[None, :] >= 0) & (s[None, :] <= 1)
    return patch, (left, top, right, bottom), mask


def transfer_mers_by_model(
    target_albedo_path: Path,
    target_mer_source: Path | Image.Image,
    source_mers_path: Path,
    source_model_path: Path,
    target_model_path: Path,
    overlap_policy: str = "reject",
):
    if overlap_policy not in ("reject", "average"):
        raise ValueError("overlap_policy must be 'reject' or 'average'.")

    source_width, source_height, source_cubes = _texture_geometry(source_model_path)
    target_model_width, target_model_height, target_cubes = _texture_geometry(target_model_path)

    with Image.open(source_mers_path) as image:
        source_mers = image.convert("RGBA")
    with Image.open(target_albedo_path) as image:
        target_albedo = image.convert("RGBA")
    if isinstance(target_mer_source, Image.Image):
        target_mer = target_mer_source.convert("RGB")
    else:
        with Image.open(target_mer_source) as image:
            target_mer = image.convert("RGB")

    if target_albedo.size != target_mer.size:
        raise ValueError(
            "Target albedo and target MER dimensions must match "
            f"({target_albedo.width}x{target_albedo.height} vs {target_mer.width}x{target_mer.height})."
        )
    target_width, target_height = target_mer.size
    source_scale_x = source_mers.width / source_width
    source_scale_y = source_mers.height / source_height
    target_scale_x = target_mer.width / target_model_width
    target_scale_y = target_mer.height / target_model_height

    source_by_signature: dict[tuple, list[dict[str, UVFace]]] = defaultdict(list)
    target_by_signature: dict[tuple, list[dict[str, UVFace]]] = defaultdict(list)
    for signature, _cube_index, faces in source_cubes:
        source_by_signature[signature].append(faces)
    for signature, _cube_index, faces in target_cubes:
        target_by_signature[signature].append(faces)

    shared = source_by_signature.keys() & target_by_signature.keys()
    if not shared:
        raise ValueError("The source and target models have no cuboids with matching position, size, and rotation.")
    if source_by_signature.keys() != target_by_signature.keys():
        raise ValueError("The source and target models contain unmatched cuboids; refusing a partial UV transfer.")
    for signature in shared:
        if len(source_by_signature[signature]) != 1 or len(target_by_signature[signature]) != 1:
            raise ValueError("Duplicate cuboids make the source-to-target face mapping ambiguous.")
        for source_faces, target_faces in zip(source_by_signature[signature], target_by_signature[signature]):
            if source_faces.keys() != target_faces.keys():
                raise ValueError("A matching cuboid has different face definitions in the source and target models.")

    source_alpha = source_mers.getchannel("A")
    target_alpha = np.zeros((target_height, target_width), dtype=np.uint8)
    assigned = np.zeros((target_height, target_width), dtype=bool)
    if overlap_policy == "average":
        alpha_sums = np.zeros((target_height, target_width), dtype=np.uint32)
        alpha_counts = np.zeros((target_height, target_width), dtype=np.uint16)
    transferred_faces = 0
    for signature in shared:
        source_occurrences = source_by_signature[signature]
        target_occurrences = target_by_signature[signature]
        for source_faces, target_faces in zip(source_occurrences, target_occurrences):
            for face_name in source_faces:
                patch, bounds, mask = _uv_alpha_patch(
                    source_alpha,
                    _scale_uv_face(source_faces[face_name], source_scale_x, source_scale_y),
                    _scale_uv_face(target_faces[face_name], target_scale_x, target_scale_y),
                    target_mer.width,
                    target_mer.height,
                )
                left, top, right, bottom = bounds
                values = np.asarray(patch, dtype=np.uint8)
                region_mask = mask[top:bottom, left:right]
                region_assigned = assigned[top:bottom, left:right]
                region_alpha = target_alpha[top:bottom, left:right]
                if overlap_policy == "reject":
                    conflict = region_mask & region_assigned & (region_alpha != values)
                    if np.any(conflict):
                        raise ValueError("Overlapping target UV faces map different subsurface values to the same texels.")
                    region_alpha[region_mask] = values[region_mask]
                else:
                    region_sums = alpha_sums[top:bottom, left:right]
                    region_counts = alpha_counts[top:bottom, left:right]
                    region_sums[region_mask] += values[region_mask].astype(np.uint32)
                    region_counts[region_mask] += 1
                region_assigned[region_mask] = True
                transferred_faces += 1
    if transferred_faces == 0:
        raise ValueError("The source and target models have no matching cuboid faces.")
    if overlap_policy == "average":
        target_alpha[assigned] = (
            (alpha_sums[assigned] + alpha_counts[assigned] // 2) // alpha_counts[assigned]
        ).astype(np.uint8)

    target_pixels = np.asarray(target_mer, dtype=np.uint8)
    target_pixels = np.dstack((target_pixels, target_alpha))
    return Image.fromarray(target_pixels, "RGBA"), transferred_faces


def build_normal_source(source_path: Path, normal_path: Path) -> NormalSource:
    with Image.open(source_path) as source_image, Image.open(normal_path) as normal_image:
        if source_image.size != normal_image.size:
            raise ValueError(
                "Source albedo and normal dimensions must match "
                f"({source_image.width}x{source_image.height} vs {normal_image.width}x{normal_image.height})."
            )
        source_albedo = np.asarray(source_image.convert("RGBA").convert("RGB"), dtype=np.float32) / 255.0
        source_normal = np.asarray(normal_image.convert("RGBA"), dtype=np.uint8)
    return NormalSource(source_albedo, source_normal)


def texture_descriptors(albedo: np.ndarray, radius: int, method: str) -> np.ndarray:
    height, width, _ = albedo.shape
    gray = 0.2126 * albedo[:, :, 0] + 0.7152 * albedo[:, :, 1] + 0.0722 * albedo[:, :, 2]

    if method == "Edge gradients":
        dx = (np.roll(gray, -1, axis=1) - np.roll(gray, 1, axis=1)) * 0.5
        dy = (np.roll(gray, -1, axis=0) - np.roll(gray, 1, axis=0)) * 0.5
        channels = np.stack((dx, dy), axis=2)
    else:
        channels = gray[:, :, None]

    patches = []
    for offset_y in range(-radius, radius + 1):
        for offset_x in range(-radius, radius + 1):
            patches.append(np.roll(np.roll(channels, offset_y, axis=0), offset_x, axis=1))
    descriptors = np.stack(patches, axis=2).reshape(height * width, -1)

    if method in ("Normalized pattern", "Edge gradients"):
        descriptors -= descriptors.mean(axis=1, keepdims=True)
        scale = np.sqrt(np.mean(descriptors * descriptors, axis=1, keepdims=True))
        descriptors /= np.maximum(scale, 1e-4)
    return descriptors.astype(np.float32, copy=False)


def transfer_normal(target_path: Path, source: NormalSource, method: str, tolerance: float, radius: int):
    with Image.open(target_path) as image:
        target_image = image.convert("RGBA")
        target_pixels = np.asarray(target_image, dtype=np.uint8)
        target_albedo = np.asarray(target_image.convert("RGB"), dtype=np.float32) / 255.0

    target_height, target_width, _ = target_pixels.shape
    if method == "Copy source normal":
        if source.normal.shape[:2] != (target_height, target_width):
            raise ValueError("Copy source normal requires matching source and target dimensions.")
        output = source.normal.copy()
        output[:, :, 3] = target_pixels[:, :, 3]
        return Image.fromarray(output, "RGBA"), 0, 0.0

    source_descriptors = texture_descriptors(source.albedo, radius, method)
    target_descriptors = texture_descriptors(target_albedo, radius, method)
    source_normals = source.normal.reshape(-1, 4)
    source_norm_squared = np.sum(source_descriptors * source_descriptors, axis=1)
    target_norm_squared = np.sum(target_descriptors * target_descriptors, axis=1)
    output = np.zeros((len(target_descriptors), 4), dtype=np.uint8)
    output[:, 2] = 255
    output[:, 3] = target_pixels[:, :, 3].reshape(-1)
    matched = np.zeros(len(target_descriptors), dtype=bool)
    errors = []
    batch_size = max(1, min(32, 2_000_000 // max(1, len(source_descriptors))))

    for start in range(0, len(target_descriptors), batch_size):
        end = min(start + batch_size, len(target_descriptors))
        block = target_descriptors[start:end]
        distances_squared = (
            target_norm_squared[start:end, None]
            + source_norm_squared[None, :]
            - 2 * (block @ source_descriptors.T)
        )
        np.maximum(distances_squared, 0, out=distances_squared)
        distances = np.sqrt(distances_squared / source_descriptors.shape[1])
        best_indices = np.argmin(distances, axis=1)
        best_errors = distances[np.arange(end - start), best_indices]
        valid = best_errors <= tolerance
        target_indices = np.arange(start, end)[valid]
        output[target_indices, :3] = source_normals[best_indices[valid], :3]
        matched[target_indices] = True
        errors.extend(best_errors[valid].tolist())

    transparent = target_pixels[:, :, 3].reshape(-1) == 0
    output[transparent, :3] = (128, 128, 255)
    unmatched = int(np.count_nonzero(~matched & ~transparent))
    average_error = float(np.mean(errors)) if errors else 0.0
    result = Image.fromarray(output.reshape(target_height, target_width, 4), "RGBA")
    return result, unmatched, average_error


def write_texture_set(destination: Path, color_name: str, generated_maps: set[str]) -> None:
    texture_set = {"color": color_name}
    if "normal" in generated_maps:
        texture_set["normal"] = f"{color_name}_normal"
    if "mers" in generated_maps:
        texture_set["metalness_emissive_roughness_subsurface"] = f"{color_name}_mers"
    elif "mer" in generated_maps:
        texture_set["metalness_emissive_roughness"] = f"{color_name}_mer"

    document = {
        "format_version": "1.21.30",
        "minecraft:texture_set": texture_set,
    }
    with destination.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(document, file, ensure_ascii=False, indent=2)
        file.write("\n")


class TexturePBRApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Texture PBR Map Generator")
        self.geometry("900x920")
        self.minsize(760, 760)

        self.source_albedo = tk.StringVar()
        self.source_mer = tk.StringVar()
        self.source_mers = tk.StringVar()
        self.source_model = tk.StringVar()
        self.target_model = tk.StringVar()
        self.source_normal = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.enable_mer = tk.BooleanVar(value=True)
        self.enable_mers = tk.BooleanVar(value=False)
        self.enable_normal = tk.BooleanVar(value=True)
        self.mer_tolerance = tk.DoubleVar(value=0)
        self.mers_method = tk.StringVar(value="Albedo pattern matching")
        self.uv_overlap_policy = tk.StringVar(value="Reject conflicting faces")
        self.normal_method = tk.StringVar(value="Normalized pattern")
        self.normal_tolerance = tk.DoubleVar(value=1.5)
        self.patch_radius = tk.IntVar(value=1)
        self.overwrite = tk.BooleanVar(value=False)
        self.targets: list[Path] = []
        self._build_ui()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(7, weight=1)

        ttk.Label(outer, text="Texture PBR Map Generator", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 12)
        )

        mode_frame = ttk.LabelFrame(outer, text="Generate", padding=8)
        mode_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        ttk.Checkbutton(mode_frame, text="MER", variable=self.enable_mer, command=self._update_enabled).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(mode_frame, text="MERS (subsurface)", variable=self.enable_mers, command=self._update_enabled).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(mode_frame, text="Normal", variable=self.enable_normal, command=self._update_enabled).pack(side="left")

        source_frame = ttk.LabelFrame(outer, text="Source textures", padding=10)
        source_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        source_frame.columnconfigure(1, weight=1)
        self._source_albedo_widgets = self._file_row(source_frame, 0, "Source albedo", self.source_albedo, self._browse_source_albedo)
        self.mer_source_widgets = self._file_row(source_frame, 1, "Source MER", self.source_mer, self._browse_source_mer)
        self.mers_source_widgets = self._file_row(source_frame, 2, "Source MERS", self.source_mers, self._browse_source_mers)
        self.source_model_widgets = self._file_row(source_frame, 3, "Source model JSON", self.source_model, self._browse_source_model)
        self.target_model_widgets = self._file_row(source_frame, 4, "Target model JSON", self.target_model, self._browse_target_model)
        self.normal_source_widgets = self._file_row(source_frame, 5, "Source normal", self.source_normal, self._browse_source_normal)
        ttk.Label(source_frame, text="Overlapping model UVs").grid(row=6, column=0, sticky="w", padx=(0, 10), pady=4)
        self.uv_overlap_box = ttk.Combobox(
            source_frame,
            textvariable=self.uv_overlap_policy,
            values=("Reject conflicting faces", "Average subsurface alpha"),
            state="readonly",
        )
        self.uv_overlap_box.grid(row=6, column=1, sticky="ew", pady=4)

        target_frame = ttk.LabelFrame(outer, text="Target albedos", padding=10)
        target_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 10))
        target_frame.columnconfigure(0, weight=1)
        target_frame.rowconfigure(0, weight=1)
        self.target_list = tk.Listbox(target_frame, selectmode=tk.EXTENDED, height=7, exportselection=False)
        self.target_list.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(0, 10))
        target_scroll = ttk.Scrollbar(target_frame, orient="vertical", command=self.target_list.yview)
        target_scroll.grid(row=0, column=1, rowspan=3, sticky="ns", padx=(0, 10))
        self.target_list.configure(yscrollcommand=target_scroll.set)
        ttk.Button(target_frame, text="Add PNGs...", command=self._add_targets).grid(row=0, column=2, sticky="ew", pady=(0, 6))
        ttk.Button(target_frame, text="Remove selected", command=self._remove_targets).grid(row=1, column=2, sticky="ew", pady=3)
        ttk.Button(target_frame, text="Clear list", command=self._clear_targets).grid(row=2, column=2, sticky="ew", pady=(6, 0))

        settings_frame = ttk.Frame(outer)
        settings_frame.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        settings_frame.columnconfigure(0, weight=1)
        settings_frame.columnconfigure(1, weight=1)
        self.mer_frame = ttk.LabelFrame(settings_frame, text="MER palette matching", padding=10)
        self.mer_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.normal_frame = ttk.LabelFrame(settings_frame, text="MERS and normal detail transfer", padding=10)
        self.normal_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        ttk.Label(self.mer_frame, text="Max color distance (Delta E76)").grid(row=0, column=0, sticky="w")
        self.mer_scale = ttk.Scale(self.mer_frame, from_=0, to=50, variable=self.mer_tolerance, command=self._update_mer_label)
        self.mer_scale.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.mer_frame.columnconfigure(0, weight=1)
        self.mer_label = ttk.Label(self.mer_frame, text="0 (exact)")
        self.mer_label.grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Label(self.normal_frame, text="MERS transfer method").grid(row=0, column=0, sticky="w")
        self.mers_method_box = ttk.Combobox(
            self.normal_frame,
            textvariable=self.mers_method,
            values=MERS_METHODS,
            state="readonly",
        )
        self.mers_method_box.grid(row=1, column=0, sticky="ew", pady=(4, 6))
        self.mers_method_box.bind("<<ComboboxSelected>>", lambda _event: self._update_enabled())
        ttk.Label(self.normal_frame, text="Normal transfer method").grid(row=2, column=0, sticky="w")
        self.normal_method_box = ttk.Combobox(
            self.normal_frame,
            textvariable=self.normal_method,
            values=NORMAL_METHODS,
            state="readonly",
        )
        self.normal_method_box.grid(row=3, column=0, sticky="ew", pady=(4, 6))
        self.normal_method_box.bind("<<ComboboxSelected>>", lambda _event: self._update_enabled())
        ttk.Label(self.normal_frame, text="Max pattern-match error").grid(row=4, column=0, sticky="w")
        self.normal_scale = ttk.Scale(self.normal_frame, from_=0, to=2, variable=self.normal_tolerance, command=self._update_normal_label)
        self.normal_scale.grid(row=5, column=0, sticky="ew", pady=(4, 0))
        self.normal_frame.columnconfigure(0, weight=1)
        self.normal_label = ttk.Label(self.normal_frame, text="1.50")
        self.normal_label.grid(row=6, column=0, sticky="w", pady=(4, 6))
        ttk.Label(self.normal_frame, text="Patch radius").grid(row=7, column=0, sticky="w")
        self.radius_spin = ttk.Spinbox(self.normal_frame, from_=1, to=4, textvariable=self.patch_radius, width=6)
        self.radius_spin.grid(row=8, column=0, sticky="w", pady=(4, 0))

        output_frame = ttk.LabelFrame(outer, text="Output", padding=10)
        output_frame.grid(row=5, column=0, sticky="ew", pady=(0, 10))
        output_frame.columnconfigure(1, weight=1)
        self._file_row(output_frame, 0, "Output folder", self.output_dir, self._browse_output, folder=True)
        ttk.Checkbutton(output_frame, text="Overwrite existing maps", variable=self.overwrite).grid(row=1, column=1, sticky="w")

        action_frame = ttk.Frame(outer)
        action_frame.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        self.run_button = ttk.Button(action_frame, text="Generate selected maps", command=self._generate)
        self.run_button.pack(side="left")
        self.progress = ttk.Progressbar(action_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

        log_frame = ttk.LabelFrame(outer, text="Results", padding=8)
        log_frame.grid(row=7, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=log_scroll.set)
        self._update_enabled()

    def _file_row(self, parent, row: int, label: str, variable: tk.StringVar, command, folder: bool = False):
        text_label = ttk.Label(parent, text=label)
        text_label.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        button = ttk.Button(parent, text="Browse...", command=command)
        button.grid(row=row, column=2, sticky="e", padx=(8, 0), pady=4)
        return text_label, entry, button

    def _update_enabled(self) -> None:
        source_state = "normal" if self.enable_mer.get() or self.enable_mers.get() or self.enable_normal.get() else "disabled"
        for widget in self._source_albedo_widgets:
            widget.configure(state=source_state)
        mer_state = "normal" if self.enable_mer.get() else "disabled"
        mers_state = "normal" if self.enable_mers.get() else "disabled"
        normal_state = "normal" if self.enable_normal.get() else "disabled"
        for widget in self.mer_source_widgets:
            widget.configure(state=mer_state)
        for widget in self.mers_source_widgets:
            widget.configure(state=mers_state)
        model_state = "normal" if (
            self.enable_mers.get() and self.mers_method.get() == "Model UV remap (merge target MER)"
        ) else "disabled"
        for widget in (*self.source_model_widgets, *self.target_model_widgets):
            widget.configure(state=model_state)
        self.uv_overlap_box.configure(state="readonly" if model_state == "normal" else "disabled")
        for widget in self.normal_source_widgets:
            widget.configure(state=normal_state)
        self.mers_method_box.configure(state="readonly" if self.enable_mers.get() else "disabled")
        for widget in (self.mer_scale,):
            widget.configure(state=mer_state)
        self.mer_label.configure(state=mer_state)
        self.normal_method_box.configure(state="readonly" if self.enable_normal.get() else "disabled")
        descriptor_state = "normal" if (
            self.enable_mers.get() and self.mers_method.get() == "Albedo pattern matching"
        ) or (
            self.enable_normal.get() and self.normal_method.get() != "Copy source normal"
        ) else "disabled"
        for widget in (self.normal_scale, self.radius_spin):
            widget.configure(state=descriptor_state)
        self.normal_label.configure(state=descriptor_state)
        self.mer_frame.configure(labelanchor="nw")
        self.normal_frame.configure(labelanchor="nw")

    def _update_mer_label(self, _value: str = "") -> None:
        value = self.mer_tolerance.get()
        self.mer_label.configure(text="0 (exact)" if value == 0 else f"{value:.0f} Delta E")

    def _update_normal_label(self, _value: str = "") -> None:
        self.normal_label.configure(text=f"{self.normal_tolerance.get():.2f}")

    def _browse_source_albedo(self) -> None:
        path = filedialog.askopenfilename(title="Choose shared source albedo", filetypes=[("PNG images", "*.png"), ("All files", "*.*")])
        if path:
            self.source_albedo.set(path)

    def _browse_source_mer(self) -> None:
        path = filedialog.askopenfilename(title="Choose source MER map", filetypes=[("PNG images", "*.png"), ("All files", "*.*")])
        if path:
            self.source_mer.set(path)

    def _browse_source_mers(self) -> None:
        path = filedialog.askopenfilename(title="Choose source MERS map", filetypes=[("PNG images", "*.png"), ("All files", "*.*")])
        if path:
            self.source_mers.set(path)

    def _browse_source_model(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose model JSON for source MERS UVs",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.source_model.set(path)

    def _browse_target_model(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose model JSON for target UVs",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.target_model.set(path)

    def _browse_source_normal(self) -> None:
        path = filedialog.askopenfilename(title="Choose source normal map", filetypes=[("PNG images", "*.png"), ("All files", "*.*")])
        if path:
            self.source_normal.set(path)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.output_dir.set(path)

    def _add_targets(self) -> None:
        paths = filedialog.askopenfilenames(title="Choose target albedo textures", filetypes=[("PNG images", "*.png"), ("All files", "*.*")])
        known = {path.resolve() for path in self.targets}
        for path in paths:
            item = Path(path)
            if item.resolve() not in known:
                self.targets.append(item)
                known.add(item.resolve())
                self.target_list.insert("end", str(item))

    def _remove_targets(self) -> None:
        for index in reversed(self.target_list.curselection()):
            self.target_list.delete(index)
            del self.targets[index]

    def _clear_targets(self) -> None:
        self.targets.clear()
        self.target_list.delete(0, "end")

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _generate(self) -> None:
        enabled = []
        if self.enable_mer.get():
            enabled.append("mer")
        if self.enable_mers.get():
            enabled.append("mers")
        if self.enable_normal.get():
            enabled.append("normal")
        if not enabled:
            messagebox.showerror("No maps selected", "Enable MER, MERS, Normal, or any combination.")
            return
        source_albedo = Path(self.source_albedo.get())
        output_dir = Path(self.output_dir.get())
        model_mers = self.mers_method.get() == "Model UV remap (merge target MER)"
        needs_source_albedo = (
            self.enable_mer.get()
            or self.enable_normal.get()
            or (self.enable_mers.get() and not model_mers)
        )
        if needs_source_albedo and not source_albedo.is_file():
            messagebox.showerror("Missing source", "Choose the source albedo texture.")
            return
        if not self.targets:
            messagebox.showerror("No targets", "Add one or more target albedo textures.")
            return
        if not output_dir.is_dir():
            messagebox.showerror("Missing output folder", "Choose an existing output folder.")
            return

        mer_palette = None
        mers_source = None
        normal_source = None
        mers_path = None
        source_model_path = None
        target_model_path = None
        if self.enable_mer.get():
            mer_path = Path(self.source_mer.get())
            if not mer_path.is_file():
                messagebox.showerror("Missing MER source", "Choose the source MER map.")
                return
            try:
                mer_palette = build_mer_palette(source_albedo, mer_path)
            except Exception as error:
                messagebox.showerror("Could not load source MER pair", str(error))
                return
        if self.enable_mers.get():
            mers_path = Path(self.source_mers.get())
            if not mers_path.is_file():
                messagebox.showerror("Missing MERS source", "Choose the source MERS map.")
                return
            if model_mers:
                source_model_path = Path(self.source_model.get())
                target_model_path = Path(self.target_model.get())
                if not source_model_path.is_file() or not target_model_path.is_file():
                    messagebox.showerror("Missing model", "Choose both source and target model JSON files.")
                    return
                try:
                    _texture_geometry(source_model_path)
                    _texture_geometry(target_model_path)
                except Exception as error:
                    messagebox.showerror("Could not load model UVs", str(error))
                    return
                if not self.enable_mer.get():
                    missing_mer = [
                        output_dir / f"{target.stem}_mer.png"
                        for target in self.targets
                        if not (output_dir / f"{target.stem}_mer.png").is_file()
                    ]
                    if missing_mer:
                        messagebox.showerror(
                            "Missing target MER maps",
                            f"{len(missing_mer)} target MER map(s) are missing from the output folder. "
                            "Generate MER in this run or put each target's <name>_mer.png there first.",
                        )
                        return
            else:
                try:
                    mers_source = build_mers_source(source_albedo, mers_path)
                except Exception as error:
                    messagebox.showerror("Could not load source MERS pair", str(error))
                    return
        if self.enable_normal.get():
            normal_path = Path(self.source_normal.get())
            if not normal_path.is_file():
                messagebox.showerror("Missing normal source", "Choose the source normal map.")
                return
            try:
                normal_source = build_normal_source(source_albedo, normal_path)
            except Exception as error:
                messagebox.showerror("Could not load source normal pair", str(error))
                return

        if mer_palette and mer_palette.ambiguous_colors:
            if not messagebox.askyesno(
                "Ambiguous MER source colors",
                f"{mer_palette.ambiguous_colors} source albedo colors have multiple MER values. "
                "The most common MER value will be used. Continue?",
            ):
                return

        planned_outputs = []
        for target in self.targets:
            if self.enable_mer.get():
                planned_outputs.append(output_dir / f"{target.stem}_mer.png")
            if self.enable_mers.get():
                planned_outputs.append(output_dir / f"{target.stem}_mers.png")
            if self.enable_normal.get():
                planned_outputs.append(output_dir / f"{target.stem}_normal.png")
            planned_outputs.append(output_dir / f"{target.stem}.texture_set.json")
        existing = [destination for destination in planned_outputs if destination.exists()]
        if existing and not self.overwrite.get():
            messagebox.showerror(
                "Output files exist",
                f"{len(existing)} output file(s) already exist. Enable overwrite or choose another folder.",
            )
            return
        if existing and self.overwrite.get() and not messagebox.askyesno("Confirm overwrite", f"Replace {len(existing)} existing file(s)?"):
            return

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._append_log("Enabled maps: " + ", ".join(enabled))
        if self.enable_mer.get() and self.enable_mers.get():
            self._append_log("MERS is referenced by a texture set if it is successfully generated alongside MER.")
        if self.enable_mers.get() and model_mers:
            self._append_log("Model UV mode remaps source subsurface alpha and preserves target MER RGB.")
        self.progress.configure(maximum=len(self.targets), value=0)
        self.run_button.configure(state="disabled")
        created = 0
        skipped = 0
        texture_sets_created = 0
        try:
            for target_index, target in enumerate(self.targets, start=1):
                self.progress.configure(value=target_index - 1)
                self.update_idletasks()
                generated_maps = set()
                target_mer_result = None
                if self.enable_mer.get():
                    try:
                        result, unmatched = transfer_mer(target, mer_palette, self.mer_tolerance.get())
                        if unmatched:
                            self._append_log(f"SKIP {target.name} MER: {unmatched} unmatched pixel(s)")
                            skipped += 1
                        else:
                            destination = output_dir / f"{target.stem}_mer.png"
                            result.save(destination, format="PNG")
                            target_mer_result = result
                            generated_maps.add("mer")
                            self._append_log(f"OK   {target.name} -> {destination.name}")
                            created += 1
                    except Exception as error:
                        self._append_log(f"ERROR {target.name} MER: {error}")
                        skipped += 1

                if self.enable_mers.get():
                    try:
                        if model_mers:
                            target_mer_source = target_mer_result
                            if target_mer_source is None:
                                if self.enable_mer.get():
                                    raise ValueError("The target MER could not be generated for this texture.")
                                target_mer_source = output_dir / f"{target.stem}_mer.png"
                            result, transferred_faces = transfer_mers_by_model(
                                target,
                                target_mer_source,
                                mers_path,
                                source_model_path,
                                target_model_path,
                                overlap_policy=(
                                    "average"
                                    if self.uv_overlap_policy.get() == "Average subsurface alpha"
                                    else "reject"
                                ),
                            )
                            unmatched, error = 0, None
                        elif self.mers_method.get() == "Copy source (matching layout)":
                            result = copy_mers_layout(target, mers_source)
                            unmatched, error = 0, None
                        else:
                            result, unmatched, error = transfer_mers(
                                target, mers_source, "Normalized pattern", self.normal_tolerance.get(), int(self.patch_radius.get())
                            )
                        if unmatched:
                            self._append_log(f"SKIP {target.name} MERS: {unmatched} unmatched pixel(s)")
                            skipped += 1
                        else:
                            destination = output_dir / f"{target.stem}_mers.png"
                            result.save(destination, format="PNG")
                            generated_maps.add("mers")
                            if model_mers:
                                detail = f" (transferred {transferred_faces} model face(s); target MER RGB preserved)"
                            else:
                                detail = " (copied by matching UV position)" if error is None else f" (mean descriptor error {error:.3f})"
                            self._append_log(f"OK   {target.name} -> {destination.name}{detail}")
                            created += 1
                    except Exception as error:
                        self._append_log(f"ERROR {target.name} MERS: {error}")
                        skipped += 1

                if self.enable_normal.get():
                    try:
                        result, unmatched, error = transfer_normal(
                            target, normal_source, self.normal_method.get(), self.normal_tolerance.get(), int(self.patch_radius.get())
                        )
                        if unmatched:
                            self._append_log(f"SKIP {target.name} Normal: {unmatched} unmatched pixel(s)")
                            skipped += 1
                        else:
                            destination = output_dir / f"{target.stem}_normal.png"
                            result.save(destination, format="PNG")
                            generated_maps.add("normal")
                            self._append_log(
                                f"OK   {target.name} -> {destination.name} "
                                f"(mean descriptor error {error:.3f})"
                            )
                            created += 1
                    except Exception as error:
                        self._append_log(f"ERROR {target.name} Normal: {error}")
                        skipped += 1

                if generated_maps:
                    texture_set_path = output_dir / f"{target.stem}.texture_set.json"
                    try:
                        write_texture_set(texture_set_path, target.stem, generated_maps)
                        texture_sets_created += 1
                        self._append_log(f"SET  {target.name} -> {texture_set_path.name}")
                    except Exception as error:
                        self._append_log(f"ERROR {target.name} texture set: {error}")
                        skipped += 1

                self.progress.configure(value=target_index)
                self.update_idletasks()
        finally:
            self.run_button.configure(state="normal")
        self._append_log(
            f"Finished: {created} map(s), {texture_sets_created} texture set(s), {skipped} skipped/error(s)."
        )


def main() -> None:
    app = TexturePBRApp()
    app.mainloop()


if __name__ == "__main__":
    main()
