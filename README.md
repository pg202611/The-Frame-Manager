# The Frame Manager 3.0.1

**An unofficial, English-language Windows photo manager for Samsung The Frame Art Mode.**

The Frame Manager is a Python desktop application built with assistance from ChatGPT. It allows you to browse, import, delete, and configure Art Mode photos from a Windows computer, without relying on SmartThings or the TV remote for routine library management.

> **Important:** This edition deliberately **does not offer JPEG comparison or replacement of photos already on the TV**. A previous mass-replacement workflow was followed by a slideshow problem on one tested TV. The slideshow worked again after a Smart Hub reset and fresh import, but the precise cause was not established. The feature was removed as a precaution.

Developed and tested primarily with a **2022 Samsung The Frame, 32-inch (LS03B)**. Compatibility with other models and firmware versions is **not guaranteed**. This project is independent of Samsung.

## Features

- **Browse two libraries:** View TV photos and thumbnails alongside JPEG files on your computer.
- **Import photos:** Upload one photo or a batch, checking each new upload against the TV inventory.
- **Optional optimization when importing:** Upload an unchanged JPEG or optimize it at JPEG quality **90, 85, or 80**. Your original Windows files are never modified.
- **Delete photos:** Remove TV copies individually or in batches, with a verification step. Local originals remain untouched.
- **Mats and Art effects:** Change available mat styles, mat colors, and artistic filters individually or for selected groups.
- **Safe batch controls:** Monitor progress in the bottom status bar and request **Stop after current photo** without interrupting a photo mid-operation.
- **Source ID tracking:** Track stable source IDs, TV IDs, photo metadata, and history in a local SQLite database.
- **Sortable tables:** Sort by column, resize columns, and keep their widths across sessions.
- **Exports and backups:** Export CSV data and back up the local database.

### Intentionally excluded

- **Replace JPEG / batch JPEG replacement:** Not available in this edition; there is no replacement workflow hidden behind the interface.
- **Compare JPEGs:** The dedicated quality-comparison window and its processing code have been removed.
- **Automated cropping:** The separate cropper used in the development project is not part of this package.
- **PC-driven slideshow:** The TV's built-in slideshow is used. There is no continuous PC connection required after a normal upload or settings operation.

## Requirements

- Windows 11 (tested platform).
- Python **3.12** with the Windows `py` launcher.
- A compatible Samsung The Frame TV reachable on the **same trusted local network**.
- JPEG source files of **1920 × 1080** pixels, up to **15 MB** each, for upload.
- Dependencies listed in [`requirements.txt`](requirements.txt): `samsungtvws`, `Pillow`, and `packaging`.

No ChatGPT account, cloud account, or subscription is required to run the program.

## Installation

1. Install [Python 3.12](https://www.python.org/downloads/) and its Windows `py` launcher.
2. Download the release ZIP and extract all files into a **new folder**.
3. Open Command Prompt in that folder and run:

   ```bat
   py -3.12 -m pip install -r requirements.txt
   ```

4. Double-click **`start_manager.bat`**.
5. In **Settings**, enter the local IPv4 address of **your own TV**. The public release does **not** contain a preconfigured address.
6. Allow TV pairing if Samsung prompts for authorization.
7. Select your local JPEG folder, review the detected photos, and start by testing a **small batch of 1–3 photos**.

The public edition's interface, comments, diagnostics, and documentation are in **English**. The private/personal edition of the project is maintained separately.

## Using the program

### Importing photos

Select JPEGs from the right-hand library. In the middle panel, choose **None**, **JPEG 90**, **JPEG 85**, or **JPEG 80** under *JPEG optimization*. Click **Import** and confirm. Each source JPEG remains unchanged on your PC.

If the TV returns Samsung error **`-11`** (insufficient space), stop and inspect the TV; free space in the general TV settings is not always the same as available Art Mode storage.

### Editing Art Mode settings

Select one or more TV photos from the left-hand library. Change only the desired mat, color, or Art effect and click **Apply to selected photos**. A filter applied by the program is recorded locally, but the tested API does **not reliably reveal the active filter for each photo**. An unknown filter must not be interpreted as *None*.

### Removing photos

Select TV entries and click **Delete**, confirming the action. Deleting removes copies **from the TV**, not the corresponding source JPEGs on Windows.

### Batch cancellation

Use **Stop after current photo** while a batch is running. The current photo is allowed to complete before subsequent photos are skipped. This is a voluntary stop mechanism, not an emergency termination of a Samsung operation.

## Upgrading from 3.0 or 2.1.x

1. Close the previous program completely.
2. Back up the entire `the_frame_manager_data/` folder, including `manager_v2.sqlite-wal` and `manager_v2.sqlite-shm` if they exist.
3. Extract 3.0.1 into a **new program folder**. Do **not** mix obsolete Python scripts with this release.
4. Copy the old `the_frame_manager_data/` into the new folder, or start fresh if your Smart Hub was reset and previous Samsung photo IDs no longer represent the same photos.
5. Launch `start_manager.bat` and refresh the TV inventory before applying changes.

The database file retains the historical filename `manager_v2.sqlite` for compatibility. Avoid sharing it publicly.

## Known limitations and safety notes

- Samsung's Art Mode API is accessed through an **unofficial third-party library**, and behavior can change across models and firmware versions.
- Successful upload and mat changes can be verified from the TV inventory; **per-photo filter effects cannot be independently verified through the tested API**.
- A slideshow problem was observed after mass JPEG replacement on the development TV. The issue was resolved after a Smart Hub reset and fresh import, but causation remains **unproven**. The related feature has therefore been removed.
- No software can guarantee that TV firmware or storage will never fail. **Back up all original images and test with small batches first.**

## Privacy and security

This public source/release ZIP contains **no personal photos, original JPEGs, device-pairing token, personal IP address, SQLite database, operation logs, or developer preferences**. It does not introduce project-specific telemetry.

The application will create `the_frame_manager_data/` at runtime. That folder may contain sensitive information, including:

- `token.txt` — Samsung pairing/connection token.
- `preferences.json` — TV IP address and local settings.
- `manager_v2.sqlite` and its journal files — photo associations, file paths, and operation history.
- `journal.log`, `operations.jsonl`, exports, backups, optimized image copies, and thumbnails.

**Never upload your working data directory, personal photos, logs, or full post-use program folder to GitHub.** Share only a clean source ZIP. The included `.gitignore` helps but does not prevent every possible mistake.

## Files

```text
The-Frame-Manager/
├── the_frame_manager.py  # English desktop interface and batch operations
├── frame_core.py         # TV API, JPEG imports, SQLite and preferences
├── start_manager.bat     # Windows launcher
├── requirements.txt      # Python dependencies
├── README.md             # Documentation
└── .gitignore            # Excludes private local data and images
```

## Credits and license

Created with ChatGPT assistance, using the open-source [`samsungtvws`](https://github.com/xchwarze/samsung-tv-ws-api) project. Samsung, The Frame, Art Mode, and SmartThings belong to their respective trademark owners.

This project is not associated with, sponsored by, or supported by Samsung. Refer to the **LICENSE** file in the repository, if present, for redistribution rights. Making a repository public does not automatically establish an open-source license.
