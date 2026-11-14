# Lossless Image Compression Algorithm (LIC)

An advanced lossless image compression engine built in Python, engineered for clean 2D raster imagery including line-art, comics, manga, diagrams, technical illustrations, and UI screenshots.

> [!NOTE]
> For a walkthrough of the original December 2025 prototype (Base-77 ASCII encoding and legacy slides), see [`old_readme.md`](old_readme.md).

---

## Table of Contents

- [Overview](#overview)
- [Architecture & Formats (LIC3 to LIC6)](#architecture--formats-lic3-to-lic6)
- [How the Algorithm Works](#how-the-algorithm-works)
  - [1. Compression Pipeline](#1-compression-pipeline)
  - [2. Decompression Pipeline](#2-decompression-pipeline)
  - [3. Mathematical Foundations](#3-mathematical-foundations)
- [Repository Structure](#repository-structure)
- [Installation & Quickstart](#installation--quickstart)
- [Benchmark Results (Cumulative Milestones)](#benchmark-results-cumulative-milestones)
- [Complexity & Performance Optimizations](#complexity--performance-optimizations)
- [Upcoming Work & Remaining Optimizations](#upcoming-work--remaining-optimizations)
- [License & Notice](#license--notice)

---

## Overview

Traditional codecs like PNG/DEFLATE compress scanline streams via 1D line prediction filters paired with LZ77 and Huffman coding.

This project implements a geometric 2D alternative:
1. **Reversible Channel Decorrelation**: Decoupling Red, Green, and Blue using a modular Green-Difference transform ($G, R-G, B-G$).
2. **Dominant Background Omission**: Detecting the canvas background color and omitting it completely from coordinate payloads.
3. **2D Contour Demarcation & Flood Fill**: Tracing outer perimeter loops of solid shapes into 8-direction Chain Codes, omitting interior pixels entirely.
4. **Morton 2D Z-Order Indexing**: Preserving 2D neighborhood compactness across both X and Y dimensions.
5. **Horizontal Line Reduction & Delta Encoding**: Collapsing contiguous pixels into 1D extremity segments with relative coordinate deltas ($\Delta z$).
6. **Binary Bitstream Serialization**: Packing metadata and coordinates into variable-length unsigned LEB128 bytes, without ASCII formatting.
7. **Entropy Coding**: Compressing the resulting binary bitstream with Python's LZMA/XZ implementation at preset 9.

---

## Architecture & Formats (LIC3 to LIC6)

The codec includes seamless backward compatibility across multiple milestones:

| Format Magic | Milestones & Features Included |
| :--- | :--- |
| **`LIC3`** | Background Omission + Szudzik Pairing + Line Reduction + Delta Encoding + Binary Varint + LZMA. |
| **`LIC4`** | Replaced Szudzik pairing with **Morton 2D Z-Order Curve Indexing** (bit-interleaving). |
| **`LIC5`** | Added **2D Contour Demarcation** (Moore-Neighbor 8-direction chain codes) + Painter-order flood-fill reconstruction. |
| **`LIC6`** *(current encoder)* | Added **Reversible Green-Difference Channel Decorrelation** for RGB/RGBA data. |

---

## How the Algorithm Works

### Pipeline Overview

```
[ Input Image (PNG / RGBA) ]
       │
       ▼
1. Reversible Channel Decorrelation for RGB/RGBA: dr=(r-g)%256, g=g, db=(b-g)%256
       │
       ▼
2. Dominant Background Omission: Identify mode color, pre-fill canvas
       │
       ▼
3. 2D Contour Demarcation: Trace outer boundaries of connected color components (area ≥ 500 px)
   into 4-bit nibble chain codes (Painter layer 1)
       │
       ▼
4. Non-covered scanline segment extraction with Morton 2D Z-order curve
       │
       ▼
5. Sort segments, apply Delta Encoding (Δz = z_i - z_{i-1})
       │
       ▼
6. Binary Varint (LEB128) serialization: magic | width | height | channels | bg | contours | segments
       │
       ▼
7. LZMA Compression (preset=9) → Output: compressed/<image_name>.lic
```

---

### 1. Compression Pipeline

Implemented in [`lossless-image-compression-algorithm/algo_de_compression.py`](lossless-image-compression-algorithm/algo_de_compression.py):

1. **Reversible Channel Decorrelation**:
   Multi-channel RGB/RGBA images undergo a modular difference transform: $\Delta R = (R - G) \pmod{256}$ and $\Delta B = (B - G) \pmod{256}$, keeping $G$ as reference.
2. **Dominant Background Omission**:
   Detects the most frequent color after decorrelation using packed uint32 values. The decoder pre-fills the canvas with it; pixels that match reconstructed contours need no separate coordinate data.
3. **2D Contour Demarcation & Chain Codes**:
   For each non-background color occurring at least 500 times, labels 4-connected components in a cropped mask. Components of at least 500 pixels are traced as 8-direction chains; chains longer than 10 steps are stored as 4-bit nibbles (2 steps per byte). The decoder fills the traced boundary's interior.
4. **Morton Z-Order Curve Indexing**:
   Maps $(x, y) \to \mathbb{N}$ by bit-interleaving 16-bit coordinates, maintaining 2D spatial locality.
5. **Delta Varint Serialization**:
   Segments are sorted by start position, delta-encoded, and packed into unsigned LEB128 bytes.
6. **Archive Output**:
   The binary LIC6 bitstream is wrapped in an LZMA/XZ archive (preset 9) and saved by default to [`lossless-image-compression-algorithm/compressed/<image_name>.lic`](lossless-image-compression-algorithm/compressed/).

Inputs are opened with Pillow. RGB and RGBA images keep their modes; all other modes (including grayscale, palette, and grayscale-alpha) are converted to RGBA before encoding. The current encoder writes LIC6. The decoder also accepts LIC3, LIC4, and LIC5 archives.

---

### 2. Decompression Pipeline

Implemented in [`lossless-image-compression-algorithm/algo_de_decompression.py`](lossless-image-compression-algorithm/algo_de_decompression.py):

1. **Read Bitstream**: Unpacks LZMA archive into raw binary bitstream.
2. **Canvas Pre-fill**: Initializes an image canvas pre-filled with the dominant background color.
3. **Contour Reconstruction & Fill (Painter Layer 1)**: Reconstructs boundaries from chain codes and fills their interiors with `binary_fill_holes`.
4. **Scanline Overlay (Painter Layer 2)**: Decodes Morton delta positions and paints either individual pixels or horizontal same-color spans over the canvas.
5. **Inverse Channel Decorrelation**: Restores original RGB channels: $R = (\Delta R + G) \pmod{256}$ and $B = (\Delta B + G) \pmod{256}$.
6. **Output**: Saves an RGB, RGBA, or grayscale PNG to [`lossless-image-compression-algorithm/decompressed/<archive_name>_decompressed.png`](lossless-image-compression-algorithm/decompressed/) by default. An optional second command-line argument sets an explicit output path.

---

### 3. Mathematical Foundations

#### Reversible Green-Difference Transform
$$\Delta R = (R - G) \bmod 256, \quad G = G, \quad \Delta B = (B - G) \bmod 256$$
Inversion:
$$R = (\Delta R + G) \bmod 256, \quad G = G, \quad B = (\Delta B + G) \bmod 256$$

#### Morton 2D Z-Order Bijection
Interleaves the bits of 16-bit unsigned coordinates $x$ and $y$:
$$z = \text{interleave}(x, y) = \sum_{i=0}^{15} \left( (x_i \ll 2i) \mid (y_i \ll (2i + 1)) \right)$$

---

## Repository Structure

```text
image-lossless-compression/
├── README.md                                          # Complete technical documentation
├── old_readme.md                                      # Original Dec 2025 algorithm documentation
├── extracted_chat_history.md                          # Full persistent session transcript & log
├── NOTICE                                             # Authorship & attribution notice
├── LICENSE                                            # Apache License 2.0
├── requirements.txt
└── lossless-image-compression-algorithm/
    ├── algo_de_compression.py                         # LIC6 compression pipeline
    ├── algo_de_decompression.py                       # LIC6 decompression pipeline (backward compatible)
    ├── batch_test_compression.py                      # Benchmarking tool with graceful signal handling
    ├── notes.txt                                      # Research exploration logs
    ├── compressed/                                    # Default output folder for .lic archives
    ├── decompressed/                                  # Default output folder for reconstructed PNGs
    ├── images/                                        # Test PNG benchmark suite
    └── results/                                       # Timestamped benchmark reports & cumulative history
```

---

## Installation & Quickstart

### Setup

```bash
cd .. && source bin/activate
cd image-lossless-compression
pip install -r requirements.txt
```

### Running Compression & Decompression

```bash
cd lossless-image-compression-algorithm

# Compress image (saved to compressed/<image_stem>.lic)
python algo_de_compression.py images/image_test_9_prime.png

# Decompress archive (saved to decompressed/<archive_stem>_decompressed.png)
python algo_de_decompression.py compressed/image_test_9_prime.lic
```

### Running Batch Benchmarks

```bash
# Benchmark images with bit-for-bit verification enabled by default
python batch_test_compression.py -n all

# Skip the reconstruction comparison for a faster run
python batch_test_compression.py -n all --no-verify

---

## Benchmark Results (Cumulative Milestones)

Results tracked across [`results/cumulative_history.txt`](lossless-image-compression-algorithm/results/cumulative_history.txt):

| Image Filename | Dimensions | Original Size | Szudzik (`LIC3`) | Morton + Contours (`LIC5`) | Channel Decorrelation (`LIC6`) | Net Space Saved | Lossless Match |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `image_test_9_prime.png` | $579 \times 782$ | 236,863 B | 94,076 B (39.7%) | 92,656 B (39.1%) | **92,708 B (39.1%)** | **-144,155 B (-60.9%)** | ✅ 100% |
| `cdd20-cartoon-5266407.png` | $3000 \times 3000$ | 385,804 B | 234,400 B (60.8%) | 219,760 B (57.0%) | **219,808 B (57.0%)** | **-165,996 B (-43.0%)** | ✅ 100% |
| `cute-cat-relaxing-cozy-mat.png` | $1500 \times 2000$ | 2,449,768 B | 2,402,568 B (98.1%) | 2,402,192 B (98.1%) | **2,343,216 B (95.7%)** | **-106,552 B (-4.3%)** | ✅ 100% |
| `Screenshot 01-26-28.png` | $638 \times 844$ | 252,149 B | 206,052 B (81.7%) | 201,420 B (79.9%) | **195,212 B (77.4%)** | **-56,937 B (-22.6%)** | ✅ 100% |
| `Screenshot 01-27-00.png` | $638 \times 844$ | 277,084 B | 228,600 B (82.5%) | 225,180 B (81.3%) | **218,280 B (78.8%)** | **-58,804 B (-21.2%)** | ✅ 100% |
| `Screenshot 01-25-52.png` | $642 \times 885$ | 314,798 B | 265,744 B (84.4%) | 262,692 B (83.4%) | **255,568 B (81.2%)** | **-59,230 B (-18.8%)** | ✅ 100% |
| `Screenshot 01-26-09.png` | $642 \times 885$ | 312,541 B | 248,748 B (79.6%) | 247,984 B (79.3%) | **241,020 B (77.1%)** | **-71,521 B (-22.9%)** | ✅ 100% |
| `Screenshot 01-26-00.png` | $642 \times 885$ | 258,792 B | 174,880 B (67.6%) | 167,992 B (64.9%) | **167,608 B (64.8%)** | **-91,184 B (-35.2%)** | ✅ 100% |
| `Screenshot 01-26-18.png` | $642 \times 885$ | 127,692 B | 77,076 B (60.4%) | 72,792 B (57.0%) | **72,212 B (56.6%)** | **-55,480 B (-43.4%)** | ✅ 100% |
| `Screenshot 01-23-25.png` | $698 \times 967$ | 177,268 B | 120,700 B (68.1%) | 119,400 B (67.4%) | **119,604 B (67.5%)** | **-57,664 B (-32.5%)** | ✅ 100% |
| `Screenshot 01-26-46.png` | $638 \times 844$ | 86,225 B | 58,824 B (68.2%) | 58,324 B (67.6%) | **58,220 B (67.5%)** | **-28,005 B (-32.5%)** | ✅ 100% |

---

## Complexity & Performance Optimizations

1. **Elimination of $O(C \times W \times H)$ Mask Bottlenecks**:
   Replaced per-color full-image scans with **uint32 scalar packing + single-pass `argsort` + boundary offsets**.
2. **Localized Bounding-Box Component Labeling**:
   Morphological labeling and hole filling operate exclusively on cropped sub-regions rather than full canvas grids.
3. **Asynchronous Signal-Safe Benchmarking**:
   `batch_test_compression.py` traps interrupt signals (`SIGINT`, `SIGTERM`), compiling and persisting all completed images immediately.

---

## Upcoming Work & Remaining Optimizations

The following remaining optimizations from the research roadmap have not yet been implemented:

1. **Optional Entropy Coder Selection**:
   - The current encoder and decoder use LZMA/XZ archives. A future option could add another entropy coder for workloads that prioritize speed over archive size.
2. **Direct Camera RAW Ingestion (Bayer Mosaic)**:
   - Add native 12-bit/14-bit Bayer matrix loading via `rawpy` to compress untouched camera sensor arrays without demosaicing overhead.
3. **Cross-Row Segment Delta Prediction**:
   - For vertical or diagonal strokes, delta-encode segment start positions relative to the corresponding segment in row $y-1$, yielding smaller deltas than Morton scanline jumps.
4. **C/Rust Decoding Core**:
   - Port `inv_morton2d`, chain code unpacking, and `binary_fill_holes` to a compiled shared C/Rust library to bring decompression times down from milliseconds into sub-millisecond territory.

---

## License & Notice

This project is licensed under the [Apache License, Version 2.0](LICENSE).

See the [NOTICE](NOTICE) file for original authorship and attribution details:
- **Original Author & Creator:** Koné Abdoul-Aziz Moyé (aka kips_seven)
- Redistributions, forks, and derivative works must retain the attribution notices in the `NOTICE` file per Apache-2.0 Section 4(d).
