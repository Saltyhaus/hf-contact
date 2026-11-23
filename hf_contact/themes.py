"""
Looks: AG7JC (charcoal, bone and amber -- the same as OffGrid Comms), Dark,
Night (all red, for night vision), Light, and Classic (the original teal
and orange). Picked in Settings; applies when HF Contact starts (Settings
offers to restart it).
"""

# BG window, PANEL cards, FIELD text boxes, FG text, DIM quiet text,
# ACCENT links / the call / echo lines, WARM title / sent lines,
# BTN buttons, GO the main button (GO_FG its text), STOP hang up.
THEMES = {
    "ag7jc": dict(label="AG7JC",
                  BG="#121417", PANEL="#1a1d21", FIELD="#0f1114", FG="#F3F1EC", DIM="#9a958b",
                  ACCENT="#F2A93B", WARM="#F2A93B", GREEN="#5cbf73", RED="#ff6b5e", GRAY="#7d7a73",
                  BORDER="#2c3036", BTN="#2c3036", BTN_ACTIVE="#383d44", BTN_PRESS="#1f2328",
                  GO="#F2A93B", GO_ACTIVE="#f5b95c", GO_PRESS="#c9862a", GO_FG="#121417",
                  STOP="#b23a2f", STOP_ACTIVE="#c94a3e", STOP_PRESS="#8a2c23", STOP_FG="#ffffff",
                  TITLE_FONT=("TkFixedFont", 16, "bold")),
    "dark": dict(label="Dark",
                 BG="#222226", PANEL="#2e2e32", FIELD="#1d1d20", FG="#f2f2f4", DIM="#9a9aa2",
                 ACCENT="#78aeed", WARM="#ffa348", GREEN="#5cc275", RED="#ff7b63", GRAY="#8b8b93",
                 BORDER="#3a3a40", BTN="#3a3a40", BTN_ACTIVE="#46464d", BTN_PRESS="#2a2a2e",
                 GO="#3584e4", GO_ACTIVE="#4a93ea", GO_PRESS="#1c62c4", GO_FG="#ffffff",
                 STOP="#c01c28", STOP_ACTIVE="#d42a36", STOP_PRESS="#9a141f", STOP_FG="#ffffff"),
    "night": dict(label="Night (red)",
                  BG="#000000", PANEL="#0c0000", FIELD="#000000", FG="#ff6b5e", DIM="#a8433a",
                  ACCENT="#ff8a7e", WARM="#ff6b5e", GREEN="#ff4a3a", RED="#ff2a1f", GRAY="#6b1a14",
                  BORDER="#3a0a07", BTN="#2a0503", BTN_ACTIVE="#3d0703", BTN_PRESS="#1c0200",
                  GO="#5a0c06", GO_ACTIVE="#6e1008", GO_PRESS="#3d0703", GO_FG="#ff8a7e",
                  STOP="#7a0f08", STOP_ACTIVE="#8e130a", STOP_PRESS="#5a0c06", STOP_FG="#ff8a7e"),
    "light": dict(label="Light",
                  BG="#f6f6f8", PANEL="#ffffff", FIELD="#fafafb", FG="#1f1f1f", DIM="#6b6b70",
                  ACCENT="#1a5fb4", WARM="#c64600", GREEN="#26803a", RED="#c01c28", GRAY="#9a9aa0",
                  BORDER="#d6d6dc", BTN="#e6e6ea", BTN_ACTIVE="#dcdce2", BTN_PRESS="#d0d0d6",
                  GO="#3584e4", GO_ACTIVE="#4a93ea", GO_PRESS="#1c62c4", GO_FG="#ffffff",
                  STOP="#c01c28", STOP_ACTIVE="#d42a36", STOP_PRESS="#9a141f", STOP_FG="#ffffff"),
    "classic": dict(label="Classic (teal)",
                    BG="#12161a", PANEL="#1a1f24", FIELD="#0c0f12", FG="#cfd8dc", DIM="#7d8a94",
                    ACCENT="#00d9d9", WARM="#ff9500", GREEN="#33cc66", RED="#ff4444", GRAY="#5a6570",
                    BORDER="#262d33", BTN="#262d33", BTN_ACTIVE="#323a42", BTN_PRESS="#1b2026",
                    GO="#009a9a", GO_ACTIVE="#00b3b3", GO_PRESS="#00797a", GO_FG="#ffffff",
                    STOP="#b71c1c", STOP_ACTIVE="#d32626", STOP_PRESS="#7f1010", STOP_FG="#ffffff"),
}
DEFAULT = "ag7jc"
CHOICES = [(k, t["label"]) for k, t in THEMES.items()]


def get(name):
    """The palette for a theme name (unknown names get the default)."""
    return THEMES.get((name or "").lower(), THEMES[DEFAULT])


def by_label(label):
    return next((k for k, t in THEMES.items() if t["label"] == label), DEFAULT)
