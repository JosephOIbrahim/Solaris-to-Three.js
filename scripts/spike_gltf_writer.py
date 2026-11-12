"""Spike: can a pure-Python glTF writer built on pxr carry the fixture stage?

This is evidence for docs/decisions/0001-gltf-writer.md, not production code. It writes one
.glb from a USD stage with no third-party packages beyond usd-core, then validates the
container structurally and prints what it had to leave out. Run it with:

    py -3.12 scripts/spike_gltf_writer.py fixtures/stage/shot.usda --out spike/hero.glb

What it exercises, because these are the parts a writer route has to be able to do:
  meshes (quads triangulated), world transforms, UsdPreviewSurface to PBR, a camera,
  punctual lights, a PointInstancer via EXT_mesh_gpu_instancing, and an embedded image.

What it deliberately does not do, and reports instead: subdivision surfaces (usd-core has
no OpenSubdiv), material variants, animation, and the rect and dome lights.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
import zlib

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade

COMP = {"float": 5126, "uint": 5125}
TYPE = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "MAT4": 16}


class Glb:
    def __init__(self):
        self.bin = bytearray()
        self.views, self.accessors = [], []
        self.json = {"asset": {"version": "2.0", "generator": "webexport spike"},
                     "extensionsUsed": [], "scenes": [{"nodes": []}], "scene": 0,
                     "nodes": [], "meshes": [], "materials": [], "cameras": [],
                     "images": [], "textures": [], "samplers": [], "accessors": [],
                     "bufferViews": [], "buffers": []}
        self._material_index = {}

    # -- buffer plumbing -------------------------------------------------------------
    def view(self, data: bytes, target: int | None = None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        offset = len(self.bin)
        self.bin += data
        v = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            v["target"] = target
        self.views.append(v)
        return len(self.views) - 1

    def accessor(self, values, kind, gtype, minmax=False, target=34962) -> int:
        fmt = {"float": "f", "uint": "I"}[kind]
        flat = [c for v in values for c in v] if gtype != "SCALAR" else list(values)
        data = struct.pack("<%d%s" % (len(flat), fmt), *flat)
        acc = {"bufferView": self.view(data, target), "componentType": COMP[kind],
               "count": len(values), "type": gtype}
        if minmax and values:
            lo = [min(v[i] for v in values) for i in range(TYPE[gtype])]
            hi = [max(v[i] for v in values) for i in range(TYPE[gtype])]
            acc["min"], acc["max"] = lo, hi
        self.accessors.append(acc)
        return len(self.accessors) - 1

    # -- materials -------------------------------------------------------------------
    def material(self, mat: UsdShade.Material | None, label: str):
        if mat and mat.GetPath() in self._material_index:
            return self._material_index[mat.GetPath()]
        base, metal, rough, image = (0.8, 0.8, 0.8), 0.0, 0.5, None
        if mat:
            surf = mat.ComputeSurfaceSource("universal")[0]
            if surf:
                for name, default in (("diffuseColor", None), ("base_color", None),
                                      ("metallic", 0.0), ("metalness", 0.0),
                                      ("roughness", 0.5), ("specular_roughness", 0.5)):
                    inp = surf.GetInput(name)
                    value = inp.Get() if inp else None
                    if value is None:
                        continue
                    if name in ("diffuseColor", "base_color"):
                        base = (value[0], value[1], value[2])
                    elif name in ("metallic", "metalness"):
                        metal = float(value)
                    else:
                        rough = float(value)
        m = {"name": label, "pbrMetallicRoughness": {
            "baseColorFactor": [base[0], base[1], base[2], 1.0],
            "metallicFactor": metal, "roughnessFactor": rough}}
        self.json["materials"].append(m)
        idx = len(self.json["materials"]) - 1
        if mat:
            self._material_index[mat.GetPath()] = idx
        return idx

    def image(self, path: str) -> bool:
        """Embed a PNG or JPEG unchanged; glTF allows both without transcoding."""
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg") or not os.path.exists(path):
            return False
        mime = "image/png" if ext == ".png" else "image/jpeg"
        data = open(path, "rb").read()
        self.json["images"].append({"mimeType": mime, "bufferView": self.view(data)})
        self.json["samplers"].append({"magFilter": 9729, "minFilter": 9987,
                                      "wrapS": 10497, "wrapT": 10497})
        self.json["textures"].append({"sampler": len(self.json["samplers"]) - 1,
                                      "source": len(self.json["images"]) - 1})
        return True

    # -- writing ---------------------------------------------------------------------
    def write(self, path: str) -> int:
        self.json["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(self.json, separators=(",", ":")).encode()
        js += b" " * ((4 - len(js) % 4) % 4)
        body = bytearray(self.bin)
        body += b"\0" * ((4 - len(body) % 4) % 4)
        total = 12 + 8 + len(js) + 8 + len(body)
        with open(path, "wb") as f:
            f.write(b"glTF" + struct.pack("<II", 2, total))
            f.write(struct.pack("<I4s", len(js), b"JSON") + js)
            f.write(struct.pack("<I4s", len(body), b"BIN\0") + bytes(body))
        return total


def triangulate(counts, indices):
    out, cursor = [], 0
    for n in counts:
        face = indices[cursor:cursor + n]
        cursor += n
        for i in range(1, n - 1):
            out += [face[0], face[i], face[i + 1]]
    return out


def spike(stage_path, out_path):
    t0 = time.perf_counter()
    stage = Usd.Stage.Open(stage_path)
    g = Glb()
    report = {"meshes": 0, "triangles": 0, "instances": 0, "materials": 0,
              "cameras": 0, "lights": 0, "images": 0, "subdiv_meshes": [],
              "dropped_lights": [], "variants": [], "unsupported": []}

    # materials and images first, so primitives can point at them
    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Material):
            mat = UsdShade.Material(prim)
            surf = mat.ComputeSurfaceSource("universal")[0]
            if surf:
                for inp in surf.GetInputs():
                    src = inp.GetConnectedSource()
                    if not src:
                        continue
                    shader = UsdShade.Shader(src[0].GetPrim())
                    f = shader.GetInput("file")
                    if f and f.Get():
                        if g.image(f.Get().resolvedPath or f.Get().path):
                            report["images"] += 1

    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(prim).ComputePurpose() in ("default", "render"):
            mesh = UsdGeom.Mesh(prim)
            counts = mesh.GetFaceVertexCountsAttr().Get()
            indices = mesh.GetFaceVertexIndicesAttr().Get()
            pts = mesh.GetPointsAttr().Get()
            if not counts or not indices or not pts:
                continue
            tris = triangulate(counts, indices)
            xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            world = [xf.Transform(Gf.Vec3d(p)) for p in pts]
            pos = [[p[0], p[1], p[2]] for p in world]
            flat = []
            for i in range(0, len(tris), 3):
                a, b, c = (Gf.Vec3d(world[tris[i + k]][0], world[tris[i + k]][1], world[tris[i + k]][2])
                           for k in range(3))
                n = (b - a).GetCross(c - a).GetNormalized() if (b - a).GetCross(c - a).GetLength() else Gf.Vec3d(0, 1, 0)
                flat += [[n[0], n[1], n[2]]] * 3
            st = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
            uv = []
            if st and st.HasValue():
                vals = st.Get()
                idxs = st.GetIndices()
                per_vertex = [None] * len(pts)
                if idxs:
                    for corner, vi in enumerate(idxs):
                        per_vertex[vi] = vals[corner]
                else:
                    per_vertex = list(vals)
                for vi in tris:
                    p = per_vertex[vi] if vi < len(per_vertex) else None
                    uv.append([p[0], 1.0 - p[1]] if p else [0.0, 0.0])
            attrs = {"POSITION": g.accessor(pos, "float", "VEC3", minmax=True),
                     "NORMAL": g.accessor(flat, "float", "VEC3")}
            if uv:
                attrs["TEXCOORD_0"] = g.accessor(uv, "float", "VEC2")
            idx = g.accessor(tris, "uint", "SCALAR", target=34963)
            bound = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
            mat_idx = g.material(bound, bound.GetPath().name if bound else "default")
            g.json["meshes"].append({"name": prim.GetName(), "primitives": [
                {"attributes": attrs, "indices": idx, "mode": 4, "material": mat_idx}]})
            g.json["nodes"].append({"name": str(prim.GetPath()), "mesh": len(g.json["meshes"]) - 1})
            g.json["scenes"][0]["nodes"].append(len(g.json["nodes"]) - 1)
            report["meshes"] += 1
            report["triangles"] += len(tris) // 3
            if mesh.GetSubdivisionSchemeAttr().Get() not in (None, "none"):
                report["subdiv_meshes"].append(str(prim.GetPath()))

    # instancing
    g.json["extensionsUsed"].append("EXT_mesh_gpu_instancing")
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.PointInstancer):
            continue
        pi = UsdGeom.PointInstancer(prim)
        pos = pi.GetPositionsAttr().Get()
        protos = pi.GetPrototypesRel().GetTargets()
        if not pos or not protos:
            continue
        proto = stage.GetPrimAtPath(protos[0])
        mesh = UsdGeom.Mesh(proto)
        pts = mesh.GetPointsAttr().Get()
        idxs = mesh.GetFaceVertexIndicesAttr().Get()
        cnts = mesh.GetFaceVertexCountsAttr().Get()
        tris = triangulate(cnts, idxs)
        verts = [[p[0], p[1], p[2]] for p in pts]
        attrs = {"POSITION": g.accessor(verts, "float", "VEC3", minmax=True),
                 "NORMAL": g.accessor([[0, 1, 0]] * len(verts), "float", "VEC3")}
        g.json["meshes"].append({"name": proto.GetName(), "primitives": [
            {"attributes": attrs, "indices": g.accessor(tris, "uint", "SCALAR", target=34963),
             "mode": 4,
             "material": g.material(UsdShade.MaterialBindingAPI(proto).ComputeBoundMaterial()[0],
                                    proto.GetName())}]})
        xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        world = [[xf.Transform(Gf.Vec3d(p))[i] for i in range(3)] for p in pos]
        g.json["nodes"].append({"name": str(prim.GetPath()), "mesh": len(g.json["meshes"]) - 1,
                                "extensions": {"EXT_mesh_gpu_instancing": {"attributes": {
                                    "TRANSLATION": g.accessor(world, "float", "VEC3")}}}})
        g.json["scenes"][0]["nodes"].append(len(g.json["nodes"]) - 1)
        report["instances"] += len(pos)

    # camera
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Camera):
            cam = UsdGeom.Camera(prim)
            f = cam.GetFocalLengthAttr().Get() or 35.0
            v = cam.GetVerticalApertureAttr().Get() or 20.25
            import math
            g.json["cameras"].append({"type": "perspective",
                                      "perspective": {"yfov": 2 * math.atan(v / (2 * f)),
                                                      "znear": 0.05, "zfar": 200.0}})
            g.json["nodes"].append({"name": str(prim.GetPath()),
                                    "camera": len(g.json["cameras"]) - 1})
            report["cameras"] += 1
            break

    # lights
    g.json["extensionsUsed"].append("KHR_lights_punctual")
    lights = []
    for prim in stage.Traverse():
        type_name = prim.GetTypeName()
        if not type_name.endswith("Light"):
            continue
        if type_name in ("RectLight", "DiskLight", "DomeLight", "DomeLight_1"):
            report["dropped_lights"].append(f"{type_name} {prim.GetPath()}")
            continue
        intensity = prim.GetAttribute("inputs:intensity")
        exposure = prim.GetAttribute("inputs:exposure")
        li = (intensity.Get() if intensity and intensity.HasAuthoredValue() else 1.0)
        le = (exposure.Get() if exposure and exposure.HasAuthoredValue() else 0.0)
        light = {"type": "point" if type_name == "SphereLight" else "directional",
                 "intensity": 4 * 3.14159265 * li * (2 ** le)}
        g.json["nodes"].append({"name": str(prim.GetPath()),
                                "extensions": {"KHR_lights_punctual": {"light": len(lights)}}})
        lights.append(light)
        report["lights"] += 1
    if lights:
        g.json["extensions"] = {"KHR_lights_punctual": {"lights": lights}}

    report["materials"] = len(g.json["materials"])
    report["variants"] = [f"{p.GetPath()}:{n}" for p in stage.Traverse()
                          for n in p.GetVariantSets().GetNames()]
    total = g.write(out_path)
    report["bytes"] = total
    report["ms"] = round((time.perf_counter() - t0) * 1000)

    # structural validation: the same checks the M7 unit test will make
    raw = open(out_path, "rb").read()
    magic, version, length = struct.unpack("<4sII", raw[:12])
    jlen, jtype = struct.unpack("<I4s", raw[12:20])
    doc = json.loads(raw[20:20 + jlen])
    blen, btype = struct.unpack("<I4s", raw[20 + jlen:28 + jlen])
    checks = {
        "magic": magic == b"glTF", "version": version == 2, "length_matches": length == len(raw),
        "json_chunk": jtype == b"JSON", "bin_chunk": btype == b"BIN\0",
        "buffers_sized": doc["buffers"][0]["byteLength"] <= blen,
    }
    for acc in doc["accessors"]:
        v = doc["bufferViews"][acc["bufferView"]]
        need = acc["count"] * TYPE[acc["type"]] * (4 if acc["componentType"] in (5125, 5126) else 2)
        checks[f"accessor_{len(checks)}_fits"] = need <= v["byteLength"]
    for img in doc.get("images", []):
        checks[f"image_{len(checks)}_resolves"] = "bufferView" in img
    report["valid"] = all(checks.values())
    report["failed_checks"] = [k for k, v in checks.items() if not v]
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("--out", default="spike/hero.glb")
    a = ap.parse_args(argv)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    report = spike(a.stage, a.out)
    print(json.dumps(report, indent=1))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
