"""Community simulation."""

def community_html() -> str:
    return """
<h2>Community</h2>
<p class="hint">Community runs (DC bus between houses, shared storage, power-to-X) — version 1.0 feature. Yield and demand shared with house.</p>
<div class="row">
  <div class="field name"><label>Community object</label><select id="commObj"><option value="">— select —</option></select></div>
</div>
<p class="hint">Select houses that belong to the community. Full simulation engine will be added.</p>
<div class="actions">
  <button type="button" disabled>Run community simulation (soon)</button>
</div>
<p><a href="/form">Back to main</a></p>
<script>
fetch("/api/workspace").then(r => r.json()).then(d => {
  const sel = document.getElementById("commObj");
  (d.objects || []).forEach(n => {
    const o = document.createElement("option"); o.value = n; o.textContent = n; sel.appendChild(o);
  });
});
</script>
"""
