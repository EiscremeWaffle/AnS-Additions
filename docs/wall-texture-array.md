# Wall Texture Array

This documents the state-index mapping used by `texture_data.polished_tuff_wall_side` in `textures/terrain_texture.json`. The array is indexed by the wall's `E`, `N`, `S`, `W`, and `Post` state values. Indices are zero-based: the first string in the array is index 0.

## Index Formula

Map each directional state to a number:

| State | Number |
| --- | ---: |
| `none` | 0 |
| `short` | 1 |
| `tall` | 2 |

Then calculate:

```text
index = (Post ? 1 : 0) + 8*E + 2*N + 32*S + 128*W
```

`Post` is `true`/`false`; it adds 1 or 0. The directional weights are positional state fields, not a count of connections. Values `none`, `short`, and `tall` are the only valid directional values; the unused fourth two-bit value is not a wall state.

There are 81 directional combinations for each `Post` value, or 162 reachable state combinations total. The highest reachable index is 341. An array entry is reachable only if it is one of the indices produced by the formula; gaps between valid indices and entries after 341 are not valid wall states. Those slots should use `textures/misc/missing_texture`.

## Post Rules

All 81 directional combinations can be represented when `Post=true`.

When `Post=false`, only these four patterns are valid:

| Connections | State pattern | Index |
| --- | --- | ---: |
| North + South, short | `E=none N=short S=short W=none` | 34 |
| North + South, tall | `E=none N=tall S=tall W=none` | 68 |
| East + West, short | `E=short N=none S=none W=short` | 136 |
| East + West, tall | `E=tall N=none S=none W=tall` | 272 |

Every other `Post=false` combination is invalid: this includes fewer or more than two connections, adjacent directions, or opposite arms with different heights. Keep its state comment for diagnosis, but set its texture path to `textures/misc/missing_texture`.

## some Examples

| state | Index | texture in the current array |
| --- | ---: | --- |
| `E=none N=short S=short W=none Post=false` | 34 | `textures/blocks/sandstone_smooth` |
| `E=short N=none S=none W=short Post=false` | 136 | `textures/blocks/diamond_block` |
| `E=tall N=none S=none W=tall Post=false` | 272 | `textures/blocks/birch_trapdoor` |
| `E=tall N=tall S=none W=tall Post=true` | 277 | `textures/blocks/iron_bars` |
| `E=tall N=tall S=tall W=tall Post=true` | 341 | `textures/blocks/rail_detector` |

The weights and examples were checked against the state annotations in the current array. When editing this list, derive indices from the formula rather than counting source lines: comments and formatting do not affect array indices.