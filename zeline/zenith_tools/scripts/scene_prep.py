"""
tools/scene_prep.py — Scene Preparation (USD) + ComfyUI workflow patch  (v4.1)

Dua helper untuk pipeline kreatif/robotika (sk18/sk19), keduanya keyless & lokal:

1. USD scene assembly  — bangun scene .usda programmatically (ground, light, asset,
   domain randomization) buat Isaac Sim / Omniverse. Butuh `pxr` (usd-core) saat
   eksekusi nyata; di sini ada fallback writer .usda mentah biar tetap kepakai
   tanpa Omniverse terpasang.
2. ComfyUI graph patch — patch node graph JSON (prompt/seed/model) sebelum POST
   ke ComfyUI API. Pure-dict, gak butuh dependency.

Pakai:
    from scene_prep import UsdaScene, patch_comfy_graph
    s = UsdaScene("scene.usda"); s.add_ground(); s.add_cube("/World/box", (0,0,1)); s.save()
    g = patch_comfy_graph(graph, prompt="a neon city", seed=42)
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Optional


# ───────────────── USD scene (fallback writer, no pxr needed) ─────────────────
class UsdaScene:
    """Penulis .usda minimal. Kalau `pxr` ada, pakai itu utk validitas penuh;
    kalau gak, tulis .usda teks (cukup buat scene sederhana / placeholder)."""

    def __init__(self, path: str, up_axis: str = "Z", meters_per_unit: float = 1.0):
        self.path = Path(path)
        self.up_axis = up_axis
        self.meters_per_unit = meters_per_unit
        self._prims: list = []

    def add_ground(self, size: float = 10.0):
        self._prims.append(("Cube", "/World/ground", {"size": size, "xform": (0, 0, -size / 2)}))
        return self

    def add_cube(self, prim_path: str, translate=(0, 0, 0), size: float = 1.0):
        self._prims.append(("Cube", prim_path, {"size": size, "xform": translate}))
        return self

    def add_sphere(self, prim_path: str, translate=(0, 0, 0), radius: float = 0.5):
        self._prims.append(("Sphere", prim_path, {"radius": radius, "xform": translate}))
        return self

    def add_distant_light(self, intensity: float = 1000.0):
        self._prims.append(("DistantLight", "/World/sun", {"intensity": intensity}))
        return self

    def randomize(self, seed: int = 0):
        """Domain randomization sederhana (deterministik dari seed) — penting sim-to-real.

        Gak pakai RNG global; turunkan offset dari seed biar reproducible.
        """
        for i, (kind, path, props) in enumerate(self._prims):
            if "xform" in props:
                x, y, z = props["xform"]
                jitter = ((seed * 7 + i * 13) % 100) / 100.0 - 0.5   # -0.5..0.5 deterministik
                props["xform"] = (x + jitter, y + jitter, z)
        return self

    def _try_pxr(self) -> bool:
        try:
            from pxr import Usd, UsdGeom, UsdLux, Gf
        except Exception:
            return False
        stage = Usd.Stage.CreateNew(str(self.path))
        UsdGeom.SetStageUpAxis(stage, self.up_axis)
        UsdGeom.SetStageMetersPerUnit(stage, self.meters_per_unit)
        UsdGeom.Xform.Define(stage, "/World")
        for kind, path, props in self._prims:
            if kind == "Cube":
                p = UsdGeom.Cube.Define(stage, path); p.GetSizeAttr().Set(props.get("size", 1.0))
            elif kind == "Sphere":
                p = UsdGeom.Sphere.Define(stage, path); p.GetRadiusAttr().Set(props.get("radius", 0.5))
            elif kind == "DistantLight":
                p = UsdLux.DistantLight.Define(stage, path); p.GetIntensityAttr().Set(props.get("intensity", 1000.0)); continue
            else:
                continue
            if "xform" in props:
                UsdGeom.Xformable(p.GetPrim()).AddTranslateOp().Set(Gf.Vec3d(*props["xform"]))
        stage.GetRootLayer().Save()
        return True

    def _write_text(self):
        lines = ['#usda 1.0', '(', f'    upAxis = "{self.up_axis}"',
                 f'    metersPerUnit = {self.meters_per_unit}', ')', '', 'def Xform "World"', '{']
        for kind, path, props in self._prims:
            name = path.split("/")[-1]
            lines.append(f'    def {kind} "{name}"')
            lines.append('    {')
            if "size" in props:
                lines.append(f'        double size = {props["size"]}')
            if "radius" in props:
                lines.append(f'        double radius = {props["radius"]}')
            if "intensity" in props:
                lines.append(f'        float inputs:intensity = {props["intensity"]}')
            if "xform" in props:
                x, y, z = props["xform"]
                lines.append(f'        double3 xformOp:translate = ({x}, {y}, {z})')
                lines.append('        uniform token[] xformOpOrder = ["xformOp:translate"]')
            lines.append('    }')
        lines.append('}')
        self.path.write_text("\n".join(lines) + "\n")

    def save(self) -> str:
        if not self._try_pxr():
            self._write_text()
        return str(self.path)


# ───────────────── ComfyUI graph patch ─────────────────
def patch_comfy_graph(graph: dict, prompt: Optional[str] = None, seed: Optional[int] = None,
                      negative: Optional[str] = None, ckpt: Optional[str] = None) -> dict:
    """Patch node graph ComfyUI (format API). Cari node by class_type, set input.

    Aman: deep-copy, gak ngubah graph asli. Heuristik node umum (CLIPTextEncode,
    KSampler, CheckpointLoaderSimple). Untuk graph custom, patch manual by node id.
    """
    g = copy.deepcopy(graph)
    pos_set = False
    for node in g.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        inp = node.setdefault("inputs", {})
        if ct == "CLIPTextEncode":
            if prompt is not None and not pos_set:
                inp["text"] = prompt; pos_set = True
            elif negative is not None:
                inp["text"] = negative
        elif ct == "KSampler" and seed is not None:
            inp["seed"] = seed
        elif ct == "CheckpointLoaderSimple" and ckpt is not None:
            inp["ckpt_name"] = ckpt
    return g


if __name__ == "__main__":
    s = UsdaScene("/tmp/_scene_smoke.usda").add_ground().add_cube("/World/box", (0, 0, 1)).add_distant_light()
    print("USD scene →", s.randomize(seed=3).save())
    demo = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
            "2": {"class_type": "KSampler", "inputs": {"seed": 0}}}
    print(json.dumps(patch_comfy_graph(demo, prompt="neon city", seed=42), indent=2))
