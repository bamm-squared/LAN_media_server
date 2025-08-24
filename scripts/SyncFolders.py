# -*- coding: utf-8 -*-
"""
Quick script to sync all the files between two folders.
"""
import os
import shutil

def sync_folders(folder1, folder2):
    """
    Synchronize two folders by copying files that exist in one
    but not the other. It does not delete or overwrite files.
    """

    # Ensure both folders exist
    if not os.path.isdir(folder1) or not os.path.isdir(folder2):
        raise ValueError("Both paths must be existing directories.")

    # Helper to copy missing files
    def copy_missing(src, dst):
        for root, _, files in os.walk(src):
            rel_dir = os.path.relpath(root, src)
            dst_dir = os.path.join(dst, rel_dir)
            os.makedirs(dst_dir, exist_ok=True)

            for f in files:
                src_file = os.path.join(root, f)
                dst_file = os.path.join(dst_dir, f)

                if not os.path.exists(dst_file):
                    shutil.copy2(src_file, dst_file)
                    print(f"Copied: {src_file} -> {dst_file}")

    # Copy missing files both ways
    copy_missing(folder1, folder2)
    copy_missing(folder2, folder1)


if __name__ == "__main__":
    folder_a = "path/to/folderA"
    folder_b = "path/to/folderB"

    sync_folders(folder_a, folder_b)
    print("Sync complete.")
