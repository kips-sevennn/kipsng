#!/usr/bin/env python3
# Copyright 2026 Koné Abdoul-Aziz Moyé
# Licensed under the Apache License, Version 2.0
# http://www.apache.org/licenses/LICENSE-2.0

"""
Lossless Image Decompression Pipeline (LIC6)
Reads a binary LZMA archive and reconstructs the image with 100% lossless fidelity:
- Pre-fills canvas with the dominant background color.
- Reconstructs solid shapes from 2D contour boundary chain codes via flood-fill.
- Overlays Morton scanline segments using the Painter model.
- Applies inverse Green-Difference channel decorrelation if flagged (format LIC6).
Backward compatible with formats LIC5 (Contours), LIC4 (Morton only), and LIC3 (Szudzik pairing).
"""

from PIL import Image
from math import sqrt, floor
import lzma
import numpy as np
from scipy.ndimage import binary_fill_holes
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DECOMPRESSED_DIR = os.path.join(SCRIPT_DIR, "decompressed")

# Moore-Neighbor 8 directions: (dx, dy)
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]

def inverse_decorrelate_rgba(arr: np.ndarray) -> np.ndarray:
    """
    Inverts Green-Difference decorrelation:
    R = (dr + G) mod 256
    G = g
    B = (db + G) mod 256
    A = a
    """
    out = arr.copy()
    dr = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    db = arr[:, :, 2].astype(np.int16)
    out[:, :, 0] = ((dr + g) % 256).astype(np.uint8)
    out[:, :, 1] = g.astype(np.uint8)
    out[:, :, 2] = ((db + g) % 256).astype(np.uint8)
    return out

def inverse_pairing_szudzik(integer: int) -> tuple[int, int]:
    """
    Inverse of Szudzik's pairing function recovering (x, y) from integer z.
    """
    s = floor(sqrt(integer))
    if integer - s*s < s: 
        return (integer - s*s, s)
    else: 
        return (s, integer - s*s - s)

def inv_morton2d(z: int) -> tuple[int, int]:
    """
    Inverse of Morton 2D Z-order curve recovering (x, y) from integer z.
    """
    def compact_by_1(a: int) -> int:
        a &= 0x55555555
        a = (a ^ (a >> 1)) & 0x33333333
        a = (a ^ (a >> 2)) & 0x0f0f0f0f
        a = (a ^ (a >> 4)) & 0x00ff00ff
        a = (a ^ (a >> 8)) & 0x0000ffff
        return a

    x = compact_by_1(z)
    y = compact_by_1(z >> 1)
    return x, y

def decode_varint(stream: bytes, offset: int) -> tuple[int, int]:
    """
    Decodes an unsigned variable-length integer (LEB128) from a binary buffer.
    """
    val = 0
    shift = 0
    while True:
        b = stream[offset]
        offset += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return val, offset

def decompress_bitstream_to_image(bitstream: bytes) -> Image.Image:
    """
    Parses the binary bitstream and reconstructs the image.
    Supports LIC6 (Decorrelation + Contours + Morton), LIC5 (Contours + Morton),
    LIC4 (Morton only), and LIC3 (Szudzik pairing).
    """
    offset = 0
    magic = bitstream[offset:offset+4]
    offset += 4
    if magic not in (b"LIC6", b"LIC5", b"LIC4", b"LIC3"):
        raise ValueError(f"Invalid magic header: {magic} (expected LIC6, LIC5, LIC4, or LIC3)")

    width, offset = decode_varint(bitstream, offset)
    height, offset = decode_varint(bitstream, offset)
    channels = bitstream[offset]
    offset += 1

    is_decorrelated = False
    if magic == b"LIC6":
        is_decorrelated = (bitstream[offset] == 1)
        offset += 1

    dominant_bg = tuple(bitstream[offset:offset+channels])
    offset += channels

    # 1. Initialize canvas pre-filled with the dominant background color
    canvas = np.full((height, width, channels), dominant_bg, dtype=np.uint8)

    # 2. Section A: Reconstruct Contour Shapes (for LIC6 and LIC5)
    if magic in (b"LIC6", b"LIC5"):
        num_contours, offset = decode_varint(bitstream, offset)
        for _ in range(num_contours):
            c_color = tuple(bitstream[offset:offset+channels])
            offset += channels
            csx, offset = decode_varint(bitstream, offset)
            csy, offset = decode_varint(bitstream, offset)
            chain_len, offset = decode_varint(bitstream, offset)

            num_bytes = (chain_len + 1) // 2
            raw_nibbles = bitstream[offset:offset+num_bytes]
            offset += num_bytes

            chain = []
            for b in raw_nibbles:
                chain.append(b & 0x0F)
                if len(chain) == chain_len:
                    break
                d2 = (b >> 4) & 0x0F
                if d2 != 0x0F:
                    chain.append(d2)
                    if len(chain) == chain_len:
                        break

            # Draw outer boundary
            b_img = np.zeros((height, width), dtype=bool)
            cx, cy = csx, csy
            b_img[cy, cx] = True
            for d in chain:
                dx, dy = DIRS[d]
                cx += dx
                cy += dy
                b_img[cy, cx] = True

            # Flood-fill solid shape
            filled = binary_fill_holes(b_img)
            canvas[filled] = c_color

    # 3. Section B: Replay Scanline Segments (overlaid painter style)
    inverse_pos_fn = inv_morton2d if magic in (b"LIC6", b"LIC5", b"LIC4") else inverse_pairing_szudzik

    num_colors, offset = decode_varint(bitstream, offset)
    for _ in range(num_colors):
        color = tuple(bitstream[offset:offset+channels])
        offset += channels
        num_elements, offset = decode_varint(bitstream, offset)

        cur_z = 0
        for _ in range(num_elements):
            tagged, offset = decode_varint(bitstream, offset)
            is_segment = tagged & 1
            delta_z = tagged >> 1
            cur_z += delta_z

            x1, y1 = inverse_pos_fn(cur_z)
            if is_segment:
                span, offset = decode_varint(bitstream, offset)
                canvas[y1, x1 : x1 + span + 1] = color
            else:
                canvas[y1, x1] = color

    # 4. Invert channel decorrelation if applied
    if is_decorrelated:
        canvas = inverse_decorrelate_rgba(canvas)

    mode = "RGBA" if channels == 4 else ("RGB" if channels == 3 else "L")
    return Image.fromarray(canvas, mode=mode)

def decompress_archive(archive_path: str = None, output_image: str = None) -> str:
    """
    Reads an LZMA archive containing binary bitstream and writes the output image to decompressed/.
    If output_image is not specified, defaults to 'decompressed/<archive_stem>_decompressed.png'.
    """
    if archive_path is None:
        archive_path = os.path.join(SCRIPT_DIR, "compressed", "image_test_9_prime.lic")
        if not os.path.exists(archive_path):
            archive_path = os.path.join(SCRIPT_DIR, "compressed.lic")

    if not os.path.exists(archive_path):
        raise FileNotFoundError(f"Archive file not found: {archive_path}")

    os.makedirs(DEFAULT_DECOMPRESSED_DIR, exist_ok=True)

    if output_image is None:
        stem = os.path.splitext(os.path.basename(archive_path))[0]
        output_image = os.path.join(DEFAULT_DECOMPRESSED_DIR, f"{stem}_decompressed.png")
    else:
        out_dir = os.path.dirname(output_image)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

    with lzma.open(archive_path, "rb") as f:
        bitstream = f.read()

    result_image = decompress_bitstream_to_image(bitstream)
    result_image.save(output_image)
    w, h = result_image.size
    print(f"Decompressed '{archive_path}' -> '{output_image}' ({w}x{h}, mode {result_image.mode})")
    return output_image

if __name__ == "__main__":
    target_archive = sys.argv[1] if len(sys.argv) > 1 else None
    target_out = sys.argv[2] if len(sys.argv) > 2 else None
    decompress_archive(target_archive, target_out)
