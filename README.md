# The Frame Manager 3.0

**An unofficial Windows photo manager for Samsung The Frame Art Mode**

The Frame Manager is a free, community-built Python application created with help from ChatGPT. It lets you manage the photos in your Samsung The Frame's Art Mode directly from a Windows PC, without having to rely on the TV remote or SmartThings for routine photo management.

The software was developed and tested primarily with a **2022 Samsung The Frame 32-inch (LS03B)**. Compatibility with other models or firmware versions **is not guaranteed**. It is not affiliated with or endorsed by Samsung.

> **Start small:** Back up your data and test the application with one to three photos before running any batch operation.

## Features

- **Browse and organize:** View Art Mode photos and TV thumbnails alongside your local JPEG collection.
- **Import and delete:** Upload or remove individual photos or batches, with TV-side verification.
- **Mats and filters:** Change mat styles, mat colors, and available Art Mode filters, individually or in batches.
- **JPEG optimization:** Compare the original with JPEG quality 90, 85, and 80; choose the balance between visual quality and file size.
- **Reversible replacement:** Replace a TV photo with any supported JPEG version, including restoring the unoptimized original. Upload and verification happen before the previous TV photo is deleted.
- **Batch controls:** Monitor progress in the status bar and request **Stop after this photo** during batch operations.
- **Photo tracking:** Maintain stable source-photo IDs, TV-to-source associations, operation history, and a local SQLite database.
- **Customizable interface:** Sort and resize columns, remember their widths and other preferences, and use the interface in **English or French**.
- **Exports and backups:** Export CSV records and create local database backups.

**Not included:** The separate photo-cropping application used during development and the experimental art-filter preview. Source photos must already meet the upload requirements below.

## Requirements

- Windows 11 (the tested environment).
- Python **3.12**, with the Windows `py` launcher available.
- A Samsung The Frame with compatible Art Mode API functionality, reachable from the PC on the same trusted local network.
- Source images in **JPEG format, exactly 1920 × 1080 pixels, no larger than 15 MB** for upload.
- Python packages listed in [`requirements.txt`](requirements.txt): `samsungtvws`, `Pillow`, and `packaging`.

No cloud account, paid API, or ChatGPT subscription is needed to run the application.

## Installation (Windows)

1. Install [Python 3.12](https://www.python.org/downloads/) and make sure the Windows `py` launcher is installed.
2. Download the public release ZIP and extract its **files into a new folder**.
3. Open **Command Prompt** in that folder and install dependencies:

   ```bat
   py -3.12 -m pip install -r requirements.txt
   ```

4. Double-click **`lancer_manager.bat`**.
5. Open **Settings / Paramètres**, enter **your own TV's local IPv4 address**, and save. **No TV IP address is preconfigured** in this release.
6. Approve the connection on your TV if prompted.
7. Select your local source-JPEG folder. Verify the detected photos and TV associations before sending changes to the TV.
8. If you prefer English, select it in **Settings / Paramètres**.

The program creates a private `the_frame_manager_data/` folder when first used. **Do not upload or share that folder.**

## Upgrading from version 2.1.x

1. Close the previous version completely.
2. Back up the **entire** `the_frame_manager_data/` folder, including any `manager_v2.sqlite-wal` and `manager_v2.sqlite-shm` files that may be present.
3. Extract v3.0 into a **new program folder**, rather than mixing old and new Python scripts.
4. Copy your backed-up `the_frame_manager_data/` directory into the new v3.0 folder.
5. Install dependencies if needed, launch `lancer_manager.bat`, and verify your source IDs, TV associations, IP address, JPEG settings, and column widths.

The database file intentionally retains the name `manager_v2.sqlite` for compatibility with existing records. The application does **not** modify your original source JPEGs.

## How JPEG replacement works

For a photo already on the TV, you can choose **Original (no optimization)**, **JPEG 90**, **JPEG 85**, or **JPEG 80**. You can move between these versions in either direction, even when the new file is larger.

For each replacement, the app prepares the selected version from your local source JPEG, uploads it, checks that it is present on the TV, and restores known mat/filter settings before removing the previous TV photo. The TV normally assigns a **new Samsung content ID**; the app keeps the source-photo ID stable in its local database.

**Important:** A replacement needs enough temporary space for the new photo **before** the old one can be deleted. If the TV rejects the upload or an expected setting cannot be restored, the app attempts to preserve the old photo and stops the batch rather than blindly continuing. A failed operation may leave an extra uploaded copy that requires manual review.

## Known limitations

- **Art filters cannot be reliably read back per photo** through the tested API. The app records filters it applied itself, but an unknown filter remains *unknown*—not necessarily *none*. Filters changed using the remote or SmartThings may not be reflected in its database.
- The TV may report **`-11` (insufficient space)** despite showing available space in its general storage settings.
- Samsung's **Art Mode API is unofficially accessed through a third-party library**, and behavior can change with different TV models or firmware updates.
- The app has been tested primarily with **one 2022 32-inch The Frame**; features may not work on every model.
- There is no guarantee against data loss or other software/device issues. Keep your own originals and backups.

## Privacy and security

The **public release archive** does not include the developer's photos, device-pairing token, local SQLite database, operation logs, personal Windows paths, personal TV IP address, or preferences. It does not add project-specific telemetry. It connects to the TV configured by the user through the third-party `samsungtvws` package.

Once you use the app, **`the_frame_manager_data/` becomes private**. It can contain:

- `token.txt` — Samsung connection/pairing token.
- `preferences.json` — TV IP address and user preferences.
- `manager_v2.sqlite`, `-wal`, `-shm` — photo metadata, file paths, and history.
- `journal.log`, `operations.jsonl`, exports, backups, caches, and thumbnails.

**Never publish your working application folder, private data directory, original photos, logs, or screenshots revealing private images or paths.** Share only a clean source/release archive. The included `.gitignore` reduces accidental uploads but is not an absolute security guarantee.

## Project files

```text
The_Frame_Manager_3.0/
├── the_frame_manager.py   # Interface, photo lists, and batch operations
├── frame_core.py          # Samsung API, JPEG handling, SQLite, and preferences
├── lancer_manager.bat     # Windows launcher
├── requirements.txt       # Python dependencies
├── README.md              # This guide
└── .gitignore             # Excludes private runtime data and image files
```

## Acknowledgments and disclaimer

Built with assistance from ChatGPT and the open-source [`samsungtvws`](https://github.com/xchwarze/samsung-tv-ws-api) package. **Samsung**, **The Frame**, and **SmartThings** are trademarks of their respective owners. This is an independent, unofficial project.

If you want others to be legally allowed to modify and redistribute the code, add an appropriate open-source **`LICENSE`** file to the repository. Publishing a repository publicly by itself does not grant such permissions.
