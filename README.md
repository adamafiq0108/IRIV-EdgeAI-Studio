# IRIV EdgeAI Studio - latest update

| | |
|---|---|
| **Version** | `1.0.1` |
| **File** | [`irivai-1.0.1.tar.gz`](irivai-1.0.1.tar.gz) (7.0M) |
| **SHA-256** | `718da84afb4ebf0b2509ceeb83bf6d540f96e356defe1f351af8f935eee2ce6e` |
| **Built from** | commit `af63915` on `main` |

This branch holds only the latest manual-update file. Your settings,
recordings, models and systems are kept when you update.

## Update a board

1. **Download** [`irivai-1.0.1.tar.gz`](https://github.com/adamafiq0108/IRIV-EdgeAI-Studio/raw/updates/irivai-1.0.1.tar.gz) to your computer.
2. Open the dashboard and sign in with an **admin** account.
3. Open the account menu (top right) and choose **About**.
4. In the **Software update** card, press **Choose update file**, pick the
   file, then press **Install**. The board checks every file against its
   manifest, installs the new version beside the old one, and restarts.

The previous version is kept: the same card offers **Go back to <version>**
if you need it.

## Check the download (optional)

```bash
sha256sum irivai-1.0.1.tar.gz
# must print: 718da84afb4ebf0b2509ceeb83bf6d540f96e356defe1f351af8f935eee2ce6e
```

## Download from the board itself

```bash
curl -LO https://github.com/adamafiq0108/IRIV-EdgeAI-Studio/raw/updates/irivai-1.0.1.tar.gz
```
