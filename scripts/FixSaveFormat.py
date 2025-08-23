# -*- coding: utf-8 -*-
"""
Script to save VLC frame captured images with the
correct filename for referencing.
- In VLC, go to Preferences>Video
- Set the Video snapshots Prefix as $N
- Check Sequential Numbering
- Image saves as "Video.mp400123.png", this makes
    is save as "Video.png"
"""

import os

# Change this to your folder path
folder_path = "path/to/your/folder"

for filename in os.listdir(folder_path):
    if filename.endswith(".png") and ".mp4" in filename:
        # Split at ".mp4" and take the first part
        new_name = filename.split(".mp4")[0] + ".png"
        
        # Get full paths
        old_path = os.path.join(folder_path, filename)
        new_path = os.path.join(folder_path, new_name)
        
        # Rename the file
        os.rename(old_path, new_path)
        print(f"Renamed: {filename} -> {new_name}")
