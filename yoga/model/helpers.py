from ._assimp import ffi

import io
import re
import os.path
import unidecode
import yoga.image


def normalize_path(path):
    # Expects a unicode path, returns a ascii one.
    # Paths are normalized to a standard linux relative path,
    # without a point, and lowercase.
    # That is to say /images\subfolder/..\texture.png -> images/texture.png
    # It does not correspond to an effective path,
    # as the backslashes on linux are wrongly seen as separators.
    # This function is meant to give a standard output.

    path = unidecode.unidecode(path)
    split_path = re.findall(r"[\w\s\-_.:]+", path)
    normalized_path = ""
    ignored_folders = 0

    for i, name in enumerate(reversed(split_path)):
        if name == "." or name[-1:] == ":":
            continue
        elif name == "..":
            ignored_folders += 1
        elif ignored_folders > 0:
            ignored_folders -= 1
        elif i == 0:
            normalized_path = name
        else:
            normalized_path = name + "/" + normalized_path

    normalized_path = normalized_path.lower()
    return normalized_path


def normalize_textures(textures):
    if textures is None:
        return None

    # Normalizes all the paths in the texture dict.
    normalized_textures = dict()
    for path in textures:
        normalized_path = normalize_path(path.decode("utf-8"))
        if normalized_path in normalized_textures:
            raise ValueError("Multiple textures are resolved to the same path %s." % normalized_path) # noqa
        normalized_textures[normalized_path] = textures[path]

    return normalized_textures


def find_valid_path(path, root_path):
    # Note: we cannot use normalized paths here,
    # because we need to find a file on the system.

    tested_path = path
    if os.path.isfile(tested_path):
        return tested_path

    tested_path = os.path.join(root_path, path)
    if os.path.isfile(tested_path):
        return tested_path

    tested_path = os.path.join(root_path, os.path.basename(path))
    if os.path.isfile(tested_path):
        return tested_path

    # Still not able to find it, it might be a Windows path,
    # while this program is executed on Linux.
    # So paths like "..\\image.png" are seen as entire filename,
    # we try some trick.

    path = path.replace("\\", "/")

    tested_path = path
    if os.path.isfile(tested_path):
        return tested_path

    tested_path = os.path.join(root_path, path)
    if os.path.isfile(tested_path):
        return tested_path

    tested_path = os.path.join(root_path, os.path.basename(path))
    if os.path.isfile(tested_path):
        return tested_path

    return None


def find_valid_texture_path(path, textures):
    # The path and the textures' paths are supposed to have
    # already been normalized.

    split_path = reversed(path.split("/"))
    split_paths = map(lambda p: p.split("/"), textures.keys())

    for i, name in enumerate(split_path):
        split_paths = filter(lambda sp: len(sp) > i and sp[-(i+1)] == name, split_paths) # noqa

        if len(split_paths) == 0:
            break
        elif len(split_paths) == 1:
            return "/".join(split_paths[0])

    return None


def model_embed_images(images, images_bytes,
                       optimize_textures, fallback_texture, root_path,
                       image_options, textures, quiet):
    optimized_textures = {}
    normalized_textures = normalize_textures(textures)

    image = images
    while image:
        if image.bytes_length > 0:
            continue

        image_path = ffi.string(image.path).decode("utf-8")

        # If textures exists, we don't look for files on the file system
        valid_image_path = None
        if normalized_textures is not None:
            valid_image_path = normalize_path(image_path)
            valid_image_path = find_valid_texture_path(valid_image_path, normalized_textures)  # noqa
        else:
            valid_image_path = find_valid_path(image_path, root_path)
            if valid_image_path is not None:
                valid_image_path = os.path.abspath(valid_image_path)

        # Unable to find a valid image path
        if valid_image_path is None:
            if fallback_texture is not None:
                print("Warning: Cannot resolve file %s, using the fallback texture instead." % image_path) # noqa
                valid_image_path = None
            else:
                raise ValueError("Cannot resolve file %s" % image_path)

        # If valid_image_path have already been seen, do not reoptimize...
        if valid_image_path in optimized_textures:
            optimized_texture = optimized_textures[valid_image_path]
            image.bytes_length = optimized_texture.bytes_length
            image.bytes = optimized_texture.bytes
            image.id = optimized_texture.id
            image = image.next
            continue

        # Get the bytes indeed
        image_io = None
        if valid_image_path is None:
            image_io = fallback_texture
        elif textures is not None:
            image_io = textures[valid_image_path]
        else:
            image_io = io.BytesIO(open(valid_image_path, "rb").read())

        # Optimizing the texture if requested
        if optimize_textures:
            if not quiet:
                if valid_image_path is not None:
                    print("Optimizing texture %s..." % valid_image_path)
                else:
                    print("Optimizing fallback texture...")
            output_io = io.BytesIO()
            yoga.image.optimize(image_io, output_io, image_options)
            image_io = output_io

        image_io.seek(0)
        image_bytes = image_io.read()

        # Convert to cffi
        image_bytes_c = ffi.new("char[%d]" % len(image_bytes), image_bytes)
        image.bytes_length = len(image_bytes)
        image.bytes = image_bytes_c
        image.id = len(optimized_textures)

        optimized_textures[valid_image_path] = image
        image = image.next

        # @note Save the bytes to a dictionnary so that the garbage collector
        # does not occur before exporting the scene a bit later
        images_bytes[valid_image_path] = image_bytes_c
