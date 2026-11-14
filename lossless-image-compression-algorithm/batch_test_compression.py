#!/usr/bin/env python3
# Copyright 2026 Koné Abdoul-Aziz Moyé
# Licensed under the Apache License, Version 2.0
# http://www.apache.org/licenses/LICENSE-2.0

"""
Batch Image Compression & Benchmarking Tool (with Graceful Signal Handling)
Compresses images using LIC6 (Decorrelation + Contours + Flood Fill + Morton + Delta + LZMA),
verifies lossless fidelity, and writes results to timestamped files inside the results/ folder.
Gracefully saves partial results if interrupted (SIGINT, SIGTERM, or KeyboardInterrupt).
"""

import os
import sys
import time
import argparse
import glob
import signal
from datetime import datetime
from PIL import Image, ImageChops
import lzma
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from algo_de_compression import serialize_image_to_bitstream
from algo_de_decompression import decompress_bitstream_to_image

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable

# Global state for signal handler
STATE = {
    "interrupted": False,
    "saved": False,
    "results": [],
    "total_orig_bytes": 0,
    "total_comp_bytes": 0,
    "total_comp_time": 0.0,
    "total_decomp_time": 0.0,
    "success_count": 0,
    "fidelity_failures": 0,
    "timestamped_file": None,
    "cumulative_file": None,
    "total_found": 0,
    "verify": True
}

def save_report():
    """Writes the current progress/report to files cleanly."""
    if STATE["saved"] or not STATE["results"]:
        return
    STATE["saved"] = True

    results = STATE["results"]
    total_orig_bytes = STATE["total_orig_bytes"]
    total_comp_bytes = STATE["total_comp_bytes"]
    total_comp_time = STATE["total_comp_time"]
    success_count = STATE["success_count"]
    fidelity_failures = STATE["fidelity_failures"]
    timestamped_file = STATE["timestamped_file"]
    cumulative_file = STATE["cumulative_file"]
    total_found = STATE["total_found"]
    verify = STATE["verify"]
    is_partial = STATE["interrupted"]

    timestamp_readable = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    net_savings_bytes = total_orig_bytes - total_comp_bytes
    net_savings_pct = (net_savings_bytes / total_orig_bytes * 100.0) if total_orig_bytes > 0 else 0.0

    status_tag = " (PARTIAL RUN - INTERRUPTED)" if is_partial else ""

    report_lines = [
        "=" * 90,
        f"             BINARY BITSTREAM LOSSLESS IMAGE COMPRESSION REPORT{status_tag}",
        "=" * 90,
        f"Date & Time:           {timestamp_readable}",
        f"Format:                LIC6 (Decorrelation + Contours + Flood Fill + Morton + Delta + LZMA)",
        f"Images Processed:      {len(results)} of {total_found:,} available",
        f"Fidelity Verification: {'Enabled (All tests verified bit-for-bit)' if verify else 'Disabled'}",
        "-" * 90,
        f"{'Filename':<38} | {'Dimensions':<10} | {'Original':<10} | {'Compressed':<10} | {'Ratio':<7} | {'Time':<7} | {'Lossless'}",
        "-" * 90,
    ]

    for r in results:
        if "error" in r:
            report_lines.append(f"{r['filename']:<38} | ERROR: {r['error']}")
        else:
            time_str = f"{r['comp_time']:.2f}s"
            lossless_str = "YES" if r["lossless_ok"] else ("NO" if r["lossless_ok"] is False else "N/A")
            report_lines.append(
                f"{r['filename']:<38} | {r['dimensions']:<10} | {r['orig_size']:>7,} B  | {r['comp_size']:>7,} B   | {r['ratio_pct']:>6.1f}% | {time_str:>7} | {lossless_str}"
            )

    report_lines.extend([
        "-" * 90,
        "SUMMARY STATISTICS:",
        f"  Total Images Processed:   {len(results)}",
        f"  Successful Reductions:    {success_count} ({success_count / len(results) * 100:.1f}%)",
        f"  Total Original Size:      {total_orig_bytes:,} bytes ({total_orig_bytes / 1024:.2f} KB)",
        f"  Total Compressed Size:    {total_comp_bytes:,} bytes ({total_comp_bytes / 1024:.2f} KB)",
        f"  Net Space Difference:     {net_savings_bytes:+,} bytes ({net_savings_pct:+.1f}%)",
        f"  Total Compression Time:   {total_comp_time:.2f}s (avg {total_comp_time / len(results):.3f}s/img)",
    ])
    if verify:
        report_lines.append(f"  Fidelity Failures:        {fidelity_failures} (Exact 100% matches: {len(results) - fidelity_failures})")
    report_lines.append("=" * 90 + "\n")

    report_content = "\n".join(report_lines)

    if timestamped_file:
        with open(timestamped_file, "w", encoding="utf-8") as f:
            f.write(report_content)

    if cumulative_file:
        with open(cumulative_file, "a", encoding="utf-8") as f:
            f.write(report_content)

    print("\n" + report_content)
    print(f"Report saved to:  {timestamped_file}")
    print(f"Appended to cumulative history: {cumulative_file}")

def signal_handler(signum, frame):
    """Handles SIGINT (Ctrl+C), SIGTERM, and custom termination signals."""
    signame = signal.Signals(signum).name if hasattr(signal, "Signals") else f"Signal {signum}"
    print(f"\n[!] Caught {signame}. Saving collected results before exit...")
    STATE["interrupted"] = True
    save_report()
    sys.exit(0)

def parse_args():
    parser = argparse.ArgumentParser(
        description="Batch compression tester for PNG images using Binary Bitstream Serialization."
    )
    parser.add_argument(
        "-n", "--count",
        type=str,
        default=None,
        help="Number of images to process (e.g. 10, 50, or 'all'). Prompted if not provided."
    )
    parser.add_argument(
        "-i", "--input-dir",
        type=str,
        default=os.path.join(SCRIPT_DIR, "images"),
        help="Directory containing PNG images (default: images/)."
    )
    parser.add_argument(
        "-r", "--results-dir",
        type=str,
        default=os.path.join(SCRIPT_DIR, "results"),
        help="Folder where all test reports are saved without erasing previous runs (default: results/)."
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        default=True,
        help="Verify bit-for-bit lossless fidelity by decompressing each image (default: True)."
    )
    parser.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="Skip decompression fidelity verification for faster benchmarking."
    )
    return parser.parse_args()

def main():
    args = parse_args()

    # Register signal handlers for clean exit on interrupt/kill
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal_handler)

    os.makedirs(args.results_dir, exist_ok=True)

    all_files = sorted(
        glob.glob(os.path.join(args.input_dir, "*.png")) +
        glob.glob(os.path.join(args.input_dir, "*.jpg"))
    )
    total_found = len(all_files)

    if total_found == 0:
        print(f"Error: No image files found in '{args.input_dir}'.")
        sys.exit(1)

    print(f"Discovered {total_found:,} image(s) in '{args.input_dir}'.")

    count_str = args.count
    if count_str is None:
        try:
            user_input = input(f"How many images would you like to process? [1-{total_found}, or 'all'] (default: {total_found}): ").strip()
            count_str = user_input if user_input else str(total_found)
        except (EOFError, KeyboardInterrupt):
            count_str = str(total_found)
            print(f"\nDefaulting to {count_str} images.")

    if count_str.lower() == "all":
        limit = total_found
    else:
        try:
            limit = min(int(count_str), total_found)
            if limit <= 0:
                print("Count must be greater than 0.")
                sys.exit(1)
        except ValueError:
            print(f"Invalid count '{count_str}', defaulting to {total_found}.")
            limit = total_found

    selected_files = all_files[:limit]

    run_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    timestamped_file = os.path.join(args.results_dir, f"batch_test_{run_timestamp}.txt")
    cumulative_file = os.path.join(args.results_dir, "cumulative_history.txt")

    # Update global state
    STATE["timestamped_file"] = timestamped_file
    STATE["cumulative_file"] = cumulative_file
    STATE["total_found"] = total_found
    STATE["verify"] = args.verify

    print(f"\nProcessing {len(selected_files)} image(s)...")
    print(f"Fidelity verification: {'Enabled' if args.verify else 'Disabled'}")
    print(f"Saving report to: '{timestamped_file}'\n")

    for file_path in tqdm(selected_files, desc="Compressing", unit="img"):
        if STATE["interrupted"]:
            break

        base_name = os.path.basename(file_path)
        orig_size = os.path.getsize(file_path)
        STATE["total_orig_bytes"] += orig_size

        try:
            orig_img = Image.open(file_path)
            im = orig_img.convert("RGBA") if orig_img.mode not in ("RGB", "RGBA") else orig_img

            t0 = time.time()
            bitstream = serialize_image_to_bitstream(im)
            comp_bytes = lzma.compress(bitstream, preset=9)
            comp_time = time.time() - t0

            comp_size = len(comp_bytes)
            STATE["total_comp_bytes"] += comp_size
            STATE["total_comp_time"] += comp_time

            ratio_pct = (comp_size / orig_size) * 100.0
            is_success = comp_size < orig_size
            if is_success:
                STATE["success_count"] += 1

            decomp_time = 0.0
            lossless_ok = None
            if args.verify:
                t1 = time.time()
                rec_img = decompress_bitstream_to_image(bitstream)
                decomp_time = time.time() - t1
                STATE["total_decomp_time"] += decomp_time

                diff = ImageChops.difference(im, rec_img)
                lossless_ok = (diff.getbbox() is None)
                if not lossless_ok:
                    STATE["fidelity_failures"] += 1

            STATE["results"].append({
                "filename": base_name,
                "dimensions": f"{im.size[0]}x{im.size[1]}",
                "mode": im.mode,
                "orig_size": orig_size,
                "comp_size": comp_size,
                "ratio_pct": ratio_pct,
                "is_success": is_success,
                "comp_time": comp_time,
                "decomp_time": decomp_time,
                "lossless_ok": lossless_ok
            })

        except Exception as e:
            STATE["results"].append({
                "filename": base_name,
                "error": str(e)
            })

    # Save final report cleanly
    save_report()

if __name__ == "__main__":
    main()
