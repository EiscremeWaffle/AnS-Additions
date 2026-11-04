#!/usr/bin/env python3
"""Generate per-wood folders (color, MER, normal, texture_set.json) from one finished wood set."""

import json
from pathlib import Path
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from texture_wood_palette_gui import build_palette, recolor, wood_name

CHANNELS = ("color", "mer", "normal")
SUFFIXES = {"color": "", "mer": "_mer", "normal": "_normal"}
TEXTURE_SET_KEYS = {"color": "color", "mer": "metalness_emissive_roughness", "normal": "normal"}


def folder_name(path):
    name = wood_name(path)
    return "dark_oak" if name == "big_oak" else name


def plank_set(path):
    """Return {channel: Path or None} for a planks texture, using its texture_set.json."""
    path = Path(path)
    paths = {"color": path, "mer": None, "normal": None}
    texture_set = path.with_name(path.stem + ".texture_set.json")
    if texture_set.is_file():
        data = json.loads(texture_set.read_text(encoding="utf-8")).get("minecraft:texture_set", {})
        for channel in ("mer", "normal"):
            reference = data.get(TEXTURE_SET_KEYS[channel])
            # Texture sets may use a uniform color array instead of a texture name.
            if isinstance(reference, str) and path.with_name(reference + ".png").is_file():
                paths[channel] = path.with_name(reference + ".png")
    return paths


def find_templates(folder):
    templates = []
    for color in sorted(folder.rglob("*.png")):
        if not color.stem.endswith(("_mer", "_normal")):
            templates.append({channel: color.with_name(color.stem + SUFFIXES[channel] + ".png") for channel in CHANNELS})
    return templates


def texture_set_text(template):
    path = template["color"].with_name(template["color"].stem + ".texture_set.json")
    if path.is_file():
        return path.read_text(encoding="utf-8")
    data = {TEXTURE_SET_KEYS[channel]: template[channel].stem for channel in CHANNELS if template[channel].is_file()}
    return json.dumps({"format_version": "1.21.30", "minecraft:texture_set": data}, indent=2) + "\n"


class WoodDialog(tk.Toplevel):
    def __init__(self, parent, wood):
        super().__init__(parent)
        self.title("Destination wood")
        self.transient(parent)
        self.result = None
        self.values = {"name": tk.StringVar(value=wood["name"])}
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Folder / wood name").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.values["name"], width=60).grid(row=0, column=1, sticky="ew", pady=4)
        for row, channel in enumerate(CHANNELS, start=1):
            self.values[channel] = tk.StringVar(value=str(wood[channel] or ""))
            ttk.Label(frame, text=f"{channel} planks").grid(row=row, column=0, sticky="w")
            ttk.Entry(frame, textvariable=self.values[channel]).grid(row=row, column=1, sticky="ew", pady=4)
            ttk.Button(frame, text="Browse...", command=lambda c=channel: self.browse(c)).grid(row=row, column=2, padx=6)
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=1, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="OK", command=self.accept).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left")
        self.grab_set()

    def browse(self, channel):
        path = filedialog.askopenfilename(parent=self, filetypes=[("PNG images", "*.png")])
        if path:
            self.values[channel].set(path)

    def accept(self):
        self.result = {"name": self.values["name"].get().strip()}
        for channel in CHANNELS:
            value = self.values[channel].get().strip()
            self.result[channel] = Path(value) if value else None
        self.destroy()


class WoodSetApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Wood Texture Set Generator")
        self.geometry("980x820")
        self.minsize(820, 720)
        self.template_dir = tk.StringVar()
        self.source = {channel: tk.StringVar() for channel in CHANNELS}
        self.output = tk.StringVar()
        self.tolerance = tk.DoubleVar(value=0)
        self.keep_unmatched = tk.BooleanVar(value=True)
        self.overwrite = tk.BooleanVar(value=False)
        self.woods = []
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(12, weight=1)
        self.file_row(frame, 0, "Finished wood folder", self.template_dir, self.browse_template_dir)
        self.file_row(frame, 1, "Source planks (color)", self.source["color"], self.browse_source)
        self.file_row(frame, 2, "Source planks MER", self.source["mer"], lambda: self.browse_file(self.source["mer"]))
        self.file_row(frame, 3, "Source planks normal", self.source["normal"], lambda: self.browse_file(self.source["normal"]))
        ttk.Label(frame, text="Destination woods").grid(row=4, column=0, sticky="nw", pady=5)
        self.wood_list = ttk.Treeview(frame, columns=("name", *CHANNELS), show="headings", height=9)
        for column, width in (("name", 110), ("color", 170), ("mer", 150), ("normal", 170)):
            self.wood_list.heading(column, text=column)
            self.wood_list.column(column, width=width)
        self.wood_list.grid(row=4, column=1, sticky="ew", pady=5)
        self.wood_list.bind("<Double-1>", lambda _event: self.edit_wood())
        controls = ttk.Frame(frame)
        controls.grid(row=4, column=2, sticky="n", padx=6)
        for text, command in (("Add planks...", self.add_woods), ("Edit...", self.edit_wood), ("Remove", self.remove_woods)):
            ttk.Button(controls, text=text, command=command).pack(fill="x", pady=2)
        ttk.Label(frame, text="RGB RMS tolerance").grid(row=5, column=0, sticky="w")
        ttk.Scale(frame, from_=0, to=30, variable=self.tolerance).grid(row=5, column=1, sticky="ew", pady=6)
        ttk.Label(frame, textvariable=self.tolerance, width=10).grid(row=5, column=2)
        self.file_row(frame, 6, "Output folder", self.output, self.browse_output)
        ttk.Label(frame, text="Creates <output>/<wood name>/ for every destination wood.").grid(row=7, column=1, sticky="w")
        ttk.Checkbutton(frame, text="Keep unmatched colors", variable=self.keep_unmatched).grid(row=8, column=1, sticky="w")
        ttk.Checkbutton(frame, text="Overwrite existing files", variable=self.overwrite).grid(row=9, column=1, sticky="w")
        actions = ttk.Frame(frame)
        actions.grid(row=10, column=1, sticky="w", pady=10)
        ttk.Button(actions, text="Analyze", command=lambda: self.run(False)).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Generate", command=lambda: self.run(True)).pack(side="left")
        self.log = tk.Text(frame, height=12, wrap="word", state="disabled")
        self.log.grid(row=12, column=0, columnspan=3, sticky="nsew")

    def file_row(self, frame, row, text, variable, command):
        ttk.Label(frame, text=text).grid(row=row, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(frame, text="Browse...", command=command).grid(row=row, column=2, padx=6)

    def browse_file(self, variable):
        path = filedialog.askopenfilename(filetypes=[("PNG images", "*.png")])
        if path:
            variable.set(path)

    def browse_template_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.template_dir.set(path)
            if not self.output.get():
                self.output.set(str(Path(path).parent))

    def browse_source(self):
        path = filedialog.askopenfilename(filetypes=[("PNG images", "*.png")])
        if path:
            for channel, value in plank_set(path).items():
                self.source[channel].set(str(value or ""))

    def browse_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output.set(path)

    def add_woods(self):
        for filename in filedialog.askopenfilenames(filetypes=[("PNG images", "*.png")]):
            if Path(filename) not in [wood["color"] for wood in self.woods]:
                self.woods.append({"name": folder_name(filename), **plank_set(filename)})
        self.refresh_woods()

    def refresh_woods(self):
        self.wood_list.delete(*self.wood_list.get_children())
        for index, wood in enumerate(self.woods):
            values = [wood["name"], *(wood[channel].name if wood[channel] else "(missing)" for channel in CHANNELS)]
            self.wood_list.insert("", "end", iid=str(index), values=values)

    def edit_wood(self):
        selected = self.wood_list.selection()
        if selected:
            index = int(selected[0])
            dialog = WoodDialog(self, self.woods[index])
            self.wait_window(dialog)
            if dialog.result:
                self.woods[index] = dialog.result
                self.refresh_woods()

    def remove_woods(self):
        for index in sorted((int(item) for item in self.wood_list.selection()), reverse=True):
            del self.woods[index]
        self.refresh_woods()

    def report(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.update_idletasks()

    def run(self, write):
        try:
            template_dir = Path(self.template_dir.get())
            output = Path(self.output.get())
            source = {channel: Path(value.get()) if value.get() else None for channel, value in self.source.items()}
            if not template_dir.is_dir() or not source["color"] or not source["color"].is_file() or not self.woods:
                raise ValueError("Select the finished wood folder, source planks, and destination woods.")
            if write and not output.is_dir():
                raise ValueError("Choose an existing output folder.")
            templates = find_templates(template_dir)
            if not templates:
                raise ValueError("No color PNGs found in the finished wood folder.")
            used = {channel: any(template[channel].is_file() for template in templates) for channel in CHANNELS}
            inputs = {path.resolve() for template in templates for path in template.values() if path.is_file()}
            inputs.update(path.resolve() for path in source.values() if path)
            inputs.update(wood[channel].resolve() for wood in self.woods for channel in CHANNELS if wood[channel])
            jobs = []
            ambiguous = []
            names = set()
            for wood in self.woods:
                name = wood["name"]
                if not re.fullmatch(r"[a-z0-9_]+", name):
                    raise ValueError(f"'{name}': wood names must contain lowercase letters, numbers, and underscores only.")
                if name in names:
                    raise ValueError(f"Duplicate wood name: {name}")
                names.add(name)
                palettes = {}
                for channel in CHANNELS:
                    if not used[channel]:
                        continue
                    if not source[channel] or not source[channel].is_file():
                        raise ValueError(f"Source {channel} planks texture is missing.")
                    if not wood[channel] or not wood[channel].is_file():
                        raise ValueError(f"{name}: {channel} planks texture is missing; use Edit to set it.")
                    palettes[channel] = build_palette(source[channel], wood[channel])
                    self.report(f"{name} {channel}: {wood[channel].name}, {len(palettes[channel].mapping)} colors, "
                                f"{palettes[channel].ambiguous} ambiguous")
                    if palettes[channel].ambiguous:
                        ambiguous.append(f"{name} {channel}: {palettes[channel].ambiguous}")
                for template in templates:
                    folder = output / name / template["color"].parent.relative_to(template_dir)
                    for channel in CHANNELS:
                        if template[channel].is_file():
                            jobs.append((template[channel], folder / template[channel].name, palettes[channel]))
                    jobs.append((texture_set_text(template), folder / (template["color"].stem + ".texture_set.json"), None))
            for _, destination, _ in jobs:
                if destination.resolve() in inputs:
                    raise ValueError(f"Output would overwrite an input: {destination}")
            if write and ambiguous and not messagebox.askyesno(
                "Ambiguous palettes", "Some source colors have multiple replacements:\n" + "\n".join(ambiguous)
                + "\n\nUse the most common replacement?"
            ):
                return
            existing = [destination for _, destination, _ in jobs if destination.exists()]
            if write and existing:
                if not self.overwrite.get():
                    raise ValueError(f"{len(existing)} outputs exist; enable overwrite or choose another folder.")
                if not messagebox.askyesno("Overwrite", f"Replace {len(existing)} existing files?"):
                    return
            for item, destination, palette in jobs:
                if palette is None:
                    if write:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_text(item, encoding="utf-8")
                    self.report(f"{'WROTE' if write else 'READY'} {destination}")
                    continue
                image, unmatched = recolor(item, palette, self.tolerance.get())
                status = "READY"
                if unmatched and not self.keep_unmatched.get():
                    status = "SKIP"
                elif write:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    image.save(destination, format="PNG")
                    status = "WROTE"
                self.report(f"{status} {item.name} -> {destination}; {unmatched} unmatched pixels")
            self.report("Finished.")
        except Exception as error:
            messagebox.showerror("Wood texture sets", str(error))


if __name__ == "__main__":
    WoodSetApp().mainloop()
