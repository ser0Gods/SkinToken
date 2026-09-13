# Bone-naming mappings

## Where the mapping files are

- Folder: `D:\GenerativeAI\SkinToken\mappings\`
- One JSON file per target naming convention: `<convention>.json`
- Current conventions:
  - `mesh2motion.json` — the mesh2motion convention (reference asset: `example_human.glb` in the project root, 66 named joints)

## Format

Flat JSON object:

- key = `bone_<n>` — index-named bone as emitted by the SkinTokens rigging pipeline
- value = target bone name in the convention

The rename step loads `mappings\<convention>.json` and renames every listed bone.
Bones missing from the file are left untouched.

## mesh2motion.json — notes

- 52 entries, `bone_0` … `bone_51` (taken from the Adept rigged run).
- 14 template names have **no matching bone** in this skeleton and therefore
  stay unmapped by design:
  - `neck_01`
  - `head_leaf`
  - `index_04_leaf` / `middle_04_leaf` / `ring_04_leaf` / `pinky_04_leaf` / `thumb_04_leaf` — both hands (10)
  - `ball_leaf_r`, `ball_leaf_l`
- Conventions used:
  - `bone_5` → `head` (the skeleton has no neck bone; it could be read as `neck_01` instead)
  - finger-chain identities (which chain is index vs. middle vs. ring vs. pinky vs. thumb)
    are inferred from pose geometry — verify visually on a renamed GLB
  - side convention: in this skeleton `+X` is the character's right (character faces
    `+Z`), so the `bone_6`/`bone_44` chains are named `_r` and the `bone_25`/`bone_48` chains `_l`

## Making a mapping for a new asset / convention

1. Print the skeleton: `python show_skeleton.py <rigged.glb>` (script in project root)
2. Compare `bone_<n>` indices against the convention's tree
3. Add a new `<convention>.json` in this folder (or adjust `bone_<n>` keys in an existing
   file); target names (values) stay the same across conventions of the same convention
