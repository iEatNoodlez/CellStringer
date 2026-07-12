"""
CellStringer
------------
Desktop GUI for building and editing custom .CDO (Celltron Data Offline)
site-template files for Celltron battery testers (see SPEC.md): Site ->
Plant -> String -> Jar trees that load straight into the tester, so
operators don't have to key sites in by hand. Pure stdlib (tkinter) -- no
third-party dependencies at runtime. Can be frozen into a single portable
.exe with PyInstaller (see build_exe.bat).

Not affiliated with or endorsed by Celltron; CELLTRON is a trademark of its
respective owner.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cdo_format as fmt
import lib_format

APP_TITLE = "CellStringer"
SCAN_INTERVAL_MS = 2000
DEFAULT_CONDUCTANCE = 1650
DEFAULT_JAR_COUNT = 4


def default_watch_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    """Locate a bundled asset both when run as a script and when frozen
    into a PyInstaller onefile exe (which unpacks data files to a temp
    dir referenced by sys._MEIPASS)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def _make_uppercase(entry: ttk.Entry | ttk.Combobox, var: tk.StringVar):
    """Force a StringVar to uppercase as the user types, preserving the
    cursor position. The tester only ever displays uppercase text."""
    def _cb(*_args):
        cur = var.get()
        upper = cur.upper()
        if upper != cur:
            pos = entry.index(tk.INSERT)
            var.set(upper)
            entry.icursor(pos)
    var.trace_add("write", _cb)


def _next_jar_name(existing: list[fmt.Jar]) -> str:
    n = len(existing) + 1
    names = {j.name for j in existing}
    while f"BAT{n}" in names:
        n += 1
    return f"BAT{n}"


def _c_to_f(c: int) -> int:
    return round(c * 9 / 5 + 32)


def _f_to_c(f: int) -> int:
    return round((f - 32) * 5 / 9)


_TEST_TYPE_BY_LABEL = {v: k for k, v in fmt.TEST_TYPES.items()}
_STRAPS_BY_LABEL = {v: k for k, v in fmt.STRAPS_PER_JAR_OPTIONS.items()}


# ---------------------------------------------------------------------------
# Small modal dialogs
# ---------------------------------------------------------------------------

class SiteDialog(tk.Toplevel):
    """Add/edit a site. Result stored in self.result (str name) or None."""

    def __init__(self, parent, title: str, initial_name: str = ""):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result: str | None = None

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")

        ttk.Label(frm, text="Site name:").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value=initial_name.upper())
        entry = ttk.Entry(frm, textvariable=self.name_var, width=32)
        entry.grid(row=1, column=0, pady=(2, 10))
        _make_uppercase(entry, self.name_var)
        entry.focus_set()
        entry.icursor(tk.END)

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, sticky="e")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="OK", command=self._on_ok, default="active").pack(side="right")

        self.bind("<Return>", lambda e: self._on_ok())
        self.bind("<Escape>", lambda e: self.destroy())

        self.grab_set()
        self.wait_window()

    def _on_ok(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Invalid name", "Site name cannot be empty.", parent=self)
            return
        try:
            fmt.validate_name(name, "Site name")
        except fmt.CdoFormatError as e:
            messagebox.showerror("Invalid name", str(e), parent=self)
            return
        self.result = name
        self.destroy()


class PlantDialog(tk.Toplevel):
    """Add/edit a plant. Result stored in self.result (name, site) or None."""

    def __init__(self, parent, title: str, site_names: list[str],
                 initial_name: str = "", initial_site: str = ""):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result: tuple[str, str] | None = None

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")

        ttk.Label(frm, text="Site:").grid(row=0, column=0, sticky="w")
        self.site_var = tk.StringVar(value=initial_site or (site_names[0] if site_names else ""))
        site_combo = ttk.Combobox(frm, textvariable=self.site_var, values=site_names,
                                   state="readonly", width=31)
        site_combo.grid(row=1, column=0, sticky="we", pady=(2, 8))

        ttk.Label(frm, text="Plant name:").grid(row=2, column=0, sticky="w")
        self.name_var = tk.StringVar(value=initial_name.upper())
        entry = ttk.Entry(frm, textvariable=self.name_var, width=34)
        entry.grid(row=3, column=0, pady=(2, 10))
        _make_uppercase(entry, self.name_var)
        entry.focus_set()
        entry.icursor(tk.END)

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, sticky="e")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="OK", command=self._on_ok, default="active").pack(side="right")

        self.bind("<Return>", lambda e: self._on_ok())
        self.bind("<Escape>", lambda e: self.destroy())

        if not site_names:
            messagebox.showwarning("No sites", "Add at least one site before creating a plant.",
                                    parent=self)

        self.grab_set()
        self.wait_window()

    def _on_ok(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Invalid name", "Plant name cannot be empty.", parent=self)
            return
        if not self.site_var.get():
            messagebox.showerror("No site", "Select a site.", parent=self)
            return
        try:
            fmt.validate_name(name, "Plant name")
        except fmt.CdoFormatError as e:
            messagebox.showerror("Invalid name", str(e), parent=self)
            return
        self.result = (name, self.site_var.get())
        self.destroy()


class StringDialog(tk.Toplevel):
    """Add/edit a string. Result stored in self.result (dict) or None.
    GUID/string-id are device plumbing and are never shown here --
    auto-generated for new strings, preserved as-is when editing an
    existing one. Temperature is entered in Fahrenheit and stored as
    Celsius (the device's native unit) -- see SPEC.md Section 8."""

    def __init__(self, parent, title: str, plant_labels: list[str],
                 initial_plant_label: str = "", initial: fmt.String | None = None,
                 show_jar_count: bool = False, lib_models: list[lib_format.Model] | None = None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result: dict | None = None
        self._battery = initial.battery if initial else None
        b = self._battery

        lib_models = lib_models or []
        lib_mfr_names = sorted({m.manufacturer for m in lib_models})
        lib_mfr_names_upper = {n.upper() for n in lib_mfr_names}
        # (manufacturer, model) upper -> reference conductance, for autofill
        lib_index = {(m.manufacturer.upper(), m.name.upper()): m.conductance for m in lib_models}

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")

        left = ttk.Frame(frm)
        left.grid(row=0, column=0, sticky="n", padx=(0, 20))
        right = ttk.Frame(frm)
        right.grid(row=0, column=1, sticky="n")

        # -- left column: identity + battery link ---------------------------
        row = 0
        ttk.Label(left, text="Plant:").grid(row=row, column=0, sticky="w")
        row += 1
        self.plant_var = tk.StringVar(value=initial_plant_label or
                                       (plant_labels[0] if plant_labels else ""))
        ttk.Combobox(left, textvariable=self.plant_var, values=plant_labels,
                     state="readonly", width=36).grid(row=row, column=0, sticky="we", pady=(2, 8))
        row += 1

        ttk.Label(left, text="String name:").grid(row=row, column=0, sticky="w")
        row += 1
        self.name_var = tk.StringVar(value=(initial.name if initial else "").upper())
        name_entry = ttk.Entry(left, textvariable=self.name_var, width=38)
        name_entry.grid(row=row, column=0, sticky="we", pady=(2, 8))
        _make_uppercase(name_entry, self.name_var)
        name_entry.focus_set()
        row += 1

        ttk.Label(left, text="Battery tech ID:").grid(row=row, column=0, sticky="w")
        row += 1
        self.tech_var = tk.StringVar(value=(initial.battery.tech_id if initial else "").upper())
        tech_entry = ttk.Entry(left, textvariable=self.tech_var, width=38)
        tech_entry.grid(row=row, column=0, sticky="we", pady=(2, 8))
        _make_uppercase(tech_entry, self.tech_var)
        row += 1

        ttk.Label(left, text="Manufacturer:").grid(row=row, column=0, sticky="w")
        row += 1
        self.mfr_var = tk.StringVar(value=(initial.battery.manufacturer if initial else "").upper())
        mfr_combo = ttk.Combobox(left, textvariable=self.mfr_var, values=lib_mfr_names, width=36)
        mfr_combo.grid(row=row, column=0, sticky="we", pady=(2, 8))
        _make_uppercase(mfr_combo, self.mfr_var)
        row += 1

        ttk.Label(left, text="Model:").grid(row=row, column=0, sticky="w")
        row += 1
        self.model_var = tk.StringVar(value=(initial.battery.model if initial else "").upper())
        model_combo = ttk.Combobox(left, textvariable=self.model_var, width=36)
        model_combo.grid(row=row, column=0, sticky="we", pady=(2, 8))
        _make_uppercase(model_combo, self.model_var)
        row += 1
        self._model_combo = model_combo  # exposed read-only for tests

        ttk.Label(left, text="Reference conductance (0-65535):").grid(row=row, column=0, sticky="w")
        row += 1
        self.cond_var = tk.StringVar(
            value=str(initial.battery.conductance) if initial else str(DEFAULT_CONDUCTANCE))
        ttk.Spinbox(left, from_=0, to=65535, textvariable=self.cond_var, width=10).grid(
            row=row, column=0, sticky="w", pady=(2, 8))
        row += 1

        if lib_models:
            ttk.Label(left, text=f"(autocomplete from {len(lib_models)} loaded library "
                                  f"model(s); conductance auto-fills on an exact match)",
                      foreground="#666", justify="left", wraplength=320).grid(
                row=row, column=0, sticky="w", pady=(0, 8))
            row += 1

        def _model_candidates() -> list[str]:
            mfr = self.mfr_var.get().strip().upper()
            if mfr in lib_mfr_names_upper:
                return sorted({m.name for m in lib_models if m.manufacturer.upper() == mfr})
            return sorted({m.name for m in lib_models})

        def _filter_combo(combo: ttk.Combobox, candidates_fn) -> None:
            text = combo.get().strip().lower()
            candidates = candidates_fn()
            if text:
                candidates = [c for c in candidates if text in c.lower()]
            combo["values"] = candidates

        def _refresh_model_options(*_args) -> None:
            model_combo["values"] = _model_candidates()

        def _maybe_autofill_conductance(*_args) -> None:
            key = (self.mfr_var.get().strip().upper(), self.model_var.get().strip().upper())
            if key in lib_index:
                self.cond_var.set(str(lib_index[key]))

        mfr_combo.bind("<KeyRelease>", lambda e: _filter_combo(mfr_combo, lambda: lib_mfr_names))
        model_combo.bind("<KeyRelease>", lambda e: _filter_combo(model_combo, _model_candidates))
        _refresh_model_options()
        # Traces registered after the widgets already carry their initial
        # (possibly non-library) values, so opening the dialog to edit an
        # existing string never silently overwrites its conductance --
        # autofill only fires on an actual subsequent edit to either field.
        self.mfr_var.trace_add("write", _refresh_model_options)
        self.mfr_var.trace_add("write", _maybe_autofill_conductance)
        self.model_var.trace_add("write", _maybe_autofill_conductance)

        self.jar_count_var = tk.StringVar(value=str(DEFAULT_JAR_COUNT))
        if show_jar_count:
            ttk.Label(left, text=f"Number of jars (1-{fmt.MAX_JARS_PER_STRING}):").grid(
                row=row, column=0, sticky="w")
            row += 1
            ttk.Spinbox(left, from_=fmt.MIN_JARS_PER_STRING, to=fmt.MAX_JARS_PER_STRING,
                        textvariable=self.jar_count_var, width=10).grid(
                row=row, column=0, sticky="w", pady=(2, 8))
            row += 1
            ttk.Label(left, text="(jars are auto-named BAT1, BAT2, ... -- rename or add/remove\n"
                                  "individual jars afterwards in the Jars tab)",
                      foreground="#666", justify="left").grid(
                row=row, column=0, sticky="w", pady=(0, 8))
            row += 1

        # -- right column: test settings + alarm thresholds -----------------
        row = 0
        ttk.Label(right, text="Test settings", font=("Segoe UI", 9, "bold")).grid(
            row=row, column=0, sticky="w", pady=(0, 4))
        row += 1

        ttk.Label(right, text="Test type:").grid(row=row, column=0, sticky="w")
        row += 1
        self.test_type_var = tk.StringVar(
            value=fmt.TEST_TYPES[b.test_type if b else fmt.TEST_TYPE_VOLTS_AND_COND])
        ttk.Combobox(right, textvariable=self.test_type_var, values=list(fmt.TEST_TYPES.values()),
                     state="readonly", width=22).grid(row=row, column=0, sticky="w", pady=(2, 8))
        row += 1

        ttk.Label(right, text="Tests per jar:").grid(row=row, column=0, sticky="w")
        row += 1
        self.tests_per_jar_var = tk.StringVar(value=str(b.tests_per_jar if b else 1))
        ttk.Combobox(right, textvariable=self.tests_per_jar_var, values=["1", "2", "3"],
                     state="readonly", width=8).grid(row=row, column=0, sticky="w", pady=(2, 8))
        row += 1

        ttk.Label(right, text="Straps per jar:").grid(row=row, column=0, sticky="w")
        row += 1
        self.straps_var = tk.StringVar(
            value=fmt.STRAPS_PER_JAR_OPTIONS[b.straps_per_jar if b else 0])
        ttk.Combobox(right, textvariable=self.straps_var,
                     values=list(fmt.STRAPS_PER_JAR_OPTIONS.values()),
                     state="readonly", width=8).grid(row=row, column=0, sticky="w", pady=(2, 8))
        row += 1

        ttk.Separator(right, orient="horizontal").grid(row=row, column=0, sticky="we", pady=6)
        row += 1

        ttk.Label(right, text="Alarm thresholds", font=("Segoe UI", 9, "bold")).grid(
            row=row, column=0, sticky="w", pady=(0, 4))
        row += 1

        def spin_row(label, lo, hi, initial_value):
            nonlocal row
            ttk.Label(right, text=label).grid(row=row, column=0, sticky="w")
            row += 1
            v = tk.StringVar(value=str(initial_value))
            ttk.Spinbox(right, from_=lo, to=hi, textvariable=v, width=10).grid(
                row=row, column=0, sticky="w", pady=(2, 8))
            row += 1
            return v

        ttk.Label(right, text="Jar voltage (V):").grid(row=row, column=0, sticky="w")
        row += 1
        self.jar_voltage_var = tk.StringVar(value=str(b.jar_voltage if b else 12))
        jar_voltage_combo = ttk.Combobox(
            right, textvariable=self.jar_voltage_var,
            values=[str(v) for v in fmt.JAR_VOLTAGE_OPTIONS],
            state="readonly", width=8)
        jar_voltage_combo.grid(row=row, column=0, sticky="w", pady=(2, 8))
        row += 1

        self.volt_upper_var = spin_row("Voltage alarm upper (mV):", 0, 65535,
                                        b.voltage_upper_mv if b else 15000)
        self.volt_lower_var = spin_row("Voltage alarm lower (mV):", 0, 65535,
                                        b.voltage_lower_mv if b else 13200)

        def _on_jar_voltage_change(*_args):
            upper, lower = fmt.JAR_VOLTAGE_DEFAULT_THRESHOLDS_MV[int(self.jar_voltage_var.get())]
            self.volt_upper_var.set(str(upper))
            self.volt_lower_var.set(str(lower))
        # trace, not a <<ComboboxSelected>> bind, so it only fires on an
        # actual change (this is set up after the initial .set() above, so
        # opening the dialog to edit an existing string never clobbers its
        # custom thresholds just by displaying them).
        self.jar_voltage_var.trace_add("write", _on_jar_voltage_change)

        self.temp_upper_f_var = spin_row("Temp alarm upper (°F):", -40, 300,
                                          _c_to_f(b.temp_upper_c if b else 30))
        self.temp_lower_f_var = spin_row("Temp alarm lower (°F):", -40, 300,
                                          _c_to_f(b.temp_lower_c if b else 10))
        self.string_g_alarm_var = spin_row("String G alarm %:", 0, 255,
                                            b.string_g_alarm_pct if b else 60)
        self.string_g_warn_var = spin_row("String G warn %:", 0, 255,
                                           b.string_g_warn_pct if b else 70)
        self.jar_g_alarm_var = spin_row("Jar G alarm %:", 0, 255,
                                         b.jar_g_alarm_pct if b else 60)
        self.jar_g_warn_var = spin_row("Jar G warn %:", 0, 255,
                                        b.jar_g_warn_pct if b else 70)

        btns = ttk.Frame(frm)
        btns.grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="OK", command=self._on_ok, default="active").pack(side="right")

        self.bind("<Return>", lambda e: self._on_ok())
        self.bind("<Escape>", lambda e: self.destroy())

        if not plant_labels:
            messagebox.showwarning("No plants", "Add at least one plant before creating a string.",
                                    parent=self)

        self.grab_set()
        self.wait_window()

    def _on_ok(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Invalid name", "String name cannot be empty.", parent=self)
            return
        if not self.plant_var.get():
            messagebox.showerror("No plant", "Select a plant.", parent=self)
            return
        try:
            conductance = int(self.cond_var.get())
            jar_voltage = int(self.jar_voltage_var.get())
            volt_upper = int(self.volt_upper_var.get())
            volt_lower = int(self.volt_lower_var.get())
            temp_upper_c = _f_to_c(int(self.temp_upper_f_var.get()))
            temp_lower_c = _f_to_c(int(self.temp_lower_f_var.get()))
            string_g_alarm = int(self.string_g_alarm_var.get())
            string_g_warn = int(self.string_g_warn_var.get())
            jar_g_alarm = int(self.jar_g_alarm_var.get())
            jar_g_warn = int(self.jar_g_warn_var.get())
            tests_per_jar = int(self.tests_per_jar_var.get())
        except ValueError:
            messagebox.showerror("Invalid value", "All numeric fields must be integers.", parent=self)
            return

        battery = fmt.BatteryAssignment(
            tech_id=self.tech_var.get().strip(),
            conductance=conductance,
            manufacturer=self.mfr_var.get().strip(),
            model=self.model_var.get().strip(),
            guid=self._battery.guid if self._battery else fmt.new_guid(),
            test_type=_TEST_TYPE_BY_LABEL[self.test_type_var.get()],
            tests_per_jar=tests_per_jar,
            straps_per_jar=_STRAPS_BY_LABEL[self.straps_var.get()],
            jar_voltage=jar_voltage,
            temp_upper_c=temp_upper_c,
            temp_lower_c=temp_lower_c,
            string_g_alarm_pct=string_g_alarm,
            string_g_warn_pct=string_g_warn,
            voltage_upper_mv=volt_upper,
            voltage_lower_mv=volt_lower,
            jar_g_alarm_pct=jar_g_alarm,
            jar_g_warn_pct=jar_g_warn,
        )
        try:
            fmt.validate_string_fields(name, battery)
        except fmt.CdoFormatError as e:
            messagebox.showerror("Invalid string", str(e), parent=self)
            return

        jar_count = DEFAULT_JAR_COUNT
        if self.jar_count_var.get():
            try:
                jar_count = int(self.jar_count_var.get())
            except ValueError:
                messagebox.showerror("Invalid jar count", "Number of jars must be an integer.",
                                      parent=self)
                return
            try:
                fmt.validate_jar_count(jar_count, name)
            except fmt.CdoFormatError as e:
                messagebox.showerror("Invalid jar count", str(e), parent=self)
                return

        self.result = dict(name=name, plant_label=self.plant_var.get(), battery=battery,
                            jar_count=jar_count)
        self.destroy()


class JarDialog(tk.Toplevel):
    """Add/edit a jar. Result stored in self.result (name, string) or None."""

    def __init__(self, parent, title: str, string_labels: list[str],
                 initial_name: str = "", initial_string: str = ""):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result: tuple[str, str] | None = None

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")

        ttk.Label(frm, text="String:").grid(row=0, column=0, sticky="w")
        self.string_var = tk.StringVar(value=initial_string or (string_labels[0] if string_labels else ""))
        string_combo = ttk.Combobox(frm, textvariable=self.string_var, values=string_labels,
                                     state="readonly", width=42)
        string_combo.grid(row=1, column=0, sticky="we", pady=(2, 8))

        ttk.Label(frm, text="Jar name:").grid(row=2, column=0, sticky="w")
        self.name_var = tk.StringVar(value=initial_name.upper())
        entry = ttk.Entry(frm, textvariable=self.name_var, width=34)
        entry.grid(row=3, column=0, pady=(2, 10))
        _make_uppercase(entry, self.name_var)
        entry.focus_set()
        entry.icursor(tk.END)

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, sticky="e")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="OK", command=self._on_ok, default="active").pack(side="right")

        self.bind("<Return>", lambda e: self._on_ok())
        self.bind("<Escape>", lambda e: self.destroy())

        if not string_labels:
            messagebox.showwarning("No strings", "Add at least one string before creating a jar.",
                                    parent=self)

        self.grab_set()
        self.wait_window()

    def _on_ok(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Invalid name", "Jar name cannot be empty.", parent=self)
            return
        if not self.string_var.get():
            messagebox.showerror("No string", "Select a string.", parent=self)
            return
        try:
            fmt.validate_name(name, "Jar name")
        except fmt.CdoFormatError as e:
            messagebox.showerror("Invalid name", str(e), parent=self)
            return
        self.result = (name, self.string_var.get())
        self.destroy()


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class CellStringerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1080x640")
        self.minsize(720, 440)

        self.sites: list[fmt.Site] = []
        self.current_path: Path | None = None
        self.dirty: bool = False

        self.watch_dir: Path = default_watch_dir()
        self._known_cdo_files: list[str] = []

        self.lib_models: list[lib_format.Model] = []
        self.lib_path: Path | None = None

        # parallel row->object maps, rebuilt on every refresh (mirrors the
        # tree walk, so treeview iids stay simple integers)
        self._plant_rows: list[tuple[fmt.Site, fmt.Plant]] = []
        self._string_rows: list[tuple[fmt.Site, fmt.Plant, fmt.String]] = []
        self._jar_rows: list[tuple[fmt.Site, fmt.Plant, fmt.String, fmt.Jar]] = []

        self._set_app_icon()
        self._build_menu()
        self._build_layout()
        self._refresh_all()
        self._scan_directory()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_app_icon(self):
        ico_path = resource_path("app_icon.ico")
        if ico_path.exists():
            try:
                self.iconbitmap(default=str(ico_path))
                return
            except tk.TclError:
                pass
        png_path = resource_path("app_icon.png")
        if png_path.exists():
            try:
                self._icon_image = tk.PhotoImage(file=str(png_path))
                self.iconphoto(True, self._icon_image)
            except tk.TclError:
                pass

    # -- UI construction ---------------------------------------------------

    def _build_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="New File", command=self._new_file, accelerator="Ctrl+N")
        file_menu.add_command(label="Open...", command=self._open_dialog, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Save", command=self._save, accelerator="Ctrl+S")
        file_menu.add_command(label="Save As...", command=self._save_as)
        file_menu.add_separator()
        file_menu.add_command(label="Import Battery Library (.lib)...", command=self._import_library)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)

        self.bind_all("<Control-n>", lambda e: self._new_file())
        self.bind_all("<Control-o>", lambda e: self._open_dialog())
        self.bind_all("<Control-s>", lambda e: self._save())

    def _build_layout(self):
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True)

        # --- Sidebar: folder watcher -------------------------------------
        sidebar = ttk.Frame(root, padding=8)
        sidebar.pack(side="left", fill="y")

        ttk.Label(sidebar, text="Watched folder:", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.folder_var = tk.StringVar(value=str(self.watch_dir))
        folder_label = ttk.Label(sidebar, textvariable=self.folder_var, wraplength=220,
                                  foreground="#444")
        folder_label.pack(anchor="w", pady=(0, 4))
        ttk.Button(sidebar, text="Change Folder...", command=self._change_folder).pack(
            anchor="w", fill="x")

        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", pady=8)

        ttk.Label(sidebar, text=".cdo files found:", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.cdo_listbox = tk.Listbox(sidebar, width=32, height=18, exportselection=False)
        self.cdo_listbox.pack(fill="y", expand=False, pady=(2, 4))
        self.cdo_listbox.bind("<Double-Button-1>", lambda e: self._load_selected_from_sidebar())

        ttk.Button(sidebar, text="Load Selected", command=self._load_selected_from_sidebar).pack(
            fill="x")

        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", pady=8)

        ttk.Label(sidebar, text="Battery library:", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.lib_status_var = tk.StringVar(value="No library loaded")
        ttk.Label(sidebar, textvariable=self.lib_status_var, wraplength=220,
                  foreground="#444").pack(anchor="w", pady=(0, 4))
        ttk.Button(sidebar, text="Import Library...", command=self._import_library).pack(
            anchor="w", fill="x")

        # --- Main area: notebook -------------------------------------------
        main = ttk.Frame(root, padding=8)
        main.pack(side="left", fill="both", expand=True)

        notebook = ttk.Notebook(main)
        notebook.pack(fill="both", expand=True)

        site_tab = ttk.Frame(notebook, padding=6)
        plant_tab = ttk.Frame(notebook, padding=6)
        string_tab = ttk.Frame(notebook, padding=6)
        jar_tab = ttk.Frame(notebook, padding=6)
        notebook.add(site_tab, text="Sites")
        notebook.add(plant_tab, text="Plants")
        notebook.add(string_tab, text="Strings")
        notebook.add(jar_tab, text="Jars")

        self._build_site_tab(site_tab)
        self._build_plant_tab(plant_tab)
        self._build_string_tab(string_tab)
        self._build_jar_tab(jar_tab)

        # --- Status bar ------------------------------------------------------
        status = ttk.Frame(self, relief="sunken", padding=(6, 2))
        status.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar()
        ttk.Label(status, textvariable=self.status_var).pack(side="left")

    def _make_tree(self, parent, columns: dict[str, tuple[str, int]]):
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(side="top", fill="both", expand=True)

        cols = list(columns.keys())
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        for c, (text, width) in columns.items():
            tree.heading(c, text=text)
            tree.column(c, width=width, anchor="w", stretch=(c == cols[-1]))
        tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="left", fill="y")
        return tree

    def _build_site_tab(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(side="top", fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="Add...", command=self._add_site).pack(side="left")
        ttk.Button(toolbar, text="Edit...", command=self._edit_site).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Delete", command=self._delete_site).pack(side="left")

        self.site_tree = self._make_tree(parent, {
            "name": ("Site Name", 260),
            "plants": ("Plants", 80),
            "jars": ("Total Jars", 100),
        })
        self.site_tree.bind("<Double-Button-1>", lambda e: self._edit_site())

    def _build_plant_tab(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(side="top", fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="Add...", command=self._add_plant).pack(side="left")
        ttk.Button(toolbar, text="Edit...", command=self._edit_plant).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Delete", command=self._delete_plant).pack(side="left")

        self.plant_tree = self._make_tree(parent, {
            "site": ("Site", 200),
            "name": ("Plant Name", 240),
            "strings": ("Strings", 80),
        })
        self.plant_tree.bind("<Double-Button-1>", lambda e: self._edit_plant())

    def _build_string_tab(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(side="top", fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="Add...", command=self._add_string).pack(side="left")
        ttk.Button(toolbar, text="Edit...", command=self._edit_string).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Delete", command=self._delete_string).pack(side="left")

        self.string_tree = self._make_tree(parent, {
            "plant": ("Site / Plant", 220),
            "name": ("String Name", 160),
            "manufacturer": ("Manufacturer", 180),
            "model": ("Model", 160),
            "jars": ("Jars", 60),
        })
        self.string_tree.bind("<Double-Button-1>", lambda e: self._edit_string())

    def _build_jar_tab(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(side="top", fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="Add...", command=self._add_jar).pack(side="left")
        ttk.Button(toolbar, text="Edit...", command=self._edit_jar).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Delete", command=self._delete_jar).pack(side="left")

        self.jar_tree = self._make_tree(parent, {
            "string": ("Site / Plant / String", 320),
            "name": ("Jar Name", 160),
        })
        self.jar_tree.bind("<Double-Button-1>", lambda e: self._edit_jar())

    # -- Data <-> UI sync ----------------------------------------------------

    def _refresh_all(self):
        self._refresh_site_tree()
        self._refresh_plant_tree()
        self._refresh_string_tree()
        self._refresh_jar_tree()
        self._refresh_status()
        self._refresh_title()

    def _refresh_site_tree(self):
        tree = self.site_tree
        tree.delete(*tree.get_children())
        for i, site in enumerate(self.sites):
            n_jars = sum(len(st.jars) for p in site.plants for st in p.strings)
            tree.insert("", "end", iid=str(i), values=(site.name, len(site.plants), n_jars))

    def _refresh_plant_tree(self):
        tree = self.plant_tree
        tree.delete(*tree.get_children())
        self._plant_rows = [(site, plant) for site in self.sites for plant in site.plants]
        for i, (site, plant) in enumerate(self._plant_rows):
            tree.insert("", "end", iid=str(i), values=(site.name, plant.name, len(plant.strings)))

    def _refresh_string_tree(self):
        tree = self.string_tree
        tree.delete(*tree.get_children())
        self._string_rows = [
            (site, plant, string)
            for site in self.sites for plant in site.plants for string in plant.strings
        ]
        for i, (site, plant, string) in enumerate(self._string_rows):
            tree.insert("", "end", iid=str(i), values=(
                f"{site.name} / {plant.name}", string.name,
                string.battery.manufacturer, string.battery.model, len(string.jars),
            ))

    def _refresh_jar_tree(self):
        tree = self.jar_tree
        tree.delete(*tree.get_children())
        self._jar_rows = [
            (site, plant, string, jar)
            for site in self.sites for plant in site.plants
            for string in plant.strings for jar in string.jars
        ]
        for i, (site, plant, string, jar) in enumerate(self._jar_rows):
            tree.insert("", "end", iid=str(i), values=(
                f"{site.name} / {plant.name} / {string.name}", jar.name,
            ))

    def _refresh_status(self):
        n_sites = len(self.sites)
        n_plants = sum(len(s.plants) for s in self.sites)
        n_strings = sum(len(p.strings) for s in self.sites for p in s.plants)
        n_jars = sum(len(st.jars) for s in self.sites for p in s.plants for st in p.strings)
        size = fmt.SIGNATURE_SIZE + fmt.region_size_for(self.sites)
        loaded = f"Loaded: {self.current_path.name}" if self.current_path else "New (unsaved) file"
        self.status_var.set(
            f"{loaded}   |   {n_sites} site(s), {n_plants} plant(s), {n_strings} string(s), "
            f"{n_jars} jar(s)   |   file size if saved: {size} bytes"
        )

    def _refresh_title(self):
        name = self.current_path.name if self.current_path else "Untitled"
        star = "*" if self.dirty else ""
        self.title(f"{APP_TITLE} - {name}{star}")

    def _mark_dirty(self):
        self.dirty = True
        self._refresh_all()

    # -- Site CRUD ----------------------------------------------------------

    def _add_site(self):
        dlg = SiteDialog(self, "Add Site")
        if dlg.result is None:
            return
        if any(s.name == dlg.result for s in self.sites):
            messagebox.showerror("Duplicate", f"Site '{dlg.result}' already exists.")
            return
        self.sites.append(fmt.Site(name=dlg.result))
        self._mark_dirty()

    def _selected_index(self, tree: ttk.Treeview, label: str) -> int | None:
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("No selection", f"Select a {label} first.")
            return None
        return int(sel[0])

    def _edit_site(self):
        idx = self._selected_index(self.site_tree, "site")
        if idx is None:
            return
        old = self.sites[idx]
        dlg = SiteDialog(self, "Edit Site", initial_name=old.name)
        if dlg.result is None or dlg.result == old.name:
            return
        if any(j != idx and s.name == dlg.result for j, s in enumerate(self.sites)):
            messagebox.showerror("Duplicate", f"Site '{dlg.result}' already exists.")
            return
        old.name = dlg.result
        self._mark_dirty()

    def _delete_site(self):
        idx = self._selected_index(self.site_tree, "site")
        if idx is None:
            return
        site = self.sites[idx]
        if site.plants:
            messagebox.showerror(
                "Cannot delete",
                f"'{site.name}' contains {len(site.plants)} plant(s). "
                f"Delete those first.",
            )
            return
        if not messagebox.askyesno("Delete site", f"Delete '{site.name}'?"):
            return
        del self.sites[idx]
        self._mark_dirty()

    # -- Plant CRUD -----------------------------------------------------------

    def _site_names(self) -> list[str]:
        return [s.name for s in self.sites]

    def _add_plant(self):
        if not self.sites:
            messagebox.showwarning("No sites", "Add at least one site first.")
            return
        dlg = PlantDialog(self, "Add Plant", self._site_names())
        if dlg.result is None:
            return
        name, site_name = dlg.result
        site = next(s for s in self.sites if s.name == site_name)
        if any(p.name == name for p in site.plants):
            messagebox.showerror("Duplicate", f"'{site_name}' already has a plant named '{name}'.")
            return
        site.plants.append(fmt.Plant(name=name))
        self._mark_dirty()

    def _edit_plant(self):
        idx = self._selected_index(self.plant_tree, "plant")
        if idx is None:
            return
        old_site, old_plant = self._plant_rows[idx]
        dlg = PlantDialog(self, "Edit Plant", self._site_names(),
                           initial_name=old_plant.name, initial_site=old_site.name)
        if dlg.result is None:
            return
        name, site_name = dlg.result
        new_site = next(s for s in self.sites if s.name == site_name)
        if any(p is not old_plant and p.name == name for p in new_site.plants):
            messagebox.showerror("Duplicate", f"'{site_name}' already has a plant named '{name}'.")
            return
        old_plant.name = name
        if new_site is not old_site:
            old_site.plants.remove(old_plant)
            new_site.plants.append(old_plant)
        self._mark_dirty()

    def _delete_plant(self):
        idx = self._selected_index(self.plant_tree, "plant")
        if idx is None:
            return
        site, plant = self._plant_rows[idx]
        if plant.strings:
            messagebox.showerror(
                "Cannot delete",
                f"'{plant.name}' contains {len(plant.strings)} string(s). "
                f"Delete those first.",
            )
            return
        if not messagebox.askyesno("Delete plant", f"Delete '{plant.name}'?"):
            return
        site.plants.remove(plant)
        self._mark_dirty()

    # -- String CRUD ------------------------------------------------------------

    def _plant_labels(self) -> list[str]:
        return [f"{s.name} / {p.name}" for s in self.sites for p in s.plants]

    def _resolve_plant_label(self, label: str) -> tuple[fmt.Site, fmt.Plant]:
        for s in self.sites:
            for p in s.plants:
                if f"{s.name} / {p.name}" == label:
                    return s, p
        raise KeyError(label)

    def _add_string(self):
        if not self._plant_labels():
            messagebox.showwarning("No plants", "Add at least one plant first.")
            return
        dlg = StringDialog(self, "Add String", self._plant_labels(), show_jar_count=True,
                            lib_models=self.lib_models)
        if dlg.result is None:
            return
        _site, plant = self._resolve_plant_label(dlg.result["plant_label"])
        if any(st.name == dlg.result["name"] for st in plant.strings):
            messagebox.showerror("Duplicate", f"This plant already has a string named "
                                               f"'{dlg.result['name']}'.")
            return
        jars = [fmt.Jar(name=f"BAT{i + 1}") for i in range(dlg.result["jar_count"])]
        plant.strings.append(fmt.String(name=dlg.result["name"], battery=dlg.result["battery"],
                                         jars=jars))
        self._mark_dirty()

    def _edit_string(self):
        idx = self._selected_index(self.string_tree, "string")
        if idx is None:
            return
        old_site, old_plant, old_string = self._string_rows[idx]
        dlg = StringDialog(self, "Edit String", self._plant_labels(),
                            initial_plant_label=f"{old_site.name} / {old_plant.name}",
                            initial=old_string, show_jar_count=False,
                            lib_models=self.lib_models)
        if dlg.result is None:
            return
        new_site, new_plant = self._resolve_plant_label(dlg.result["plant_label"])
        if any(st is not old_string and st.name == dlg.result["name"] for st in new_plant.strings):
            messagebox.showerror("Duplicate", f"This plant already has a string named "
                                               f"'{dlg.result['name']}'.")
            return
        old_string.name = dlg.result["name"]
        old_string.battery = dlg.result["battery"]
        if new_plant is not old_plant:
            old_plant.strings.remove(old_string)
            new_plant.strings.append(old_string)
        self._mark_dirty()

    def _delete_string(self):
        idx = self._selected_index(self.string_tree, "string")
        if idx is None:
            return
        _site, plant, string = self._string_rows[idx]
        if string.jars:
            messagebox.showerror(
                "Cannot delete",
                f"'{string.name}' contains {len(string.jars)} jar(s). Delete those first.",
            )
            return
        if not messagebox.askyesno("Delete string", f"Delete '{string.name}'?"):
            return
        plant.strings.remove(string)
        self._mark_dirty()

    # -- Jar CRUD -----------------------------------------------------------------

    def _string_labels(self) -> list[str]:
        return [f"{s.name} / {p.name} / {st.name}"
                for s in self.sites for p in s.plants for st in p.strings]

    def _resolve_string_label(self, label: str) -> fmt.String:
        for s in self.sites:
            for p in s.plants:
                for st in p.strings:
                    if f"{s.name} / {p.name} / {st.name}" == label:
                        return st
        raise KeyError(label)

    def _add_jar(self):
        if not self._string_labels():
            messagebox.showwarning("No strings", "Add at least one string first.")
            return
        dlg = JarDialog(self, "Add Jar", self._string_labels())
        if dlg.result is None:
            return
        name, string_label = dlg.result
        string = self._resolve_string_label(string_label)
        if any(j.name == name for j in string.jars):
            messagebox.showerror("Duplicate", f"This string already has a jar named '{name}'.")
            return
        if len(string.jars) >= fmt.MAX_JARS_PER_STRING:
            messagebox.showerror(
                "Jar limit reached",
                f"'{string.name}' already has {len(string.jars)} jars, the device maximum "
                f"({fmt.MAX_JARS_PER_STRING}) per string.",
            )
            return
        string.jars.append(fmt.Jar(name=name))
        self._mark_dirty()

    def _edit_jar(self):
        idx = self._selected_index(self.jar_tree, "jar")
        if idx is None:
            return
        _site, _plant, old_string, old_jar = self._jar_rows[idx]
        old_label = None
        for s in self.sites:
            for p in s.plants:
                for st in p.strings:
                    if st is old_string:
                        old_label = f"{s.name} / {p.name} / {st.name}"
        dlg = JarDialog(self, "Edit Jar", self._string_labels(),
                         initial_name=old_jar.name, initial_string=old_label)
        if dlg.result is None:
            return
        name, string_label = dlg.result
        new_string = self._resolve_string_label(string_label)
        if any(j is not old_jar and j.name == name for j in new_string.jars):
            messagebox.showerror("Duplicate", f"This string already has a jar named '{name}'.")
            return
        old_jar.name = name
        if new_string is not old_string:
            old_string.jars.remove(old_jar)
            new_string.jars.append(old_jar)
        self._mark_dirty()

    def _delete_jar(self):
        idx = self._selected_index(self.jar_tree, "jar")
        if idx is None:
            return
        _site, _plant, string, jar = self._jar_rows[idx]
        if not messagebox.askyesno("Delete jar", f"Delete '{jar.name}'?"):
            return
        string.jars.remove(jar)
        self._mark_dirty()

    # -- File operations ----------------------------------------------------

    def _new_file(self):
        if not self._confirm_discard_changes():
            return
        self.sites = []
        self.current_path = None
        self.dirty = False
        self._refresh_all()

    def _confirm_discard_changes(self) -> bool:
        if not self.dirty:
            return True
        choice = messagebox.askyesnocancel(
            "Unsaved changes", "You have unsaved changes. Save before continuing?")
        if choice is None:
            return False
        if choice:
            return self._save()
        return True

    def _open_dialog(self):
        if not self._confirm_discard_changes():
            return
        path = filedialog.askopenfilename(
            title="Open .cdo file",
            initialdir=str(self.watch_dir),
            filetypes=[(".cdo files", "*.cdo"), ("All files", "*.*")],
        )
        if not path:
            return
        self._load_file(Path(path))

    def _load_selected_from_sidebar(self):
        sel = self.cdo_listbox.curselection()
        if not sel:
            return
        if not self._confirm_discard_changes():
            return
        filename = self.cdo_listbox.get(sel[0])
        self._load_file(self.watch_dir / filename)

    def _load_file(self, path: Path):
        try:
            data = path.read_bytes()
            sites = fmt.parse_cdo(data)
        except (fmt.CdoFormatError, OSError) as e:
            messagebox.showerror("Failed to load", f"Could not load '{path.name}':\n{e}")
            return
        self.sites = sites
        self.current_path = path
        self.dirty = False
        self._refresh_all()
        n_jars = sum(len(st.jars) for s in sites for p in s.plants for st in p.strings)
        self.status_var.set(f"Loaded '{path.name}': {len(sites)} site(s), {n_jars} jar(s) total.")

    def _build_bytes_or_show_error(self) -> bytes | None:
        try:
            return fmt.build_cdo(self.sites)
        except fmt.CdoFormatError as e:
            messagebox.showerror("Cannot save", f"The site tree is not valid:\n{e}")
            return None

    def _save(self) -> bool:
        if self.current_path is None:
            return self._save_as()
        return self._write_to(self.current_path)

    def _save_as(self) -> bool:
        path = filedialog.asksaveasfilename(
            title="Save .cdo file as",
            initialdir=str(self.watch_dir),
            defaultextension=".cdo",
            filetypes=[(".cdo files", "*.cdo"), ("All files", "*.*")],
        )
        if not path:
            return False
        return self._write_to(Path(path))

    def _write_to(self, path: Path) -> bool:
        data = self._build_bytes_or_show_error()
        if data is None:
            return False

        if path.exists():
            backup_name = f"{path.stem}.{datetime.now():%Y%m%d-%H%M%S}.bak"
            backup_path = path.with_name(backup_name)
            try:
                shutil.copy2(path, backup_path)
            except OSError as e:
                if not messagebox.askyesno(
                        "Backup failed",
                        f"Could not create a backup of the existing file:\n{e}\n\n"
                        f"Continue and overwrite anyway?"):
                    return False

        try:
            path.write_bytes(data)
        except OSError as e:
            messagebox.showerror("Save failed", f"Could not write '{path}':\n{e}")
            return False

        self.current_path = path
        self.dirty = False
        self._refresh_status()
        self._refresh_title()
        self._scan_directory()
        return True

    # -- Directory watcher --------------------------------------------------

    def _change_folder(self):
        chosen = filedialog.askdirectory(title="Choose folder to watch for .cdo files",
                                          initialdir=str(self.watch_dir))
        if not chosen:
            return
        self.watch_dir = Path(chosen)
        self.folder_var.set(str(self.watch_dir))
        self._known_cdo_files = []
        self._scan_directory()

    def _scan_directory(self):
        try:
            found = sorted(
                p.name for p in self.watch_dir.glob("*.cdo") if p.is_file()
            )
        except OSError:
            found = []

        if found != self._known_cdo_files:
            self._known_cdo_files = found
            selected = self.cdo_listbox.curselection()
            selected_name = self.cdo_listbox.get(selected[0]) if selected else None
            self.cdo_listbox.delete(0, tk.END)
            for name in found:
                self.cdo_listbox.insert(tk.END, name)
            if selected_name in found:
                self.cdo_listbox.selection_set(found.index(selected_name))

        self.after(SCAN_INTERVAL_MS, self._scan_directory)

    # -- Battery library ------------------------------------------------------

    def _import_library(self):
        path = filedialog.askopenfilename(
            title="Import battery library",
            initialdir=str(self.watch_dir),
            filetypes=[(".lib files", "*.lib"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            data = Path(path).read_bytes()
            _manufacturers, models = lib_format.parse_lib(data)
        except (lib_format.LibFormatError, OSError) as e:
            messagebox.showerror("Failed to load library", f"Could not load '{Path(path).name}':\n{e}")
            return
        self.lib_models = models
        self.lib_path = Path(path)
        self.lib_status_var.set(f"{self.lib_path.name} ({len(models)} model(s))")

    # -- Misc -----------------------------------------------------------------

    def _show_about(self):
        messagebox.showinfo(
            "About",
            f"{APP_TITLE}\n\n"
            "Builds and edits custom .CDO site-template files (Site -> Plant -> "
            "String -> Jar) for Celltron battery testers, so operators don't have "
            "to key sites in by hand.\n\n"
            "Import is destructive on-device: a timestamped backup is made "
            "automatically whenever an existing file is overwritten.\n\n"
            "Not affiliated with or endorsed by Celltron.",
        )

    def _on_close(self):
        if self._confirm_discard_changes():
            self.destroy()


def main():
    app = CellStringerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
