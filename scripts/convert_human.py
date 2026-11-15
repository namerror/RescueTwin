"""Run with Blender --background --factory-startup --python this_file -- SOURCE OUTPUT TEXTURES."""
import json
from pathlib import Path
import sys

import bpy

source, output, textures = map(Path, sys.argv[sys.argv.index('--')+1:])
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.fbx(filepath=str(source.resolve()))
meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
if not meshes:
    raise ValueError('No human mesh imported')
for material in {m for o in meshes for m in o.data.materials if m}:
    old = list({n.image.filepath: n.image for n in material.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image
           and Path(n.image.filepath).stem.endswith('_color')}.values())
    if len(old) != 1:
        raise ValueError(f'{material.name}: expected one diffuse source texture')
    path = textures / (Path(old[0].filepath).stem + '.png')
    if not path.is_file():
        raise ValueError(f'Missing diffuse texture {path.name}')
    nodes = material.node_tree.nodes; nodes.clear()
    shader = nodes.new('ShaderNodeBsdfPrincipled'); shader.inputs['Roughness'].default_value=.85
    if 'Specular IOR Level' in shader.inputs:
        shader.inputs['Specular IOR Level'].default_value=.2
    texture = nodes.new('ShaderNodeTexImage'); texture.image=bpy.data.images.load(str(path.resolve()))
    texture.image.pack()
    result = nodes.new('ShaderNodeOutputMaterial')
    material.node_tree.links.new(texture.outputs['Color'],shader.inputs['Base Color'])
    material.node_tree.links.new(shader.outputs['BSDF'],result.inputs['Surface'])
    if any(tag in material.name.lower() for tag in ('hair','opacity')):
        material.node_tree.links.new(texture.outputs['Alpha'],shader.inputs['Alpha'])
        if hasattr(material,'surface_render_method'): material.surface_render_method='DITHERED'
    material.diffuse_color=(1,1,1,1)
for obj in bpy.context.scene.objects:
    obj.select_set(obj.type in ('MESH','ARMATURE'))
output.parent.mkdir(parents=True,exist_ok=True)
bpy.ops.export_scene.gltf(filepath=str(output.resolve()),export_format='GLB',use_selection=True,
                          export_animations=False,export_morph=False)
print('HUMAN_EXPORT',json.dumps(dict(file=output.name,meshes=len(meshes),vertices=sum(len(o.data.vertices) for o in meshes),blender=bpy.app.version_string)))
