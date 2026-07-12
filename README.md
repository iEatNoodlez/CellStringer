# CellStringer

Desktop GUI for building and editing custom `.CDO` site-template files
(Site → Plant → String → Jar trees) for **CELLTRON™ Advantage Digital**
battery testers, so operators don't have to key sites in by hand. See
[SPEC.md](SPEC.md) for the full file format this tool implements.

Not affiliated with or endorsed by Celltron; CELLTRON is a trademark of its
respective owner.

## Files

- `cdo_format.py` — core `.CDO` format engine (parse/build/validate). No GUI
  dependencies; safe to reuse from scripts.
- `lib_format.py` — reads Celltron/Midtronics `.lib` battery library files
  (reused from the sibling CellLibrarian project) to power the manufacturer/
  model autocomplete described below. Read-only here; CellStringer doesn't
  write `.lib` files.
- `cell_stringer.py` — the Tkinter GUI application (the main program).
- `build_exe.bat` — packages the GUI into a single portable `.exe`.
- `example.cdo` — a sample site (1 plant, 1 string, 4 jars) to try the app
  with.
- `app_icon.ico` / `app_icon.png` — the app icon, used for the window/taskbar
  icon and baked into the built `.exe`.

## Running it

You need Python 3.9+ (Tkinter is included with standard Windows Python
installs — no extra packages required):

```
python cell_stringer.py
```

## Building a portable .exe

If you want a single file that runs on any Windows machine without Python
installed:

```
build_exe.bat
```

This installs PyInstaller (build-time only), then produces
`dist\CellStringer.exe` — copy that one file anywhere and double-click to run
it. No installer, no other files needed.

## Using the app

- **Watched folder** (left sidebar): the app continuously scans this folder
  (every 2 seconds) for `.cdo` files and lists them. It defaults to the
  folder the program is run from. Double-click a file, or select it and
  click **Load Selected**, to open it for editing.
- **Sites tab**: add, edit, or delete sites (top-level). Deleting a site
  that still contains plants is blocked — delete those first.
- **Plants tab**: add, edit, or delete plants, each attached to a site
  (picked from a dropdown). Deleting a plant that still contains strings is
  blocked.
- **Strings tab**: add, edit, or delete strings, each attached to a plant.
  A string carries the battery assignment (tech ID, manufacturer, model,
  reference conductance), the test settings (test type, tests per jar,
  straps per jar), and the alarm thresholds (jar voltage, upper/lower
  voltage alarms, upper/lower temperature alarms, and String/Jar
  conductance alarm and warn percentages). Jar voltage is a fixed choice of
  `2, 4, 6, 8, 10, 12, 16, 18` V — picking one auto-fills its confirmed
  default voltage alarm pair (you can still edit those afterward).
  Temperature is entered in Fahrenheit and stored on the device as Celsius.
  When you add a string you also pick an initial jar count (1-240, the
  device's confirmed maximum per string) — jars are auto-named `BAT1`,
  `BAT2`, ... and can be renamed or added/removed afterwards in the Jars
  tab, up to that same 240-jar cap. Deleting a string that still contains
  jars is blocked.
- **Battery library autocomplete** (left sidebar, or File → Import Battery
  Library...): import a `.lib` battery library exported from CellLibrarian
  (or the device itself) and the Manufacturer/Model fields in the String
  dialog become autocompleting dropdowns sourced from it — the Model list
  narrows to whatever the currently-typed Manufacturer makes. Typing
  something that isn't in the library is completely fine, you just keep
  typing free text. Reference conductance auto-fills whenever the typed
  Manufacturer + Model pair exactly matches a library entry (and stays put
  otherwise — it never overwrites a value you typed yourself for an
  unmatched pair). The library is a session-only reference — it isn't saved
  into the `.CDO` file, and needs re-importing each time you relaunch.
- **Jars tab**: add, edit, or delete individual jars within a string.
- All names and text fields are forced to UPPERCASE, since that's all the
  tester displays.
- A GUID and a few other device-plumbing fields are generated automatically
  behind the scenes (required by the file format) — they aren't shown in the
  UI, and are preserved as-is when you edit a string loaded from a real file.
- **Save / Save As** (File menu, or Ctrl+S): validates the whole site tree
  against the device's rules (name lengths, ASCII-only, valid GUIDs, value
  ranges, etc.) before writing. If you're overwriting a file that already
  exists, the previous version is automatically backed up next to it as
  `name.YYYYMMDD-HHMMSS.bak` first — import is destructive on the device, so
  keep those backups until you've confirmed the new site on-device.

## Notes

- This tool only builds/edits **templates** — sites that haven't been tested
  yet. If you try to open a real exported `.CDO` that already has test
  results (measurements) on a jar, it will refuse to load rather than risk
  corrupting data it can't safely round-trip. See [SPEC.md](SPEC.md) §9.
- The device erases its existing database on import — always keep a backup of
  a working `.CDO` before importing a new one.
- The `count` field on multi-plant/multi-string trees follows a confirmed
  propagation rule (first string's jar count propagates up through its plant
  and site — never a sum); see [SPEC.md](SPEC.md) §11 for the exact rule and
  a worked example.
- The format has been confirmed against a real hardware import→export
  round-trip, not just static analysis of sample files — see the note at the
  top of [SPEC.md](SPEC.md).
