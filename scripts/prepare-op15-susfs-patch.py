"""Adapt the pinned SUSFS patch's context to ACK android16-6.12-2026-03_r54."""

import argparse
from pathlib import Path


def adapt_patch(text: str) -> str:
    # r54 added dma-buf.h and renamed the page-count helper. Keep both
    # upstream changes; only adjust the context used to locate SUSFS hunks.
    replacements = (
        (
            " #include <linux/user_events.h>\n"
            " #include <linux/rseq.h>\n"
            " #include <linux/ksm.h>\n",
            " #include <linux/rseq.h>\n"
            " #include <linux/ksm.h>\n"
            " #include <linux/dma-buf.h>\n",
        ),
        (
            " \tif (!vma_pages(vma))\n \t\tgoto show_pad;",
            " \tif (!vma_data_pages(vma))\n \t\tgoto show_pad;",
        ),
    )
    for old, new in replacements:
        if text.count(old) != 1:
            raise ValueError("Unexpected SUSFS patch context; check the pinned source revisions")
        text = text.replace(old, new, 1)
    return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    args.destination.write_text(adapt_patch(args.source.read_text()), encoding="utf-8")
