#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crimson Desert PAM Extractor - GUI launcher (v2)
"""

import os
import sys
import json
import subprocess
import threading
import queue
import struct
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# -- drag-and-drop (optional, install with: pip install tkinterdnd2) ----------
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

SCRIPT_DIR   = Path(__file__).parent
EXTRACTOR    = SCRIPT_DIR / "cd_extractor.py"
PYTHON       = sys.executable
RECENT_FILE  = SCRIPT_DIR / "recent.json"
MAX_RECENT   = 10

# -- Colour palette (dark theme) ----------------------------------------------
BG        = "#1e1e2e"
BG2       = "#2a2a3e"
BG3       = "#313145"
ACCENT    = "#c678dd"   # purple
ACCENT2   = "#98c379"   # green
WARN      = "#e5c07b"   # yellow
ERR       = "#e06c75"   # red
FG        = "#abb2bf"
FG_BRIGHT = "#ffffff"
FG_DIM    = "#555566"

# -- Recent-files helpers -----------------------------------------------------
def load_recent():
    try:
        return json.loads(RECENT_FILE.read_text(encoding="utf-8"))[:MAX_RECENT]
    except Exception:
        return []

def save_recent(path, recent):
    path = str(path)
    if path in recent:
        recent.remove(path)
    recent.insert(0, path)
    recent = recent[:MAX_RECENT]
    try:
        RECENT_FILE.write_text(json.dumps(recent, indent=2), encoding="utf-8")
    except Exception:
        pass
    return recent

# -- Quick PAM header reader (no full parse - header fields only) -------------
SUBMESH_TABLE   = 0x410
SUBMESH_STRIDE  = 0x218
HDR_MESH_COUNT  = 0x10
HDR_BBOX_MIN    = 0x14
HDR_BBOX_MAX    = 0x20
SUBMESH_TEX_OFF = 0x10
SUBMESH_MAT_OFF = 0x110

def quick_pam_info(path):
    """Return a short info string about a PAM file for the preview panel."""
    try:
        data = Path(path).read_bytes()
        if data[:4] != b'PAR ':
            return "Not a valid PAM file."
        n    = struct.unpack_from('<I', data, HDR_MESH_COUNT)[0]
        bmin = struct.unpack_from('<3f', data, HDR_BBOX_MIN)
        bmax = struct.unpack_from('<3f', data, HDR_BBOX_MAX)
        lines = [
            f"Submeshes : {n}",
            f"BBox min  : ({bmin[0]:.3f}, {bmin[1]:.3f}, {bmin[2]:.3f})",
            f"BBox max  : ({bmax[0]:.3f}, {bmax[1]:.3f}, {bmax[2]:.3f})",
            f"Size (cm) : {abs(bmax[0]-bmin[0])*100:.1f} x "
                         f"{abs(bmax[1]-bmin[1])*100:.1f} x "
                         f"{abs(bmax[2]-bmin[2])*100:.1f}",
            "",
            "  #   Material  /  Texture",
        ]
        for i in range(min(n, 32)):
            off = SUBMESH_TABLE + i * SUBMESH_STRIDE
            if off + SUBMESH_MAT_OFF + 256 > len(data):
                break
            tex_s = data[off + SUBMESH_TEX_OFF: off + SUBMESH_TEX_OFF + 256].split(b'\x00')[0].decode('utf-8', errors='replace') or '-'
            mat_s = data[off + SUBMESH_MAT_OFF: off + SUBMESH_MAT_OFF + 256].split(b'\x00')[0].decode('utf-8', errors='replace') or '-'
            lines.append(f"  {i:>2}  {mat_s}  /  {tex_s}")
        if n > 32:
            lines.append(f"  ... and {n - 32} more")
        return "\n".join(lines)
    except Exception as e:
        return f"Could not read file: {e}"

# -- Main application window --------------------------------------------------
class App(TkinterDnD.Tk if HAS_DND else tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Crimson Desert PAM Extractor")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(700, 640)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._queue   = queue.Queue()
        self._running = False
        self._recent  = load_recent()

        self._build_styles()
        self._build_ui()

        # Register the whole window as a drop target so files can be dropped
        # anywhere on the window, not only on the dedicated label.
        if HAS_DND:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)

        self.after(100, self._poll_queue)

    # -- Styles ---------------------------------------------------------------
    def _build_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".",              background=BG,  foreground=FG,  font=("Segoe UI", 10))
        s.configure("Main.TFrame",    background=BG)
        s.configure("Card.TFrame",    background=BG2, relief="flat")
        s.configure("TLabel",         background=BG,  foreground=FG)
        s.configure("H.TLabel",       background=BG,  foreground=FG_BRIGHT, font=("Segoe UI", 11, "bold"))
        s.configure("Sect.TLabel",    background=BG2, foreground=ACCENT,    font=("Segoe UI", 9, "bold"))
        s.configure("Sub.TLabel",     background=BG2, foreground=FG)
        s.configure("Hint.TLabel",    background=BG2, foreground=FG_DIM,    font=("Segoe UI", 8))
        s.configure("TRadiobutton",   background=BG2, foreground=FG, selectcolor=BG2, indicatorcolor=ACCENT)
        s.configure("TCheckbutton",   background=BG2, foreground=FG, selectcolor=BG2, indicatorcolor=ACCENT)
        s.configure("TEntry",         fieldbackground=BG3, foreground=FG_BRIGHT, insertcolor=FG_BRIGHT)
        s.configure("TCombobox",      fieldbackground=BG3, foreground=FG_BRIGHT)
        s.map("TCombobox",            fieldbackground=[("readonly", BG3)])
        s.configure("Run.TButton",    background=ACCENT, foreground=FG_BRIGHT,
                    font=("Segoe UI", 11, "bold"), padding=(20, 8))
        s.map("Run.TButton",          background=[("active", "#9d5fbb"), ("disabled", BG3)],
                                      foreground=[("disabled", "#666")])
        s.configure("Small.TButton",  background=BG3, foreground=FG, padding=(8, 4))
        s.map("Small.TButton",        background=[("active", BG2)])
        s.configure("TProgressbar",   troughcolor=BG3, background=ACCENT2, thickness=8)

    # -- UI layout ------------------------------------------------------------
    def _build_ui(self):
        root = ttk.Frame(self, style="Main.TFrame", padding=12)
        root.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)

        row = 0

        # Title
        hdr = ttk.Frame(root, style="Main.TFrame")
        hdr.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(hdr, text="CRIMSON DESERT  PAM EXTRACTOR",
                  style="H.TLabel").pack(side="left")
        dnd_hint = "  drag & drop enabled" if HAS_DND else "  (pip install tkinterdnd2 for drag & drop)"
        ttk.Label(hdr, text=dnd_hint, background=BG,
                  foreground=ACCENT2 if HAS_DND else FG_DIM,
                  font=("Segoe UI", 8)).pack(side="left", padx=8)
        row += 1

        row = self._build_input_card(root, row)
        row = self._build_info_card(root, row)
        row = self._build_output_card(root, row)
        row = self._build_options_card(root, row)
        row = self._build_run_area(root, row)
        row = self._build_log_area(root, row)
        root.rowconfigure(row - 1, weight=1)

    # -- INPUT card -----------------------------------------------------------
    def _build_input_card(self, parent, row):
        card = ttk.Frame(parent, style="Card.TFrame", padding=10)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="  Input", style="Sect.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        self._mode = tk.StringVar(value="file")
        fr = ttk.Frame(card, style="Card.TFrame")
        fr.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Radiobutton(fr, text="Single file (.pam / .pamlod)",
                        variable=self._mode, value="file",
                        command=self._on_mode_change,
                        style="TRadiobutton").pack(side="left", padx=(0, 20))
        ttk.Radiobutton(fr, text="Batch - whole folder",
                        variable=self._mode, value="folder",
                        command=self._on_mode_change,
                        style="TRadiobutton").pack(side="left")

        ttk.Label(card, text="Path:", style="Sub.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 6))
        self._input_var = tk.StringVar()
        self._input_var.trace_add("write", self._on_input_changed)
        ttk.Entry(card, textvariable=self._input_var).grid(
            row=2, column=1, sticky="ew", padx=4)
        ttk.Button(card, text="Browse...", style="Small.TButton",
                   command=self._browse_input).grid(row=2, column=2, sticky="e")

        ttk.Label(card, text="Recent:", style="Sub.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self._recent_var = tk.StringVar()
        self._recent_combo = ttk.Combobox(card, textvariable=self._recent_var,
                                          state="readonly", height=12)
        self._recent_combo.grid(row=3, column=1, sticky="ew", padx=4, pady=(6, 0))
        self._recent_combo.bind("<<ComboboxSelected>>", self._on_recent_select)
        ttk.Button(card, text="Clear", style="Small.TButton",
                   command=self._clear_recent).grid(
            row=3, column=2, sticky="e", pady=(6, 0))
        self._refresh_recent_combo()

        # Drop zone label -- also registered as a drop target for visual clarity
        if HAS_DND:
            self._drop_lbl = tk.Label(
                card,
                text="  [ Drop .pam / .pamlod files or a folder anywhere on this window ]",
                bg=BG3, fg=ACCENT2, font=("Segoe UI", 9), anchor="center",
                pady=8, relief="flat"
            )
            self._drop_lbl.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
            self._drop_lbl.drop_target_register(DND_FILES)
            self._drop_lbl.dnd_bind("<<Drop>>", self._on_drop)

        return row + 1

    # -- Mesh info preview ----------------------------------------------------
    def _build_info_card(self, parent, row):
        card = ttk.Frame(parent, style="Card.TFrame", padding=10)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        card.columnconfigure(0, weight=1)

        hdr = ttk.Frame(card, style="Card.TFrame")
        hdr.grid(row=0, column=0, sticky="ew")
        ttk.Label(hdr, text="  Mesh Info", style="Sect.TLabel").pack(side="left")
        self._info_shown = False
        self._info_toggle_btn = ttk.Button(hdr, text="Show", style="Small.TButton",
                                           command=self._toggle_info)
        self._info_toggle_btn.pack(side="right")

        self._info_frame = ttk.Frame(card, style="Card.TFrame")
        self._info_text = tk.Text(
            self._info_frame, bg=BG3, fg=ACCENT2, relief="flat",
            font=("Consolas", 8), state="disabled", height=10
        )
        vsb = ttk.Scrollbar(self._info_frame, orient="vertical",
                            command=self._info_text.yview)
        self._info_text["yscrollcommand"] = vsb.set
        self._info_text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        return row + 1

    # -- OUTPUT card ----------------------------------------------------------
    def _build_output_card(self, parent, row):
        card = ttk.Frame(parent, style="Card.TFrame", padding=10)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="  Output", style="Sect.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(card, text="Folder:", style="Sub.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 6))
        self._output_var = tk.StringVar()
        ttk.Entry(card, textvariable=self._output_var).grid(
            row=1, column=1, sticky="ew", padx=4)
        btn_fr = ttk.Frame(card, style="Card.TFrame")
        btn_fr.grid(row=1, column=2, sticky="e")
        ttk.Button(btn_fr, text="Browse...", style="Small.TButton",
                   command=self._browse_output).pack(side="left", padx=(0, 4))
        ttk.Button(btn_fr, text="Same as input", style="Small.TButton",
                   command=self._fill_same_as_input).pack(side="left")
        ttk.Label(card, text="Leave blank = extractor default (subfolder per file)",
                  style="Hint.TLabel").grid(row=2, column=1, sticky="w", padx=4)

        fr_sub = ttk.Frame(card, style="Card.TFrame")
        fr_sub.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._subfolder_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr_sub,
                        text="Create subfolder named after file  (e.g. sword/sword.fbx)",
                        variable=self._subfolder_var,
                        style="TCheckbutton").pack(side="left")

        return row + 1

    # -- OPTIONS card ---------------------------------------------------------
    def _build_options_card(self, parent, row):
        card = ttk.Frame(parent, style="Card.TFrame", padding=10)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(card, text="  Options", style="Sect.TLabel").grid(
            row=0, column=0, columnspan=6, sticky="w", pady=(0, 8))

        # format
        ttk.Label(card, text="Format:", style="Sub.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10))
        self._fmt = tk.StringVar(value="fbx")
        fr_fmt = ttk.Frame(card, style="Card.TFrame")
        fr_fmt.grid(row=1, column=1, sticky="w")
        ttk.Radiobutton(fr_fmt, text="FBX", variable=self._fmt,
                        value="fbx", style="TRadiobutton").pack(side="left", padx=(0, 16))
        ttk.Radiobutton(fr_fmt, text="OBJ", variable=self._fmt,
                        value="obj", style="TRadiobutton").pack(side="left")

        # mesh mode
        ttk.Label(card, text="Mesh mode:", style="Sub.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        self._mesh_mode = tk.StringVar(value="combined")
        fr_mm = ttk.Frame(card, style="Card.TFrame")
        fr_mm.grid(row=2, column=1, sticky="w", pady=(8, 0), columnspan=4)
        for lbl, val in [("Combined", "combined"),
                          ("Split submeshes", "split"),
                          ("Info only", "info")]:
            ttk.Radiobutton(fr_mm, text=lbl, variable=self._mesh_mode,
                            value=val, style="TRadiobutton").pack(
                side="left", padx=(0, 16))

        # checkboxes row 1
        fr_chk1 = ttk.Frame(card, style="Card.TFrame")
        fr_chk1.grid(row=3, column=0, columnspan=6, sticky="w", pady=(10, 0))
        self._copy_tex = tk.BooleanVar(value=True)
        self._open_out = tk.BooleanVar(value=True)
        ttk.Checkbutton(fr_chk1, text="Copy DDS textures to output",
                        variable=self._copy_tex,
                        style="TCheckbutton").pack(side="left", padx=(0, 24))
        ttk.Checkbutton(fr_chk1, text="Open output folder when done",
                        variable=self._open_out,
                        style="TCheckbutton").pack(side="left")

        # checkboxes row 2
        fr_chk2 = ttk.Frame(card, style="Card.TFrame")
        fr_chk2.grid(row=4, column=0, columnspan=6, sticky="w", pady=(6, 0))
        self._save_log_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr_chk2,
                        text="Save log to file  (extract_log.txt in output folder)",
                        variable=self._save_log_var,
                        style="TCheckbutton").pack(side="left")

        return row + 1

    # -- Run + progress -------------------------------------------------------
    def _build_run_area(self, parent, row):
        frm = ttk.Frame(parent, style="Main.TFrame")
        frm.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        frm.columnconfigure(0, weight=1)

        self._run_btn = ttk.Button(frm, text="EXTRACT", style="Run.TButton",
                                   command=self._run)
        self._run_btn.grid(row=0, column=0, sticky="ew")

        self._progress = ttk.Progressbar(frm, mode="determinate",
                                         maximum=100, style="TProgressbar")
        self._progress.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(frm, textvariable=self._status_var, background=BG,
                  foreground="#888",
                  font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=(2, 0))

        return row + 1

    # -- Log area -------------------------------------------------------------
    def _build_log_area(self, parent, row):
        card = ttk.Frame(parent, style="Card.TFrame")
        card.grid(row=row, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)

        hdr = ttk.Frame(card, style="Card.TFrame")
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 0))
        ttk.Label(hdr, text="  Log", style="Sect.TLabel").pack(side="left")
        ttk.Button(hdr, text="Save...", style="Small.TButton",
                   command=self._save_log_dialog).pack(side="right", padx=2)
        ttk.Button(hdr, text="Clear", style="Small.TButton",
                   command=self._clear_log).pack(side="right", padx=2)

        self._log = tk.Text(
            card, bg=BG3, fg=FG, insertbackground=FG_BRIGHT,
            relief="flat", font=("Consolas", 9), state="disabled",
            wrap="word", height=10
        )
        self._log.grid(row=1, column=0, sticky="nsew", padx=4, pady=(4, 4))
        vsb = ttk.Scrollbar(card, orient="vertical", command=self._log.yview)
        vsb.grid(row=1, column=1, sticky="ns", pady=(4, 4))
        self._log["yscrollcommand"] = vsb.set

        self._log.tag_config("ok",   foreground=ACCENT2)
        self._log.tag_config("err",  foreground=ERR)
        self._log.tag_config("warn", foreground=WARN)
        self._log.tag_config("hdr",  foreground=ACCENT)
        self._log.tag_config("dim",  foreground=FG_DIM)

        return row + 1

    # -- Event handlers -------------------------------------------------------
    def _on_mode_change(self):
        self._update_info_preview()

    def _on_input_changed(self, *_):
        self._update_info_preview()

    def _on_drop(self, event):
        """Handle drag-and-drop of files or folders (tkinterdnd2)."""
        raw = event.data.strip()
        # tkinterdnd2 delivers paths space-separated; paths with spaces are
        # wrapped in braces: {C:/path with spaces/file.pam}
        paths = []
        current = ""
        in_brace = False
        for ch in raw:
            if ch == "{":
                in_brace = True
            elif ch == "}":
                in_brace = False
                if current.strip():
                    paths.append(current.strip())
                current = ""
            elif ch == " " and not in_brace:
                if current.strip():
                    paths.append(current.strip())
                    current = ""
            else:
                current += ch
        if current.strip():
            paths.append(current.strip())

        if paths:
            p = Path(paths[0])
            self._mode.set("folder" if p.is_dir() else "file")
            self._input_var.set(str(p))

    def _on_recent_select(self, _=None):
        val = self._recent_var.get()
        if val:
            p = Path(val)
            self._mode.set("folder" if p.is_dir() else "file")
            self._input_var.set(val)

    def _clear_recent(self):
        self._recent = []
        try:
            RECENT_FILE.unlink()
        except Exception:
            pass
        self._refresh_recent_combo()

    def _refresh_recent_combo(self):
        self._recent_combo["values"] = self._recent
        self._recent_combo.set(self._recent[0] if self._recent else "")

    # -- Mesh info preview ----------------------------------------------------
    def _toggle_info(self):
        self._info_shown = not self._info_shown
        if self._info_shown:
            self._info_frame.grid(row=1, column=0, sticky="ew", pady=(6, 0))
            self._info_toggle_btn.configure(text="Hide")
            self._update_info_preview()
        else:
            self._info_frame.grid_forget()
            self._info_toggle_btn.configure(text="Show")

    def _update_info_preview(self):
        if not self._info_shown:
            return
        path = self._input_var.get().strip()
        if not path or self._mode.get() != "file":
            self._set_info_text("Select a single .pam file to see mesh info.")
            return
        p = Path(path)
        if not p.exists():
            self._set_info_text("File not found.")
            return
        if p.suffix.lower() == ".pamlod":
            self._set_info_text(".pamlod - mesh info preview not supported.")
            return
        self._set_info_text(quick_pam_info(p))

    def _set_info_text(self, text):
        self._info_text.configure(state="normal")
        self._info_text.delete("1.0", "end")
        self._info_text.insert("end", text)
        self._info_text.configure(state="disabled")

    def _fill_same_as_input(self):
        """Set output folder to the same directory as the current input."""
        inp = self._input_var.get().strip()
        if not inp:
            return
        p = Path(inp)
        parent = p.parent if p.is_file() else p
        self._output_var.set(str(parent))

    # -- Browse dialogs -------------------------------------------------------
    def _browse_input(self):
        if self._mode.get() == "file":
            path = filedialog.askopenfilename(
                title="Select PAM / PAMLOD file",
                filetypes=[("PAM files", "*.pam *.pamlod"), ("All files", "*.*")]
            )
        else:
            path = filedialog.askdirectory(title="Select folder with PAM files")
        if path:
            self._input_var.set(path)

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self._output_var.set(path)

    # -- Log helpers ----------------------------------------------------------
    def _log_write(self, text, tag=None):
        self._log.configure(state="normal")
        if tag:
            self._log.insert("end", text, tag)
        else:
            self._log.insert("end", text)
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _save_log_dialog(self):
        path = filedialog.asksaveasfilename(
            title="Save log",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"extract_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        if path:
            Path(path).write_text(self._log.get("1.0", "end"), encoding="utf-8")

    # -- Queue polling (thread-safe UI updates) -------------------------------
    def _poll_queue(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                k = msg.get("kind")
                if k == "log":
                    self._log_write(msg["text"], msg.get("tag"))
                elif k == "progress":
                    done, total = msg["done"], msg["total"]
                    pct = int(done / total * 100) if total else 0
                    self._progress["value"] = pct
                    self._status_var.set(
                        f"[{done}/{total}]  {msg.get('name', '')}")
                elif k == "done":
                    self._on_done(msg.get("ok", True))
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _put(self, kind, **kwargs):
        self._queue.put({"kind": kind, **kwargs})

    # -- Run extraction -------------------------------------------------------
    def _run(self):
        if self._running:
            return

        inp = self._input_var.get().strip()
        if not inp:
            messagebox.showerror("Missing input",
                                 "Please select a PAM file or folder first.")
            return
        if not EXTRACTOR.exists():
            messagebox.showerror("Missing extractor",
                                 f"cd_extractor.py not found at:\n{EXTRACTOR}")
            return

        mode = self._mode.get()
        if mode == "file":
            files = [inp]
        else:
            folder = Path(inp)
            files = sorted(
                [str(f) for f in folder.glob("*.pam")] +
                [str(f) for f in folder.glob("*.pamlod")]
            )
            if not files:
                messagebox.showerror("No files found",
                                     f"No .pam or .pamlod files found in:\n{folder}")
                return

        out_dir   = self._output_var.get().strip() or None
        make_sub  = self._subfolder_var.get()
        fmt       = self._fmt.get()
        mesh_mode = self._mesh_mode.get()
        copy_tex  = self._copy_tex.get()
        open_out  = self._open_out.get()
        save_log  = self._save_log_var.get()

        self._running = True
        self._run_btn.configure(state="disabled")
        self._progress["value"] = 0
        self._log_write(
            f"--- Starting: {len(files)} file(s)  fmt={fmt} ---\n", "hdr"
        )

        self._recent = save_recent(inp, self._recent)
        self._refresh_recent_combo()

        threading.Thread(
            target=self._worker,
            args=(files, out_dir, make_sub, fmt, mesh_mode,
                  copy_tex, open_out, save_log),
            daemon=True
        ).start()

    def _worker(self, files, out_dir, make_sub, fmt, mesh_mode,
                copy_tex, open_out, save_log):
        ok_count = 0
        fail_count = 0
        last_out = None
        log_lines = []

        for i, fpath in enumerate(files, 1):
            name = Path(fpath).name
            stem = Path(fpath).stem
            self._put("progress", done=i - 1, total=len(files), name=name)
            self._put("log", text=f"[{i}/{len(files)}] {name}  ", tag="dim")

            cmd = [PYTHON, str(EXTRACTOR), fpath, "--format", fmt]
            if mesh_mode == "split":
                cmd += ["--split"]
            elif mesh_mode == "info":
                cmd += ["--info-only"]
            if out_dir:
                effective_out = str(Path(out_dir) / stem) if make_sub else out_dir
                cmd += ["-o", effective_out]
                last_out = effective_out
            else:
                # extractor default: creates {input_dir}/{stem}/
                last_out = str(Path(fpath).parent / stem)
            if copy_tex:
                cmd += ["--copy-textures"]

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", env=env
                )
                stdout = result.stdout.strip()
                stderr = result.stderr.strip()

                if result.returncode == 0:
                    ok_count += 1
                    self._put("log", text="OK\n", tag="ok")
                    for line in stdout.splitlines():
                        self._put("log", text=f"    {line}\n", tag="dim")
                    log_lines.append(f"OK  {name}\n{stdout}")
                else:
                    fail_count += 1
                    self._put("log", text="FAILED\n", tag="err")
                    err_text = stderr or stdout
                    for line in err_text.splitlines()[-6:]:
                        self._put("log", text=f"    {line}\n", tag="err")
                    log_lines.append(f"FAIL  {name}\n{err_text}")

            except Exception as ex:
                fail_count += 1
                self._put("log", text=f"ERROR: {ex}\n", tag="err")
                log_lines.append(f"ERROR  {name}: {ex}")

        self._put("progress", done=len(files), total=len(files), name="")
        summary = f"\n--- Done: OK={ok_count}  FAIL={fail_count} ---\n"
        self._put("log", text=summary,
                  tag="ok" if fail_count == 0 else "warn")

        if save_log and last_out:
            try:
                log_dir = (Path(last_out)
                           if Path(last_out).is_dir()
                           else Path(last_out).parent)
                log_dir.mkdir(parents=True, exist_ok=True)
                log_path = log_dir / "extract_log.txt"
                log_path.write_text(
                    f"Crimson Desert PAM Extractor log\n"
                    f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"{'='*60}\n" + "\n".join(log_lines) + summary,
                    encoding="utf-8"
                )
                self._put("log", text=f"  Log saved: {log_path}\n", tag="dim")
            except Exception as e:
                self._put("log", text=f"  Could not save log: {e}\n", tag="warn")

        if open_out and last_out:
            out_path = Path(last_out)
            folder_to_open = out_path if out_path.is_dir() else out_path.parent
            try:
                subprocess.Popen(
                    ["powershell", "-noprofile", "-command",
                     f"Start-Process explorer '{folder_to_open}'"]
                )
            except Exception:
                pass

        self._put("done", ok=(fail_count == 0))

    def _on_done(self, ok=True):
        self._running = False
        self._run_btn.configure(state="normal")
        self._status_var.set("Completed - all OK." if ok
                             else "Completed with errors.")


# -- Entry point --------------------------------------------------------------
if __name__ == "__main__":
    app = App()
    app.mainloop()
