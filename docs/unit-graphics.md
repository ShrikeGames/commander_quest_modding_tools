# Unit graphics

How a summoned unit is drawn, and what it would take to change it.

## The chain

A summon card names a Blueprint class, and that Blueprint pulls in the visuals:

```
DA_Card_Summon_Human_Assassin
  summonUnit -> BP_Unit_Assassin            (BlueprintGeneratedClass)
  UnitData   -> DA_Unit_Human_Assassin      (stats, tags)

BP_Unit_Assassin references
  /Game/ArtAssets/Model/Human/Human_Assasin                       SkeletalMesh
  /Game/ArtAssets/Animation/Human/ABP_Human_Assassin              AnimBlueprint
  /Game/ArtAssets/Model/Human/Human_Texture/MI_Human_Assasin      MaterialInstance
  /Game/ArtAssets/Model/Human/Human_Texture/MI_Human_Assasin_Enemy
  /Game/Core/BP_Minion                                            parent class

MI_Human_Assasin references
  /Game/ArtAssets/Model/Human/Human_Texture/T_Human_Assasin_D     Texture2D
```

So there are three ways in, of very different difficulty.

## Retexturing: possible, but harder than card art

Card art is `PF_B8G8R8A8`, uncompressed BGRA, which is why replacing it is a
resize and a channel swap. Unit textures are not:

| format | textures |
|---|---|
| `PF_DXT1` | 292 |
| `PF_DXT5` | 9 |

DXT1 and DXT5 are block-compressed (BC1 and BC3), so writing one means encoding
4x4 pixel blocks rather than copying bytes, and these textures carry mip chains
that would need regenerating to keep the payload length intact. Both are
well-documented and implementable, but it is a real piece of work rather than
the splice that card art allows.

## Swapping a mesh: the cheap option

The Blueprint reaches its mesh through an object reference, which is a four-byte
package index, and references to other packages can be added the same way tag
names are. Pointing one unit's Blueprint at another unit's mesh and material is
therefore within reach of the machinery that already exists, and it gives you
"the assassin now looks like a cataphract" without any texture encoding.

## Replacing a mesh outright: not practical

`Human_Assasin` is a `SkeletalMesh`: vertex buffers, skin weights, a skeleton and
bone bindings, all cooked into a platform-specific layout. Authoring one means
writing a mesh cooker, and the animation Blueprint expects a matching skeleton.
This is out of scope.

## Summary

| change | difficulty |
|---|---|
| Card art | done, splice the pixels |
| Unit texture | needs a BC1/BC3 encoder and mip regeneration |
| Point a unit at different existing art | needs import insertion, similar to name insertion |
| New skeletal mesh | impractical |
