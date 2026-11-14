"""
Fit a window to the screen it's on.

ScrollArea holds a window's content. When the window is taller than
the content, the content stretches to fill it (so the History box can
grow). When it's shorter, as on a 768-pixel laptop screen or with big
text, a scrollbar appears instead of the bottom being cut off.
"""

import tkinter as tk
from tkinter import ttk


class ScrollArea(ttk.Frame):
    def __init__(self, parent, bg, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._bar_set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self._bar_shown = False
        self.body.bind("<Configure>", lambda _e: self._fit())
        self.canvas.bind("<Configure>", lambda _e: self._fit())
        # wheel over the window scrolls it, except over a widget that scrolls itself
        top = self.winfo_toplevel()
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            top.bind(seq, self._wheel, add="+")

    def _fit(self):
        need = self.body.winfo_reqheight()
        have = self.canvas.winfo_height()
        self.canvas.itemconfigure(self._win, width=self.canvas.winfo_width(), height=max(need, have))
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), max(need, have)))

    def _bar_set(self, lo, hi):
        show = not (float(lo) <= 0.0 and float(hi) >= 1.0)
        if show != self._bar_shown:
            self._bar_shown = show
            if show:
                self.bar.pack(side="right", fill="y", before=self.canvas)
            else:
                self.bar.pack_forget()
        self.bar.set(lo, hi)

    def _wheel(self, event):
        if not self._bar_shown:
            return
        w = event.widget
        if isinstance(w, (tk.Text, tk.Listbox)) or (isinstance(w, str) and "listbox" in w.lower()):
            return                          # it scrolls itself
        if isinstance(w, tk.Misc) and w.winfo_toplevel() is not self.winfo_toplevel():
            return
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            self.canvas.yview_scroll(-3, "units")
        else:
            self.canvas.yview_scroll(3, "units")

    def needed_height(self):
        self.update_idletasks()
        return self.body.winfo_reqheight()


def fit_to_screen(win, want_w, want_h, margin=90, saved=None):
    """Size a window: the saved size if it still fits this screen, else
    what it wants, but never taller or wider than the screen."""
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = want_w, want_h
    if saved:
        try:
            sw_, sh_ = (int(x) for x in str(saved).split("x")[:2])
            w, h = sw_, sh_
        except ValueError:
            pass
    w = max(200, min(w, sw - 40))
    h = max(200, min(h, sh - margin))
    win.geometry(f"{w}x{h}")
    return w, h


TEXT_SIZES = {"small": 0.9, "normal": 1.0, "large": 1.2}


def text_scale(name):
    return TEXT_SIZES.get(str(name or "normal").lower(), 1.0)
