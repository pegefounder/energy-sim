"""Compare communities."""

def compare_communities_html() -> str:
    return """
<h2>Compare communities</h2>
<p class="hint">Difference is B − A. Same colour rules as house compare.</p>
<div class="row">
  <div class="field name"><label>Community A</label><select id="cA"><option value="">— select —</option></select></div>
  <div class="field wide"><label>Simulation A</label><select id="sA"><option value="">— file —</option></select></div>
</div>
<div class="row">
  <div class="field name"><label>Community B</label><select id="cB"><option value="">— select —</option></select></div>
  <div class="field wide"><label>Simulation B</label><select id="sB"><option value="">— file —</option></select></div>
</div>
<p class="hint" id="cStatus"></p>
<div class="actions"><button type="button" id="cBtn" disabled>Compare — write PDF (soon)</button></div>
<p><a href="/form">Back to main</a></p>
<script>
function fill(sel, obj) {
  sel.innerHTML = "<option value=''>— file —</option>";
  if (!obj) return;
  fetch("/api/object-files?object=" + encodeURIComponent(obj)).then(r => r.json()).then(data => {
    (data.files || []).forEach(f => {
      const o = document.createElement("option"); o.value = f.path; o.textContent = f.name; sel.appendChild(o);
    });
  });
}
fetch("/api/workspace").then(r => r.json()).then(d => {
  ["cA","cB"].forEach(id => {
    const sel = document.getElementById(id);
    (d.objects || []).forEach(n => {
      const o = document.createElement("option"); o.value = n; o.textContent = n; sel.appendChild(o);
    });
  });
});
document.getElementById("cA").addEventListener("change", function() { fill(document.getElementById("sA"), this.value); });
document.getElementById("cB").addEventListener("change", function() { fill(document.getElementById("sB"), this.value); });
</script>
"""
