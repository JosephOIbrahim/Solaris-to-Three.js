# The lighting bridge

Needle's render delegate creates three.js lights from USD lights, but it does not convert their units and it leaves dome lights alone. The bridge in `viewer/main.js` does both. This page is the arithmetic, so you can predict what a light will do before you open the page.

## Two unit systems

**USD lights emit radiance; three.js punctual lights emit luminous intensity.** A USD `RectLight` with intensity 10 means every point on its surface glows at 10, so a bigger light puts out more. A three.js `PointLight` or `SpotLight` has no surface; its `intensity` is candela, the total output in a direction. Converting means multiplying by the emitting area.

USD also has `inputs:exposure`, a photographic stop. Both systems agree that one stop doubles the light.

## The formulas

For every light, the USD radiance is:

```
L = intensity × 2^exposure
```

Then each type converts to three.js intensity:

| USD light | three.js intensity |
|---|---|
| `RectLight` | L × width × height |
| `DiskLight` | L × π × radius² |
| `SphereLight` | L × π × radius² (its projected disk, seen from anywhere) |
| `DistantLight` | L × π × (angle / 2)², angle in radians |
| any light with `inputs:normalize = 1` | L, because the light is already normalised to its size |
| `DomeLight`, `DomeLight_1` | not a light in three.js; see below |

Point and spot lights get `decay = 2`, which is physical inverse-square falloff. Colour comes from `inputs:color` unchanged.

## The dome

**The dome becomes the environment, not a light.** The bridge loads the dome's `inputs:texture:file`, prefilters it with `PMREMGenerator` and sets it as `scene.environment` and `scene.background`. The dome's radiance L becomes `scene.environmentIntensity` and `scene.backgroundIntensity`. The background gets a slight blur (0.35) so it reads as a backdrop, not a sharp photo.

The bridge reads `.hdr` only. An `.exr` dome is task T09 in `plan/tasks.json`, and the census warns about it.

The texture path is resolved relative to the root layer. If a real export authors the dome in a sublayer, resolve against that layer's URL instead.

## Shadows

Every mesh casts and receives shadows. Only a light whose prim path ends in `/key` casts them, with a 1024 map, bias -0.0005 and radius 4. That is a placeholder rule; choosing shadow casters properly is task T08.

## Tone mapping

The renderer uses AgX tone mapping. Matching Karma's view transform (your OCIO config in Houdini) is not attempted yet, so compare looks by eye, not by pixel value.
