# Lossless Image Compression Algorithm (Original Architecture)

> **Historical Document**: This document describes the original design and implementation of the algorithm as created in **December 2025** by **Koné Abdoul-Aziz Moyé**, prior to subsequent optimizations (such as Line Reduction, Background Omission, and Binary Bitstream Serialization).
>
> The original presentation slides accompanying this version can be found in [`readme.pdf`](readme.pdf).

---

## 1. Original Concept & Motivation

The original algorithm explored an alternative mathematical paradigm to image compression:
Rather than processing 2D pixel grids through standard scanline prediction filters (as in PNG/DEFLATE), the algorithm treated an image as a **discrete dictionary of color classes**, mapping each color to the coordinates where it appeared.

To reduce 2D spatial coordinates into compressible numerical streams:
1. Every pixel $(x, y) \in \mathbb{N}^2$ was mapped bijectively to a 1D scalar $z \in \mathbb{N}$.
2. The scalars were sorted and delta-encoded.
3. Repeated differences were compacted using a custom string run-length format.
4. Decimal numbers were converted into a 77-symbol ASCII alphabet (Base-77).
5. The resulting text dictionary was archived using LZMA (`preset=9`).

---

## 2. Original Compression Pipeline

The original compression process (`algo_de_compression.py`) operated in sequential steps:

### Step 1: Pixel Extraction (`getpixel`)
Iterated pixel-by-pixel across the entire width and height of the image using Pillow:
```python
(width, height) = im.size
color_positions = {}
for x in range(width):
    for y in range(height):
        pixel_color = im.getpixel((x, y))
        if pixel_color not in color_positions:
            color_positions[pixel_color] = []
        color_positions[pixel_color].append((x, y))
```
*Note:* Every individual pixel was collected. Background pixels (e.g., massive white canvases) were treated identically to foreground strokes.

---

### Step 2: Szudzik's Elegant Pairing Function
To map $\mathbb{N}^2 \to \mathbb{N}$ bijectively without gaps, Matthew Szudzik's pairing function was applied to every coordinate pair:

$$\pi(x, y) = \begin{cases} y^2 + x & \text{if } x < y \\ x^2 + x + y & \text{if } x \ge y \end{cases}$$

In Python:
```python
def pairing_function(pixel: tuple) -> int:
    if int(pixel[0]) < int(pixel[1]): 
        return int(pixel[1])**2 + int(pixel[0])
    else: 
        return int(pixel[0])**2 + int(pixel[0]) + int(pixel[1])
```

---

### Step 3: Backward Delta Encoding
For each color, the list of paired integers was sorted in ascending order. Then, adjacent differences were calculated iterating backwards:
```python
def delta_encode(positions: list) -> list:
    for i in range(len(positions)):
        positions[i] = pairing_function(positions[i])
    positions.sort()
    
    # Calculate differences (iterate backwards)
    for i in range(1, len(positions)):
        positions[-i] -= positions[-(i+1)] 
    
    # Add '+' prefix to indicate a positive difference
    for i in range(1, len(positions)):
        positions[i] = "+" + str(positions[i])
    return positions
```

---

### Step 4: String Rewriting (Run-Length Encoding)
Consecutive identical differences were compacted using multiplication syntax `*`:
- Input list: `[100, '+5', '+3', '+3', '+12', '+35', '+35', '+35']`
- Compacted string: `'100+5+3*2+12+35*3'`

```python
def rewrite(positions: list) -> str:
    left, right = 0, 1
    split_phrase = []

    while right < len(positions):
        if positions[right] == positions[left]:
            right += 1
        else:
            if right - left == 1:
                split_phrase.append(str(positions[left]))
            else:
                split_phrase.append(f'{positions[left]}*{right-left}')
            left = right
            right += 1
    if right - left == 1:
        split_phrase.append(str(positions[left]))
    else:
        split_phrase.append(f'{positions[left]}*{right-left}')
    return ''.join(split_phrase)
```

---

### Step 5: Base-77 Radix Conversion (`BaseN.py`)
To shrink character lengths in the intermediate text file, all decimal integers were translated into Base-77 using 77 printable ASCII characters:
```python
digitset = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!"$%&-/;<>=?@[]^_|~'

for color in color_positions.keys():
    temp_list = findall(r"\d+|[*+]", color_positions[color])
    for i in range(len(temp_list)):
        if temp_list[i].isdigit(): 
            temp_list[i] = DecToBaseN(int(temp_list[i]), digitset)
    color_positions[color] = ''.join(temp_list)
```

---

### Step 6: Text File Serialization & LZMA Packaging
The encoded dictionary was written line-by-line into an intermediate text file `information.txt`:
```text
(255, 255, 255, 255):0+1+2*15+40...
(0, 0, 0, 255):aB+4*3+gH...
width,height:579,782
```
Finally, `information.txt` was compressed into an archive using Python's `lzma` module:
```python
with open("information.txt", "rb") as info_file, lzma.open("information.txt.xz", "wb", preset=9) as compressed_file:
    compressed_file.write(info_file.read())
```

---

## 3. Original Decompression Pipeline

The original decoder (`algo_de_decompression.py`) performed the reciprocal sequence:
1. **Unpacked Archive**: Extracted `information.txt.xz` into `decompressed.txt`.
2. **Evaluated String Tuples**: Used `eval()` to parse string representations of color tuples:
   ```python
   color_positions = {eval(k): v for k, v in color_positions.items()}
   ```
3. **Decoded Base-77 to Base-10**: Reconstructed decimal strings using `BaseNToDec(num, digitset)`.
4. **Expanded Multipliers & Delta Decoded**: Replaced `val*rep` tokens and computed cumulative prefix sums to recover the original scalar integers.
5. **Inverse Pairing**:
   ```python
   def inverse_pairing(integer: int) -> tuple:
       s = floor(sqrt(integer))
       if integer - s*s < s: 
           return (integer - s*s, s)
       else: 
           return (s, integer - s*s - s)
   ```
6. **Reconstructed Canvas**: Called `img.putpixel(coord, color)` pixel-by-pixel for every coordinate.

---

## 4. Original Experimental Results (from `fichier_log.txt`)

| Image | Resolution | Format | Original Size | Compressed (.xz) | Result | Ratio |
|---|---|---|---|---|---|---|
| `image_test_1.jpg` | $217 \times 232$ | JPEG | 6,049 B | 25,960 B | **Failed** | $+329\%$ |
| `image_test_4.jpg` | $225 \times 225$ | JPEG | 10,917 B | 2,752 B | **Success** | $-74.8\%$ |
| `image_test_5.jpg` | $1779 \times 2560$ | JPEG | 305,367 B | 1,426,936 B | **Failed** (took 8h) | $+367\%$ |
| `image_test_6.png` | $691 \times 978$ | PNG | 214,216 B | 127,788 B | **Success** | **$-40.3\%$** |
| `image_test_7.png` | $768 \times 1024$ | PNG (Color) | 68,308 B | 85,404 B | **Failed** | $+25.0\%$ |
| `image_test_7_prime.png` | $581 \times 779$ | PNG (Quantized) | 191,210 B | 106,720 B | **Success** | **$-44.2\%$** |
| `image_test_8_prime.png` | $688 \times 907$ | PNG (Quantized) | 322,403 B | 198,836 B | **Success** | **$-38.3\%$** |
| `image_test_9.jpg` | $710 \times 948$ | JPEG | 83,948 B | 120,436 B | **Failed** | $+43.5\%$ |
| `image_test_9_prime.png` | $579 \times 782$ | PNG (Line-art) | 236,863 B | 129,964 B | **Success** | **$-45.1\%$** |

---

## 5. Identified Challenges in the Original Version

1. **Every Pixel Was Stored Individually**:
   A uniform horizontal line of 500 white pixels produced 500 separate pairing coordinates.
2. **Full Background Encoding**:
   The canvas background (often $>80\%$ of pixels) was fully listed and encoded in the text file.
3. **Execution Bottleneck**:
   Nested Python loops over `getpixel` and `putpixel` caused high execution times (up to 8 hours on a $1779 \times 2560$ image).
4. **Intermediate ASCII Overhead**:
   Converting numbers to ASCII characters and formatting with delimiters (`+`, `*`, `:`, `\n`) added significant uncompressed overhead before passing to LZMA.
5. **Security**:
   `eval(k)` in the decompressor introduced arbitrary code execution risks when parsing dictionary keys.

These challenges led to the subsequent architectural enhancements documented in the main [`README.md`](README.md): **Line Reduction**, **Dominant Background Omission**, **Vectorized Array Operations**, **Direct Binary Bitstream Serialization** and much more ;)
