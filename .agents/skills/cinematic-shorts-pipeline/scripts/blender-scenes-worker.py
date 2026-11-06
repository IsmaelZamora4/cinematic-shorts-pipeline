#!/usr/bin/env python3
"""Fixed Blender 5.2 worker for one validated scene-spec v1 request."""
import importlib.util
import json
import math
import os
import re
import stat
import sys
import time
from pathlib import Path


MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MIN_CAMERA_TARGET_DISTANCE_M = 0.01
CAMERA_UP_WORLD = (0.0, 0.0, 1.0)
WORKER_VERSION = 1
# Blender stores many RNA properties as float32; allow its rounding, not policy changes.
FLOAT_REL_TOL = 1e-6
FLOAT_ABS_TOL = 1e-7
MAX_SCENE_BLEND_BYTES = 512 * 1024 * 1024
MAX_FRAME_PNG_BYTES = 32 * 1024 * 1024
EXPECTED_SPEC_FIELDS = {
    "scene_spec_version", "project_id", "scene_id", "render", "materials", "objects", "camera", "lights",
}
REQUEST_FIELDS = {
    "request_version", "project_id", "scene_spec", "expected_blender_version",
    "expected_blender_version_string", "staging_root",
}


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"non-standard JSON number: {value}")


def _parse_bounded_json(data):
    if not isinstance(data, bytes):
        raise ValueError("worker request must be bytes")
    if len(data) > MAX_REQUEST_BYTES:
        raise ValueError(f"worker request exceeds maximum size of {MAX_REQUEST_BYTES} bytes")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("worker request must contain valid UTF-8") from exc
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > 128:
                raise ValueError("worker request JSON exceeds maximum nesting depth of 128")
        elif character in "]}":
            depth = max(0, depth - 1)
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_pairs, parse_constant=_reject_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("worker request is not valid strict JSON") from exc


def _load_pipeline_validator():
    scripts_dir = Path(__file__).resolve().parent
    pipeline_path = scripts_dir / "pipeline.py"
    if not pipeline_path.is_file():
        raise ValueError("fixed pipeline scene-spec validator is unavailable")
    sibling_modules = {
        name: (scripts_dir / f"{name}.py").resolve()
        for name in ("scene_package_builder", "scene_package_validator", "png_rgb8_decoder")
    }

    try:
        # Load required imports explicitly so Blender's other module paths are never searched.
        for name in ("png_rgb8_decoder", "scene_package_validator", "scene_package_builder"):
            expected_path = sibling_modules[name]
            imported = sys.modules.get(name)
            if imported is not None:
                imported_path = getattr(imported, "__file__", None)
                if not isinstance(imported_path, str) or Path(imported_path).resolve() != expected_path:
                    raise ValueError(f"fixed pipeline dependency {name} is already loaded from another location")
                continue
            if not expected_path.is_file():
                raise ImportError(f"fixed pipeline dependency {name} is missing")
            dependency_spec = importlib.util.spec_from_file_location(name, expected_path)
            if dependency_spec is None or dependency_spec.loader is None:
                raise ImportError(f"fixed pipeline dependency {name} is unavailable")
            dependency = importlib.util.module_from_spec(dependency_spec)
            sys.modules[name] = dependency
            try:
                dependency_spec.loader.exec_module(dependency)
            except BaseException:
                sys.modules.pop(name, None)
                raise
            if Path(dependency.__file__).resolve() != expected_path:
                raise ValueError(f"fixed pipeline dependency {name} did not load from its sibling file")

        loader_spec = importlib.util.spec_from_file_location("blender_worker_pipeline_contract", pipeline_path)
        if loader_spec is None or loader_spec.loader is None:
            raise ValueError("fixed pipeline scene-spec validator is unavailable")
        module = importlib.util.module_from_spec(loader_spec)
        loader_spec.loader.exec_module(module)
        return module
    except ImportError as exc:
        raise ValueError("fixed pipeline scene-spec validator dependencies are unavailable") from exc


def _is_reparse_or_symlink(path_stat):
    if stat.S_ISLNK(path_stat.st_mode):
        return True
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(path_stat, "st_file_attributes", 0) & reparse_attribute)


def _check_directory_path(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("worker staging_root must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            entry = current.lstat()
        except OSError as exc:
            raise ValueError("worker staging_root does not exist as a directory") from exc
        if _is_reparse_or_symlink(entry) or not stat.S_ISDIR(entry.st_mode):
            raise ValueError("worker staging_root contains a symlink, reparse point, or non-directory")
    return path


def _validate_request(request):
    if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
        raise ValueError("worker request must contain exactly the supported fields")
    if type(request.get("request_version")) is not int or request["request_version"] != 1:
        raise ValueError("worker request_version must be integer 1")
    project_id = request.get("project_id")
    if not isinstance(project_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", project_id):
        raise ValueError("worker request project_id must be a slug")
    version = request.get("expected_blender_version")
    if (
        not isinstance(version, list) or len(version) != 3
        or any(type(component) is not int or component < 0 for component in version)
        or version[:2] != [5, 2]
    ):
        raise ValueError("worker requires an expected Blender 5.2.x version tuple")
    version_string = request.get("expected_blender_version_string")
    if not isinstance(version_string, str) or version_string != ".".join(str(value) for value in version):
        raise ValueError("worker expected Blender version string does not match its tuple")
    staging_root = request.get("staging_root")
    if not isinstance(staging_root, str) or not staging_root:
        raise ValueError("worker staging_root must be a non-empty absolute path")
    staging = _check_directory_path(staging_root)

    spec = request.get("scene_spec")
    if not isinstance(spec, dict) or set(spec) != EXPECTED_SPEC_FIELDS:
        raise ValueError("worker scene_spec must be the exact scene-spec v1 object")
    validator = _load_pipeline_validator()
    storyboard = {
        "aspect_ratio": "9:16", "fps": 12, "total_frames": 24,
        "scenes": [{"id": spec.get("scene_id"), "start_frame": 0, "end_frame": 24}],
    }
    errors = validator._validate_blender_scene_spec(spec, project_id, storyboard)
    if errors:
        raise ValueError("worker scene_spec validation failed: " + "; ".join(errors))
    return {
        "request_version": 1,
        "project_id": project_id,
        "scene_spec": spec,
        "expected_blender_version": list(version),
        "expected_blender_version_string": version_string,
        "staging_root": str(staging),
    }


def _vector3(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} must be an xyz coordinate")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{label} must contain only finite coordinates")
    return result


def _subtract(first, second):
    return tuple(a - b for a, b in zip(first, second))


def _add_scaled(first, delta, factor):
    return tuple(a + factor * b for a, b in zip(first, delta))


def _length(vector):
    return math.sqrt(sum(value * value for value in vector))


def _dot(first, second):
    return sum(a * b for a, b in zip(first, second))


def _cross(first, second):
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _camera_samples(scene_spec):
    """Interpolate all output-frame poses and reject singular paths analytically."""
    try:
        keyframes = scene_spec["camera"]["keyframes"]
        first, last = keyframes
        p0 = _vector3(first["location_m"], "camera frame 0 location")
        p1 = _vector3(last["location_m"], "camera frame 23 location")
        t0 = _vector3(first["target_m"], "camera frame 0 target")
        t1 = _vector3(last["target_m"], "camera frame 23 target")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"worker camera trajectory is invalid: {exc}") from exc

    direction_start = _subtract(t0, p0)
    direction_end = _subtract(t1, p1)
    direction_delta = _subtract(direction_end, direction_start)
    delta_squared = _dot(direction_delta, direction_delta)
    closest_t = 0.0 if delta_squared == 0 else max(
        0.0, min(1.0, -_dot(direction_start, direction_delta) / delta_squared),
    )
    minimum_distance = _length(_add_scaled(direction_start, direction_delta, closest_t))
    if not math.isfinite(minimum_distance) or minimum_distance <= MIN_CAMERA_TARGET_DISTANCE_M:
        raise ValueError("interpolated camera-target distance reaches the invalid minimum")

    samples = []
    up = CAMERA_UP_WORLD
    for frame in range(24):
        factor = frame / 23
        location = _add_scaled(p0, _subtract(p1, p0), factor)
        target = _add_scaled(t0, _subtract(t1, t0), factor)
        direction = _subtract(target, location)
        distance = _length(direction)
        up_cross = _cross(direction, up)
        up_cross_length = _length(up_cross)
        if (
            not all(math.isfinite(value) for value in (*location, *target, *direction))
            or not math.isfinite(distance)
            or distance <= MIN_CAMERA_TARGET_DISTANCE_M
            or up_cross_length <= distance * 1e-8
        ):
            raise ValueError(f"camera orientation is invalid at frame {frame}")
        samples.append({
            "frame": frame,
            "t": factor,
            "location_m": location,
            "target_m": target,
            "direction": direction,
        })
    return samples, minimum_distance


def _matches_value(actual, expected):
    if isinstance(expected, float):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isclose(
            actual, expected, rel_tol=FLOAT_REL_TOL, abs_tol=FLOAT_ABS_TOL,
        )
    if isinstance(expected, (tuple, list)):
        try:
            values = tuple(actual)
        except TypeError:
            return False
        return len(values) == len(expected) and all(_matches_value(a, b) for a, b in zip(values, expected))
    return type(actual) is type(expected) and actual == expected


def _set_value(owner, attribute, value, label):
    if not hasattr(owner, attribute):
        raise RuntimeError(f"Blender 5.2.x does not expose required setting {label}")
    setattr(owner, attribute, value)
    actual = getattr(owner, attribute)
    if not _matches_value(actual, value):
        raise RuntimeError(f"Blender did not retain required setting {label}")


def _set_socket(node, name, value, *, required=True):
    socket = node.inputs.get(name)
    if socket is None:
        if required:
            raise RuntimeError(f"Blender Principled BSDF is missing required socket {name}")
        return
    if not hasattr(socket, "default_value"):
        raise RuntimeError(f"Blender Principled BSDF socket {name} has no default value")
    socket.default_value = value
    if not _matches_value(socket.default_value, value):
        raise RuntimeError(f"Blender did not retain required material socket {name}")


def _make_materials(bpy, spec):
    material_by_id = {}
    for item in spec["materials"]:
        material = bpy.data.materials.new(name=f"material-{item['id']}")
        material.use_nodes = True
        nodes = material.node_tree.nodes
        nodes.clear()
        shader = nodes.new(type="ShaderNodeBsdfPrincipled")
        shader.location = (0, 0)
        output = nodes.new(type="ShaderNodeOutputMaterial")
        output.location = (300, 0)
        material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])

        _set_socket(shader, "Base Color", tuple(map(float, item["base_color_linear_rgba"])))
        _set_socket(shader, "Metallic", float(item["metallic"]))
        _set_socket(shader, "Roughness", float(item["roughness"]))
        _set_socket(shader, "Alpha", 1.0)
        _set_socket(shader, "IOR", 1.5)
        _set_socket(shader, "Emission Color", (0.0, 0.0, 0.0, 1.0))
        _set_socket(shader, "Emission Strength", 0.0)
        neutral_sockets = {
            "Subsurface Weight": 0.0,
            "Subsurface Radius": (1.0, 1.0, 1.0),
            "Subsurface Scale": 0.0,
            "Anisotropic IOR Level": 0.0,
            "Anisotropic Rotation": 0.0,
            "Specular IOR Level": 0.5,
            "Transmission Weight": 0.0,
            "Coat Weight": 0.0,
            "Coat Roughness": 0.03,
            "Coat IOR": 1.5,
            "Coat Tint": (1.0, 1.0, 1.0, 1.0),
            "Sheen Weight": 0.0,
            "Sheen Roughness": 0.5,
            "Sheen Tint": (1.0, 1.0, 1.0, 1.0),
            "Thin Film Thickness": 0.0,
            "Thin Film IOR": 1.33,
        }
        for socket_name, value in neutral_sockets.items():
            _set_socket(shader, socket_name, value, required=False)
        material_by_id[item["id"]] = material
    return material_by_id


def _add_primitive(bpy, item):
    arguments = {
        "location": tuple(item["location_m"]),
        "rotation": tuple(item["rotation_rad"]),
        "enter_editmode": False,
        "align": "WORLD",
    }
    primitive = item["primitive"]
    if primitive == "cube":
        bpy.ops.mesh.primitive_cube_add(size=2.0, **arguments)
    elif primitive == "uv_sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0, **arguments)
    elif primitive == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.0, depth=2.0, **arguments)
    elif primitive == "plane":
        bpy.ops.mesh.primitive_plane_add(size=2.0, **arguments)
    else:
        raise RuntimeError("validated scene spec contains an unsupported primitive")
    obj = bpy.context.object
    if obj is None or obj.type != "MESH":
        raise RuntimeError("Blender failed to create the declared primitive")
    obj.name = f"object-{item['id']}"
    obj.data.name = f"mesh-{item['id']}"
    obj.scale = tuple(item["scale"])
    return obj


def _aim_at(obj, target, Vector):
    direction = Vector(target) - obj.location
    if not math.isfinite(direction.length) or direction.length <= MIN_CAMERA_TARGET_DISTANCE_M:
        raise RuntimeError(f"object {obj.name} cannot be aimed at its declared target")
    if direction.cross(Vector(CAMERA_UP_WORLD)).length <= direction.length * 1e-8:
        raise RuntimeError(f"object {obj.name} has an invalid up orientation")
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _set_action_interpolation(owner, expected_paths, interpolation):
    animation = owner.animation_data
    if animation is None or animation.action is None or animation.action_slot is None:
        raise RuntimeError("Blender did not create an action and slot for the keyframes")
    found = set()
    for layer in animation.action.layers:
        for strip in layer.strips:
            if strip.type != "KEYFRAME":
                continue
            bag = strip.channelbag(animation.action_slot)
            if bag is None:
                continue
            for curve in bag.fcurves:
                if curve.data_path not in expected_paths:
                    continue
                channel = (curve.data_path, curve.array_index)
                if channel in found or not 0 <= curve.array_index < expected_paths[curve.data_path]:
                    raise RuntimeError("Blender action contains an unexpected animation curve")
                found.add(channel)
                if len(curve.keyframe_points) != 24 or any(
                    not math.isclose(point.co[0], frame, abs_tol=1e-6)
                    for frame, point in enumerate(curve.keyframe_points)
                ):
                    raise RuntimeError("Blender action does not contain the expected 24 keyframes")
                for point in curve.keyframe_points:
                    point.interpolation = interpolation
                    if point.interpolation != interpolation:
                        raise RuntimeError("Blender did not retain keyframe interpolation")
    expected = {(path, index) for path, count in expected_paths.items() for index in range(count)}
    if found != expected:
        raise RuntimeError("Blender action is missing required animation curves")


def _configure_scene(bpy, spec, staging_root):
    from mathutils import Vector

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    if scene is None:
        raise RuntimeError("Blender factory scene is unavailable")
    render = scene.render
    cycles = scene.cycles
    _set_value(render, "engine", "CYCLES", "render.engine")
    _set_value(cycles, "device", "CPU", "cycles.device")
    _set_value(render, "resolution_x", 540, "render.resolution_x")
    _set_value(render, "resolution_y", 960, "render.resolution_y")
    _set_value(render, "resolution_percentage", 100, "render.resolution_percentage")
    _set_value(render, "pixel_aspect_x", 1.0, "render.pixel_aspect_x")
    _set_value(render, "pixel_aspect_y", 1.0, "render.pixel_aspect_y")
    _set_value(render, "fps", 12, "render.fps")
    _set_value(render, "fps_base", 1.0, "render.fps_base")
    _set_value(render, "threads_mode", "FIXED", "render.threads_mode")
    _set_value(render, "threads", 2, "render.threads")
    _set_value(render, "film_transparent", False, "render.film_transparent")
    _set_value(render, "use_motion_blur", False, "render.use_motion_blur")
    _set_value(render, "use_file_extension", True, "render.use_file_extension")
    _set_value(render, "use_overwrite", False, "render.use_overwrite")
    _set_value(render, "use_stamp", False, "render.use_stamp")
    _set_value(render, "use_compositing", False, "render.use_compositing")
    _set_value(render, "use_sequencer", False, "render.use_sequencer")
    _set_value(render, "use_border", False, "render.use_border")
    _set_value(render, "use_crop_to_border", False, "render.use_crop_to_border")
    _set_value(render, "dither_intensity", 0.0, "render.dither_intensity")
    _set_value(render.image_settings, "file_format", "PNG", "render.image_settings.file_format")
    _set_value(render.image_settings, "color_mode", "RGB", "render.image_settings.color_mode")
    _set_value(render.image_settings, "color_depth", "8", "render.image_settings.color_depth")
    _set_value(render.image_settings, "compression", 15, "render.image_settings.compression")
    _set_value(cycles, "samples", 8, "cycles.samples")
    _set_value(cycles, "use_denoising", False, "cycles.use_denoising")
    _set_value(cycles, "use_preview_denoising", False, "cycles.use_preview_denoising")
    _set_value(cycles, "use_adaptive_sampling", False, "cycles.use_adaptive_sampling")
    _set_value(cycles, "use_animated_seed", False, "cycles.use_animated_seed")
    _set_value(cycles, "max_bounces", 4, "cycles.max_bounces")
    _set_value(cycles, "diffuse_bounces", 2, "cycles.diffuse_bounces")
    _set_value(cycles, "glossy_bounces", 2, "cycles.glossy_bounces")
    _set_value(cycles, "transmission_bounces", 2, "cycles.transmission_bounces")
    _set_value(cycles, "caustics_reflective", False, "cycles.caustics_reflective")
    _set_value(cycles, "caustics_refractive", False, "cycles.caustics_refractive")
    _set_value(cycles, "seed", spec["render"]["seed"], "cycles.seed")

    _set_value(scene.view_settings, "view_transform", "Standard", "view_settings.view_transform")
    _set_value(scene.view_settings, "look", "None", "view_settings.look")
    _set_value(scene.view_settings, "exposure", 0.0, "view_settings.exposure")
    _set_value(scene.view_settings, "gamma", 1.0, "view_settings.gamma")
    _set_value(scene.display_settings, "display_device", "sRGB", "display_settings.display_device")
    _set_value(scene.unit_settings, "system", "NONE", "unit_settings.system")
    _set_value(scene.unit_settings, "scale_length", 1.0, "unit_settings.scale_length")

    world = bpy.data.worlds.new(name="pipeline-world")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is None:
        raise RuntimeError("Blender World node tree lacks Background")
    _set_value(background.inputs["Color"], "default_value", (0.0, 0.0, 0.0, 1.0), "world.background.color")
    _set_value(background.inputs["Strength"], "default_value", 0.0, "world.background.strength")
    scene.world = world

    materials = _make_materials(bpy, spec)
    for item in spec["objects"]:
        obj = _add_primitive(bpy, item)
        obj.data.materials.append(materials[item["material_id"]])

    for item in spec["lights"]:
        data = bpy.data.lights.new(name=f"light-data-{item['id']}", type="AREA")
        data.shape = "SQUARE"
        data.size = item["size_m"]
        data.energy = item["power_w"]
        data.color = tuple(item["color_linear_rgb"])
        obj = bpy.data.objects.new(name=f"light-{item['id']}", object_data=data)
        scene.collection.objects.link(obj)
        obj.location = tuple(item["location_m"])
        _aim_at(obj, item["target_m"], Vector)
        if (
            data.shape != "SQUARE" or not _matches_value(data.size, float(item["size_m"]))
            or not _matches_value(data.energy, float(item["power_w"]))
            or not _matches_value(data.color, tuple(map(float, item["color_linear_rgb"])))
        ):
            raise RuntimeError(f"Blender did not retain effective light settings for {item['id']}")

    samples, minimum_distance = _camera_samples(spec)
    camera_data = bpy.data.cameras.new(name=f"camera-data-{spec['scene_id']}")
    camera_data.lens = spec["camera"]["lens_mm"]
    camera_data.sensor_fit = "VERTICAL"
    camera_data.sensor_width = 36.0
    camera_data.clip_start = 0.01
    camera_data.clip_end = 100.0
    camera_data.dof.use_dof = False
    camera_obj = bpy.data.objects.new(name=f"camera-{spec['scene_id']}", object_data=camera_data)
    scene.collection.objects.link(camera_obj)
    camera_obj.rotation_mode = "QUATERNION"
    scene.camera = camera_obj
    for sample in samples:
        camera_obj.location = sample["location_m"]
        camera_obj.rotation_quaternion = (
            Vector(sample["direction"]).to_track_quat("-Z", "Y")
        )
        camera_obj.keyframe_insert(data_path="location", frame=sample["frame"])
        camera_obj.keyframe_insert(data_path="rotation_quaternion", frame=sample["frame"])
    _set_action_interpolation(camera_obj, {"location": 3, "rotation_quaternion": 4}, "LINEAR")

    output_root = Path(staging_root) / "scenes"
    output_root.mkdir(mode=0o700)
    output_dir = output_root / spec["scene_id"]
    frames_dir = output_dir / "frames"
    output_dir.mkdir(mode=0o700)
    frames_dir.mkdir(mode=0o700)
    scene.render.filepath = "//frames/frame-"
    scene.frame_start = 0
    scene.frame_end = 23
    for frame in range(24):
        scene.cycles.seed = (spec["render"]["seed"] + frame) % 2147483648
        scene.keyframe_insert(data_path="cycles.seed", frame=frame)
    _set_action_interpolation(scene, {"cycles.seed": 1}, "CONSTANT")
    scene.frame_set(0)
    if (
        scene.render.engine != "CYCLES" or cycles.device != "CPU" or cycles.samples != 8
        or render.threads != 2 or render.fps != 12 or (render.resolution_x, render.resolution_y) != (540, 960)
        or camera_data.sensor_fit != "VERTICAL" or not _matches_value(camera_data.sensor_width, 36.0)
        or not _matches_value(camera_data.lens, float(spec["camera"]["lens_mm"]))
        or not _matches_value(camera_data.clip_start, 0.01)
        or not _matches_value(camera_data.clip_end, 100.0) or camera_data.dof.use_dof is not False
        or (scene.frame_start, scene.frame_end) != (0, 23)
    ):
        raise RuntimeError("Blender effective scene settings differ from blender-scene-policy-v1")
    return scene, camera_obj, output_dir, frames_dir, samples, minimum_distance


def _write_response(path, response):
    payload = (json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > MAX_RESPONSE_BYTES:
        raise RuntimeError("worker response exceeds the supported maximum size")
    with Path(path).open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    import argparse
    import bpy
    from mathutils import Vector

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if argv is None and "--" in sys.argv else argv)
    request_path = Path(args.request)
    response_path = request_path.with_name("response.json")
    start_time = time.perf_counter()
    try:
        if not request_path.is_absolute():
            raise ValueError("worker request path must be absolute")
        request_stat = request_path.lstat()
        if _is_reparse_or_symlink(request_stat) or not stat.S_ISREG(request_stat.st_mode):
            raise ValueError("worker request path must be a regular non-link file")
        if request_stat.st_size > MAX_REQUEST_BYTES:
            raise ValueError("worker request exceeds the supported maximum size")
        with request_path.open("rb") as stream:
            request_bytes = stream.read(MAX_REQUEST_BYTES + 1)
        request = _validate_request(_parse_bounded_json(request_bytes))
        observed_version = tuple(bpy.app.version)
        if observed_version != tuple(request["expected_blender_version"]):
            raise RuntimeError("Blender bpy.app.version differs from preflight version")
        if getattr(bpy.app, "version_cycle", None) != "release":
            raise RuntimeError("Blender prerelease or development runtime is unsupported")
        observed_version_string = ".".join(str(component) for component in observed_version)
        if not bool(bpy.app.build_options.cycles):
            raise RuntimeError("this Blender build does not include Cycles")
        scene, camera_obj, output_dir, frames_dir, samples, minimum_distance = _configure_scene(
            bpy, request["scene_spec"], request["staging_root"],
        )
        blend_path = output_dir / "scene.blend"
        if blend_path.exists():
            raise RuntimeError("worker refuses to overwrite scene.blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
        if not blend_path.is_file() or blend_path.stat().st_size <= 0:
            raise RuntimeError("Blender did not produce a non-empty scene.blend")
        if blend_path.stat().st_size > MAX_SCENE_BLEND_BYTES:
            raise RuntimeError("scene.blend exceeds its supported staging size")

        render_start = time.perf_counter()
        for sample in samples:
            scene.frame_set(sample["frame"])
            camera_obj.location = sample["location_m"]
            camera_obj.rotation_quaternion = Vector(sample["direction"]).to_track_quat("-Z", "Y")
            scene.cycles.seed = (request["scene_spec"]["render"]["seed"] + sample["frame"]) % 2147483648
            scene.render.filepath = f"//frames/frame-{sample['frame']:06d}"
            frame_path = frames_dir / f"frame-{sample['frame']:06d}.png"
            if frame_path.exists():
                raise RuntimeError(f"worker refuses to overwrite frame {sample['frame']}")
            bpy.ops.render.render(write_still=True)
            if not frame_path.is_file() or frame_path.stat().st_size <= 0:
                raise RuntimeError(f"Blender did not produce frame {sample['frame']}")
            if frame_path.stat().st_size > MAX_FRAME_PNG_BYTES:
                raise RuntimeError(f"frame {sample['frame']} exceeds its supported staging size")
        render_seconds = time.perf_counter() - render_start

        output_names = [f"scenes/{request['scene_spec']['scene_id']}/scene.blend"] + [
            f"scenes/{request['scene_spec']['scene_id']}/frames/frame-{frame:06d}.png" for frame in range(24)
        ]
        response = {
            "record_type": "blender-scenes-worker-result",
            "worker_version": WORKER_VERSION,
            "blender_version": list(observed_version),
            "blender_version_string": observed_version_string,
            "project_id": request["project_id"],
            "scene_id": request["scene_spec"]["scene_id"],
            "frames": list(range(24)),
            "outputs": output_names,
            "minimum_camera_target_distance_m": minimum_distance,
            "render_seconds": render_seconds,
            "worker_seconds": time.perf_counter() - start_time,
        }
        _write_response(response_path, response)
        return 0
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:2048]
        try:
            _write_response(response_path, {
                "record_type": "blender-scenes-worker-error",
                "worker_version": WORKER_VERSION,
                "error": error,
            })
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
