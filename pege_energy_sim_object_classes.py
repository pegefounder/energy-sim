"""Object classes: 24×365, agricultural, charging station, factory, pipeline, storage, transmission, transport."""

OBJECT_CLASSES = [
    "24×365",
    "agricultural",
    "charging station",
    "factory",
    "pipeline",
    "storage",
    "transmission",
    "transport",
]

def object_classes_html() -> str:
    buttons = []
    for cls in OBJECT_CLASSES:
        slug = cls.replace("×", "x").replace(" ", "-")
        buttons.append(
            f'<a href="/object-classes/{slug}" class="nav" style="margin-right:14px">{cls}</a>'
        )
    nav = "\n  ".join(buttons)
    return f"""
<h2>Object classes</h2>
<nav style="margin-bottom:14px">
  {nav}
</nav>
<p class="hint">Coming soon.</p>
<p><a href="/form">Back to main</a></p>
"""

def object_class_page(cls: str) -> str:
    buttons = []
    for c in OBJECT_CLASSES:
        slug = c.replace("×", "x").replace(" ", "-")
        on = " on" if c == cls else ""
        buttons.append(
            f'<a href="/object-classes/{slug}" class="nav{on}" style="margin-right:14px">{c}</a>'
        )
    nav = "\n  ".join(buttons)
    return f"""
<h2>Object classes</h2>
<nav style="margin-bottom:14px">
  {nav}
</nav>
<style>
.nav.on {{ background:#003399; color:#fff; padding:4px 10px; border-radius:4px; }}
</style>
<h3>{cls}</h3>
<p class="hint">Coming soon.</p>
<p><a href="/object-classes">Back to object classes</a></p>
"""
