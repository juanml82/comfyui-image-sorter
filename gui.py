#!/usr/bin/env python3
"""GUI launcher for the ComfyUI image organizer pipeline."""

import os
import re
import subprocess
import sys
import threading
import signal
from pathlib import Path

import customtkinter as ctk

# ── Paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.py"

# (label, script, output_file_or_None, system_prompt_file_or_None)
STEPS_LEFT = [
    ("1. Extract Prompts", "1_extract.py",      "extract_results.jsonl", None),
    ("2. Canonicalize",    "2_canonicalize.py", "canonicalized.jsonl",  "2_canonicalize.py"),
]
STEPS_RIGHT = [
    ("1b. Tag with Vision",      "1b_vision_tag.py",          "vision_tags.jsonl",          "1b_vision_tag.py"),
    ("2b. Canonicalize Vision",  "2b_canonicalize_vision.py", "vision_canonicalized.jsonl", "2b_canonicalize_vision.py"),
]
STEPS_FULL = [
    ("3. Merge",   "3_merge.py",  "merged.jsonl", "3_merge.py"),
    ("4. Preview", "4_preview.py", "__NO_OPEN__", None),
    ("5. Apply",   "5_apply.py",   None,           None),
]

ALL_STEPS = STEPS_LEFT + STEPS_RIGHT + STEPS_FULL

# Fixed control widths so the name │ Run │ Open │ Prompt columns tabulate cleanly
RUN_W, OPEN_W, PROMPT_W = 64, 58, 92


def read_config() -> dict:
    """Parse config.py for path variables."""
    if not CONFIG_FILE.exists():
        return {"IMG_DIR": "", "out_root": "",
                "API_BASE": "", "API_MODEL": "", "API_KEY": ""}
    text = CONFIG_FILE.read_text()
    cfg = {}
    for key in ("IMG_DIR", "out_root", "API_BASE", "API_MODEL", "API_KEY"):
        m = re.search(
            rf'^{key}\s*(?::\s*\w+)?\s*=\s*["\']([^"\']*)["\']',
            text, re.MULTILINE,
        )
        cfg[key] = m.group(1) if m else ""
    return cfg


def save_config(cfg: dict) -> str:
    """Rewrite path variables in config.py, preserving everything else."""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_FILE}")

    text = CONFIG_FILE.read_text()
    original = text

    for key, val in cfg.items():
        pattern = (
            rf'^(#{key}[^\n]*\n)?'
            rf'\s*{key}\s*(?::\s*\w+)?\s*='
            rf'\s*(["\']).*?\2\s*(#.*)?$'
        )
        replacement = f'{key} = "{val}"'
        text, n = re.subn(pattern, replacement, text, flags=re.MULTILINE)
        if n == 0:
            raise ValueError(
                f"Line for {key!r} not found in {CONFIG_FILE.name}. "
                f"Expected a line like:  {key} = \"...\""
            )

    if text != original:
        CONFIG_FILE.write_text(text)
    return text


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ComfyUI Image Organizer")
        self.geometry("900x800")
        self.minsize(720, 600)

        self._running = False
        self._preview_proc = None
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI layout ────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Config frame ─────────────────────────────────────────────
        ctk.CTkLabel(self, text="Configuration",
                     font=("Arial", 14, "bold"), anchor="w").pack(
            fill="x", padx=12, pady=(12, 0))
        cfg_frame = ctk.CTkFrame(self)
        cfg_frame.pack(fill="x", padx=12, pady=4)

        self.cfg_vars = {}
        rows = [
            ("IMG_DIR",   "Image folder:"),
            ("out_root",  "Output folder:"),
            ("API_BASE",  "LLM endpoint:"),
            ("API_MODEL", "Model name:"),
            ("API_KEY",   "API key:"),
        ]
        for i, (key, label) in enumerate(rows):
            ctk.CTkLabel(cfg_frame, text=label, anchor="w").grid(
                row=i, column=0, padx=8, pady=3, sticky="ew")
            entry_kwargs = {"show": "*"} if key == "API_KEY" else {}
            entry = ctk.CTkEntry(cfg_frame, **entry_kwargs)
            entry.grid(row=i, column=1, padx=4, pady=3, sticky="ew")
            self.cfg_vars[key] = entry

        cfg_frame.columnconfigure(1, weight=1)

        save_btn = ctk.CTkButton(
            cfg_frame, text="💾 Save Config  ⚠ Do it!", command=self._save_cfg)
        save_btn.grid(row=len(rows), column=1, padx=8, pady=(4, 8), sticky="e")

        # Populate entries from file
        cfg = read_config()
        for key, entry in self.cfg_vars.items():
            entry.insert(0, cfg.get(key, ""))

        # ── Thinking warning (high-contrast banner) ──────────────────
        self._warn_frame = ctk.CTkFrame(
            self,
            fg_color=("#fdf1cf", "#3a2c10"),      # amber bg: light & dark theme
            border_width=1,
            border_color=("#d97706", "#b45309"),  # visible outline in both themes
        )
        self._warn_frame.pack(fill="x", padx=12, pady=(8, 0))

        self._thinking_warn = ctk.CTkLabel(
            self._warn_frame,
            text="⚠ Ensure thinking/reasoning is DISABLED on your LLM server for steps # 1 and # 2 "
                 "(e.g. -rea off in llama.cpp, or --jinja with a non-thinking model). "
                 "Chain-of-thought WILL fail in those steps.",
            font=("Arial", 12, "bold"),           # was plain Arial 11 — bigger & bolder
            text_color=("#b91c1c", "#fca5a5"),    # strong red: light & dark theme
            wraplength=860, anchor="w", justify="left")
        self._thinking_warn.pack(fill="x", padx=12, pady=(7, 7))

        def _update_warning_wrap(event):          # track window width for wrapping
            w = event.widget.winfo_width()
            if w > 60:
                self._thinking_warn.configure(
                    wraplength=max(300, w - 28))
        self._warn_frame.bind("<Configure>", _update_warning_wrap)

        # ── Pipeline section title ───────────────────────────────────
        ctk.CTkLabel(self, text="Pipeline Steps",
                     font=("Arial", 14, "bold"), anchor="w").pack(
            fill="x", padx=12, pady=(10, 0))

        # Initialise button trackers
        self.step_buttons = []
        self.open_buttons = []
        self.edit_buttons = []

        # ── Two-column top section (tabulated grids) ─────────────────
        cols_frame = ctk.CTkFrame(self, fg_color="transparent")
        cols_frame.pack(fill="x", padx=12, pady=4)
        cols_frame.columnconfigure(0, weight=1)
        cols_frame.columnconfigure(1, weight=1)

        ctk.CTkLabel(cols_frame, text="Prompt-based",
                     font=("Arial", 11), text_color="#888888",
                     anchor="w").grid(
            row=0, column=0, sticky="ew", padx=(0, 4), pady=(4, 0))
        ctk.CTkLabel(cols_frame, text="Vision-based",
                     font=("Arial", 11), text_color="#888888",
                     anchor="w").grid(
            row=0, column=1, sticky="ew", padx=(4, 0), pady=(4, 0))

        ctk.CTkFrame(cols_frame, height=1, fg_color="#3b4261",
                     corner_radius=0).grid(
            row=1, column=0, sticky="ew", padx=(0, 4), pady=(2, 4))
        ctk.CTkFrame(cols_frame, height=1, fg_color="#3b4261",
                     corner_radius=0).grid(
            row=1, column=1, sticky="ew", padx=(4, 0), pady=(2, 4))

        left_grid = ctk.CTkFrame(cols_frame, fg_color="transparent")
        left_grid.grid(row=2, column=0, sticky="ew", padx=(0, 8), pady=(0, 6))
        for i, (name, script, output, prompt_file) in enumerate(STEPS_LEFT):
            self._tabular_row(left_grid, i, name, script, output, prompt_file)

        right_grid = ctk.CTkFrame(cols_frame, fg_color="transparent")
        right_grid.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(0, 6))
        for i, (name, script, output, prompt_file) in enumerate(STEPS_RIGHT):
            self._tabular_row(right_grid, i, name, script, output, prompt_file)

        # ── Shared downstream (centered tabulated block) ─────────────
        full_frame = ctk.CTkFrame(self, fg_color="transparent")
        full_frame.pack(fill="x", padx=12, pady=4)

        header_row = ctk.CTkFrame(full_frame, fg_color="transparent")
        header_row.pack(anchor="center")
        ctk.CTkLabel(header_row, text="Shared downstream",
                     font=("Arial", 11), text_color="#888888",
                     anchor="w").pack(side="left")
        ctk.CTkFrame(header_row, width=200, height=1,
                     fg_color="#3b4261", corner_radius=0).pack(
            side="left", fill="x", expand=True, padx=(12, 0))

        shared_grid = ctk.CTkFrame(full_frame, fg_color="transparent")
        shared_grid.pack(anchor="center", pady=(2, 4))
        for i, (name, script, output, prompt_file) in enumerate(STEPS_FULL):
            self._tabular_row(shared_grid, i, name, script, output, prompt_file)

        # ── Output console ───────────────────────────────────────────
        ctk.CTkLabel(self, text="Console Output",
                     font=("Arial", 14, "bold"), anchor="w").pack(
            fill="x", padx=12, pady=(10, 0))
        out_frame = ctk.CTkFrame(self)
        out_frame.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        self.console = ctk.CTkTextbox(
            out_frame, wrap="word", font=("Monospace", 11))
        self.console.pack(fill="both", expand=True, padx=4, pady=4)
        self.console.configure(state="disabled")

        self._log("Ready. Edit config above, click Save Config, then Run a step.")

    # ── Tabulated pipeline row (grid columns: name │ Run │ Open │ Prompt)
    def _tabular_row(self, parent, row: int,
                     name: str, script: str,
                     output: str | None, prompt_file: str | None):
        """One tabulated row. All rows in the same grid frame share column
        widths, so Run / Open / Prompt line up vertically regardless of how
        long the step label is."""
        ctk.CTkLabel(parent, text=name, anchor="w").grid(
            row=row, column=0, sticky="ew", padx=(4, 10), pady=3)

        run_btn = ctk.CTkButton(
            parent, text="▶ Run", width=RUN_W,
            command=lambda s=script: self._run_step(s))
        run_btn.grid(row=row, column=1, padx=(0, 4))

        if output is None or output == "__NO_OPEN__":
            # No meaningful Open action (Apply opens out_root via None;
            # Preview is opened automatically by Run)
            if output is None:
                open_btn = ctk.CTkButton(
                    parent, text="Open", width=OPEN_W,
                    fg_color="#555", hover_color="#666",
                    command=lambda o=output: self._open_output(o))
                open_btn.grid(row=row, column=2, padx=(0, 4))
                self.open_buttons.append(open_btn)
            # "__NO_OPEN__" → placeholder so columns stay aligned
            else:
                ctk.CTkLabel(parent, text="", width=OPEN_W).grid(
                    row=row, column=2, padx=(0, 4))
        else:
            open_btn = ctk.CTkButton(
                parent, text="Open", width=OPEN_W,
                fg_color="#555", hover_color="#666",
                command=lambda o=output: self._open_output(o))
            open_btn.grid(row=row, column=2, padx=(0, 4))
            self.open_buttons.append(open_btn)

        if prompt_file:
            edit_btn = ctk.CTkButton(
                parent, text="✏️ Prompt", width=PROMPT_W,
                fg_color="#444", hover_color="#555",
                command=lambda f=prompt_file: self._edit_prompt(f))
            edit_btn.grid(row=row, column=3)
            self.edit_buttons.append(edit_btn)

        self.step_buttons.append(run_btn)

    # ── Actions ──────────────────────────────────────────────────────
    def _log(self, msg: str):
        self.console.configure(state="normal")
        self.console.insert("end", msg + "\n")
        self.console.see("end")
        self.console.configure(state="disabled")

    def _save_cfg(self):
        vals = {k: e.get().strip() for k, e in self.cfg_vars.items()}
        try:
            save_config(vals)
            self._log(f"✓ Config saved → {CONFIG_FILE}")
        except Exception as e:
            self._log(f"✗ Config save failed: {e}")

    def _run_step(self, script: str):
        # ── Kill any lingering preview server ──────────────────────
        if self._preview_proc and self._preview_proc.poll() is None:
            self._log("↩ Stopping preview server…")
            self._preview_proc.send_signal(signal.SIGINT)
            try:
                self._preview_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._preview_proc.kill()
            self._preview_proc = None

        if self._running:
            self._log("⚠ Already running. Wait for it to finish.")
            return

        is_preview = (script == "4_preview.py")

        if not is_preview:
            self._running = True
            self._set_buttons_state("disabled")

        self._log(f"\n{'=' * 50}\n▶ {script}\n{'=' * 50}")

        env = {**os.environ, "PYTHONPATH": str(SCRIPT_DIR)}
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT_DIR / script)],
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )

        if is_preview:
            self._preview_proc = proc

            def pump_preview():
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    self.after(0, self._log, line)

            threading.Thread(target=pump_preview, daemon=True).start()
            # Re-enable buttons shortly after the server starts up
            self.after(1500, self._set_buttons_state, "normal")
        else:
            def pump():
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    self.after(0, self._log, line)
                proc.wait()
                self.after(0, self._step_done, proc.returncode, script)

            threading.Thread(target=pump, daemon=True).start()

    def _step_done(self, code: int, script: str):
        self._running = False
        self._set_buttons_state("normal")
        status = "✓ done" if code == 0 else f"✗ exited with code {code}"
        self._log(f"{status}: {script}")

    def _set_buttons_state(self, state: str):
        for btn in self.step_buttons:
            btn.configure(state=state)
        for btn in self.open_buttons:
            btn.configure(state=state)
        for btn in self.edit_buttons:
            btn.configure(state=state)

    def _open_output(self, output: str | None):
        cfg = read_config()
        if output is None:
            target = Path(cfg["out_root"])
        else:
            target = Path(cfg["IMG_DIR"]) / output

        if not target.exists():
            self._log(f"⚠ Not found: {target}")
            return

        subprocess.Popen(["xdg-open", str(target)])
        self._log(f"Opened: {target}")

    def _on_close(self):
        if self._preview_proc and self._preview_proc.poll() is None:
            self._preview_proc.send_signal(signal.SIGINT)
            try:
                self._preview_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._preview_proc.kill()
        self.destroy()

    def _edit_prompt(self, label: str):
        """Open config.py in the platform's default GUI text editor."""
        target = CONFIG_FILE
        if not target.exists():
            self._log(f"⚠ Not found: {target}")
            return

        if sys.platform == "win32":
            os.startfile(str(target))          # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])

        self._log(f"✏️ Opened config.py — look for {label} to edit")


if __name__ == "__main__":
    app = App()
    app.mainloop()
