#!/usr/bin/env python3
# Copyright 2026 Koné Abdoul-Aziz Moyé
# Licensed under the Apache License, Version 2.0
# http://www.apache.org/licenses/LICENSE-2.0

"""
Lossless Image Compression Pipeline (LIC6)
Integrates:
1. Reversible Channel Decorrelation (Green-Difference Transform: dr=(r-g)%256, g=g, db=(b-g)%256).
2. Dominant Background Omission (statistical mode color pre-filled).
3. 2D Contour Boundary Demarcation & Flood Fill (Moore-neighbor chain-code tracing for solid shapes).
4. Morton 2D Z-Order Indexing with 1D Scanline Line Reduction on remaining details.
5. Delta Encoding on coordinate positions.
6. Binary Bitstream Serialization (Varint / LEB128) before LZMA compression.
"""

from PIL import Image
import lzma
import numpy as np
from scipy.ndimage import label, binary_fill_holes
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COMPRESSED_DIR = os.path.join(SCRIPT_DIR, "compressed")

# Moore-Neighbor 8 directions: (dx, dy)
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]

def forward_decorrelate_rgba(arr: np.ndarray) -> np.ndarray:
    """
    Applies reversible Green-Difference decorrelation:
    dr = (R - G) mod 256
    g  = G
    db = (B - G) mod 256
    a  = A
    """
    out = arr.copy()
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    out[:, :, 0] = ((r - g) % 256).astype(np.uint8)
    out[:, :, 1] = g.astype(np.uint8)
    out[:, :, 2] = ((b - g) % 256).astype(np.uint8)
    return out

def morton2d(x: int, y: int) -> int:
    """
    Morton 2D Z-order curve mapping (x, y) -> N.
    Interleaves bits of x and y to preserve 2D spatial locality.
    """
    def split_by_1(a: int) -> int:
        a &= 0x0000ffff
        a = (a | (a << 8)) & 0x00ff00ff
        a = (a | (a << 4)) & 0x0f0f0f0f
        a = (a | (a << 2)) & 0x33333333
        a = (a | (a << 1)) & 0x55555555
        return a

    return split_by_1(int(x)) | (split_by_1(int(y)) << 1)

def encode_varint(val: int) -> bytes:
    """
    Encodes an integer into variable-length bytes (LEB128).
    Eliminates all ASCII text formatting and string delimiter overhead.
    """
    buf = bytearray()
    while val >= 0x80:
        buf.append((val & 0x7F) | 0x80)
        val >>= 7
    buf.append(val & 0x7F)
    return bytes(buf)

def trace_contour_cropped(sub_mask: np.ndarray, start_x: int, start_y: int) -> list[int]:
    """
    Traces the outer perimeter boundary of a 2D shape inside a localized cropped mask.
    Returns the sequence of 8-direction steps (0..7).
    """
    h_sub, w_sub = sub_mask.shape
    curr_x, curr_y = start_x, start_y
    b_dir = 6
    chain = []
    for _ in range(500000):
        found = False
        for i in range(8):
            dir_idx = (b_dir + i) % 8
            dx, dy = DIRS[dir_idx]
            nx, ny = curr_x + dx, curr_y + dy
            if 0 <= nx < w_sub and 0 <= ny < h_sub and sub_mask[ny, nx]:
                chain.append(dir_idx)
                curr_x, curr_y = nx, ny
                b_dir = (dir_idx + 4 + 1) % 8
                found = True
                break
        if not found or (curr_x == start_x and curr_y == start_y):
            break
    return chain

def serialize_image_to_bitstream(im: Image.Image) -> bytes:
    """
    Scans the image and builds a compact binary bitstream (format LIC6):
    1. Applies Green-Difference channel decorrelation to eliminate inter-channel redundancy.
    2. Identifies the dominant background color to omit it from coordinates.
    3. Traces outer contours for large solid shapes (area >= 500 px) for flood-fill.
    4. Encodes non-filled foreground details as Morton delta-encoded scanlines.
    """
    width, height = im.size
    raw_arr = np.asarray(im)
    channels = raw_arr.shape[2] if len(raw_arr.shape) == 3 else 1

    # Apply channel decorrelation if multi-channel (RGB or RGBA)
    is_decorrelated = (channels in (3, 4))
    if is_decorrelated:
        arr = forward_decorrelate_rgba(raw_arr)
    else:
        arr = raw_arr

    # 1. Fast uint32 packing for O(N log N) scalar operations
    if channels == 4:
        packed = (arr[:, :, 0].astype(np.uint32)
                | (arr[:, :, 1].astype(np.uint32) << 8)
                | (arr[:, :, 2].astype(np.uint32) << 16)
                | (arr[:, :, 3].astype(np.uint32) << 24))
    elif channels == 3:
        packed = (arr[:, :, 0].astype(np.uint32)
                | (arr[:, :, 1].astype(np.uint32) << 8)
                | (arr[:, :, 2].astype(np.uint32) << 16))
    else:
        packed = arr.reshape(height, width).astype(np.uint32)

    unique_packed, inverse, u_counts = np.unique(packed, return_inverse=True, return_counts=True)
    dom_idx = np.argmax(u_counts)
    dom_packed = unique_packed[dom_idx]

    if channels == 4:
        dominant_color = (
            int(dom_packed & 0xFF),
            int((dom_packed >> 8) & 0xFF),
            int((dom_packed >> 16) & 0xFF),
            int((dom_packed >> 24) & 0xFF)
        )
    elif channels == 3:
        dominant_color = (
            int(dom_packed & 0xFF),
            int((dom_packed >> 8) & 0xFF),
            int((dom_packed >> 16) & 0xFF)
        )
    else:
        dominant_color = (int(dom_packed),)

    sorted_indices = np.argsort(inverse.ravel(), kind='mergesort')
    color_boundaries = np.concatenate(([0], np.cumsum(u_counts)))

    # 2. Detect large solid shapes via localized bounding box labeling
    sim_canvas = np.full((height, width, channels), dominant_color, dtype=np.uint8)
    contour_shapes = []

    for ci in range(len(unique_packed)):
        if unique_packed[ci] == dom_packed or u_counts[ci] < 500:
            continue

        start = color_boundaries[ci]
        end = color_boundaries[ci + 1]
        flat_idx = sorted_indices[start:end]
        ys = flat_idx // width
        xs = flat_idx % width

        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())
        crop_h = y1 - y0 + 1
        crop_w = x1 - x0 + 1

        sub_mask = np.zeros((crop_h, crop_w), dtype=bool)
        sub_mask[ys - y0, xs - x0] = True

        lbl, n_comp = label(sub_mask)
        if n_comp == 0:
            continue

        sizes = np.bincount(lbl.ravel())
        valid_comps = np.where(sizes[1:] >= 500)[0] + 1
        if len(valid_comps) == 0:
            continue

        c_packed = unique_packed[ci]
        if channels == 4:
            c_tuple = (
                int(c_packed & 0xFF),
                int((c_packed >> 8) & 0xFF),
                int((c_packed >> 16) & 0xFF),
                int((c_packed >> 24) & 0xFF)
            )
        elif channels == 3:
            c_tuple = (
                int(c_packed & 0xFF),
                int((c_packed >> 8) & 0xFF),
                int((c_packed >> 16) & 0xFF)
            )
        else:
            c_tuple = (int(c_packed),)

        for comp_id in valid_comps:
            comp = (lbl == comp_id)
            c_ys, c_xs = np.where(comp)
            sub_start_y, sub_start_x = int(c_ys[0]), int(c_xs[0])
            global_start_x = x0 + sub_start_x
            global_start_y = y0 + sub_start_y

            chain = trace_contour_cropped(comp, sub_start_x, sub_start_y)
            if len(chain) > 10:
                contour_shapes.append((c_tuple, global_start_x, global_start_y, chain))
                b_sub = np.zeros((crop_h, crop_w), dtype=bool)
                cx, cy = sub_start_x, sub_start_y
                b_sub[cy, cx] = True
                for d in chain:
                    dx, dy = DIRS[d]
                    cx += dx
                    cy += dy
                    b_sub[cy, cx] = True
                filled_sub = binary_fill_holes(b_sub)
                sim_canvas[y0:y0 + crop_h, x0:x0 + crop_w][filled_sub] = c_tuple

    # 3. Extract scanline segments only for pixels that differ from sim_canvas
    mismatch_mask = np.any(sim_canvas != arr, axis=-1)

    color_elements = {}
    for y in range(height):
        if not np.any(mismatch_mask[y]):
            continue
        row = arr[y]
        m_row = mismatch_mask[y]
        x = 0
        while x < width:
            if not m_row[x]:
                x += 1
                continue
            c = tuple(int(v) for v in row[x])
            x_end = x
            while x_end + 1 < width and m_row[x_end + 1] and np.array_equal(row[x_end + 1], row[x]):
                x_end += 1

            if c not in color_elements:
                color_elements[c] = []
            z1 = morton2d(x, y)
            span = x_end - x
            color_elements[c].append((z1, span))
            x = x_end + 1

    # 4. Build binary bitstream
    bitstream = bytearray()
    # Format magic: LIC6 (Channel decorrelation + Contours + Morton)
    bitstream.extend(b"LIC6")
    bitstream.extend(encode_varint(width))
    bitstream.extend(encode_varint(height))
    bitstream.append(channels)
    bitstream.append(1 if is_decorrelated else 0)
    bitstream.extend(bytes(dominant_color))

    # Section A: Contour Shapes (Demarcation + Chain codes)
    bitstream.extend(encode_varint(len(contour_shapes)))
    for c_tuple, sx, sy, chain in contour_shapes:
        bitstream.extend(bytes(c_tuple))
        bitstream.extend(encode_varint(sx))
        bitstream.extend(encode_varint(sy))
        bitstream.extend(encode_varint(len(chain)))
        for i in range(0, len(chain), 2):
            d1 = chain[i]
            d2 = chain[i + 1] if i + 1 < len(chain) else 0x0F
            bitstream.append((d2 << 4) | (d1 & 0x0F))

    # Section B: Overlay Scanline Segments
    bitstream.extend(encode_varint(len(color_elements)))
    for c, elems in color_elements.items():
        elems.sort(key=lambda item: item[0])
        bitstream.extend(bytes(c))
        bitstream.extend(encode_varint(len(elems)))

        prev_z = 0
        for z1, span in elems:
            delta_z = z1 - prev_z
            prev_z = z1
            if span == 0:
                bitstream.extend(encode_varint((delta_z << 1) | 0))
            else:
                bitstream.extend(encode_varint((delta_z << 1) | 1))
                bitstream.extend(encode_varint(span))

    return bytes(bitstream)

def compress_image(image_path: str, output_archive: str = None) -> str:
    """
    Compresses an image directly to a binary LZMA archive in compressed/.
    If output_archive is not specified, defaults to 'compressed/<image_stem>.lic'.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    os.makedirs(DEFAULT_COMPRESSED_DIR, exist_ok=True)

    if output_archive is None:
        stem = os.path.splitext(os.path.basename(image_path))[0]
        output_archive = os.path.join(DEFAULT_COMPRESSED_DIR, f"{stem}.lic")
    else:
        out_dir = os.path.dirname(output_archive)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

    orig = Image.open(image_path)
    im = orig.convert("RGBA") if orig.mode not in ("RGB", "RGBA") else orig

    # Build compact binary bitstream
    bitstream = serialize_image_to_bitstream(im)

    # Compress bitstream directly with LZMA preset 9
    with lzma.open(output_archive, "wb", preset=9) as f:
        f.write(bitstream)

    orig_size = os.path.getsize(image_path)
    comp_size = os.path.getsize(output_archive)
    print(f"Compressed '{image_path}' -> '{output_archive}'")
    print(f"Original size: {orig_size:,} bytes | Compressed size: {comp_size:,} bytes | Ratio: {100 * comp_size / orig_size:.1f}%")
    return output_archive

if __name__ == "__main__":
    target_image = sys.argv[1] if len(sys.argv) > 1 else os.path.join(SCRIPT_DIR, "images", "image_test_9_prime.png")
    target_out = sys.argv[2] if len(sys.argv) > 2 else None
    compress_image(target_image, target_out)
