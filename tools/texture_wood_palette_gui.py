#!/usr/bin/env python3
"""Batch recolor wood templates using corresponding source/destination pixels."""

from collections import Counter, defaultdict
from dataclasses import dataclass
import os
from pathlib import Path
import re
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image


@dataclass
class WoodPalette:
    mapping: dict
    ambiguous: int


def wood_name(path):
    name = Path(path).stem
    for prefix in ("planks_", "wood_"):
        if name.startswith(prefix):
            return name[len(prefix):]
    if name.endswith("_planks"):
        return name[:-7]
    return name


def image_pixels(image):
    if hasattr(image, "get_flattened_data"):
        return image.get_flattened_data()
    return image.getdata()


def build_palette(source, destination):
    with Image.open(source) as image:
        source_image = image.convert("RGBA")
    with Image.open(destination) as image:
        destination_image = image.convert("RGBA")
    if source_image.size != destination_image.size:
        raise ValueError("Palette images must have identical dimensions and matching layouts.")
    samples = defaultdict(Counter)
    for source_pixel, destination_pixel in zip(image_pixels(source_image), image_pixels(destination_image)):
        if source_pixel[3] and destination_pixel[3]:
            samples[source_pixel[:3]][destination_pixel[:3]] += 1
    if not samples:
        raise ValueError("Palette pair has no overlapping visible pixels.")
    return WoodPalette(
        {color: counts.most_common(1)[0][0] for color, counts in samples.items()},
        sum(len(counts) > 1 for counts in samples.values()),
    )


def recolor(path, palette, tolerance):
    with Image.open(path) as image:
        target = image.convert("RGBA")
    lookup = {}
    output = []
    unmatched = 0
    for pixel in image_pixels(target):
        if pixel[3] == 0:
            output.append(pixel)
            continue
        color = pixel[:3]
        if color not in lookup:
            replacement = palette.mapping.get(color)
            if replacement is None and tolerance > 0:
                nearest = min(palette.mapping, key=lambda candidate: sum(
                    (color[channel] - candidate[channel]) ** 2 for channel in range(3)
                ))
                distance = (sum((color[channel] - nearest[channel]) ** 2 for channel in range(3)) / 3) ** 0.5
                if distance <= tolerance:
                    replacement = palette.mapping[nearest]
            lookup[color] = replacement
        replacement = lookup[color]
        if replacement is None:
            unmatched += 1
            replacement = color
        output.append((*replacement, pixel[3]))
    result = Image.new("RGBA", target.size)
    result.putdata(output)
    return result, unmatched


def output_name(path, source_name, destination_name):
    stem = path.stem
    aliases = {source_name}
    if source_name in ("dark_oak", "big_oak"):
        aliases.update(("dark_oak", "big_oak"))
    guard = r"(?<!dark_)(?<!pale_)(?<!big_)" if source_name == "oak" else ""
    pattern = guard + r"(?<![a-z0-9])(?:" + "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)) + r")(?![a-z0-9])"
    match = re.search(pattern, stem, re.IGNORECASE)
    if not match:
        raise ValueError(f"{path.name}: source wood name '{source_name}' not found.")
    plain_button = stem.lower() in aliases
    destination = destination_name
    if destination in ("big_oak", "dark_oak"):
        destination = "big_oak" if plain_button else "dark_oak"
    if stem.lower() == "planks_oak":
        return "planks_oak.png" if destination == "oak" else f"{destination}_gate.png"
    result = re.sub(pattern, lambda _match: destination, stem, flags=re.IGNORECASE)
    if result.lower() == "oak_gate":
        return "planks_oak.png"
    return result + ".png"


class WoodPaletteApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Wood Palette Recolor")
        self.geometry("880x780")
        self.minsize(760, 700)
        self.source = tk.StringVar()
        self.source_name = tk.StringVar(value="oak")
        self.output = tk.StringVar()
        self.tolerance = tk.DoubleVar(value=0)
        self.keep_unmatched = tk.BooleanVar(value=True)
        self.overwrite = tk.BooleanVar(value=False)
        self.palettes = []
        self.templates = []
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(10, weight=1)
        self.file_row(frame, 0, "Source wood/planks", self.source, self.browse_source)
        ttk.Label(frame, text="Source wood name").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.source_name).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text="Destination palettes").grid(row=2, column=0, sticky="nw", pady=5)
        self.palette_list = ttk.Treeview(frame, columns=("wood", "path"), show="headings", height=5)
        self.palette_list.heading("wood", text="Output wood name")
        self.palette_list.heading("path", text="Palette image")
        self.palette_list.column("wood", width=140, stretch=False)
        self.palette_list.column("path", width=360)
        self.palette_list.grid(row=2, column=1, sticky="ew", pady=5)
        controls = ttk.Frame(frame)
        controls.grid(row=2, column=2, sticky="n", padx=6)
        for text, command in (("Add...", self.add_palettes), ("Edit name...", self.edit_name), ("Remove", self.remove_palettes)):
            ttk.Button(controls, text=text, command=command).pack(fill="x", pady=2)
        ttk.Label(frame, text="Source-wood templates").grid(row=3, column=0, sticky="nw", pady=5)
        self.template_list = tk.Listbox(frame, height=7, selectmode=tk.EXTENDED, exportselection=False)
        self.template_list.grid(row=3, column=1, sticky="ew", pady=5)
        controls = ttk.Frame(frame)
        controls.grid(row=3, column=2, sticky="n", padx=6)
        ttk.Button(controls, text="Add PNGs...", command=self.add_templates).pack(fill="x", pady=2)
        ttk.Button(controls, text="Remove", command=self.remove_templates).pack(fill="x", pady=2)
        ttk.Label(frame, text="RGB RMS tolerance").grid(row=4, column=0, sticky="w")
        ttk.Scale(frame, from_=0, to=30, variable=self.tolerance).grid(row=4, column=1, sticky="ew", pady=6)
        ttk.Label(frame, textvariable=self.tolerance, width=10).grid(row=4, column=2)
        self.file_row(frame, 5, "Output folder", self.output, self.browse_output)
        ttk.Checkbutton(frame, text="Keep unmatched colors", variable=self.keep_unmatched).grid(row=6, column=1, sticky="w")
        ttk.Checkbutton(frame, text="Overwrite existing PNGs", variable=self.overwrite).grid(row=7, column=1, sticky="w")
        actions = ttk.Frame(frame)
        actions.grid(row=8, column=1, sticky="w", pady=10)
        ttk.Button(actions, text="Analyze batch", command=lambda: self.run(False)).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Recolor batch", command=lambda: self.run(True)).pack(side="left")
        self.log = tk.Text(frame, height=9, wrap="word", state="disabled")
        self.log.grid(row=10, column=0, columnspan=3, sticky="nsew")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def file_row(self, frame, row, text, variable, command):
        ttk.Label(frame, text=text).grid(row=row, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(frame, text="Browse...", command=command).grid(row=row, column=2, padx=6)

    def browse_source(self):
        path = filedialog.askopenfilename(filetypes=[("PNG images", "*.png")])
        if path:
            self.source.set(path)
            self.source_name.set(wood_name(path))

    def browse_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output.set(path)

    def add_palettes(self):
        for filename in filedialog.askopenfilenames(filetypes=[("PNG images", "*.png")]):
            path = Path(filename)
            if path not in [item[1] for item in self.palettes]:
                self.palettes.append((wood_name(path), path))
        self.refresh_palettes()

    def refresh_palettes(self):
        self.palette_list.delete(*self.palette_list.get_children())
        for index, (name, path) in enumerate(self.palettes):
            self.palette_list.insert("", "end", iid=str(index), values=(name, str(path)))

    def edit_name(self):
        selected = self.palette_list.selection()
        if selected:
            index = int(selected[0])
            name = simpledialog.askstring("Output wood name", "Wood name:", initialvalue=self.palettes[index][0])
            if name:
                self.palettes[index] = (name.strip(), self.palettes[index][1])
                self.refresh_palettes()

    def remove_palettes(self):
        for index in sorted((int(item) for item in self.palette_list.selection()), reverse=True):
            del self.palettes[index]
        self.refresh_palettes()

    def add_templates(self):
        for filename in filedialog.askopenfilenames(filetypes=[("PNG images", "*.png")]):
            path = Path(filename)
            if path not in self.templates:
                self.templates.append(path)
                self.template_list.insert("end", str(path))

    def remove_templates(self):
        for index in reversed(self.template_list.curselection()):
            del self.templates[index]
            self.template_list.delete(index)

    def report(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.update_idletasks()

    def run(self, write):
        try:
            if not Path(self.source.get()).is_file() or not self.palettes or not self.templates:
                raise ValueError("Select source, destination palettes, and templates.")
            if write and not Path(self.output.get()).is_dir():
                raise ValueError("Choose an existing output folder.")
            source_name = self.source_name.get().strip()
            for name in [source_name, *(item[0] for item in self.palettes)]:
                if not re.fullmatch(r"[a-z0-9_]+", name):
                    raise ValueError("Wood names must contain lowercase letters, numbers, and underscores only.")
            base = Path(os.path.commonpath([str(path.parent) for path in self.templates]))
            jobs = []
            destinations = set()
            for name, palette_path in self.palettes:
                palette = build_palette(Path(self.source.get()), palette_path)
                self.report(f"{name}: {len(palette.mapping)} colors, {palette.ambiguous} ambiguous")
                if write and palette.ambiguous and not messagebox.askyesno(
                    "Ambiguous palette", f"{name}: {palette.ambiguous} source colors have multiple replacements. Use the most common replacement?"
                ):
                    return
                for template in self.templates:
                    relative = template.parent.relative_to(base) / output_name(template, source_name, name)
                    destination = Path(self.output.get()) / relative
                    key = str(destination.resolve()).casefold()
                    if key in destinations:
                        raise ValueError(f"Output collision: {relative}")
                    destinations.add(key)
                    if destination.resolve() in {path.resolve() for path in [*self.templates, Path(self.source.get()), *(item[1] for item in self.palettes)]}:
                        raise ValueError(f"Output would overwrite an input: {destination}")
                    jobs.append((template, destination, palette))
            existing = [destination for _, destination, _ in jobs if destination.exists()]
            if write and existing:
                if not self.overwrite.get():
                    raise ValueError(f"{len(existing)} outputs exist; enable overwrite or choose another folder.")
                if not messagebox.askyesno("Overwrite", f"Replace {len(existing)} existing PNGs?"):
                    return
            for template, destination, palette in jobs:
                image, unmatched = recolor(template, palette, self.tolerance.get())
                status = "READY"
                if unmatched and not self.keep_unmatched.get():
                    status = "SKIP"
                elif write:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    image.save(destination, format="PNG")
                    status = "WROTE"
                self.report(f"{status} {template.name} -> {destination}; {unmatched} unmatched pixels")
            self.report("Batch finished.")
        except Exception as error:
            messagebox.showerror("Wood recolor", str(error))


if __name__ == "__main__":
    WoodPaletteApp().mainloop()