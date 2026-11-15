# Third-party assets

## Six human appearances

`humans.json` lists the six source meshes, render heights and verified GLB SHA256s.
The default `Survivor.glb` below is retained. Five additional models are converted
directly from [Microsoft Rocketbox](https://github.com/microsoft/Microsoft-Rocketbox),
revision `0943055db6ec570bcef9f2c8b41c9e5467c808f9`, under the same Microsoft MIT notice
reproduced below.

| Appearance | Source asset | Render height |
|---|---|---:|
| Adult man | `Male_Adult_04` (existing pinned web conversion) | 1.72 m |
| Adult woman | `Female_Adult_01` | 1.65 m |
| Boy | `Male_Child_01` | 1.30 m |
| Girl | `Female_Child_01` | 1.25 m |
| Older man appearance | `Male_Adult_03` | 1.68 m |
| Older woman appearance | `Female_Adult_14` | 1.60 m |

Older appearance roles are authored selections from the source Adults category;
the library does not supply exact ages for these meshes. These are synthetic
scenario labels, not demographic estimates from YOLO. YOLO detects the person
class only.

New conversions retain the source mesh/skin and standing pose, resize diffuse
textures to at most 1024 px, rebuild PBR materials (including hair opacity), embed
textures and omit unused morphs/animations. Runtime centers/grounds each model at
its declared height. `scripts/human_sources.json` pins original FBX/texture hashes;
`scripts/prepare_humans.py` reproduces missing models using Blender 5.2.2. Warm setup
verifies the six GLBs without network or Blender. The binaries stay ignored.

## CesiumMan.glb

- Source: Khronos glTF Sample Assets, `Models/CesiumMan/glTF-Binary/CesiumMan.glb`
  (https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/CesiumMan)
- © 2017 Cesium. Licensed under Creative Commons Attribution 4.0 International
  (https://creativecommons.org/licenses/by/4.0/legalcode).
- The Cesium logo on the model is a Cesium mark (LicenseRef-LegalMark-Cesium); no endorsement implied.
- Use in RescueTwin: rendered as a simulated survivor so the robot camera produces images a
  pretrained person detector can recognise. It does not depict a real person.

## Survivor.glb (active survivor model)

- Original: Microsoft Rocketbox `Male_Adult_04`, https://github.com/microsoft/Microsoft-Rocketbox.
- Browser conversion: `aarav.glb` from https://github.com/927tanmay/react-ai-voice-avatar,
  revision `fc1398c2f8ba600c3676a5277684ed4f3e2731d7`, `assets/avatars/aarav.glb`.
- Download: https://raw.githubusercontent.com/927tanmay/react-ai-voice-avatar/fc1398c2f8ba600c3676a5277684ed4f3e2731d7/assets/avatars/aarav.glb
- SHA-256: `ebe6b1596256f314d1356cfc2f42d28203b5bf1864d0bf139845cb9d13ee6500`.
- The upstream conversion renames the skeleton, prunes unused morph targets, packs
  embedded textures, and bakes a relaxed standing pose. RescueTwin normalizes height
  to 1.72 m and grounds/centers it at runtime. No animation is played.
- This is a simulated civilian; visual realism alone does not establish detector performance.
- `./setup.sh` fetches and verifies this ignored binary. For offline setup, copy the
  exact pinned GLB to `frontend/public/models/Survivor.glb` before launching.
- Original asset license: MIT, Copyright (c) 2020 Microsoft.
- Conversion project license: MIT, Copyright (c) 2026 Tanmay (927tanmay) and contributors.

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
