#!/usr/bin/env python3
"""Create Bedrock MER maps by translating a source texture palette."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image
except ImportError as error:
    raise SystemExit("Pillow is required. Install dependencies with: py -m pip install -r requirements.txt") from error

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]
LAB = tuple[float, float, float]


@dataclass
class Palette:
    mer_by_rgb: dict[RGB, RGBA]
    lab_by_rgb: dict[RGB, LAB]
    ambiguous_colors: int


def rgb_to_lab(rgb: RGB) -> LAB:
    """Convert an sRGB color to CIELAB using the D65 reference white."""
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


def build_palette(source_path: Path, mer_path: Path) -> Palette:
    with Image.open(source_path) as source_image, Image.open(mer_path) as mer_image:
        if source_image.size != mer_image.size:
            raise ValueError(
                "Source albedo and MER dimensions must match "
                f"({source_image.width}x{source_image.height} vs "
                f"{mer_image.width}x{mer_image.height})."
            )
        source_pixels = list(source_image.convert("RGB").getdata())
        mer_pixels = list(mer_image.convert("RGBA").getdata())

    samples: dict[RGB, Counter[RGBA]] = defaultdict(Counter)
    for source_color, mer_color in zip(source_pixels, mer_pixels):
        samples[source_color][mer_color] += 1

    mer_by_rgb = {color: values.most_common(1)[0][0] for color, values in samples.items()}
    lab_by_rgb = {color: rgb_to_lab(color) for color in mer_by_rgb}
    ambiguous = sum(1 for values in samples.values() if len(values) > 1)
    return Palette(mer_by_rgb, lab_by_rgb, ambiguous)


def resolve_color(color: RGB, palette: Palette, tolerance: float) -> RGBA | None:
    exact = palette.mer_by_rgb.get(color)
    if exact is not None:
        return exact
    if tolerance <= 0:
        return None

    target_lab = rgb_to_lab(color)
    closest_color = None
    closest_distance = float("inf")
    for source_color, source_lab in palette.lab_by_rgb.items():
        distance = sum((target_lab[index] - source_lab[index]) ** 2 for index in range(3)) ** 0.5
        if distance < closest_distance:
            closest_color = source_color
            closest_distance = distance

    if closest_color is None or closest_distance > tolerance:
        return None
    return palette.mer_by_rgb[closest_color]


class PaletteMERApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Texture Palette to MER")
        self.geometry("800x760")
        self.minsize(700, 650)

        self.source_albedo = tk.StringVar()
        self.source_mer = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.tolerance = tk.DoubleVar(value=0)
        self.overwrite = tk.BooleanVar(value=False)
        self.targets: list[Path] = []

        self._build_ui()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(5, weight=1)

        ttk.Label(outer, text="Texture Palette to MER", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 12)
        )

        source_frame = ttk.LabelFrame(outer, text="Source palette", padding=10)
        source_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        source_frame.columnconfigure(1, weight=1)
        self._file_row(source_frame, 0, "Source albedo", self.source_albedo, self._browse_source_albedo)
        self._file_row(source_frame, 1, "Source MER", self.source_mer, self._browse_source_mer)

        target_frame = ttk.LabelFrame(outer, text="Target textures", padding=10)
        target_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        target_frame.columnconfigure(0, weight=1)
        target_frame.rowconfigure(0, weight=1)
        self.target_list = tk.Listbox(target_frame, selectmode=tk.EXTENDED, height=8, exportselection=False)
        self.target_list.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(0, 10))
        target_scrollbar = ttk.Scrollbar(target_frame, orient="vertical", command=self.target_list.yview)
        target_scrollbar.grid(row=0, column=1, rowspan=3, sticky="ns", padx=(0, 10))
        self.target_list.configure(yscrollcommand=target_scrollbar.set)
        ttk.Button(target_frame, text="Add PNGs...", command=self._add_targets).grid(row=0, column=2, sticky="ew", pady=(0, 6))
        ttk.Button(target_frame, text="Remove selected", command=self._remove_targets).grid(row=1, column=2, sticky="ew", pady=3)
        ttk.Button(target_frame, text="Clear list", command=self._clear_targets).grid(row=2, column=2, sticky="ew", pady=(6, 0))

        settings = ttk.LabelFrame(outer, text="Matching and output", padding=10)
        settings.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="Maximum color distance (Delta E76)").grid(row=0, column=0, sticky="w")
        self.tolerance_scale = ttk.Scale(settings, from_=0, to=50, variable=self.tolerance, command=self._update_tolerance_label)
        self.tolerance_scale.grid(row=0, column=1, sticky="ew", padx=10)
        self.tolerance_label = ttk.Label(settings, text="0 (exact)", width=12)
        self.tolerance_label.grid(row=0, column=2, sticky="e")
        self._file_row(settings, 1, "Output folder", self.output_dir, self._browse_output, folder=True)
        ttk.Checkbutton(settings, text="Overwrite existing MER files", variable=self.overwrite).grid(
            row=2, column=1, sticky="w", pady=(4, 0)
        )

        action_frame = ttk.Frame(outer)
        action_frame.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self.run_button = ttk.Button(action_frame, text="Generate MER maps", command=self._generate)
        self.run_button.pack(side="left")
        self.progress = ttk.Progressbar(action_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

        log_frame = ttk.LabelFrame(outer, text="Results", padding=8)
        log_frame.grid(row=5, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)

    def _file_row(self, parent, row: int, label: str, variable: tk.StringVar, command, folder: bool = False) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(parent, text="Browse...", command=command).grid(row=row, column=2, sticky="e", padx=(8, 0), pady=4)

    def _browse_source_albedo(self) -> None:
        path = filedialog.askopenfilename(title="Choose source albedo", filetypes=[("PNG images", "*.png"), ("All files", "*.*")])
        if path:
            self.source_albedo.set(path)

    def _browse_source_mer(self) -> None:
        path = filedialog.askopenfilename(title="Choose source MER texture", filetypes=[("PNG images", "*.png"), ("All files", "*.*")])
        if path:
            self.source_mer.set(path)

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
        selected = list(self.target_list.curselection())
        for index in reversed(selected):
            self.target_list.delete(index)
            del self.targets[index]

    def _clear_targets(self) -> None:
        self.targets.clear()
        self.target_list.delete(0, "end")

    def _update_tolerance_label(self, _value: str = "") -> None:
        value = self.tolerance.get()
        self.tolerance_label.configure(text="0 (exact)" if value == 0 else f"{value:.0f} Delta E")

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _generate(self) -> None:
        source_path = Path(self.source_albedo.get())
        mer_path = Path(self.source_mer.get())
        output_path = Path(self.output_dir.get())
        if not source_path.is_file() or not mer_path.is_file():
            messagebox.showerror("Missing source", "Choose both a source albedo and its MER texture.")
            return
        if not self.targets:
            messagebox.showerror("No targets", "Add one or more target albedo textures.")
            return
        if not output_path.is_dir():
            messagebox.showerror("Missing output folder", "Choose an existing output folder.")
            return

        try:
            palette = build_palette(source_path, mer_path)
        except Exception as error:
            messagebox.showerror("Could not load source palette", str(error))
            return

        if palette.ambiguous_colors and not messagebox.askyesno(
            "Ambiguous source colors",
            f"{palette.ambiguous_colors} source colors have more than one MER value.\n"
            "The most common MER value will be used for each. Continue?",
        ):
            return

        overwrites = [path for path in self.targets if (output_path / f"{path.stem}_mer.png").exists()]
        if overwrites and self.overwrite.get() and not messagebox.askyesno(
            "Confirm overwrite", f"Replace {len(overwrites)} existing MER file(s)?"
        ):
            return

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._append_log(f"Source palette: {len(palette.mer_by_rgb)} colors; ambiguous: {palette.ambiguous_colors}")
        self.progress.configure(maximum=len(self.targets), value=0)
        self.run_button.configure(state="disabled")
        tolerance = self.tolerance.get()
        created = 0
        skipped = 0

        try:
            for index, target_path in enumerate(self.targets, start=1):
                destination = output_path / f"{target_path.stem}_mer.png"
                self.progress.configure(value=index - 1)
                self.update_idletasks()
                if destination.exists() and not self.overwrite.get():
                    self._append_log(f"SKIP {target_path.name}: output exists")
                    skipped += 1
                    continue
                try:
                    with Image.open(target_path) as target_image:
                        target_pixels = list(target_image.convert("RGB").getdata())
                        target_size = target_image.size
                    color_results = {
                        color: resolve_color(color, palette, tolerance)
                        for color in set(target_pixels)
                    }
                    unmatched_colors = sum(1 for result in color_results.values() if result is None)
                    if unmatched_colors:
                        unmatched_pixels = sum(1 for color in target_pixels if color_results[color] is None)
                        self._append_log(
                            f"SKIP {target_path.name}: {unmatched_colors} unmatched color(s), "
                            f"{unmatched_pixels} pixel(s); increase tolerance or inspect palette"
                        )
                        skipped += 1
                        continue
                    output_pixels = [color_results[color] for color in target_pixels]
                    result_image = Image.new("RGBA", target_size)
                    result_image.putdata(output_pixels)
                    result_image.save(destination, format="PNG")
                    self._append_log(f"OK   {target_path.name} -> {destination.name}")
                    created += 1
                except Exception as error:
                    self._append_log(f"ERROR {target_path.name}: {error}")
                    skipped += 1
                self.progress.configure(value=index)
                self.update_idletasks()
        finally:
            self.run_button.configure(state="normal")

        self._append_log(f"Finished: {created} created, {skipped} skipped.")


def main() -> None:
    app = PaletteMERApp()
    app.mainloop()


if __name__ == "__main__":
    main()
