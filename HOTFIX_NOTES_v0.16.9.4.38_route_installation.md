# DiTuS v0.16.9.4.38 — Route / Installation / Bonding UI Integrity Hotfix

This hotfix keeps all calculation and model engines byte-identical to the locked v0.16.9.4.38 baseline.

## UI / data-integrity fixes

- Project-tree route sections open the route object editor directly instead of forcing the workflow wizard stage.
- `Bölüm Ekle / Seçili Bölümü Düzenle / Seçili Bölümü Sil / Mevcut Güzergâhı Kabul Et` remain visible above the expandable route table.
- Direct object editors can hide StageHost workflow chrome without changing the workflow implementation.
- Automatic installation-geometry regeneration never round-trips circuit RMS current through formatted UI text; full-precision model values are preserved.
- Installation type changes use the new installation type's coherent parametric envelope rather than carrying a previous installation envelope into the new drawing.
- DUCT_BANK previews are anchored to the actual duct/cable cluster; CONCRETE_TROUGH and HDD previews use the same cable-cluster vertical basis used by the corresponding numerical representation.
- Explicit UI integrity findings are shown for missing duct layouts/assignments, duct/cable coordinate mismatch, cable-too-large-for-duct, cable intersection with trough walls, and cable outside an HDD bore.
- Incomplete DUCT/HDD/trough geometry is marked directly on the engineering canvas instead of being presented as a valid section.
- `OperatingScenarioInputError` is treated as a bonding input error, not an unexpected solver failure. Circuit-current inconsistencies list every affected cross-section with full precision, the maximum difference, likely cause, and corrective action.

## Locked engine statement

`src/ucd/calculations/**/*.py` and `src/ucd/models/**/*.py` are byte-identical to the pre-hotfix v0.16.9.4.38 engine baseline (53/53 files).
