# A&S-Additions
Fanproject adding some extra stuff into Actions & Stuff

It's Currently not updated for 1.12
So many models are broken currently.

## Repair renamed model identifiers

When an Actions & Stuff update renames geometry identifiers, the helper script
can compare the old and new resource packs and update references in this pack.
It only changes IDs whose geometry data matches exactly and whose replacement
is unambiguous. If geometry has changed, it can also match the same entity
variant key in both versions' entity files, provided that points to one
unambiguous model. This includes model identifiers defined in this pack, so
local models that still use an old source-pack ID can be renamed too.

Run a preview first (PowerShell example):

```powershell
py tools\repair_model_names.py `
  --target . `
  --old-pack "F:\Downloads\ToolCoin\Actions Stuff 1.12 (resources)" `
  --new-pack "F:\Downloads\ToolCoin\Actions Stuff 1.13 (resources)"
```

Add `--apply` after reviewing the preview to write the changes. Unmatched or
ambiguous IDs are reported and left unchanged.

## Generate PBR texture maps

Run `py tools\texture_pbr_gui.py` to generate MER, MERS, and normal maps from
source textures. MER uses palette matching; MERS uses albedo pattern matching
to transfer its per-pixel channels, including subsurface data in the alpha
channel. For mob textures with different UV layouts, choose **Model UV remap
(merge target MER)** and provide source and target model JSON files. The tool
transfers the source MERS alpha face-by-face using the model UV rectangles and
cubic resampling, then keeps the target MER RGB channels unchanged. The models
must have corresponding cuboids with matching position, size, rotation, and
face definitions, and each JSON must contain one geometry entry; ambiguous or
unmatched geometry is rejected. Conflicting face values on overlapping target
UVs are rejected by default; choose **Average subsurface alpha** to average
their alpha values per target texel.

Model UV mode uses a target `<name>_mer.png` already in the output folder when
MER is not generated in the same run. If MER is enabled, its newly generated
map is used instead. The source MERS map is paired with the source model, and
target albedos and MER maps must share dimensions and UV layout as described by
the target model.

For identical UV layouts, choose **Copy source (matching layout)** for exact
pixel-position transfer. Pattern matching is an estimate when the layouts
differ. Select MER and MERS independently. If both are generated, both PNGs
are saved, while the texture-set JSON references MERS because Bedrock uses one
packed MER/MERS map per texture set.


# Additions
Pixel Consistency
--------
A big section of this project is Pixel Consistency due to me being a giant `nitpicker` currently i have done 
the Cave Spider, Ghast and Friendly ghast but not yet all harnesses, the Elder Guardian, Big Slime and Big Magmacube, the Beacons Beam, Tripwire and Tripwire hook, Banners and alot more miscellaneous stuff

More 3D Foliage
--------
Following in the steps of the Mushrooms added Saplings, Sugarcane
